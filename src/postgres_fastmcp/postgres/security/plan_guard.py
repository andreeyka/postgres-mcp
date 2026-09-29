"""Проверка по плану в basic: отношения и функции, до которых запрос доходит через представления, правила и SQL-функции.

Валидатор видит только текст запроса; представление в public поверх чужой схемы он пропускает. PlanGuard
строит план каждого оператора (EXPLAIN без ANALYZE ничего не выполняет) и проверяет, что читают его узлы.

Выражения узлов (Output, Filter, условия, ключи сортировки и группировки — ключи EXPLAIN VERBOSE) разбираются
pglast (plan_expressions) и проверяются по тем же правилам: функции и операторы — allowed_schema или pg_catalog
из списка basic, типы — allowed_schema или pg_catalog, строковый тип таблицы без префикса — как сама таблица.
Что решает только каталог (функция или тип без схемы), спрашивается SQL сервера в той же транзакции (plan_catalog).
Оператор или агрегат без схемы (или со схемой allowed_schema) может быть объектом allowed_schema: каталог отдаёт
функции, которыми они реализованы, и те проверяются по правилам функций. Функция allowed_schema на языке sql
проверяется по тексту тела (prosrc или pg_get_function_sqlbody) теми же правилами вместе с отношениями тела;
вызовы в теле — следующим кругом, не глубже _MAX_DEFINITION_DEPTH. Тело, меняющее данные, SET search_path
вне allowed_schema и SET standard_conforming_strings не on — отказ: их не проверить. Умолчания аргументов функции
allowed_schema любого языка (планировщик подставляет их в вызов и сворачивает) проверяются как текст определения.
Машинерия типов (plan_catalog._type_machinery) — функции ввода-вывода, приведения, классы операторов, CHECK доменов,
которые Postgres вызывает сам, без имени в тексте, — проверяется для типов, до которых доходит оператор (и вложенных
в них): те же строки, что у определений, плюс вид type_function (встроенная — любая функция pg_catalog). Семена —
и типы, названные в любом проверяемом тексте (тело SQL-функции, умолчание аргумента, CHECK домена): приведение
(1)::app_e печатается без имени функции. Каждое семя спрашивается за проверку один раз. Семена и всё, что из них
выросло (функции машинерии, их тела), глубину определений не тратят; все круги ограничены _MAX_CATALOG_ROUNDS.
Функция allowed_schema не на языке sql (PL/pgSQL, PL/Python, C, internal; не агрегат и не член расширения), до которой
доходит оператор по любому из путей выше, — отказ (allow_non_sql_functions=False): её тело не проверить. Решение —
в том же разборе строк реализаций, что тела и умолчания, после остальных строк ответа.

Порядок. До всего, что разбирает SQL агента на сервере, проверяются его типы (ошибка разбора раскрыла бы структуру
таблицы без префикса). Туда же — реализации операторов и агрегатов allowed_schema, которые называет SQL агента
(функция оператора и опорные функции агрегата; вызов с константами планировщик выполнил бы), и машинерия его типов и
типов колонок его отношений (ввод константы и приведение литерала к типу колонки выполняет уже PREPARE). Затем каждый
планируемый оператор готовится и тут же снимается (PREPARE; DEALLOCATE одной командой): разбор и переписывание
берут блокировки представлений и таблиц до конца транзакции, но план не строится и функции не выполняются.
После этого читаются определения заблокированного: правила представлений; то, что планировщик сворачивает и при
чтении, — у заблокированных отношений и их потомков (CHECK, выражения и предикаты индексов, ключи секционирования,
выражения расширенной статистики, политики RLS); путь записи целей DML (триггеры, умолчания колонок и доменов,
генерируемые колонки, CHECK доменов) — их тексты и зависимости (pg_depend), чего план не показывает (свёртка
констант, LIMIT, функции операторов и агрегатов public, всё время записи); машинерия типов колонок заблокированного
и типов из зависимостей. Отношения из текстов (подзапрос политики) и тел SQL-функций проверяются как отношения
плана и тоже готовятся (PREPARE SELECT FROM …): их представления и определения (индексы таблицы встраиваемой
функции) читает следующее чтение определений. Только потом EXPLAIN:
планировщик сворачивает IMMUTABLE-вызовы, то есть выполняет их, и отклонённое представление до него не доходит.
Оператор с GENERIC_PLAN, тип параметра которого PREPARE не выводит ($1 IS NULL), готовится ещё раз с NULL
вместо каждого $N (блокировки те же — отношения называет текст); не вышло и так — отказ до EXPLAIN.
После всех EXPLAIN определения читаются ещё раз — представления, до которых дошёл только планировщик (встраивание
SQL-функций).

Проверка закрыта по умолчанию: нет плана или узел сканирования не называет, что читает, — отказ. Цена —
редкие формы: соединение или агрегат, вынесенные postgres_fdw на удалённый сервер (Foreign Scan без
Relation Name), Custom Scan без отношения. ROWS FROM из нескольких функций (Function Scan без Function Name,
так Postgres переписывает и unnest(a, b)) проверяется по тексту вызовов из Function Call (VERBOSE).
"""

