"""Сборка FastMCP: lifespan (db + текущие права), провайдер, регистрация инструментов."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastmcp import FastMCP
from fastmcp.server.providers import LocalProvider

from postgres_fastmcp.config import Settings
from postgres_fastmcp.config.database import DatabaseConfig
from postgres_fastmcp.services.db_access_service import DbAccessService
from postgres_fastmcp.tools.common import ToolDescriptions
from postgres_fastmcp.tools.explain_tools import register_explain_tools
from postgres_fastmcp.tools.health_tools import register_health_tools
from postgres_fastmcp.tools.index_tools import register_index_tools
from postgres_fastmcp.tools.objects_tools import register_objects_tools
from postgres_fastmcp.tools.schema_tools import register_schema_tools
from postgres_fastmcp.tools.sql_tools import register_sql_tools
from postgres_fastmcp.tools.top_queries_tools import register_top_queries_tools


def compose_mcp(settings: Settings) -> FastMCP:
    """Собирает MCP: lifespan с db и database_config (права), провайдер, инструменты."""
    database = settings.database

    @asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncIterator[dict[str, DbAccessService | DatabaseConfig]]:
        db_service = DbAccessService(database)
        async with db_service:
            yield {"db": db_service, "database_config": database}

    descriptions = ToolDescriptions(database)
    provider = LocalProvider()
    register_explain_tools(provider, descriptions)
    register_schema_tools(provider, descriptions)
    register_objects_tools(provider, descriptions)
    register_sql_tools(provider, descriptions)
    register_index_tools(provider, descriptions)
    register_health_tools(provider, descriptions)
    register_top_queries_tools(provider, descriptions)

    return FastMCP(
        name=settings.fastmcp.server_name,
        instructions=settings.fastmcp.instructions,
        lifespan=lifespan,
        providers=[provider],
    )
