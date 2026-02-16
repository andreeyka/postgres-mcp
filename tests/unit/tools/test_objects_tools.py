# mypy: ignore-errors
"""Unit tests for objects tools registration."""

from fastmcp.server.providers import LocalProvider


class TestObjectsTools:
    """Tests for list_objects and get_object_details tools."""

    async def test_list_objects_tool_registered(
        self,
        registered_tools_provider: LocalProvider,
    ) -> None:
        """list_objects tool is present in the provider."""
        tools = await registered_tools_provider.list_tools()
        names = [t.name for t in tools]
        assert "list_objects" in names

    async def test_get_object_details_tool_registered(
        self,
        registered_tools_provider: LocalProvider,
    ) -> None:
        """get_object_details tool is present in the provider."""
        tools = await registered_tools_provider.list_tools()
        names = [t.name for t in tools]
        assert "get_object_details" in names
