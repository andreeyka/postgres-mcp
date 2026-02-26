"""Проверки доступа к схеме и префиксам таблиц для безопасного SQL."""

from pglast.ast import RangeVar

from postgres_fastmcp.common.errors import SchemaNotAllowedError, SchemataTableAccessError, TablePrefixAccessError


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
        TablePrefixAccessError: Если имя таблицы не соответствует префиксу.
        SchemaNotAllowedError: Если схема не разрешена.
        SchemataTableAccessError: Если в пользовательском режиме запрошен доступ к information_schema.schemata.
    """
    if not allowed_schema:
        return

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
