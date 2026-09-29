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
