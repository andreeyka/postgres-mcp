# mypy: ignore-errors
"""Unit tests for health tool registration."""

from fastmcp.server.providers import LocalProvider


class TestHealthTools:
    """Tests for analyze_db_health tool."""

    async def test_analyze_db_health_tool_registered(
        self,
        registered_tools_provider: LocalProvider,
    ) -> None:
        """analyze_db_health tool is present in the provider."""
        tools = await registered_tools_provider.list_tools()
        names = [t.name for t in tools]
        assert "analyze_db_health" in names
