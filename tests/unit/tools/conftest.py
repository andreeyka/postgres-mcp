# mypy: ignore-errors
"""Fixtures for tool layer tests: basic and full (both FileSystemProvider).

Tool invocation via Client(mcp).call_tool would require lifespan context; we test
only registration and tool set. Tool behavior is covered by integration and service tests.
"""

import pytest
from fastmcp.server.providers import FileSystemProvider

from postgres_fastmcp.config import app_config
from postgres_fastmcp.config.database import DatabaseConfig
from postgres_fastmcp.enums import AccessMode
from postgres_fastmcp.server import _tools_root

# Initialize config before tool modules are imported (they access AppConfig.current at module level).
_init_db = DatabaseConfig.from_uri(
    "postgres://testuser:testpass@localhost:5432/test",
    access_mode=AccessMode.BASIC,
    write_mode=False,
)
app_config.initialize(database=_init_db)


@pytest.fixture
def database_config_full_restricted() -> DatabaseConfig:
    """DatabaseConfig for access_mode=full and write_mode=False."""
    return DatabaseConfig.from_uri(
        "postgres://testuser:testpass@localhost:5432/test",
        access_mode=AccessMode.FULL,
        write_mode=False,
    )


@pytest.fixture
def database_config_user() -> DatabaseConfig:
    """DatabaseConfig for access_mode=basic."""
    return DatabaseConfig.from_uri(
        "postgres://testuser:testpass@localhost:5432/test",
        access_mode=AccessMode.BASIC,
        write_mode=False,
    )


@pytest.fixture
def database_config_full_unrestricted() -> DatabaseConfig:
    """DatabaseConfig for access_mode=full and write_mode=True."""
    return DatabaseConfig.from_uri(
        "postgres://testuser:testpass@localhost:5432/test",
        access_mode=AccessMode.FULL,
        write_mode=True,
    )


@pytest.fixture
def basic_tools_provider(database_config_user: DatabaseConfig) -> FileSystemProvider:
    """FileSystemProvider с 4 базовыми инструментами (access_mode=basic)."""
    app_config.initialize(database=database_config_user)
    return FileSystemProvider(_tools_root() / "basic")


@pytest.fixture
def full_tools_provider() -> FileSystemProvider:
    """FileSystemProvider для full: 5 инструментов."""
    return FileSystemProvider(_tools_root() / "full")


class _CombinedToolsProvider:
    """Провайдер-обёртка: list_tools и get_tool от basic + full."""

    def __init__(self, basic: FileSystemProvider, full: FileSystemProvider) -> None:
        self._basic = basic
        self._full = full

    async def list_tools(self) -> list:
        basic_tools = await self._basic.list_tools()
        full_tools = await self._full.list_tools()
        return list(basic_tools) + list(full_tools)

    async def get_tool(self, name: str):
        tool = await self._basic.get_tool(name)
        if tool is not None:
            return tool
        return await self._full.get_tool(name)


@pytest.fixture
def registered_tools_provider(
    database_config_full_restricted: DatabaseConfig,
    full_tools_provider: FileSystemProvider,
) -> _CombinedToolsProvider:
    """Провайдер со всеми 9 инструментами (4 basic + 5 full) для тестов списка и имён."""
    app_config.initialize(database=database_config_full_restricted)
    basic = FileSystemProvider(_tools_root() / "basic")
    return _CombinedToolsProvider(basic, full_tools_provider)
