"""Запросы каталога для проверки выражений плана: SQL сервера в транзакции оператора агента.

Идут через исполнитель транзакции PlanGuard (search_path = allowed_schema, SET LOCAL уже выставлен), без
валидатора агента: это SQL сервера. Имена встраиваются как Literal. Каждое отношение, функция и тип — с
pg_catalog., каждый оператор — OPERATOR(pg_catalog.…): pg_catalog неявно первый в search_path, но
неквалифицированный оператор с разными типами аргументов (oid <> integer) public может перехватить
точным совпадением типов. IN, NULLIF и IS DISTINCT FROM ищут = так же — вместо них ANY с OPERATOR.
"""

from collections.abc import Collection

from psycopg.sql import SQL, Composable, Literal

from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.postgres.ports import StatementRunner


_BUILTIN_TYPES_SQL = (
    "SELECT t.typname AS name FROM pg_catalog.pg_type t "
    "JOIN pg_catalog.pg_namespace n ON n.oid OPERATOR(pg_catalog.=) t.typnamespace "
    "WHERE n.nspname OPERATOR(pg_catalog.=) 'pg_catalog'"
)
_PG_CATALOG_FUNCTIONS_SQL = (
    "SELECT DISTINCT p.proname AS name FROM pg_catalog.pg_proc p "
    "JOIN pg_catalog.pg_namespace n ON n.oid OPERATOR(pg_catalog.=) p.pronamespace "
    "WHERE n.nspname OPERATOR(pg_catalog.=) 'pg_catalog' AND p.proname OPERATOR(pg_catalog.=) ANY ({names})"
)
# Строковый тип отношения — сам (typrelid), через домен (typbasetype; у домена typrelid = 0, домен над
# доменом ссылается на ближайший) или через массив (typelem): '{"(1,2)"}'::users_dom раскрывает таблицу так же.
_ROW_TYPES_SQL = (
    "WITH RECURSIVE reached(name, oid) AS ("
    "SELECT t.typname, t.oid FROM pg_catalog.pg_type t "
    "JOIN pg_catalog.pg_namespace n ON n.oid OPERATOR(pg_catalog.=) t.typnamespace "
    "WHERE n.nspname OPERATOR(pg_catalog.=) {schema} AND t.typname OPERATOR(pg_catalog.=) ANY ({names}) "
    "UNION "
    "SELECT r.name, u.next FROM reached r "
    "JOIN pg_catalog.pg_type t ON t.oid OPERATOR(pg_catalog.=) r.oid "
    "CROSS JOIN LATERAL (VALUES (t.typbasetype), (t.typelem)) AS u(next) "
    "WHERE u.next OPERATOR(pg_catalog.<>) 0::pg_catalog.oid"
    ") "
    "SELECT r.name, cn.nspname AS relation_schema, c.relname AS relation_name FROM reached r "
    "JOIN pg_catalog.pg_type t ON t.oid OPERATOR(pg_catalog.=) r.oid "
    "JOIN pg_catalog.pg_class c ON c.oid OPERATOR(pg_catalog.=) t.typrelid "
    "JOIN pg_catalog.pg_namespace cn ON cn.oid OPERATOR(pg_catalog.=) c.relnamespace"
)

