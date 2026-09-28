"""Проверка по плану в basic: отношения и функции, до которых запрос доходит через представления, правила и SQL-функции.

Валидатор видит только текст запроса; представление в public поверх чужой схемы он пропускает. PlanGuard
строит план каждого оператора (EXPLAIN без ANALYZE ничего не выполняет) и проверяет, что читают его узлы.
"""

import json
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

import pglast
from pglast.ast import DeclareCursorStmt, DefElem, DeleteStmt, ExplainStmt, InsertStmt, Node, SelectStmt, UpdateStmt
from pglast.stream import RawStream

from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.postgres.security.schema_guard import is_system_relation_name
from postgres_fastmcp.shared.errors import PlanAccessError


# Выполняет один оператор EXPLAIN целиком и возвращает его строки (ячейка "QUERY PLAN").
ExplainRunner = Callable[[str], Awaitable[list[RowResult] | None]]

RELATION_KIND = "relation"
FUNCTION_KIND = "function"

# Операторы, у которых есть план; EXPLAIN и DECLARE разворачиваются до вложенного запроса.
# SHOW, PREPARE, DEALLOCATE, FETCH, CLOSE, CREATE EXTENSION плана не имеют (EXECUTE запрещён валидатором).
_PLANNABLE_TYPES = (SelectStmt, InsertStmt, UpdateStmt, DeleteStmt)

# Значения, которыми опцию EXPLAIN выключают явно: generic_plan false / off / 0 / no.
_DISABLED_OPTION_VALUES = frozenset({"false", "off", "0", "no"})

# Встроенные функции; функции allowed_schema (расширения в public) тоже допустимы.
_BUILTIN_FUNCTION_SCHEMA = "pg_catalog"


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


def _plan_document(rows: list[RowResult] | None) -> object:
    """JSON-документ EXPLAIN (FORMAT JSON): psycopg отдаёт разобранный список, текст разбирается здесь."""
    value = rows[0].cells.get("QUERY PLAN") if rows else None
    return json.loads(value) if isinstance(value, str) else value


class PlanGuard:
    """Проверяет план каждого оператора: отношения — только allowed_schema (с префиксом), функции — pg_catalog и она."""

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

    async def check(self, query: str) -> None:
        """Построить план каждого планируемого оператора запроса и проверить его узлы.

        Запрос уже прошёл валидатор, поэтому разбирается без ошибок. Ошибка планирования (отношения нет)
        приходит из исполнителя как ошибка Postgres — та же, что дало бы выполнение.

        Args:
            query: SQL агента после валидации (с тегом-комментарием).

        Raises:
            PlanAccessError: План читает отношение или функцию вне разрешённого.
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
            if "Relation Name" in node:
                self._check_relation(node.get("Schema"), str(node["Relation Name"]))
            if "Function Name" in node:
                self._check_function(node.get("Schema"), str(node["Function Name"]))

    def _check_relation(self, schema: str | None, name: str) -> None:
        """Отношение плана: ровно allowed_schema, не системное, с префиксом, если он задан."""
        outside_prefix = self._table_prefix is not None and not name.lower().startswith(self._table_prefix)
        if schema != self._allowed_schema or is_system_relation_name(name) or outside_prefix:
            raise PlanAccessError(RELATION_KIND, f"{schema or '?'}.{name}")

    def _check_function(self, schema: str | None, name: str) -> None:
        """Табличная функция плана: встроенная или из allowed_schema."""
        if schema not in (_BUILTIN_FUNCTION_SCHEMA, self._allowed_schema):
            raise PlanAccessError(FUNCTION_KIND, f"{schema or '?'}.{name}")
