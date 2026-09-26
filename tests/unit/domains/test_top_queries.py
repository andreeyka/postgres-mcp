# mypy: ignore-errors
"""Unit tests for domains.top_queries: rows from pg_stat_statements."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from postgres_fastmcp.domains.top_queries import TopQueriesCalc, get_top_queries
from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.shared.errors import InvalidSortCriteriaError, PgStatStatementsNotInstalledError


def _calc(mock_executor: MagicMock, *, installed: bool = True) -> TopQueriesCalc:
    """TopQueriesCalc с подменённой проверкой расширения и версии PostgreSQL 16."""
    calc = TopQueriesCalc(sql_driver=mock_executor, connection_id="test")
    calc._ext_inspector = MagicMock()
    calc._ext_inspector.check_extension = AsyncMock(return_value=MagicMock(is_installed=installed))
    calc._ext_inspector.get_postgres_version = AsyncMock(return_value=16)
    return calc


class TestGetTopQueries:
    """Tests for top_queries.get_top_queries dispatch."""

    @patch("postgres_fastmcp.domains.top_queries.TopQueriesCalc")
    async def test_sort_by_resources_passes_limit(self, mock_calc_cls: MagicMock, mock_db_access: MagicMock) -> None:
        """sort_by=resources returns resource rows and honours limit."""
        mock_tool = MagicMock()
        mock_tool.get_top_resource_queries = AsyncMock(return_value=[{"query": "q"}])
        mock_calc_cls.return_value = mock_tool

        result = await get_top_queries(mock_db_access, sort_by="resources", limit=7)

        assert result == [{"query": "q"}]
        mock_tool.get_top_resource_queries.assert_awaited_once_with(limit=7)

    @pytest.mark.parametrize(("sort_by", "criteria"), [("mean_time", "mean"), ("total_time", "total")])
    @patch("postgres_fastmcp.domains.top_queries.TopQueriesCalc")
    async def test_sort_by_time(
        self, mock_calc_cls: MagicMock, mock_db_access: MagicMock, sort_by: str, criteria: str
    ) -> None:
        """sort_by=mean_time/total_time returns time-ranked rows."""
        mock_tool = MagicMock()
        mock_tool.get_top_queries_by_time = AsyncMock(return_value=[{"query": "q"}])
        mock_calc_cls.return_value = mock_tool

        result = await get_top_queries(mock_db_access, sort_by=sort_by, limit=5)

        assert result == [{"query": "q"}]
        mock_tool.get_top_queries_by_time.assert_awaited_once_with(limit=5, sort_by=criteria)

    @patch("postgres_fastmcp.domains.top_queries.TopQueriesCalc")
    async def test_invalid_sort_by_raises(self, mock_calc_cls: MagicMock, mock_db_access: MagicMock) -> None:
        """get_top_queries(sort_by=invalid) raises InvalidSortCriteriaError."""
        mock_calc_cls.return_value = MagicMock()

        with pytest.raises(InvalidSortCriteriaError):
            await get_top_queries(mock_db_access, sort_by="invalid")


async def test_resource_queries_sql_is_limited(mock_executor: MagicMock) -> None:
    """sort_by=resources honours limit: the SQL ends with LIMIT and limit is passed as a parameter."""
    mock_executor.execute.return_value = [RowResult(cells={"query": b"SELECT 1", "calls": 2})]

    rows = await _calc(mock_executor).get_top_resource_queries(limit=7)

    query = mock_executor.execute.call_args.args[0]
    assert query.rstrip().rstrip(";").endswith("LIMIT {}")
    assert mock_executor.execute.call_args.kwargs["params"] == [7]
    assert rows == [{"query": "SELECT 1", "calls": 2}]


async def test_time_queries_return_rows(mock_executor: MagicMock) -> None:
    """Time ranking returns decoded rows, not a formatted string."""
    mock_executor.execute.return_value = [RowResult(cells={"query": "SELECT 1", "calls": 3})]

    rows = await _calc(mock_executor).get_top_queries_by_time(limit=3, sort_by="total")

    assert rows == [{"query": "SELECT 1", "calls": 3}]
    assert "ORDER BY total_exec_time DESC" in mock_executor.execute.call_args.args[0]
    assert mock_executor.execute.call_args.kwargs["params"] == [3]


@pytest.mark.parametrize("method", ["get_top_resource_queries", "get_top_queries_by_time"])
async def test_missing_extension_raises(mock_executor: MagicMock, method: str) -> None:
    """Without pg_stat_statements both rankings raise a user-facing error with the install hint."""
    with pytest.raises(PgStatStatementsNotInstalledError, match="CREATE EXTENSION pg_stat_statements"):
        await getattr(_calc(mock_executor, installed=False), method)()
    mock_executor.execute.assert_not_called()


def test_top_queries_sql_filters_self_queries_and_zero_calls() -> None:
    """Generated SQL must filter out pg_stat_statements self-queries and zero-call entries."""
    from pathlib import Path

    from postgres_fastmcp.domains import top_queries as mod

    src = Path(mod.__file__).read_text()
    assert "calls > 0" in src
    assert "NOT LIKE '%pg_stat_statements%'" in src