# Правила (pg_rewrite) отношений, которые эта транзакция уже заблокировала: представления и правила, до которых
# дошли разбор и переписывание запросов агента (EXPLAIN берёт AccessShareLock и держит его до конца транзакции;
# вложенные представления блокирует переписывание). pg_locks показывает и fast-path блокировки (колонка
# fastpath) — обычный путь AccessShareLock. Блокировки pg_catalog и pg_toast берут и собственные запросы
# каталога (pg_locks — сам представление), их правила не читаются; отношения без правил строк не дают.
#
# Строки (kind): rule — текст правила (definition, pg_get_ruledef: имена вне search_path — со схемой);
# function — функция или агрегат из pg_depend правила; aggregate_function — опорная функция агрегата
# (parent_schema — схема агрегата); operator и operator_function — оператор и его функция (oprcode); type — тип
# и каждый тип, до которого он ведёт через typbasetype/typelem, с отношением строкового типа (relation_*).
# pg_depend не хранит зависимостей от закреплённых (встроенных) объектов pg_catalog: они видны только в тексте.
_PG_REWRITE = "'pg_catalog.pg_rewrite'::pg_catalog.regclass::pg_catalog.oid"
_PG_PROC = "'pg_catalog.pg_proc'::pg_catalog.regclass::pg_catalog.oid"
_PG_OPERATOR = "'pg_catalog.pg_operator'::pg_catalog.regclass::pg_catalog.oid"
_PG_TYPE = "'pg_catalog.pg_type'::pg_catalog.regclass::pg_catalog.oid"
_NO_PARENT = "NULL::pg_catalog.name"
_NO_RELATION = "NULL::pg_catalog.name, NULL::pg_catalog.name"
_NO_DEFINITION = "NULL::pg_catalog.text"
# Подстановки — константы модуля выше, ввода агента в тексте нет.
RULE_DEPENDENCIES_SQL = (
    "WITH RECURSIVE rules AS ("  # noqa: S608
    "SELECT DISTINCT r.oid FROM pg_catalog.pg_locks l "
    "JOIN pg_catalog.pg_database db ON db.oid OPERATOR(pg_catalog.=) l.database "
    "JOIN pg_catalog.pg_class c ON c.oid OPERATOR(pg_catalog.=) l.relation "
    "JOIN pg_catalog.pg_namespace cn ON cn.oid OPERATOR(pg_catalog.=) c.relnamespace "
    "JOIN pg_catalog.pg_rewrite r ON r.ev_class OPERATOR(pg_catalog.=) c.oid "
    "WHERE l.locktype OPERATOR(pg_catalog.=) 'relation' "
    "AND l.pid OPERATOR(pg_catalog.=) pg_catalog.pg_backend_pid() "
    "AND db.datname OPERATOR(pg_catalog.=) pg_catalog.current_database() "
    "AND cn.nspname OPERATOR(pg_catalog.<>) ALL (ARRAY['pg_catalog', 'pg_toast']::pg_catalog.name[])"
    "), dependencies AS ("
    "SELECT DISTINCT d.refclassid, d.refobjid FROM rules u "
    f"JOIN pg_catalog.pg_depend d ON d.classid OPERATOR(pg_catalog.=) {_PG_REWRITE} "
    "AND d.objid OPERATOR(pg_catalog.=) u.oid"
    "), types(oid) AS ("
    f"SELECT x.refobjid FROM dependencies x WHERE x.refclassid OPERATOR(pg_catalog.=) {_PG_TYPE} "
    "UNION "
    "SELECT v.next FROM types y JOIN pg_catalog.pg_type t ON t.oid OPERATOR(pg_catalog.=) y.oid "
    "CROSS JOIN LATERAL (VALUES (t.typbasetype), (t.typelem)) AS v(next) "
    "WHERE v.next OPERATOR(pg_catalog.<>) 0::pg_catalog.oid"
    ") "
    "SELECT 'rule' AS kind, NULL::pg_catalog.name AS schema, NULL::pg_catalog.name AS name, "
    f"{_NO_PARENT} AS parent_schema, NULL::pg_catalog.name AS relation_schema, "
    "NULL::pg_catalog.name AS relation_name, pg_catalog.pg_get_ruledef(u.oid) AS definition FROM rules u "
    "UNION ALL "
    f"SELECT 'function', pn.nspname, p.proname, {_NO_PARENT}, {_NO_RELATION}, {_NO_DEFINITION} "
    "FROM dependencies x JOIN pg_catalog.pg_proc p ON p.oid OPERATOR(pg_catalog.=) x.refobjid "
    "JOIN pg_catalog.pg_namespace pn ON pn.oid OPERATOR(pg_catalog.=) p.pronamespace "
    f"WHERE x.refclassid OPERATOR(pg_catalog.=) {_PG_PROC} "
    "UNION ALL "
    f"SELECT 'aggregate_function', fn.nspname, f.proname, an.nspname, {_NO_RELATION}, {_NO_DEFINITION} "
    "FROM dependencies x "
    "JOIN pg_catalog.pg_aggregate a ON a.aggfnoid::pg_catalog.oid OPERATOR(pg_catalog.=) x.refobjid "
    "JOIN pg_catalog.pg_proc ap ON ap.oid OPERATOR(pg_catalog.=) x.refobjid "
    "JOIN pg_catalog.pg_namespace an ON an.oid OPERATOR(pg_catalog.=) ap.pronamespace "
    "CROSS JOIN LATERAL (VALUES (a.aggtransfn), (a.aggfinalfn), (a.aggcombinefn), (a.aggserialfn), "
    "(a.aggdeserialfn), (a.aggmtransfn), (a.aggminvtransfn), (a.aggmfinalfn)) AS s(fn) "
    "JOIN pg_catalog.pg_proc f ON f.oid OPERATOR(pg_catalog.=) s.fn::pg_catalog.oid "
    "JOIN pg_catalog.pg_namespace fn ON fn.oid OPERATOR(pg_catalog.=) f.pronamespace "
    f"WHERE x.refclassid OPERATOR(pg_catalog.=) {_PG_PROC} "
    "UNION ALL "
    f"SELECT 'operator', opn.nspname, o.oprname, {_NO_PARENT}, {_NO_RELATION}, {_NO_DEFINITION} "
    "FROM dependencies x JOIN pg_catalog.pg_operator o ON o.oid OPERATOR(pg_catalog.=) x.refobjid "
    "JOIN pg_catalog.pg_namespace opn ON opn.oid OPERATOR(pg_catalog.=) o.oprnamespace "
    f"WHERE x.refclassid OPERATOR(pg_catalog.=) {_PG_OPERATOR} "
    "UNION ALL "
    f"SELECT 'operator_function', fn.nspname, f.proname, opn.nspname, {_NO_RELATION}, {_NO_DEFINITION} "
    "FROM dependencies x JOIN pg_catalog.pg_operator o ON o.oid OPERATOR(pg_catalog.=) x.refobjid "
    "JOIN pg_catalog.pg_namespace opn ON opn.oid OPERATOR(pg_catalog.=) o.oprnamespace "
    "JOIN pg_catalog.pg_proc f ON f.oid OPERATOR(pg_catalog.=) o.oprcode::pg_catalog.oid "
    "JOIN pg_catalog.pg_namespace fn ON fn.oid OPERATOR(pg_catalog.=) f.pronamespace "
    f"WHERE x.refclassid OPERATOR(pg_catalog.=) {_PG_OPERATOR} "
    "UNION ALL "
    f"SELECT 'type', tn.nspname, t.typname, {_NO_PARENT}, cn.nspname, c.relname, {_NO_DEFINITION} "
    "FROM types y JOIN pg_catalog.pg_type t ON t.oid OPERATOR(pg_catalog.=) y.oid "
    "JOIN pg_catalog.pg_namespace tn ON tn.oid OPERATOR(pg_catalog.=) t.typnamespace "
    "LEFT JOIN pg_catalog.pg_class c ON c.oid OPERATOR(pg_catalog.=) t.typrelid "
    "LEFT JOIN pg_catalog.pg_namespace cn ON cn.oid OPERATOR(pg_catalog.=) c.relnamespace"
)


