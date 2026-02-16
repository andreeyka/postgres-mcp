# mypy: ignore-errors
"""Unit tests for SQL execution tool registration."""

from fastmcp.server.providers import LocalProvider


class TestSqlTools:
    """Tests for execute_sql tool."""

    async def test_execute_sql_tool_registered(
        self,
        registered_tools_provider: LocalProvider,
    ) -> None:
        """execute_sql tool is present in the provider."""
        tools = await registered_tools_provider.list_tools()
        names = [t.name for t in tools]
        assert "execute_sql" in names
