# mypy: ignore-errors
"""Unit tests for explain tool registration."""


class TestExplainTools:
    """Tests for explain_query tool."""

    async def test_explain_query_tool_registered(
        self,
        registered_tools_provider,
    ) -> None:
        """explain_query tool is present in the provider."""
        tools = await registered_tools_provider.list_tools()
        names = [t.name for t in tools]
        assert "explain_query" in names
