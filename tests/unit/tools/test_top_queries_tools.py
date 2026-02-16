# mypy: ignore-errors
"""Unit tests for top queries tool registration."""

from fastmcp.server.providers import LocalProvider


class TestTopQueriesTools:
    """Tests for get_top_queries tool."""

    async def test_get_top_queries_tool_registered(
        self,
        registered_tools_provider: LocalProvider,
    ) -> None:
        """get_top_queries tool is present in the provider."""
        tools = await registered_tools_provider.list_tools()
        names = [t.name for t in tools]
        assert "get_top_queries" in names
