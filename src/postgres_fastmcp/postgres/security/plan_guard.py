"""Проверка по плану в basic: отношения и функции, до которых запрос доходит через представления, правила и SQL-функции.

Валидатор видит только текст запроса; представление в public поверх чужой схемы он пропускает. PlanGuard
строит план каждого оператора (EXPLAIN без ANALYZE ничего не выполняет) и проверяет, что читают его узлы.

Проверка закрыта по умолчанию: нет плана или узел сканирования не называет, что читает, — отказ. Цена —
редкие формы: соединение или агрегат, вынесенные postgres_fdw на удалённый сервер (Foreign Scan без
Relation Name), Custom Scan без отношения. ROWS FROM из нескольких функций (Function Scan без Function Name,
так Postgres переписывает и unnest(a, b)) проверяется по тексту вызовов из Function Call (VERBOSE).
"""

import json
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

import pglast
from pglast.ast import (
    DeclareCursorStmt,
    DefElem,
    DeleteStmt,
    ExplainStmt,
    FuncCall,
    InsertStmt,
    Node,
    SelectStmt,
    String,
    UpdateStmt,
)
from pglast.parser import ParseError
from pglast.stream import RawStream
from pglast.visitors import Visitor

from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.postgres.security.policies import BASIC_ALLOWED_FUNCTIONS
from postgres_fastmcp.postgres.security.schema_guard import is_system_relation_name
from postgres_fastmcp.shared.errors import PlanAccessError, PlanUnverifiableError


# Выполняет один оператор EXPLAIN целиком и возвращает его строки (ячейка "QUERY PLAN").
ExplainRunner = Callable[[str], Awaitable[list[RowResult] | None]]

RELATION_KIND = "relation"
FUNCTION_KIND = "function"

# Операторы, у которых есть план; EXPLAIN и DECLARE разворачиваются до вложенного запроса.
# SHOW, PREPARE, DEALLOCATE, FETCH, CLOSE, CREATE EXTENSION плана не имеют (EXECUTE запрещён валидатором).
_PLANNABLE_TYPES = (SelectStmt, InsertStmt, UpdateStmt, DeleteStmt)

# Значения, которыми опцию EXPLAIN выключают явно: generic_plan false / off / 0 / no.
_DISABLED_OPTION_VALUES = frozenset({"false", "off", "0", "no"})

# Встроенные функции — только из списка basic (pg_show_all_settings, pg_ls_dir и подобные закрыты);
# функции allowed_schema (расширения в public) допустимы все.
_BUILTIN_FUNCTION_SCHEMA = "pg_catalog"

# Узлы сканирования, которые без Relation Name читают неизвестно что (scanrelid = 0 при pushdown).
_RELATION_SCAN_TYPES = frozenset({"Foreign Scan", "Custom Scan"})
_FUNCTION_SCAN_TYPE = "Function Scan"


def _option_enabled(arg: Node | None) -> bool:
    """Опция EXPLAIN включена: без значения или со значением, отличным от false/off/0/no."""
    if arg is None:
        return True
    value = getattr(arg, "sval", None)
    if value is None:
        value = getattr(arg, "ival", None)
    if value is None:
        value = getattr(arg, "boolval", None)
    return str(value).lower() not in _DISABLED_OPTION_VALUES


def _generic_plan(explain: ExplainStmt) -> bool:
    """У EXPLAIN агента включена опция generic_plan: без неё план запроса с $1 не построить."""
    return any(
        isinstance(option, DefElem) and option.defname == "generic_plan" and _option_enabled(option.arg)
        for option in explain.options or ()
    )


def _plannable(node: Node | None, *, generic: bool = False) -> tuple[Node, bool] | None:
    """Оператор, план которого проверяется, и нужен ли GENERIC_PLAN; None — у оператора нет плана."""
    if isinstance(node, _PLANNABLE_TYPES):
        return node, generic
    if isinstance(node, ExplainStmt):
        return _plannable(node.query, generic=_generic_plan(node))
    if isinstance(node, DeclareCursorStmt):
        return _plannable(node.query, generic=generic)
    return None


