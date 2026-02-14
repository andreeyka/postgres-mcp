"""SQL execution MCP tools registration."""

from typing import Annotated

from fastmcp.server.providers import LocalProvider
from fastmcp.tools.tool import ToolResult
from pydantic import Field

from postgres_fastmcp.common.errors import BaseApplicationError
from postgres_fastmcp.di.sql_execution_provider import SqlExecutionServiceProvider
from postgres_fastmcp.enums import ToolTag
from postgres_fastmcp.services.sql_execution.service import SqlExecutionService
from postgres_fastmcp.tools.common import ToolDescriptions


def register_sql_tools(provider: LocalProvider, descriptions: ToolDescriptions) -> None:
    """Register SQL execution tools."""

    @provider.tool(
        description=descriptions.execute_sql,
        tags={ToolTag.BASIC},
        annotations={"readOnlyHint": False},
    )
    async def execute_sql(
        sql: Annotated[
            str,
            Field(
                default="all",
                description=(
                    "SQL query as string value to execute against the database. For read-only modes, "
                    "only SELECT queries are allowed. For 'full' role with 'unrestricted' access_mode, "
                    "any SQL statement (DDL, DML, DCL) is permitted"
                ),
            ),
        ] = "all",
        sql_execution_service: SqlExecutionService = SqlExecutionServiceProvider,
    ) -> ToolResult:
        """Execute a SQL query against the database."""
        try:
            return ToolResult(content=await sql_execution_service.execute_sql(sql))
        except BaseApplicationError as exc:
            return ToolResult(content=f"Error: {exc}")
        except Exception as exc:  # pragma: no cover - defensive fallback
            return ToolResult(content=f"Error: {exc}")
