"""Запросы каталога для проверки выражений плана: SQL сервера в транзакции оператора агента.

Идут через исполнитель транзакции PlanGuard (search_path = allowed_schema, SET LOCAL уже выставлен), без
валидатора агента: это SQL сервера. Имена встраиваются как Literal. Все отношения — с pg_catalog.;
операторы (=, <>, IN) без схемы резолвятся в pg_catalog: он неявно первый в search_path.
"""

from collections.abc import Collection

from psycopg.sql import SQL, Composable, Literal

from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.postgres.ports import StatementRunner


_BUILTIN_TYPES_SQL = (
    "SELECT t.typname AS name FROM pg_catalog.pg_type t "
    "JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace WHERE n.nspname = 'pg_catalog'"
)
_PG_CATALOG_FUNCTIONS_SQL = (
    "SELECT DISTINCT p.proname AS name FROM pg_catalog.pg_proc p "
    "JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace "
    "WHERE n.nspname = 'pg_catalog' AND p.proname IN ({names})"
)
_ROW_TYPES_SQL = (
    "SELECT t.typname AS name FROM pg_catalog.pg_type t "
    "JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace "
    "WHERE n.nspname = {schema} AND t.typrelid <> 0 AND t.typname IN ({names})"
)


def _names(rows: list[RowResult] | None) -> frozenset[str]:
    """Значения колонки name."""
    return frozenset(str(row.cells["name"]) for row in rows or ())


def _literals(names: Collection[str]) -> Composable:
    """Список Literal через запятую для IN (...)."""
    return SQL(", ").join(Literal(name) for name in names)


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
    sql = SQL(_PG_CATALOG_FUNCTIONS_SQL).format(names=_literals(names)).as_string()
    return _names(await run(sql))


async def row_types(run: StatementRunner, schema: str, names: Collection[str]) -> frozenset[str]:
    """Какие из имён — строковые типы отношений схемы schema (таблицы, представления, составные типы)."""
    sql = SQL(_ROW_TYPES_SQL).format(schema=Literal(schema), names=_literals(names)).as_string()
    return _names(await run(sql))
