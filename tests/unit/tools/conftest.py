# mypy: ignore-errors
"""Fixtures for tool layer tests: ToolDescriptions and tool registration.

Tool invocation via Client(mcp).call_tool (see https://gofastmcp.com/patterns/testing)
would require lifespan context to be available in request context; with in-memory
transport that does not hold, so we test only registration and ToolDescriptions.
Tool behavior (invocation results, role/prefix rules) is covered by integration
tests and by service-layer unit tests.
"""

import pytest
from fastmcp.server.providers import LocalProvider

from postgres_fastmcp.config.database import DatabaseConfig
from postgres_fastmcp.enums import AccessMode, UserRole
from postgres_fastmcp.tools.common import ToolDescriptions
from postgres_fastmcp.tools.explain_tools import register_explain_tools
from postgres_fastmcp.tools.health_tools import register_health_tools
from postgres_fastmcp.tools.index_tools import register_index_tools
from postgres_fastmcp.tools.objects_tools import register_objects_tools
from postgres_fastmcp.tools.schema_tools import register_schema_tools
from postgres_fastmcp.tools.sql_tools import register_sql_tools
from postgres_fastmcp.tools.top_queries_tools import register_top_queries_tools


def _register_all_tools(provider: LocalProvider, descriptions: ToolDescriptions) -> None:
    """Register all tools on the provider (same order as server.py)."""
    register_explain_tools(provider, descriptions)
    register_schema_tools(provider, descriptions)
    register_objects_tools(provider, descriptions)
    register_sql_tools(provider, descriptions)
    register_index_tools(provider, descriptions)
    register_health_tools(provider, descriptions)
    register_top_queries_tools(provider, descriptions)


@pytest.fixture
def database_config_full_restricted() -> DatabaseConfig:
    """DatabaseConfig for full role and restricted access."""
    return DatabaseConfig.from_uri(
        "postgres://localhost/test",
        role=UserRole.ADMIN,
        access_mode=AccessMode.RESTRICTED,
    )


@pytest.fixture
def database_config_user() -> DatabaseConfig:
    """DatabaseConfig for user role."""
    return DatabaseConfig.from_uri(
        "postgres://localhost/test",
        role=UserRole.USER,
        access_mode=AccessMode.RESTRICTED,
    )


@pytest.fixture
def database_config_full_unrestricted() -> DatabaseConfig:
    """DatabaseConfig for full role and unrestricted access."""
    return DatabaseConfig.from_uri(
        "postgres://localhost/test",
        role=UserRole.ADMIN,
        access_mode=AccessMode.UNRESTRICTED,
    )


@pytest.fixture
def tool_descriptions_full(database_config_full_restricted: DatabaseConfig) -> ToolDescriptions:
    """ToolDescriptions for full/restricted."""
    return ToolDescriptions(database_config_full_restricted)


@pytest.fixture
def tool_descriptions_user(database_config_user: DatabaseConfig) -> ToolDescriptions:
    """ToolDescriptions for user role."""
    return ToolDescriptions(database_config_user)


@pytest.fixture
def tool_descriptions_unrestricted(
    database_config_full_unrestricted: DatabaseConfig,
) -> ToolDescriptions:
    """ToolDescriptions for full/unrestricted."""
    return ToolDescriptions(database_config_full_unrestricted)


@pytest.fixture
async def registered_tools_provider(
    database_config_full_restricted: DatabaseConfig,
) -> LocalProvider:
    """Provider with all tools registered (for testing tool list and names)."""
    provider = LocalProvider()
    descriptions = ToolDescriptions(database_config_full_restricted)
    _register_all_tools(provider, descriptions)
    return provider
