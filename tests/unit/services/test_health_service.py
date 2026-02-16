# mypy: ignore-errors
"""Unit tests for HealthService."""

from unittest.mock import AsyncMock, MagicMock, patch

from postgres_fastmcp.services.health.service import HealthService


class TestHealthService:
    """Tests for HealthService.analyze_db_health."""

    @patch("postgres_fastmcp.services.health.service.DatabaseHealthTool")
    async def test_analyze_db_health_returns_string_report(
        self,
        mock_health_tool_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """analyze_db_health(health_type) returns string report from health tool."""
        mock_tool = MagicMock()
        mock_tool.health = AsyncMock(return_value="Health report")
        mock_health_tool_cls.return_value = mock_tool

        service = HealthService(db=mock_db_access)
        result = await service.analyze_db_health(health_type="all")
        assert result == "Health report"

    @patch("postgres_fastmcp.services.health.service.DatabaseHealthTool")
    async def test_analyze_db_health_returns_report_for_given_health_type(
        self,
        mock_health_tool_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """analyze_db_health returns report content for requested health_type (e.g. index,vacuum)."""
        mock_tool = MagicMock()
        mock_tool.health = AsyncMock(return_value="Index health")
        mock_health_tool_cls.return_value = mock_tool

        service = HealthService(db=mock_db_access)
        result = await service.analyze_db_health(health_type="index,vacuum")
        assert result == "Index health"
