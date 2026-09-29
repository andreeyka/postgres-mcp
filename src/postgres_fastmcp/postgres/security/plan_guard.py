"""Проверка по плану в basic: отношения и функции, до которых запрос доходит через представления, правила и SQL-функции.

Валидатор видит только текст запроса; представление в public поверх чужой схемы он пропускает. PlanGuard
строит план каждого оператора (EXPLAIN без ANALYZE ничего не выполняет) и проверяет, что читают его узлы.

Выражения узлов (Output, Filter, условия, ключи сортировки и группировки — ключи EXPLAIN VERBOSE) разбираются
pglast (plan_expressions) и проверяются по тем же правилам: функции и операторы — allowed_schema или pg_catalog
из списка basic, типы — allowed_schema или pg_catalog, строковый тип таблицы без префикса — как сама таблица.
Что решает только каталог (функция или тип без схемы), спрашивается SQL сервера в той же транзакции (plan_catalog).

Проверка закрыта по умолчанию: нет плана или узел сканирования не называет, что читает, — отказ. Цена —
редкие формы: соединение или агрегат, вынесенные postgres_fdw на удалённый сервер (Foreign Scan без
Relation Name), Custom Scan без отношения. ROWS FROM из нескольких функций (Function Scan без Function Name,
так Postgres переписывает и unnest(a, b)) проверяется по тексту вызовов из Function Call (VERBOSE).
"""

import json
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
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
    TypeName,
    UpdateStmt,
)
from pglast.stream import RawStream
from pglast.visitors import Visitor

from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.postgres.security.plan_catalog import BuiltinTypeNames, pg_catalog_functions, row_types
from postgres_fastmcp.postgres.security.plan_expressions import (
    EXPRESSION_PARSERS,
    FUNCTION_CALL_KEY,
    ExpressionNames,
    expression_texts,
    parse_target_list,
)
from postgres_fastmcp.postgres.security.policies import BASIC_ALLOWED_FUNCTIONS, NAME_LOOKUP_TYPES
from postgres_fastmcp.postgres.security.schema_guard import is_system_relation_name
from postgres_fastmcp.shared.errors import PlanAccessError, PlanUnverifiableError


# Выполняет одну строку SQL (EXPLAIN или запрос каталога сервера) на курсоре транзакции оператора и
# возвращает её строки.
ExplainRunner = Callable[[str], Awaitable[list[RowResult] | None]]

RELATION_KIND = "relation"
FUNCTION_KIND = "function"
TYPE_KIND = "type"

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
    """Собирает все вызовы функций выражения, включая вложенные в аргументы."""

    def __init__(self) -> None:
        """Пустой список вызовов в порядке обхода."""
        super().__init__()
        self.calls: list[FuncCall] = []

    def visit_FuncCall(self, _ancestors: object, node: FuncCall) -> None:  # noqa: N802
        """Запомнить вызов."""
        self.calls.append(node)


class _TypeNames(Visitor):
    """Собирает имена типов SQL агента: (схема или None, имя); у имени с базой данных — две последние части."""

    def __init__(self) -> None:
        """Пустой список имён в порядке обхода."""
        super().__init__()
        self.names: list[tuple[str | None, str]] = []

    def visit_TypeName(self, _ancestors: object, node: TypeName) -> None:  # noqa: N802
        """Запомнить имя типа; части не строки или пустые пропускаются."""
        parts = [part.sval for part in node.names or () if isinstance(part, String) and part.sval]
        if len(parts) == 1:
            self.names.append((None, parts[0]))
        elif len(parts) > 1:
            self.names.append((parts[-2], parts[-1]))


def _call_name(call: FuncCall) -> tuple[str | None, str]:
    """Имя вызова: (схема или None, имя).

    Raises:
        PlanUnverifiableError: Части имени не строки, пустые или их больше двух (имя с базой данных).
    """
    parts = tuple(part.sval if isinstance(part, String) else None for part in call.funcname or ())
    match parts:
        case (str(name),) if name:
            return None, name
        case (str(schema), str(name)) if schema and name:
            return schema, name
        case _:
            raise PlanUnverifiableError(_FUNCTION_SCAN_TYPE)


def _function_call_names(call: object, *, skip_outermost: bool) -> list[tuple[str | None, str]]:
    """Имена функций (схема или None, имя) из Function Call узла Function Scan (EXPLAIN VERBOSE).

    Для ROWS FROM Postgres печатает список выражений через запятую (deparse списка в ruleutils);
    "SELECT " + текст разбирается как список целей, так что годится и одно выражение, и несколько.
    Разбор должен дать только список целей: FROM, WHERE, UNION и прочее — признак текста, который
    не является списком вызовов.

    Args:
        call: Значение Function Call из плана.
        skip_outermost: Одиночная функция: внешний вызов — сама функция узла, она уже проверена по
            Function Name/Schema (функция public печатается без схемы); проверяются только вложенные.

    Raises:
        PlanUnverifiableError: Текста нет, он не разбирается, в нём не только список целей, у одиночной
            функции он не один вызов, а у нескольких функций — ни одного вызова.
    """
    statement = parse_target_list(call)
    if statement is None:
        raise PlanUnverifiableError(_FUNCTION_SCAN_TYPE)
    collector = _FunctionCalls()
    collector(statement)
    calls = collector.calls
    if skip_outermost:
        targets = statement.targetList or ()
        outermost = targets[0].val if len(targets) == 1 else None
        if not isinstance(outermost, FuncCall):
            raise PlanUnverifiableError(_FUNCTION_SCAN_TYPE)
        calls = [found for found in calls if found is not outermost]
    elif not calls:
        raise PlanUnverifiableError(_FUNCTION_SCAN_TYPE)
    return [_call_name(found) for found in calls]


