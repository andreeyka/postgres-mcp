"""Сборка FastMCP: lifespan (db + текущие права), два провайдера (basic + full по access_mode)."""

from collections.abc import AsyncIterator
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan
from fastmcp.server.providers import FileSystemProvider

from postgres_fastmcp.config import Settings
from postgres_fastmcp.config.database import DatabaseConfig
from postgres_fastmcp.enums import AccessMode
from postgres_fastmcp.services.db_access_service import DbAccessService


def _tools_root() -> Path:
    """Корень каталогов инструментов (basic / full)."""
    return Path(__file__).resolve().parent / "tools"


def compose_mcp(settings: Settings) -> FastMCP:
    """Собирает MCP: lifespan, basic (4 tools), при full — full (5 tools). Описания по access_mode/write_mode."""
    database = settings.database

    @lifespan
    async def app_lifespan(
        _: FastMCP,
    ) -> AsyncIterator[dict[str, DbAccessService | DatabaseConfig]]:
        db_service = DbAccessService(database)
        try:
            yield {"db": db_service, "database_config": database}
        finally:
            await db_service.close()

    providers: list[FileSystemProvider] = [FileSystemProvider(_tools_root() / "basic")]
    if database.access_mode == AccessMode.FULL:
        providers.append(FileSystemProvider(_tools_root() / "full"))

    return FastMCP(
        name=settings.fastmcp.server_name,
        instructions=settings.fastmcp.instructions,
        lifespan=app_lifespan,
        providers=providers,
    )
