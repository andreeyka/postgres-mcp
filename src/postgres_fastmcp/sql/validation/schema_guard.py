"""Проверки доступа к схеме и префиксам таблиц для безопасного SQL."""

from pglast.ast import RangeVar


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
        ValueError: Если схема или таблица не разрешена.
    """
    if not allowed_schema:
        return

    schemaname = range_var.schemaname

    if schemaname is None:
        if table_prefix and range_var.relname and not range_var.relname.lower().startswith(table_prefix.lower()):
            raise ValueError(
                f"Access to table '{range_var.relname}' is not allowed. "
                f"Only tables with names starting with '{table_prefix}' are permitted."
            )
        return

    schemaname_lower = schemaname.lower()

    if schemaname_lower == "pg_catalog":
        raise ValueError(
            f"Access to schema '{schemaname}' is not allowed. Only '{allowed_schema}' schema is permitted."
        )

    if schemaname_lower == "information_schema":
        if range_var.relname and range_var.relname.lower() == "schemata":
            raise ValueError(
                f"Access to '{schemaname}.{range_var.relname}' is not allowed in user mode. "
                "Use the list_schemas tool instead (available in admin mode)."
            )
        return

    if schemaname_lower != allowed_schema.lower():
        raise ValueError(
            f"Access to schema '{schemaname}' is not allowed. Only '{allowed_schema}' schema is permitted."
        )

    if table_prefix and range_var.relname and not range_var.relname.lower().startswith(table_prefix.lower()):
        raise ValueError(
            f"Access to table '{range_var.relname}' is not allowed. "
            f"Only tables with names starting with '{table_prefix}' are permitted."
        )