@dataclass(slots=True)
class _CatalogNames:
    """Имена плана, которые решает только каталог; словари — упорядоченные множества в порядке обхода."""

    functions: dict[str, None] = field(default_factory=dict)
    unqualified_types: dict[str, None] = field(default_factory=dict)
    schema_types: dict[str, None] = field(default_factory=dict)


class PlanGuard:
    """Проверяет план каждого оператора: отношения — allowed_schema (с префиксом), функции — она и список basic.

    Выражения узлов (Output, Filter, ключи сортировки и прочие) проверяются после узлов: функции, операторы и
    типы — те же правила, строковый тип отношения allowed_schema без префикса — как само отношение.
    """

    def __init__(
        self,
        run: ExplainRunner,
        *,
        allowed_schema: str,
        table_prefix: str | None,
        builtin_types: BuiltinTypeNames | None = None,
    ) -> None:
        """Инициализация с исполнителем транзакции и правилами basic.

        Args:
            run: Выполняет строку SQL (EXPLAIN или запрос каталога) в транзакции проверяемого оператора
                (SET LOCAL уже выставлен) и возвращает строки.
            allowed_schema: Единственная схема отношений плана (public).
            table_prefix: Если задан, имена отношений плана должны начинаться с него (без учёта регистра).
            builtin_types: Кэш имён типов pg_catalog; None — свой на эту проверку.
        """
        self._run = run
        self._allowed_schema = allowed_schema
        self._table_prefix = table_prefix.lower() if table_prefix else None
        self._prefix_for_hint = table_prefix or None
        self._builtin_types = builtin_types or BuiltinTypeNames()

    async def check(self, query: str) -> None:
        """Построить план каждого планируемого оператора запроса и проверить его узлы и выражения.

        Запрос уже прошёл валидатор, поэтому разбирается без ошибок. Ошибка планирования (отношения нет)
        приходит из исполнителя как ошибка Postgres — та же, что дало бы выполнение.

        Args:
            query: SQL агента после валидации (с тегом-комментарием).

        Raises:
            PlanAccessError: План читает отношение, функцию или тип вне разрешённого.
            PlanUnverifiableError: Плана нет, узел сканирования не называет, что читает, или выражение
                не разбирается.
        """
        statements = [raw.stmt for raw in pglast.parse_sql(query)]
        await self._check_statement_types(statements)
        for raw_statement in statements:
            target = _plannable(raw_statement)
            if target is None:
                continue
            statement, generic = target
            options = "VERBOSE, FORMAT JSON, GENERIC_PLAN" if generic else "VERBOSE, FORMAT JSON"
            rows = await self._run(f"EXPLAIN ({options}) {RawStream()(statement)}")
            await self._check_plan(_plan_document(rows))

    async def _check_statement_types(self, statements: list[Node]) -> None:
        """Типы из SQL агента — до первого EXPLAIN, по тем же правилам, что типы выражений плана.

        Ошибка разбора EXPLAIN — оракул: (NULL::users).secret_note (нет колонки), '(1,2)'::users (число и
        типы полей) раскрывают структуру таблицы без префикса раньше, чем план дойдёт до проверки. Каталог
        спрашивается, только если есть тип без схемы вне кэша pg_catalog или со схемой allowed_schema без префикса.
        """
        collector = _TypeNames()
        for statement in statements:
            collector(statement)
        pending = _CatalogNames()
        for schema, name in collector.names:
            self._check_type(schema, name, pending)
        await self._check_catalog_names(pending)

    async def _check_plan(self, plan: object) -> None:
        """Сначала узлы (отношения и функции сканов), затем выражения, затем имена, которые решает каталог.

        Порядок сохраняет прежние отказы: узел, запрещённый и раньше, отклоняется с той же ошибкой, даже
        если выражение выше по плану тоже запрещено.
        """
        nodes = list(_plan_nodes(plan))
        for node in nodes:
            self._check_node(node)
        pending = _CatalogNames()
        for node in nodes:
            self._check_expressions(node, pending)
        await self._check_catalog_names(pending)

    def _check_node(self, node: dict[str, Any]) -> None:
        """Отношение или функция узла сканирования."""
        node_type = node.get("Node Type")
        if node_type in _RELATION_SCAN_TYPES and "Relation Name" not in node:
            raise PlanUnverifiableError(node_type)
        if node_type == _FUNCTION_SCAN_TYPE and "Function Name" not in node:
            self._check_function_calls(node.get("Function Call"), skip_outermost=False)
        elif node_type == _FUNCTION_SCAN_TYPE and "Function Call" in node:
            self._check_function_calls(node["Function Call"], skip_outermost=True)
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

    def _check_function_calls(self, call: object, *, skip_outermost: bool) -> None:
        """Вызовы из Function Call узла Function Scan, включая вложенные в аргументы.

        Без Function Name (ROWS FROM из нескольких функций) проверяется каждый вызов; у одиночной функции —
        все, кроме внешнего (он проверен по Function Name/Schema). Имя со схемой проверяется как у узла
        с Function Name. Имя без схемы (search_path = allowed_schema, так что это может быть и allowed_schema,
        и pg_catalog) допустимо, только если оно в списке basic.
        """
        for schema, name in _function_call_names(call, skip_outermost=skip_outermost):
            if schema is not None:
                self._check_function(schema, name)
            elif name.lower() not in BASIC_ALLOWED_FUNCTIONS:
                raise self._function_error(name)

    def _function_error(self, name: str) -> PlanAccessError:
        """Отказ по функции плана с подсказкой по правилам basic."""
        return PlanAccessError(
            FUNCTION_KIND, name, allowed_schema=self._allowed_schema, table_prefix=self._prefix_for_hint
        )

    def _check_expressions(self, node: dict[str, Any], pending: _CatalogNames) -> None:
        """Выражения узла: неразборчивое — PlanUnverifiableError, имена — по правилам basic.

        Имена функций Function Call пропускаются только у Function Scan: их уже проверил строгий путь
        _check_node. У любого другого узла Function Call проверяется как обычное выражение.
        """
        node_type = node.get("Node Type")
        strict_function_call = node_type == _FUNCTION_SCAN_TYPE
        for key, value in node.items():
            parse = EXPRESSION_PARSERS.get(key)
            if parse is None:
                continue
            texts = expression_texts(value)
            if texts is None:
                raise PlanUnverifiableError(node_type if isinstance(node_type, str) else None, key=key)
            for text in texts:
                names = parse(text)
                if names is None:
                    raise PlanUnverifiableError(node_type if isinstance(node_type, str) else None, key=key)
                check_functions = key != FUNCTION_CALL_KEY or not strict_function_call
                self._check_names(names, pending, check_functions=check_functions)

    def _check_names(self, names: ExpressionNames, pending: _CatalogNames, *, check_functions: bool) -> None:
        """Имена одного выражения; то, что решает только каталог, откладывается в pending."""
        if check_functions:
            for schema, name in names.functions:
                if schema is not None:
                    self._check_function(schema, name)
                elif name.lower() not in BASIC_ALLOWED_FUNCTIONS:
                    pending.functions[name] = None
        for schema, name in names.sequences:
            self._check_relation(schema or self._allowed_schema, name)
        for schema, name in names.operators:
            if schema is not None and schema not in (self._allowed_schema, _BUILTIN_FUNCTION_SCHEMA):
                qualified_name = f"{schema}.{name}"
                raise self._function_error(qualified_name)
        for schema, name in names.types:
            self._check_type(schema, name, pending)

    def _check_type(self, schema: str | None, name: str, pending: _CatalogNames) -> None:
        """Тип выражения: pg_catalog и allowed_schema; строковый тип отношения без префикса — через каталог.

        reg*-типы не отклоняются: их литерал уже разрешён по имени при создании объекта или планировании.
        """
        if schema == _BUILTIN_FUNCTION_SCHEMA:
            return
        if schema is not None and schema != self._allowed_schema:
            raise PlanAccessError(
                TYPE_KIND,
                f"{schema}.{name}",
                allowed_schema=self._allowed_schema,
                table_prefix=self._prefix_for_hint,
            )
        if self._table_prefix is None or name.lower().startswith(self._table_prefix):
            return
        if schema is None and name in NAME_LOOKUP_TYPES:
            return
        (pending.unqualified_types if schema is None else pending.schema_types)[name] = None

    async def _check_catalog_names(self, pending: _CatalogNames) -> None:
        """Функции без схемы вне списка basic — не из pg_catalog; строковые типы — не отношения без префикса."""
        if pending.functions:
            builtin = await pg_catalog_functions(self._run, list(pending.functions))
            for name in pending.functions:
                if name in builtin:
                    qualified_name = f"{_BUILTIN_FUNCTION_SCHEMA}.{name}"
                    raise self._function_error(qualified_name)
        candidates = dict(pending.schema_types)
        if pending.unqualified_types:
            builtin_types = await self._builtin_types.load(self._run)
            candidates.update((name, None) for name in pending.unqualified_types if name not in builtin_types)
        if not candidates:
            return
        found = await row_types(self._run, self._allowed_schema, list(candidates))
        for name in candidates:
            for relation_schema, relation_name in found.get(name, ()):
                self._check_relation(relation_schema, relation_name)
