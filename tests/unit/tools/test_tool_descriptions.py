# mypy: ignore-errors
"""Unit tests for configure() + FileSystemProvider discovery (mode-aware descriptions)."""

import sys

import pytest
from fastmcp.server.providers import FileSystemProvider

from postgres_fastmcp.config import app_config
from postgres_fastmcp.server import _tools_root
from postgres_fastmcp.tools.basic.execute_sql import _DESC_RESTRICTED, _DESC_UNRESTRICTED
from postgres_fastmcp.tools.basic.explain_query import _DESC_EXPLAIN_QUERY
from postgres_fastmcp.tools.basic.get_object_details import (
    _DESC_FULL as _GET_OBJECT_DETAILS_FULL,
    _DESC_USER as _GET_OBJECT_DETAILS_USER,
)
from postgres_fastmcp.tools.basic.list_objects import (
    _DESC_FULL as _LIST_OBJECTS_FULL,
    _DESC_USER as _LIST_OBJECTS_USER,
)


def _clear_basic_tools_modules() -> None:
    """Remove basic tool modules from sys.modules so next provider creation re-imports with new config."""
    to_remove = [k for k in sys.modules if k.startswith("postgres_fastmcp.tools.basic.")]
    for k in to_remove:
        sys.modules.pop(k, None)


def _get_tool_by_name(tools: list, name: str):
    """Return tool with given name from list returned by list_tools()."""
    for t in tools:
        if getattr(t, "name", None) == name:
            return t
    return None


class TestConfigureAndDiscovery:
    """Tests for configure() + FileSystemProvider yielding correct descriptions."""

    @pytest.mark.asyncio
    async def test_list_objects_basic_returns_user_description(
        self,
        database_config_user,
    ) -> None:
        """access_mode=basic: list_objects has USER description."""
        _clear_basic_tools_modules()
        app_config.initialize(database=database_config_user)
        provider = FileSystemProvider(_tools_root() / "basic")
        tools = await provider.list_tools()
        tool = _get_tool_by_name(tools, "list_objects")
        assert tool is not None
        assert getattr(tool, "description", None) == _LIST_OBJECTS_USER

    @pytest.mark.asyncio
    async def test_list_objects_full_returns_full_description(
        self,
        database_config_full_restricted,
    ) -> None:
        """access_mode=full: list_objects has FULL description."""
        _clear_basic_tools_modules()
        app_config.initialize(database=database_config_full_restricted)
        provider = FileSystemProvider(_tools_root() / "basic")
        tools = await provider.list_tools()
        tool = _get_tool_by_name(tools, "list_objects")
        assert tool is not None
        assert getattr(tool, "description", None) == _LIST_OBJECTS_FULL

    @pytest.mark.asyncio
    async def test_get_object_details_basic_returns_user_description(
        self,
        database_config_user,
    ) -> None:
        """access_mode=basic: get_object_details has USER description."""
        _clear_basic_tools_modules()
        app_config.initialize(database=database_config_user)
        provider = FileSystemProvider(_tools_root() / "basic")
        tools = await provider.list_tools()
        tool = _get_tool_by_name(tools, "get_object_details")
        assert tool is not None
        assert getattr(tool, "description", None) == _GET_OBJECT_DETAILS_USER

    @pytest.mark.asyncio
    async def test_get_object_details_full_returns_full_description(
        self,
        database_config_full_restricted,
    ) -> None:
        """access_mode=full: get_object_details has FULL description."""
        _clear_basic_tools_modules()
        app_config.initialize(database=database_config_full_restricted)
        provider = FileSystemProvider(_tools_root() / "basic")
        tools = await provider.list_tools()
        tool = _get_tool_by_name(tools, "get_object_details")
        assert tool is not None
        assert getattr(tool, "description", None) == _GET_OBJECT_DETAILS_FULL

    @pytest.mark.asyncio
    async def test_explain_query_same_for_all(
        self,
        database_config_user,
    ) -> None:
        """explain_query description does not depend on access_mode."""
        _clear_basic_tools_modules()
        app_config.initialize(database=database_config_user)
        provider = FileSystemProvider(_tools_root() / "basic")
        tools = await provider.list_tools()
        tool = _get_tool_by_name(tools, "explain_query")
        assert tool is not None
        assert getattr(tool, "description", None) == _DESC_EXPLAIN_QUERY

    @pytest.mark.asyncio
    async def test_execute_sql_restricted_for_basic(
        self,
        database_config_user,
    ) -> None:
        """access_mode=basic: execute_sql has RESTRICTED description."""
        _clear_basic_tools_modules()
        app_config.initialize(database=database_config_user)
        provider = FileSystemProvider(_tools_root() / "basic")
        tools = await provider.list_tools()
        tool = _get_tool_by_name(tools, "execute_sql")
        assert tool is not None
        assert getattr(tool, "description", None) == _DESC_RESTRICTED

    @pytest.mark.asyncio
    async def test_execute_sql_restricted_for_full_restricted(
        self,
        database_config_full_restricted,
    ) -> None:
        """access_mode=full, write_mode=False: execute_sql has RESTRICTED description."""
        _clear_basic_tools_modules()
        app_config.initialize(database=database_config_full_restricted)
        provider = FileSystemProvider(_tools_root() / "basic")
        tools = await provider.list_tools()
        tool = _get_tool_by_name(tools, "execute_sql")
        assert tool is not None
        assert getattr(tool, "description", None) == _DESC_RESTRICTED

    @pytest.mark.asyncio
    async def test_execute_sql_unrestricted_for_full_unrestricted(
        self,
        database_config_full_unrestricted,
    ) -> None:
        """access_mode=full, write_mode=True: execute_sql has UNRESTRICTED description."""
        _clear_basic_tools_modules()
        app_config.initialize(database=database_config_full_unrestricted)
        provider = FileSystemProvider(_tools_root() / "basic")
        tools = await provider.list_tools()
        tool = _get_tool_by_name(tools, "execute_sql")
        assert tool is not None
        assert getattr(tool, "description", None) == _DESC_UNRESTRICTED
