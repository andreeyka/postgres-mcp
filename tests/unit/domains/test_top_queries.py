# mypy: ignore-errors
"""Unit tests for services.top_queries.get_top_queries."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from postgres_fastmcp.shared.errors import InvalidSortCriteriaError
from postgres_fastmcp.domains.top_queries import TopQueriesCalc, get_top_queries


class TestGetTopQueries:
    """Tests for top_queries.get_top_queries."""

    @patch("postgres_fastmcp.domains.top_queries.TopQueriesCalc")
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

        result = await get_top_queries(mock_db_access, sort_by="resources", limit=7)
        assert result == "Resource report"
        mock_tool.get_top_resource_queries.assert_awaited_once_with(limit=7)

    @patch("postgres_fastmcp.domains.top_queries.TopQueriesCalc")
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

        result = await get_top_queries(mock_db_access, sort_by="mean_time", limit=5)
        assert result == "Mean time report"

    @patch("postgres_fastmcp.domains.top_queries.TopQueriesCalc")
    async def test_get_top_queries_sort_by_total_time_returns_total_time_report(
        self,
        mock_calc_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """get_top_queries(sort_by=total_time, limit=...) returns total time report."""
        mock_tool = MagicMock()
        mock_tool.get_top_queries_by_time = AsyncMock(return_value="Total time report")
        mock_calc_cls.return_value = mock_tool

        result = await get_top_queries(mock_db_access, sort_by="total_time", limit=10)
        assert result == "Total time report"

    @patch("postgres_fastmcp.domains.top_queries.TopQueriesCalc")
    async def test_get_top_queries_invalid_sort_by_raises(
        self,
        mock_calc_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """get_top_queries(sort_by=invalid) raises InvalidSortCriteriaError."""
        mock_tool = MagicMock()
        mock_calc_cls.return_value = mock_tool

        with pytest.raises(InvalidSortCriteriaError):
            await get_top_queries(mock_db_access, sort_by="invalid")


async def test_resource_queries_sql_is_limited(mock_executor: MagicMock) -> None:
    """sort_by=resources honours limit: the SQL ends with LIMIT and limit is passed as a parameter."""
    calc = TopQueriesCalc(sql_driver=mock_executor, connection_id="test")
    calc._ext_inspector = MagicMock()
    calc._ext_inspector.check_extension = AsyncMock(return_value=MagicMock(is_installed=True))
    calc._ext_inspector.get_postgres_version = AsyncMock(return_value=16)

    await calc.get_top_resource_queries(limit=7)

    query = mock_executor.execute.call_args.args[0]
    assert query.rstrip().rstrip(";").endswith("LIMIT {}")
    assert mock_executor.execute.call_args.kwargs["params"] == [7]


def test_top_queries_sql_filters_self_queries_and_zero_calls() -> None:
    """Generated SQL must filter out pg_stat_statements self-queries and zero-call entries."""
    from pathlib import Path

    from postgres_fastmcp.domains import top_queries as mod

    src = Path(mod.__file__).read_text()
    assert "calls > 0" in src
    assert "NOT LIKE '%pg_stat_statements%'" in src
