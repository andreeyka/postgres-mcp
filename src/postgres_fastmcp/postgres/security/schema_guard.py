"""Проверки доступа к схеме и префиксам таблиц для безопасного SQL."""

from pglast.ast import RangeVar

from postgres_fastmcp.shared.errors import (
    SchemaNotAllowedError,
    SchemataTableAccessError,
    SystemRelationAccessError,
    TablePrefixAccessError,
)


# Системные отношения: все имена pg_catalog начинаются с pg_ (создать там отношение нельзя без
# allow_system_table_mods), _pg_* — внутренние представления information_schema. Представления
# расширений в public (pg_stat_statements и т. п.) попадают сюда же. hypopg* — представления hypopg
# (hypopg_list_indexes, hypopg_hidden_indexes): показывают гипотетические и скрытые индексы всей
# сессии пулового соединения, то есть чужих вызовов.
_SYSTEM_RELATION_PREFIXES = ("pg_", "_pg_", "hypopg")


def is_system_relation_name(name: str) -> bool:
    """Имя системного отношения (или его строкового типа) по правилу R1, без учёта регистра."""
    return name.lower().startswith(_SYSTEM_RELATION_PREFIXES)


def validate_schema_access(
    range_var: RangeVar,
    *,
    allowed_schema: str | None,
    table_prefix: str | None,
) -> None:
    """Проверка того, что таблица находится в разрешенной схеме и соответствует необязательному префиксу.

    Args:
        range_var: AST узел для ссылки на таблицу.
        allowed_schema: Если задан, разрешена только эта схема (например, 'public').
        table_prefix: Если задан вместе с allowed_schema, имена таблиц должны начинаться с этого.

    Raises:
        SystemRelationAccessError: Если в basic запрошено системное отношение (pg_*, _pg_*, hypopg*).
        TablePrefixAccessError: Если имя таблицы не соответствует префиксу.
        SchemaNotAllowedError: Если схема не разрешена.
        SchemataTableAccessError: Если в пользовательском режиме запрошен доступ к information_schema.schemata.
    """
    if allowed_schema is None:
        return

    relname = range_var.relname or ""
    if is_system_relation_name(relname):
        raise SystemRelationAccessError(relname)

    schemaname = range_var.schemaname

    if schemaname is None:
        if table_prefix and range_var.relname and not range_var.relname.lower().startswith(table_prefix.lower()):
            raise TablePrefixAccessError(range_var.relname, table_prefix)
        return

    schemaname_lower = schemaname.lower()

    if schemaname_lower == "pg_catalog":
        raise SchemaNotAllowedError(schemaname, allowed_schema)

    if schemaname_lower == "information_schema":
        if range_var.relname and range_var.relname.lower() == "schemata":
            raise SchemataTableAccessError(schemaname, range_var.relname)
        return

    if schemaname_lower != allowed_schema.lower():
        raise SchemaNotAllowedError(schemaname, allowed_schema)

    if table_prefix and range_var.relname and not range_var.relname.lower().startswith(table_prefix.lower()):
        raise TablePrefixAccessError(range_var.relname, table_prefix)
