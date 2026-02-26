# mypy: ignore-errors
"""Unit tests for top queries tool registration."""


class TestTopQueriesTools:
    """Tests for get_top_queries tool."""

    async def test_get_top_queries_tool_registered(
        self,
        registered_tools_provider,
    ) -> None:
        """get_top_queries tool is present in the provider."""
        tools = await registered_tools_provider.list_tools()
        names = [t.name for t in tools]
        assert "get_top_queries" in names