def _plan_nodes(value: object) -> Iterator[dict[str, Any]]:
    """Все словари документа плана: Plans (в том числе InitPlan/SubPlan), Target Tables и прочие вложения."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _plan_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _plan_nodes(child)


def _plan_document(rows: list[RowResult] | None) -> list[dict[str, Any]]:
    """JSON-документ EXPLAIN (FORMAT JSON): psycopg отдаёт разобранный список, текст разбирается здесь.

    Raises:
        PlanUnverifiableError: Строк нет, ячейки нет или документ не список словарей с ключом Plan.
    """
    value = rows[0].cells.get("QUERY PLAN") if rows else None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise PlanUnverifiableError from None
    if not isinstance(value, list) or not value:
        raise PlanUnverifiableError
    if not all(isinstance(entry, dict) and isinstance(entry.get("Plan"), dict) for entry in value):
        raise PlanUnverifiableError
    return value


class _FunctionCalls(Visitor):
    """Собирает имена всех вызовов функций выражения, включая вложенные в аргументы."""

    def __init__(self) -> None:
        """Пустой список имён; каждое — кортеж частей имени (схема, функция) или (функция,)."""
        super().__init__()
        self.names: list[tuple[str, ...]] = []

    def visit_FuncCall(self, _ancestors: object, node: FuncCall) -> None:  # noqa: N802
        """Запомнить имя вызова; части, которые не строки, делают имя непроверяемым."""
        parts = tuple(part.sval or "" if isinstance(part, String) else "" for part in node.funcname or ())
        self.names.append(parts)


def _function_call_names(call: object) -> list[tuple[str | None, str]]:
    """Имена функций (схема или None, имя) из Function Call узла Function Scan (EXPLAIN VERBOSE).

    Для ROWS FROM Postgres печатает список выражений через запятую (deparse списка в ruleutils);
    "SELECT " + текст разбирается как список целей, так что годится и одно выражение, и несколько.

    Raises:
        PlanUnverifiableError: Текста нет, он не разбирается или в нём нет ни одного вызова функции.
    """
    if not isinstance(call, str) or not call.strip():
        raise PlanUnverifiableError(_FUNCTION_SCAN_TYPE)
    try:
        statements = pglast.parse_sql(f"SELECT {call}")
    except ParseError:
        raise PlanUnverifiableError(_FUNCTION_SCAN_TYPE) from None
    if len(statements) != 1 or not isinstance(statements[0].stmt, SelectStmt):
        raise PlanUnverifiableError(_FUNCTION_SCAN_TYPE)
    collector = _FunctionCalls()
    collector(statements[0].stmt)
    names: list[tuple[str | None, str]] = []
    for parts in collector.names:
        match parts:
            case (str(name),) if name:
                names.append((None, name))
            case (str(schema), str(name)) if schema and name:
                names.append((schema, name))
            case _:
                raise PlanUnverifiableError(_FUNCTION_SCAN_TYPE)
    if not names:
        raise PlanUnverifiableError(_FUNCTION_SCAN_TYPE)
    return names


class PlanGuard:
    """Проверяет план каждого оператора: отношения — allowed_schema (с префиксом), функции — она и список basic."""

    def __init__(self, explain: ExplainRunner, *, allowed_schema: str, table_prefix: str | None) -> None:
        """Инициализация с исполнителем EXPLAIN и правилами basic.

        Args:
            explain: Выполняет оператор EXPLAIN (тот же SET LOCAL, read-only) и возвращает строки.
            allowed_schema: Единственная схема отношений плана (public).
            table_prefix: Если задан, имена отношений плана должны начинаться с него (без учёта регистра).
        """
        self._explain = explain
        self._allowed_schema = allowed_schema
        self._table_prefix = table_prefix.lower() if table_prefix else None
        self._prefix_for_hint = table_prefix or None

    async def check(self, query: str) -> None:
        """Построить план каждого планируемого оператора запроса и проверить его узлы.

        Запрос уже прошёл валидатор, поэтому разбирается без ошибок. Ошибка планирования (отношения нет)
        приходит из исполнителя как ошибка Postgres — та же, что дало бы выполнение.

        Args:
            query: SQL агента после валидации (с тегом-комментарием).

        Raises:
            PlanAccessError: План читает отношение или функцию вне разрешённого.
            PlanUnverifiableError: Плана нет или узел сканирования не называет, что читает.
        """
        for raw in pglast.parse_sql(query):
            target = _plannable(raw.stmt)
            if target is None:
                continue
            statement, generic = target
            options = "VERBOSE, FORMAT JSON, GENERIC_PLAN" if generic else "VERBOSE, FORMAT JSON"
            rows = await self._explain(f"EXPLAIN ({options}) {RawStream()(statement)}")
            self._check_plan(_plan_document(rows))

    def _check_plan(self, plan: object) -> None:
        """Проверить каждый узел плана, где есть отношение или функция."""
        for node in _plan_nodes(plan):
            node_type = node.get("Node Type")
            if node_type in _RELATION_SCAN_TYPES and "Relation Name" not in node:
                raise PlanUnverifiableError(node_type)
            if node_type == _FUNCTION_SCAN_TYPE and "Function Name" not in node:
                self._check_function_calls(node.get("Function Call"))
            if "Relation Name" in node:
                self._check_relation(node.get("Schema"), str(node["Relation Name"]))
            if "Function Name" in node:
                self._check_function(node.get("Schema"), str(node["Function Name"]))

    def _check_relation(self, schema: str | None, name: str) -> None:
        """Отношение плана: ровно allowed_schema, не системное, с префиксом, если он задан."""
        outside_prefix = self._table_prefix is not None and not name.lower().startswith(self._table_prefix)
        if schema != self._allowed_schema or is_system_relation_name(name) or outside_prefix:
            raise PlanAccessError(
                RELATION_KIND,
                f"{schema or '?'}.{name}",
                allowed_schema=self._allowed_schema,
                table_prefix=self._prefix_for_hint,
            )

    def _check_function(self, schema: str | None, name: str) -> None:
        """Табличная функция плана: из allowed_schema или встроенная из списка basic."""
        builtin_allowed = schema == _BUILTIN_FUNCTION_SCHEMA and name.lower() in BASIC_ALLOWED_FUNCTIONS
        if schema != self._allowed_schema and not builtin_allowed:
            qualified_name = f"{schema or '?'}.{name}"
            raise self._function_error(qualified_name)

    def _check_function_calls(self, call: object) -> None:
        """Function Scan без Function Name (ROWS FROM из нескольких функций): каждый вызов из Function Call.

        Имя со схемой проверяется как у узла с Function Name. Имя без схемы (search_path = allowed_schema,
        так что это может быть и allowed_schema, и pg_catalog) допустимо, только если оно в списке basic.
        """
        for schema, name in _function_call_names(call):
            if schema is not None:
                self._check_function(schema, name)
            elif name.lower() not in BASIC_ALLOWED_FUNCTIONS:
                raise self._function_error(name)

    def _function_error(self, name: str) -> PlanAccessError:
        """Отказ по функции плана с подсказкой по правилам basic."""
        return PlanAccessError(
            FUNCTION_KIND, name, allowed_schema=self._allowed_schema, table_prefix=self._prefix_for_hint
        )