def _names(rows: list[RowResult] | None) -> frozenset[str]:
    """Значения колонки name."""
    return frozenset(str(row.cells["name"]) for row in rows or ())


def _name_array(names: Collection[str]) -> Composable:
    """ARRAY[...]::pg_catalog.name[] из Literal для OPERATOR(pg_catalog.=) ANY (...)."""
    return SQL("ARRAY[{}]::pg_catalog.name[]").format(SQL(", ").join(Literal(name) for name in names))


class BuiltinTypeNames:
    """Имена типов pg_catalog: один запрос на время жизни объекта (DbAccessService держит один на пул).

    Имя без схемы, которое есть в pg_catalog, означает тип pg_catalog (он первый в search_path). Тип,
    появившийся в pg_catalog после загрузки, лишь даёт лишний запрос строковых типов. Тип, удалённый из
    pg_catalog после загрузки (DROP EXTENSION, установленного в pg_catalog), остаётся в кэше до перезапуска:
    одноимённая таблица public без префикса, названная без схемы, пропустит проверку строкового типа.
    Риск пренебрежимый и принят сознательно. Функции так не кэшируются: пропущенная функция pg_catalog
    считалась бы функцией allowed_schema.
    """

    def __init__(self) -> None:
        """Пустой кэш: загрузка при первой проверке, где нужен."""
        self._names: frozenset[str] | None = None

    async def load(self, run: StatementRunner) -> frozenset[str]:
        """Имена типов pg_catalog; запрос — только при первом вызове."""
        if self._names is None:
            self._names = _names(await run(_BUILTIN_TYPES_SQL))
        return self._names


async def pg_catalog_functions(run: StatementRunner, names: Collection[str]) -> frozenset[str]:
    """Какие из имён — функции pg_catalog (одним запросом)."""
    sql = SQL(_PG_CATALOG_FUNCTIONS_SQL).format(names=_name_array(names)).as_string()
    return _names(await run(sql))


async def row_types(run: StatementRunner, schema: str, names: Collection[str]) -> dict[str, list[tuple[str, str]]]:
    """Какие из имён типов схемы schema ведут к строковому типу отношения — и к какому (схема, имя).

    Отношение — таблица, представление или составной тип; путь — сам тип, домен над ним или массив.
    """
    sql = SQL(_ROW_TYPES_SQL).format(schema=Literal(schema), names=_name_array(names)).as_string()
    found: dict[str, list[tuple[str, str]]] = {}
    for row in await run(sql) or ():
        relation = (str(row.cells["relation_schema"]), str(row.cells["relation_name"]))
        found.setdefault(str(row.cells["name"]), []).append(relation)
    return found
