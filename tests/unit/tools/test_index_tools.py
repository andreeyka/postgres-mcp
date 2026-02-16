# mypy: ignore-errors
"""Unit tests for index tools registration."""

from fastmcp.server.providers import LocalProvider


class TestIndexTools:
    """Tests for index analysis tools."""

    async def test_analyze_workload_indexes_tool_registered(
        self,
        registered_tools_provider: LocalProvider,
    ) -> None:
        """analyze_workload_indexes tool is present in the provider."""
        tools = await registered_tools_provider.list_tools()
        names = [t.name for t in tools]
        assert "analyze_workload_indexes" in names

    async def test_analyze_query_indexes_tool_registered(
        self,
        registered_tools_provider: LocalProvider,
    ) -> None:
        """analyze_query_indexes tool is present in the provider."""
        tools = await registered_tools_provider.list_tools()
        names = [t.name for t in tools]
        assert "analyze_query_indexes" in names