import json
import secrets
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Self

import pglast
from pglast.ast import (
    A_Const,
    A_Expr,
    CaseExpr,
    DeclareCursorStmt,
    DefElem,
    DeleteStmt,
    ExplainStmt,
    FuncCall,
    InsertStmt,
    JoinExpr,
    Node,
    ParamRef,
    RangeVar,
    SelectStmt,
    SortBy,
    String,
    SubLink,
    TypeName,
    UpdateStmt,
)
from pglast.parser import ParseError
from pglast.stream import RawStream
from pglast.visitors import Visitor
from psycopg import Error as PostgresError
from psycopg.errors import IndeterminateDatatype
from psycopg.sql import SQL, Identifier

from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.postgres.security.plan_catalog import (
    DEFINITION_DEPENDENCIES_SQL,
    BuiltinTypeNames,
    allowed_implementations,
    pg_catalog_functions,
    row_types,
)
from postgres_fastmcp.postgres.security.plan_expressions import (
    EXPRESSION_PARSERS,
    FUNCTION_CALL_KEY,
    ExpressionNames,
    expression_texts,
    parse_argument_defaults,
    parse_definition_expression,
    parse_function_body,
    parse_index_definition,
    parse_partition_key_definition,
    parse_rule_definition,
    parse_target_list,
    parse_trigger_definition,
    parser_operators,
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
# Оператор — вид имени, чья реализация (oprcode) проверяется у операторов allowed_schema.
_OPERATOR_KIND = "operator"

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

# Служебные имена проверки: подготовленный оператор (_pgmcp_check_<метка проверки>_<номер>) и точка сохранения
# для оператора с GENERIC_PLAN. Метка — случайная на каждую проверку: подготовленный оператор переживает ROLLBACK,
# и имя, оставшееся в соединении после сбоя, не должно совпасть со следующим. Литерал у них общий намеренно:
# имена подготовленных операторов и точек сохранения — разные пространства имён.
_PREPARED_PREFIX = "_pgmcp_check"
_SAVEPOINT = "_pgmcp_check"

# Разбор текстов пути записи по виду строки DEFINITION_DEPENDENCIES_SQL.
_DEFINITION_PARSERS: dict[str, Callable[[object], ExpressionNames | None]] = {
    "trigger": parse_trigger_definition,
    "index": parse_index_definition,
    "partition": parse_partition_key_definition,
    **dict.fromkeys(("check", "default", "domain", "policy", "statistics"), parse_definition_expression),
    # Умолчания аргументов функции allowed_schema (строка ALLOWED_IMPLEMENTATIONS_SQL).
    "argument_defaults": parse_argument_defaults,
}

# Кругов имён, которые решает каталог: тело функции -> функция в нём -> …, отношение текста -> его правила -> ….
# Считаются только круги с именами определений; круг, где есть лишь семена машинерии типов и то, что из них выросло
# (функции машинерии, их тела и имена в них), глубину не тратит: тип, названный в теле, — не уровень вложенности.
_MAX_DEFINITION_DEPTH = 5
# Жёсткий предел всех кругов (с несчитаемыми): имена конечны и спрашиваются по разу, но цепочка машинерии (тело
# функции сравнения называет тип, чья функция сравнения называет следующий тип, …) глубиной не ограничена.
_MAX_CATALOG_ROUNDS = 4 * _MAX_DEFINITION_DEPTH
# Виды строк реализаций, которые принадлежат функции по имени (name): тело и умолчания аргументов.
_FUNCTION_TEXT_KINDS = frozenset({"sql_body", "sql_atomic_body", "argument_defaults"})
# origin строки машинерии типа (plan_catalog._TYPE_ORIGIN); другой непустой origin — текст двоичного приведения.
_TYPE_ORIGIN = "type"
# Вид строки реализаций: функция allowed_schema не на языке sql (plan_catalog._NON_SQL_FUNCTION_ROWS).
_NON_SQL_FUNCTION_KIND = "non_sql_function"


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


def _row_key(cells: dict[str, Any]) -> tuple[object, ...]:
    """Строка каталога как ключ множества: списки (массивы Postgres) — кортежами."""
    return tuple((key, tuple(value) if isinstance(value, list) else value) for key, value in sorted(cells.items()))


class _FunctionCalls(Visitor):
    """Собирает все вызовы функций выражения, включая вложенные в аргументы."""

    def __init__(self) -> None:
        """Пустой список вызовов в порядке обхода."""
        super().__init__()
        self.calls: list[FuncCall] = []

    def visit_FuncCall(self, _ancestors: object, node: FuncCall) -> None:  # noqa: N802
        """Запомнить вызов."""
        self.calls.append(node)


class _NullParameters(Visitor):
    """Заменяет каждый параметр $N константой NULL (дерево меняется на месте)."""

    def visit_ParamRef(self, _ancestors: object, _node: ParamRef) -> A_Const:  # noqa: N802
        """NULL вместо параметра."""
        return A_Const(isnull=True)


def _with_null_parameters(text: str) -> str | None:
    """Текст оператора, где каждый $N заменён на NULL; разбирается заново, чтобы не трогать дерево EXPLAIN.

    None — полученный текст не разбирается: RawStream печатает индекс и поле NULL без скобок (($1)[1] —
    NULL[1], ($1).f — NULL.f). Такой текст нельзя отправлять: синтаксическую ошибку Postgres выдаёт на всю
    строку команд, SAVEPOINT в ней не выполняется, и откатывать было бы нечего.
    """
    [raw] = pglast.parse_sql(text)
    substituted = RawStream()(_NullParameters()(raw.stmt))
    try:
        pglast.parse_sql(substituted)
    except ParseError:
        return None
    return substituted


class _StatementNames(Visitor):
    """Имена SQL агента: (схема или None, имя); у имени с базой данных — две последние части.

    Типы проверяются до разбора на сервере (оракул ошибок разбора). Операторы и функции — кандидаты
    в операторы и агрегаты allowed_schema: их реализация проверяется до PREPARE и EXPLAIN. Типы и отношения
    (их колонки) — семена машинерии типов: ввод константы и неявное приведение литерала к типу колонки
    выполняются уже при PREPARE.
    """

    def __init__(self) -> None:
        """Пустые списки имён в порядке обхода."""
        super().__init__()
        self.types: list[tuple[str | None, str]] = []
        self.operators: list[tuple[str | None, str]] = []
        self.functions: list[tuple[str | None, str]] = []
        self.relations: list[tuple[str | None, str]] = []

    @staticmethod
    def _add(found: list[tuple[str | None, str]], parts: tuple[Node, ...] | None) -> None:
        """Добавить имя; части не строки или пустые пропускаются."""
        names = [part.sval for part in parts or () if isinstance(part, String) and part.sval]
        if len(names) == 1:
            found.append((None, names[0]))
        elif len(names) > 1:
            found.append((names[-2], names[-1]))

    def visit_TypeName(self, _ancestors: object, node: TypeName) -> None:  # noqa: N802
        """Имя типа."""
        self._add(self.types, node.names)

    def visit_RangeVar(self, _ancestors: object, node: RangeVar) -> None:  # noqa: N802
        """Отношение (или имя CTE: каталог его не найдёт или найдёт одноимённую таблицу — лишнее семя)."""
        if node.relname:
            self.relations.append((node.schemaname, node.relname))

    def _add_generated(self, node: Node) -> bool:
        """Операторы, которые разбор подставляет за узел сам (BETWEEN, CASE x WHEN, USING, IN (подзапрос)).

        Returns:
            True — у узла такие операторы есть.
        """
        generated = parser_operators(node)
        self.operators.extend((None, name) for name in generated)
        return bool(generated)

    def visit_A_Expr(self, _ancestors: object, node: A_Expr) -> None:  # noqa: N802
        """Оператор выражения (в том числе IN, = ANY, NULLIF, IS DISTINCT FROM); у BETWEEN — его сравнения."""
        if not self._add_generated(node):
            self._add(self.operators, node.name)

    def visit_SubLink(self, _ancestors: object, node: SubLink) -> None:  # noqa: N802
        """Оператор сравнения с подзапросом (x = ANY (SELECT ...)); x IN (SELECT ...) — =."""
        if not self._add_generated(node):
            self._add(self.operators, node.operName)

    def visit_CaseExpr(self, _ancestors: object, node: CaseExpr) -> None:  # noqa: N802
        """Равенство CASE x WHEN."""
        self._add_generated(node)

    def visit_JoinExpr(self, _ancestors: object, node: JoinExpr) -> None:  # noqa: N802
        """Равенство колонок JOIN USING и NATURAL JOIN."""
        self._add_generated(node)

    def visit_SortBy(self, _ancestors: object, node: SortBy) -> None:  # noqa: N802
        """Оператор ORDER BY ... USING."""
        self._add(self.operators, node.useOp)

    def visit_FuncCall(self, _ancestors: object, node: FuncCall) -> None:  # noqa: N802
        """Вызов функции или агрегата."""
        self._add(self.functions, node.funcname)


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


def _quoted_name(schema: str | None, name: str) -> str:
    """Имя в кавычках ("схема"."имя" или "имя") для to_regtype/to_regclass: разбор по search_path, как у PREPARE."""
    return Identifier(name).as_string() if schema is None else Identifier(schema, name).as_string()


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
    """Имена, которые решает только каталог; словари — упорядоченные множества в порядке обхода."""

    functions: dict[str, None] = field(default_factory=dict)
    unqualified_types: dict[str, None] = field(default_factory=dict)
    schema_types: dict[str, None] = field(default_factory=dict)
    # (вид, имя) операторов и функций без схемы или со схемой allowed_schema: чем они реализованы.
    implementations: dict[tuple[str, str], None] = field(default_factory=dict)
    # (схема, имя) отношений из текстов определений: их представления блокирует PREPARE.
    relations: dict[tuple[str, str], None] = field(default_factory=dict)
    # Семена машинерии типов: имена типов (схема или None, имя; для to_regtype) из SQL агента и текстов определений
    # и отношений SQL агента, чьи колонки и строковый тип — семена (для to_regclass; текст — идентификаторы
    # в кавычках). Имена разрешаются по search_path, как их разрешит PREPARE.
    footprint_types: dict[tuple[str | None, str], None] = field(default_factory=dict)
    footprint_relations: dict[str, None] = field(default_factory=dict)

    def _all(self) -> tuple[dict[Any, None], ...]:
        """Все множества в порядке полей."""
        return (
            self.functions,
            self.unqualified_types,
            self.schema_types,
            self.implementations,
            self.relations,
            self.footprint_types,
            self.footprint_relations,
        )

    def take(self) -> Self:
        """Забрать накопленное: self пустеет, имена следующего круга копятся в нём заново."""
        taken = type(self)(*(dict(names) for names in self._all()))
        for names in self._all():
            names.clear()
        return taken

    def empty(self) -> bool:
        """Спрашивать каталог больше нечего."""
        return not any(self._all())


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
        allow_non_sql_functions: bool = False,
    ) -> None:
        """Инициализация с исполнителем транзакции и правилами basic.

        Args:
            run: Выполняет строку SQL (EXPLAIN или запрос каталога) в транзакции проверяемого оператора
                (SET LOCAL уже выставлен) и возвращает строки.
            allowed_schema: Единственная схема отношений плана (public).
            table_prefix: Если задан, имена отношений плана должны начинаться с него (без учёта регистра).
            builtin_types: Кэш имён типов pg_catalog; None — свой на эту проверку.
            allow_non_sql_functions: Пропускать функции allowed_schema не на языке sql (их тела выполняются
                непроверенными); False — отказ.
        """
        self._run = run
        self._allowed_schema = allowed_schema
        self._table_prefix = table_prefix.lower() if table_prefix else None
        self._prefix_for_hint = table_prefix or None
        self._builtin_types = builtin_types or BuiltinTypeNames()
        self._allow_non_sql_functions = allow_non_sql_functions
        self._prepared_tag = secrets.token_hex(8)
        self._prepared_count = 0
        # Строки правил, уже проверенные в этой проверке: второе чтение после EXPLAIN их не повторяет.
        self._seen_rows: set[tuple[object, ...]] = set()
        # Имена, реализации которых уже спрошены в этой проверке (у второго чтения — только новые).
        self._looked_up: set[tuple[str, str]] = set()
        # Отношения текстов определений, уже заблокированные PREPARE в этой проверке.
        self._locked_relations: set[tuple[str, str]] = set()
        # Семена машинерии типов (имена для to_regtype и to_regclass), уже отданные каталогу в этой проверке:
        # тип, названный в теле функции своей же машинерии, не спрашивается снова — круги кончаются.
        self._seeded: set[tuple[str, str]] = set()
        # Имена, найденные машинерией типов (семена, функции машинерии, их тела): их круги глубину не тратят.
        self._free = _CatalogNames()

    async def check(self, query: str) -> None:
        """Проверить определения, до которых доходит запрос, затем план каждого планируемого оператора.

        Запрос уже прошёл валидатор, поэтому разбирается без ошибок. Ошибка разбора (отношения нет) приходит
        из исполнителя как ошибка Postgres — та же, что дало бы выполнение.

        Args:
            query: SQL агента после валидации (с тегом-комментарием).

        Raises:
            PlanAccessError: Запрос доходит до отношения, функции или типа вне разрешённого.
            PlanUnverifiableError: Плана нет, узел сканирования не называет, что читает, или выражение
                не разбирается.
        """
        statements = [raw.stmt for raw in pglast.parse_sql(query)]
        plannable = [target for target in (_plannable(statement) for statement in statements) if target is not None]
        await self._check_statement_names(statements, [statement for statement, _ in plannable])
        if not plannable:
            return
        targets = [(RawStream()(statement), generic) for statement, generic in plannable]
        for text, generic in targets:
            await self._prepare(text, generic=generic)
        await self._check_definitions()
        for text, generic in targets:
            options = "VERBOSE, FORMAT JSON, GENERIC_PLAN" if generic else "VERBOSE, FORMAT JSON"
            rows = await self._run(f"EXPLAIN ({options}) {text}")
            await self._check_plan(_plan_document(rows))
        await self._check_definitions()

    async def _prepare(self, text: str, *, generic: bool) -> None:
        """Разобрать и переписать оператор без планирования: блокировки представлений и таблиц до конца транзакции.

        PREPARE строит дерево запроса (разбор, переписывание) и берёт AccessShareLock на представления и таблицы,
        RowExclusiveLock на цели DML, но план не строит: IMMUTABLE-вызовы не сворачиваются, функции не выполняются.
        DEALLOCATE — той же командой: блокировки держит транзакция, а подготовленный оператор пережил бы ROLLBACK.
        Упал PREPARE — DEALLOCATE строки не выполняется: снимать нечего.

        GENERIC_PLAN: PREPARE без типов выводит типы $N из контекста, но, в отличие от EXPLAIN (GENERIC_PLAN),
        не принимает невыводимый ($1 IS NULL, pg_typeof($1)) — 42P18. Такой оператор готовится в точке сохранения;
        при 42P18 она откатывается, и оператор готовится ещё раз (тоже в точке сохранения) с NULL вместо каждого
        $N: отношения и представления называет текст, так что блокировки те же, а у NULL тип выводится
        (unknown). Не вышло и так (или текст с NULL не разбирается) — отказ: EXPLAIN оператора, представления
        которого не проверены, выполнил бы их IMMUTABLE-вызовы.

        Raises:
            PlanUnverifiableError: Оператор с GENERIC_PLAN не готовится ни с $N, ни с NULL.
        """
        if not generic:
            await self._run(self._prepare_command(text))
            return
        try:
            await self._run(self._savepoint_command(text))
        except IndeterminateDatatype:
            await self._run(f"ROLLBACK TO SAVEPOINT {_SAVEPOINT}; RELEASE SAVEPOINT {_SAVEPOINT}")
        else:
            return
        substituted = _with_null_parameters(text)
        if substituted is None:
            raise PlanUnverifiableError(rules=True)
        try:
            await self._run(self._savepoint_command(substituted))
        except PostgresError:
            await self._run(f"ROLLBACK TO SAVEPOINT {_SAVEPOINT}; RELEASE SAVEPOINT {_SAVEPOINT}")
            raise PlanUnverifiableError(rules=True) from None

    def _prepare_command(self, text: str) -> str:
        """PREPARE оператора под новым служебным именем и DEALLOCATE одной командой."""
        name = f"{_PREPARED_PREFIX}_{self._prepared_tag}_{self._prepared_count}"
        self._prepared_count += 1
        return f"PREPARE {name} AS {text}; DEALLOCATE {name}"

    def _savepoint_command(self, text: str) -> str:
        """Та же команда в точке сохранения: ошибка PREPARE откатывается, не обрывая транзакцию."""
        return f"SAVEPOINT {_SAVEPOINT}; {self._prepare_command(text)}; RELEASE SAVEPOINT {_SAVEPOINT}"

    async def _check_definitions(self) -> None:
        """Определения того, что заблокировала транзакция: правила, определения отношений, путь записи целей DML.

        План не показывает всего, что вычисляет правило (IMMUTABLE-вызов с константами свёрнут, LIMIT/OFFSET
        и рамки окна не печатаются, оператор или агрегат public называет себя), и не показывает вовсе того,
        что планировщик сворачивает и для SELECT (CHECK потомков при исключении по ограничениям, выражения
        индексов, ключи секционирования, выражения статистики, политики RLS), и триггеры, умолчания, генерируемые
        колонки и домены целей DML. Читается
        дважды: после PREPARE (до планирования) и после всех EXPLAIN (планировщик блокирует представления
        встраиваемых SQL-функций). Тексты проверяются как выражения плана плюс их отношения, зависимости
        из pg_depend — по правилам basic.

        Raises:
            PlanAccessError: Определение вызывает функцию, оператор, тип или отношение вне разрешённого.
            PlanUnverifiableError: Ответа нет, текст не разбирается или строка незнакомого вида.
        """
        pending = _CatalogNames()
        await self._read_definitions(pending)
        await self._check_catalog_names(pending)

    async def _read_definitions(self, pending: _CatalogNames) -> None:
        """Одно чтение DEFINITION_DEPENDENCIES_SQL; строка, проверенная раньше в этой проверке, пропускается."""
        rows = await self._run(DEFINITION_DEPENDENCIES_SQL)
        if rows is None:
            raise PlanUnverifiableError(rules=True)
        for row in rows:
            key = _row_key(row.cells)
            if key in self._seen_rows:
                continue
            self._seen_rows.add(key)
            self._check_rule_row(row.cells, pending)

    async def _lock_relations(self, relations: list[tuple[str, str]]) -> None:
        """Отношения из текстов определений: PREPARE SELECT по каждому, чтобы прочитать их правила.

        PREPARE блокирует отношение и представления, к которым оно ведёт (без планирования); их правила прочитает
        следующее чтение определений. Имя уже проверено _check_relation и встраивается как Identifier.
        """
        for relation in relations:
            self._locked_relations.add(relation)
            text = SQL("SELECT FROM {}").format(Identifier(*relation)).as_string()
            await self._prepare(text, generic=False)

    def _check_rule_row(
        self, cells: dict[str, Any], pending: _CatalogNames, free_functions: frozenset[str] = frozenset()
    ) -> None:
        """Одна строка DEFINITION_DEPENDENCIES_SQL или ALLOWED_IMPLEMENTATIONS_SQL.

        То, что решает только каталог, откладывается в pending; у строки машинерии (origin) и тела или умолчаний
        функции, найденной только машинерией (free_functions), — в self._free: такие круги глубину не тратят.
        Отказ по строке семейства двоичного приведения называет приведение.
        """
        origin = cells.get("origin")
        free = origin is not None or (cells.get("kind") in _FUNCTION_TEXT_KINDS and cells.get("name") in free_functions)
        target = self._free if free else pending
        if origin is None or origin == _TYPE_ORIGIN:
            self._check_row(cells, target)
            return
        try:
            self._check_row(cells, target)
        except PlanAccessError as error:
            raise PlanAccessError(
                error.kind,
                error.qualified_name,
                allowed_schema=self._allowed_schema,
                table_prefix=self._prefix_for_hint,
                binary_cast=str(origin),
            ) from None

    def _check_row(self, cells: dict[str, Any], pending: _CatalogNames) -> None:
        """Проверка одной строки определений или реализаций; то, что решает каталог, — в pending."""
        kind = cells.get("kind")
        schema = cells.get("schema")
        name = str(cells.get("name"))
        if kind == "rule":
            names = parse_rule_definition(cells.get("definition"))
            if names is None:
                raise PlanUnverifiableError(rules=True)
            self._check_names(names, pending, check_functions=True)
        elif kind in _DEFINITION_PARSERS:
            names = _DEFINITION_PARSERS[kind](cells.get("definition"))
            if names is None:
                raise PlanUnverifiableError(rules=True)
            self._check_names(names, pending, check_functions=True)
        elif kind == "function":
            self._check_function(schema, name)
            self._note_implementation(FUNCTION_KIND, schema, name, pending)
        elif kind == "type_function":
            # Машинерия типа (ввод-вывод, опорная функция класса операторов, оценка селективности, canonical/subdiff
            # диапазона): встроенная — любая функция pg_catalog, её сигнатуру (cstring, internal) задаёт Postgres;
            # иначе — функция allowed_schema (с проверкой тела и умолчаний), чужая схема — отказ.
            if schema != _BUILTIN_FUNCTION_SCHEMA:
                self._check_function(schema, name)
                self._note_implementation(FUNCTION_KIND, schema, name, pending)
        elif kind in ("operator_function", "aggregate_function"):
            # Встроенные операторы и агрегаты (pg_catalog) реализованы функциями вне списка basic (int4eq,
            # int4_sum) — проверяются сами; функции проверяются у операторов и агрегатов других схем.
            if cells.get("parent_schema") != _BUILTIN_FUNCTION_SCHEMA:
                self._check_function(schema, name)
                self._note_implementation(FUNCTION_KIND, schema, name, pending)
        elif kind in ("sql_body", "sql_atomic_body"):
            atomic = kind == "sql_atomic_body"
            if not atomic:
                self._check_function_config(cells.get("config"))
            names = parse_function_body(cells.get("definition"), atomic=atomic)
            if names is None:
                raise PlanUnverifiableError(rules=True)
            self._check_names(names, pending, check_functions=True)
        elif kind == "operator":
            if schema not in (self._allowed_schema, _BUILTIN_FUNCTION_SCHEMA):
                qualified_name = f"{schema}.{name}"
                raise self._function_error(qualified_name)
        elif kind == "type":
            if schema not in (self._allowed_schema, _BUILTIN_FUNCTION_SCHEMA):
                raise PlanAccessError(
                    TYPE_KIND,
                    f"{schema}.{name}",
                    allowed_schema=self._allowed_schema,
                    table_prefix=self._prefix_for_hint,
                )
            relation_name = cells.get("relation_name")
            if self._table_prefix is not None and relation_name is not None:
                self._check_relation(cells.get("relation_schema"), str(relation_name))
        else:
            raise PlanUnverifiableError(rules=True)

    def _check_function_config(self, config: object) -> None:
        """Настройки (proconfig) SQL-функции с телом prosrc: search_path — только allowed_schema.

        SET search_path, отличный от allowed_schema, резолвит имена тела при выполнении в другой схеме
        (search_path = secret: SELECT x FROM t читает secret.t) — такое тело не проверить. SECURITY DEFINER ничего
        не меняет: права владельца делают утечку опаснее, тело проверяется так же.

        SET standard_conforming_strings, отличный от on, меняет лексику тела: при off обратный слэш перед кавычкой
        экранирует её (строка не закрывается), и Postgres делит текст на строки и код иначе, чем pglast
        (он лексит под on, как и транзакция агента). То, что pglast видит в литерале, Postgres выполнил бы.

        Raises:
            PlanUnverifiableError: search_path другой, standard_conforming_strings не on или настройки
                не список строк.
        """
        if config is None:
            return
        if not isinstance(config, list):
            raise PlanUnverifiableError(rules=True)
        for entry in config:
            if not isinstance(entry, str):
                raise PlanUnverifiableError(rules=True)
            setting, _, value = entry.partition("=")
            name = setting.strip().lower()
            if name == "search_path" and value != self._allowed_schema:
                raise PlanUnverifiableError(rules=True)
            if name == "standard_conforming_strings" and value.strip().lower() != "on":
                raise PlanUnverifiableError(rules=True)

    async def _check_statement_names(self, statements: list[Node], targets: list[Node]) -> None:
        """Типы SQL агента и реализации его операторов и агрегатов allowed_schema — до PREPARE и EXPLAIN.

        Типы: ошибка разбора ((NULL::users).secret_note — нет колонки, '(1,2)'::users — число и типы полей)
        раскрывает структуру таблицы без префикса; PREPARE разбирает так же, как EXPLAIN. Каталог спрашивается,
        только если есть тип без схемы вне кэша pg_catalog или со схемой allowed_schema без префикса.

        Операторы и функции планируемых операторов (targets): оператор или агрегат allowed_schema называет себя,
        а не функции, которые вызывает, а IMMUTABLE-вызов с константами планировщик выполнил бы при EXPLAIN.
        Функция allowed_schema на языке sql — по телу: встраивание при EXPLAIN выполнило бы и его свёртки.

        Машинерия типов (plan_catalog._type_machinery): имена типов, колонки и строковые типы отношений SQL
        агента — семена того же запроса реализаций. Функцию ввода типа ('x'::тип, INSERT литерала в колонку
        типа) и подтипа диапазона вместе с CHECK домена выполняет уже PREPARE.
        """
        pending = _CatalogNames()
        collector = _StatementNames()
        for statement in statements:
            collector(statement)
        for schema, name in collector.types:
            self._check_type(schema, name, pending)
        for schema, name in collector.relations:
            self._free.footprint_relations[_quoted_name(schema, name)] = None
        reached = _StatementNames()
        for target in targets:
            reached(target)
        for schema, name in reached.operators:
            self._note_implementation(_OPERATOR_KIND, schema, name, pending)
        for schema, name in reached.functions:
            self._note_implementation(FUNCTION_KIND, schema, name, pending)
        await self._check_catalog_names(pending)

    async def _check_plan(self, plan: object) -> None:
        """Сначала узлы (отношения и функции сканов), затем выражения, затем имена, которые решает каталог.

        Порядок сохраняет прежние отказы: узел, запрещённый и раньше, отклоняется с той же ошибкой, даже
        если выражение выше по плану тоже запрещено. Табличная функция allowed_schema — кандидат в SQL-функцию:
        её тело проверяет каталог.
        """
        nodes = list(_plan_nodes(plan))
        pending = _CatalogNames()
        for node in nodes:
            self._check_node(node)
            if node.get("Node Type") == _FUNCTION_SCAN_TYPE and "Function Name" in node:
                self._note_implementation(FUNCTION_KIND, node.get("Schema"), str(node["Function Name"]), pending)
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
        for schema, name in names.functions:
            self._note_implementation(FUNCTION_KIND, schema, name, pending)
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
            self._note_implementation(_OPERATOR_KIND, schema, name, pending)
        for schema, name in names.types:
            self._check_type(schema, name, pending)
        for schema, name in names.relations:
            relation = (schema or self._allowed_schema, name)
            self._check_relation(*relation)
            if relation not in self._locked_relations:
                pending.relations[relation] = None

    def _note_implementation(self, kind: str, schema: str | None, name: str, pending: _CatalogNames) -> None:
        """Оператор или функция, которые могут быть объектом allowed_schema: их реализацию спросит каталог.

        Имя без схемы может быть и встроенным (=, count) — каталог ищет только в allowed_schema. Имя, уже
        спрошенное в этой проверке, не спрашивается снова. Функция — кандидат и в агрегат (опорные функции),
        и в SQL-функцию (тело).
        """
        if (schema is None or schema == self._allowed_schema) and (kind, name) not in self._looked_up:
            pending.implementations[kind, name] = None

    def _check_type(self, schema: str | None, name: str, pending: _CatalogNames) -> None:
        """Тип выражения: pg_catalog и allowed_schema; строковый тип отношения без префикса — через каталог.

        reg*-типы не отклоняются: их литерал уже разрешён по имени при создании объекта или планировании.
        """
        if schema == _BUILTIN_FUNCTION_SCHEMA:
            return
        # Семя машинерии: приведение (1)::app_e печатается без имени функции, а его IMMUTABLE-функцию EXPLAIN
        # свернул бы. Так — для SQL агента и для каждого текста определения (тело функции, умолчание аргумента,
        # CHECK домена); тип вне allowed_schema отклоняется ниже, и спрашивать каталог уже не придётся. Уже
        # спрошенное семя не откладывается: иначе оно одно давало бы лишний круг (и тратило глубину).
        if (TYPE_KIND, _quoted_name(schema, name)) not in self._seeded:
            self._free.footprint_types[schema, name] = None
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
        """Имена, которые решает только каталог, — кругами, пока новые не кончатся.

        Круг: реализации операторов и агрегатов allowed_schema; функции без схемы вне списка basic — не из
        pg_catalog; строковые типы — не отношения без префикса; отношения из текстов — PREPARE и новое чтение
        определений. Тексты дают новые имена — следующий круг.

        Глубина — только круги с именами определений (pending); имена машинерии типов (self._free) идут теми же
        кругами, но глубину не тратят. Все круги ограничены _MAX_CATALOG_ROUNDS.

        Raises:
            PlanUnverifiableError: Имена определений не кончились за _MAX_DEFINITION_DEPTH кругов или все имена —
                за _MAX_CATALOG_ROUNDS.
        """
        depth = 0
        for _ in range(_MAX_CATALOG_ROUNDS):
            current = pending.take()
            free = self._free.take()
            if current.empty() and free.empty():
                return
            if not current.empty():
                depth += 1
                if depth > _MAX_DEFINITION_DEPTH:
                    raise PlanUnverifiableError(rules=True)
            await self._check_implementations(current, free, pending)
            await self._check_builtin_functions({**current.functions, **free.functions})
            await self._check_row_types(
                {**current.unqualified_types, **free.unqualified_types}, {**current.schema_types, **free.schema_types}
            )
            relations = {**current.relations, **free.relations}
            if relations:
                await self._lock_relations(list(relations))
                await self._read_definitions(pending)
        raise PlanUnverifiableError(rules=True)

    async def _check_implementations(self, current: _CatalogNames, free: _CatalogNames, pending: _CatalogNames) -> None:
        """Функции операторов и агрегатов allowed_schema, тела её SQL-функций и машинерия типов — по правилам basic.

        Семена машинерии — типы SQL агента и текстов определений, отношения SQL агента (free.footprint_*) и типы
        найденных функций и операторов; семя, уже отданное каталогу в этой проверке, не повторяется. Имя типа без
        схемы из кэша типов pg_catalog семенем не становится: при search_path = allowed_schema pg_catalog первый,
        и такое имя — встроенный тип, его машинерию не проверяют (так ::text в выражении плана не стоит запроса).
        Реализации — из current (глубина) и free (машинерия): тело функции, найденной только машинерией, остаётся
        несчитаемым.

        Функция не на языке sql (строка non_sql_function) отклоняется после остальных строк ответа: отказ по функции
        чужой схемы в умолчании или машинерии той же функции точнее и не зависит от порядка строк.

        Raises:
            PlanAccessError: Строка нарушает правила basic или функция не на sql при allow_non_sql_functions=False.
            PlanUnverifiableError: Ответа нет или строку не проверить.
        """
        types = await self._type_seeds(free.footprint_types)
        relations = [name for name in free.footprint_relations if (RELATION_KIND, name) not in self._seeded]
        implementations = {**free.implementations, **current.implementations}
        if not (implementations or types or relations):
            return
        free_functions = frozenset(
            name
            for kind, name in free.implementations
            if kind == FUNCTION_KIND and (kind, name) not in current.implementations
        )
        keys = list(implementations)
        self._looked_up.update(keys)
        self._seeded.update((TYPE_KIND, name) for name in types)
        self._seeded.update((RELATION_KIND, name) for name in relations)
        rows = await allowed_implementations(
            self._run,
            self._allowed_schema,
            operators=[name for kind, name in keys if kind == _OPERATOR_KIND],
            functions=[name for kind, name in keys if kind == FUNCTION_KIND],
            types=types,
            relations=relations,
        )
        if rows is None:
            raise PlanUnverifiableError(rules=True)
        non_sql: list[dict[str, Any]] = []
        for row in rows:
            if row.cells.get("kind") == _NON_SQL_FUNCTION_KIND:
                non_sql.append(row.cells)
            else:
                self._check_rule_row(row.cells, pending, free_functions)
        if non_sql and not self._allow_non_sql_functions:
            cells = non_sql[0]
            raise PlanAccessError(
                FUNCTION_KIND,
                f"{cells.get('schema')}.{cells.get('name')}",
                allowed_schema=self._allowed_schema,
                table_prefix=self._prefix_for_hint,
                language=str(cells.get("definition")),
            )

    async def _type_seeds(self, types: dict[tuple[str | None, str], None]) -> list[str]:
        """Новые семена-типы для to_regtype: без уже спрошенных и без имён без схемы из кэша типов pg_catalog."""
        builtin: frozenset[str] = frozenset()
        if any(schema is None for schema, _ in types):
            builtin = await self._builtin_types.load(self._run)
        seeds = (_quoted_name(schema, name) for schema, name in types if schema is not None or name not in builtin)
        return [name for name in seeds if (TYPE_KIND, name) not in self._seeded]

    async def _check_builtin_functions(self, functions: dict[str, None]) -> None:
        """Функции без схемы вне списка basic: найденная в pg_catalog — отказ, иначе это функция allowed_schema."""
        if not functions:
            return
        builtin = await pg_catalog_functions(self._run, list(functions))
        for name in functions:
            if name in builtin:
                qualified_name = f"{_BUILTIN_FUNCTION_SCHEMA}.{name}"
                raise self._function_error(qualified_name)

    async def _check_row_types(self, unqualified: dict[str, None], schema_types: dict[str, None]) -> None:
        """Строковые типы: не отношения без префикса (без схемы — только имена вне кэша типов pg_catalog)."""
        candidates = dict(schema_types)
        if unqualified:
            builtin_types = await self._builtin_types.load(self._run)
            candidates.update((name, None) for name in unqualified if name not in builtin_types)
        if not candidates:
            return
        found = await row_types(self._run, self._allowed_schema, list(candidates))
        for name in candidates:
            for relation_schema, relation_name in found.get(name, ()):
                self._check_relation(relation_schema, relation_name)
