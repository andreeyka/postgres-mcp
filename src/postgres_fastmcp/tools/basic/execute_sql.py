"""Обработчик execute_sql (описание по access_mode и write_mode из AppConfig)."""

from typing import Annotated, Any

from fastmcp.tools import tool
from pydantic import Field

from postgres_fastmcp.config import app_config
from postgres_fastmcp.enums import AccessMode, ToolTag
from postgres_fastmcp.providers.sql_execution_provider import SqlExecutionServiceProvider
from postgres_fastmcp.services.sql_execution.service import SqlExecutionService


_DESC_RESTRICTED = (
    "Execute a read-only SQL query against the database. "
    "Input: sql - a SQL SELECT query (read-only operations only). "
    "Output: Query results as JSON array of rows. "
    "\n\nIMPORTANT: Only SELECT queries are allowed. DDL (CREATE, ALTER, DROP), DML (INSERT, UPDATE, DELETE), "
    "and DCL (GRANT, REVOKE) operations are blocked. "
    "This is the MAIN tool to use after you have explored the schema with list_objects and get_object_details. "
    "Always use existing columns and tables from the schema - do not invent fields. "
    "Example workflow: 1) list_objects, 2) get_object_details, 3) execute_sql (THIS TOOL) to get actual data."
)
_DESC_UNRESTRICTED = (
    "Execute any SQL query against the database (DDL, DML, DCL allowed). "
    "Input: sql - any valid PostgreSQL SQL statement. "
    "Output: Query results (for SELECT) or execution status (for DDL/DML). "
    "IMPORTANT: This tool has full database access. Use with caution. "
    "For read-only operations, prefer the read-only version if available. "
    "Always use explain_query first for SELECT queries to understand the execution plan. "
    "Example: Use for CREATE TABLE, INSERT, UPDATE, DELETE, ALTER, and other DDL/DML operations."
)
_db = app_config.current.database
_DESC = _DESC_UNRESTRICTED if (_db.access_mode == AccessMode.FULL and _db.write_mode) else _DESC_RESTRICTED
_SQL_PARAM_DESC = (
    "SQL query as string value to execute against the database. Only SELECT queries are allowed (read-only)."
    if not (_db.access_mode == AccessMode.FULL and _db.write_mode)
    else "SQL query as string value to execute against the database. Any SQL statement (DDL, DML, DCL) is permitted."
)


@tool(
    description=_DESC,
    tags={ToolTag.BASIC},
    annotations={
        "title": "Execute SQL",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def execute_sql(
    sql: Annotated[
        str,
        Field(
            default="all",
            description=_SQL_PARAM_DESC,
        ),
    ] = "all",
    sql_execution_service: SqlExecutionService = SqlExecutionServiceProvider,
) -> list[dict[str, Any]]:
    """Выполнить SQL-запрос к базе данных.

    Returns:
        Результаты запроса. FastMCP преобразует в ответ. При ошибке — исключение наружу.
    """
    return await sql_execution_service.execute_sql(sql)
