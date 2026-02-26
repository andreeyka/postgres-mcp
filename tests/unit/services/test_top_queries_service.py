# mypy: ignore-errors
"""Unit tests for TopQueriesService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from postgres_fastmcp.common.errors import InvalidSortCriteriaError
from postgres_fastmcp.services.top_queries.service import TopQueriesService


class TestTopQueriesService:
    """Tests for TopQueriesService.get_top_queries."""

    @patch("postgres_fastmcp.services.top_queries.service.TopQueriesCalc")
    async def test_get_top_queries_sort_by_resources_returns_resource_report(
        self,
        mock_calc_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """get_top_queries(sort_by=resources) returns resource usage report string."""
        mock_tool = MagicMock()
        mock_tool.get_top_resource_queries = AsyncMock(return_value="Resource report")
        mock_tool.get_top_queries_by_time = AsyncMock()
        mock_calc_cls.return_value = mock_tool

        service = TopQueriesService(db=mock_db_access)
        result = await service.get_top_queries(sort_by="resources")
        assert result == "Resource report"

    @patch("postgres_fastmcp.services.top_queries.service.TopQueriesCalc")
    async def test_get_top_queries_sort_by_mean_time_returns_mean_time_report(
        self,
        mock_calc_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """get_top_queries(sort_by=mean_time, limit=...) returns mean time report."""
        mock_tool = MagicMock()
        mock_tool.get_top_resource_queries = AsyncMock()
        mock_tool.get_top_queries_by_time = AsyncMock(return_value="Mean time report")
        mock_calc_cls.return_value = mock_tool

        service = TopQueriesService(db=mock_db_access)
        result = await service.get_top_queries(sort_by="mean_time", limit=5)
        assert result == "Mean time report"

    @patch("postgres_fastmcp.services.top_queries.service.TopQueriesCalc")
    async def test_get_top_queries_sort_by_total_time_returns_total_time_report(
        self,
        mock_calc_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """get_top_queries(sort_by=total_time, limit=...) returns total time report."""
        mock_tool = MagicMock()
        mock_tool.get_top_queries_by_time = AsyncMock(return_value="Total time report")
        mock_calc_cls.return_value = mock_tool

        service = TopQueriesService(db=mock_db_access)
        result = await service.get_top_queries(sort_by="total_time", limit=10)
        assert result == "Total time report"

    @patch("postgres_fastmcp.services.top_queries.service.TopQueriesCalc")
    async def test_get_top_queries_invalid_sort_by_raises(
        self,
        mock_calc_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """get_top_queries(sort_by=invalid) raises InvalidSortCriteriaError."""
        mock_tool = MagicMock()
        mock_calc_cls.return_value = mock_tool

        service = TopQueriesService(db=mock_db_access)
        with pytest.raises(InvalidSortCriteriaError):
            await service.get_top_queries(sort_by="invalid")
