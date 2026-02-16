# mypy: ignore-errors
"""Unit tests for schema tools registration."""

from fastmcp.server.providers import LocalProvider


EXPECTED_TOOL_NAMES = frozenset(
    {
        "list_schemas",
        "list_objects",
        "get_object_details",
        "execute_sql",
        "explain_query",
        "analyze_workload_indexes",
        "analyze_query_indexes",
        "analyze_db_health",
        "get_top_queries",
    }
)


class TestSchemaTools:
    """Tests for list_schemas tool registration and invocation."""

    async def test_list_schemas_tool_registered(
        self,
        registered_tools_provider: LocalProvider,
    ) -> None:
        """list_schemas tool is present in the provider."""
        tools = await registered_tools_provider.list_tools()
        names = [t.name for t in tools]
        assert "list_schemas" in names

    async def test_list_schemas_tool_can_be_retrieved(
        self,
        registered_tools_provider: LocalProvider,
    ) -> None:
        """list_schemas tool can be retrieved by name."""
        tool = await registered_tools_provider.get_tool("list_schemas")
        assert tool is not None
        assert tool.name == "list_schemas"

    async def test_all_expected_tools_registered(
        self,
        registered_tools_provider: LocalProvider,
    ) -> None:
        """All 9 tools are registered on the provider."""
        tools = await registered_tools_provider.list_tools()
        names = frozenset(t.name for t in tools)
        assert names >= EXPECTED_TOOL_NAMES
