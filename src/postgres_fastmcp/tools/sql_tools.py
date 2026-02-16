"""Регистрация MCP-инструментов выполнения SQL."""

from typing import Annotated, Any

from fastmcp.server.providers import LocalProvider
from pydantic import Field

from postgres_fastmcp.di.sql_execution_provider import SqlExecutionServiceProvider
from postgres_fastmcp.enums import ToolTag
from postgres_fastmcp.services.sql_execution.service import SqlExecutionService
from postgres_fastmcp.tools.common import ToolDescriptions


def register_sql_tools(provider: LocalProvider, descriptions: ToolDescriptions) -> None:
    """Зарегистрировать инструменты выполнения SQL.

    Args:
        provider: Провайдер для регистрации инструментов.
        descriptions: Описания инструментов.
    """

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
                    "only SELECT queries are allowed. For 'admin' role with 'unrestricted' access_mode, "
                    "any SQL statement (DDL, DML, DCL) is permitted"
                ),
            ),
        ] = "all",
        sql_execution_service: SqlExecutionService = SqlExecutionServiceProvider,
    ) -> list[dict[str, Any]]:
        """Выполнить SQL-запрос к базе данных.

        Returns:
            Результаты запроса. FastMCP преобразует в ответ. При ошибке — исключение наружу.
        """
        return await sql_execution_service.execute_sql(sql)
