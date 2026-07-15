"""Тул execute_sql — выполняет SQL-запрос против БД."""

from typing import Annotated, Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import Field

from postgres_fastmcp.app_context import get_db
from postgres_fastmcp.services.sql_execution.service import SqlExecutionService


async def execute_sql(
    sql: Annotated[str, Field(description="SQL statement to execute.")],
    ctx: Context = CurrentContext(),
) -> list[dict[str, Any]]:
    """Выполнить SQL-запрос. Область доступа (access_mode) и режим записи (write_mode) берутся из конфигурации БД."""
    db = get_db(ctx)
    service = SqlExecutionService(db=db)
    return await service.execute_sql(sql)
