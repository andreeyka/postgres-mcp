"""Schema and table-prefix access checks for safe SQL."""

from pglast.ast import RangeVar


def validate_schema_access(
    range_var: RangeVar,
    *,
    allowed_schema: str | None,
    table_prefix: str | None,
) -> None:
    """Check that the table is in an allowed schema and matches optional prefix.

    Args:
        range_var: AST node for the table reference.
        allowed_schema: If set, only this schema is allowed (e.g. 'public').
        table_prefix: If set with allowed_schema, table name must start with this.

    Raises:
        ValueError: If schema or table is not allowed.
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
