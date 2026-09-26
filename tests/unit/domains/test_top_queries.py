# mypy: ignore-errors
"""Unit tests for domains.top_queries: rows from pg_stat_statements."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pglast import ast, parse_sql
from pglast.enums.parsenodes import A_Expr_Kind
from pglast.visitors import Visitor
from psycopg.errors import ObjectNotInPrerequisiteState

from postgres_fastmcp.domains.top_queries import TopQueriesCalc, get_top_queries
from postgres_fastmcp.postgres.extensions import CATALOG_ERROR_MESSAGE, ExtensionStatus
from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.shared.errors import (
    ExtensionStatusUnavailableError,
    InvalidSortCriteriaError,
    PgStatStatementsNotInstalledError,
    UnsupportedServerVersionError,
)


def _calc(
    mock_executor: MagicMock, *, installed: bool = True, pg_version: int = 16, catalog_error: str | None = None
) -> TopQueriesCalc:
    """TopQueriesCalc с подменённой проверкой расширения и версией PostgreSQL (по умолчанию 16)."""
    calc = TopQueriesCalc(sql_driver=mock_executor, connection_id="test")
    calc._ext_inspector = MagicMock()
    status = ExtensionStatus(
        is_installed=installed,
        is_available=installed,
        name="pg_stat_statements",
        message="",
        default_version=None,
        catalog_error=catalog_error,
    )
    calc._ext_inspector.check_extension = AsyncMock(return_value=status)
    calc._ext_inspector.get_postgres_version = AsyncMock(return_value=pg_version)
    return calc


class _Divisions(Visitor):
    """Собирает правые операнды всех делений '/' в SQL."""

    def __init__(self) -> None:
        self.denominators: list[ast.Node] = []

    def visit_A_Expr(self, parent: object, node: ast.A_Expr) -> None:  # noqa: ARG002
        if node.name and node.name[0].sval == "/":
            self.denominators.append(node.rexpr)


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


@pytest.mark.parametrize("method", ["get_top_resource_queries", "get_top_queries_by_time"])
async def test_catalog_error_is_not_reported_as_missing_extension(mock_executor: MagicMock, method: str) -> None:
    """Каталог расширений упал: агент видит причину и подсказку повторить, а не «не установлено»."""
    calc = _calc(mock_executor, installed=False, catalog_error=CATALOG_ERROR_MESSAGE)

    with pytest.raises(ExtensionStatusUnavailableError) as exc_info:
        await getattr(calc, method)()

    message = str(exc_info.value)
    assert "pg_stat_statements" in message
    assert CATALOG_ERROR_MESSAGE in message
    assert "retry" in message
    assert "CREATE EXTENSION" not in message
    mock_executor.execute.assert_not_called()


async def test_resource_queries_guard_zero_totals(mock_executor: MagicMock) -> None:
    """Every fraction divides by NULLIF(total, 0): a zero total (no WAL, no reads) gives NULL, not division by zero."""
    mock_executor.execute.return_value = []

    await _calc(mock_executor).get_top_resource_queries(limit=7)

    query = mock_executor.execute.call_args.args[0].replace("{}", "1")
    divisions = _Divisions()
    divisions(parse_sql(query))
    assert len(divisions.denominators) == 5
    for denominator in divisions.denominators:
        assert isinstance(denominator, ast.A_Expr), denominator
        assert denominator.kind == A_Expr_Kind.AEXPR_NULLIF, denominator


@pytest.mark.parametrize("method", ["get_top_resource_queries", "get_top_queries_by_time"])
async def test_extension_not_preloaded_raises(mock_executor: MagicMock, method: str) -> None:
    """Extension created but not in shared_preload_libraries: the view raises, the agent gets the install hint."""
    mock_executor.execute.side_effect = ObjectNotInPrerequisiteState("pg_stat_statements must be loaded")

    with pytest.raises(PgStatStatementsNotInstalledError, match="shared_preload_libraries") as exc_info:
        await getattr(_calc(mock_executor), method)()

    assert isinstance(exc_info.value.__cause__, ObjectNotInPrerequisiteState)


async def test_resource_queries_require_postgres_13(mock_executor: MagicMock) -> None:
    """Before PostgreSQL 13 the resource columns do not exist: fail before querying, point to the time rankings."""
    with pytest.raises(UnsupportedServerVersionError, match="sort_by='total_time'"):
        await _calc(mock_executor, pg_version=12).get_top_resource_queries()

    mock_executor.execute.assert_not_called()


async def test_time_queries_work_before_postgres_13(mock_executor: MagicMock) -> None:
    """The time rankings still work on PostgreSQL 12 with the old column names."""
    mock_executor.execute.return_value = []

    await _calc(mock_executor, pg_version=12).get_top_queries_by_time(sort_by="total")

    assert "ORDER BY total_time DESC" in mock_executor.execute.call_args.args[0]


def test_top_queries_sql_filters_self_queries_and_zero_calls() -> None:
    """Generated SQL must filter out pg_stat_statements self-queries and zero-call entries."""
    from pathlib import Path

    from postgres_fastmcp.domains import top_queries as mod

    src = Path(mod.__file__).read_text()
    assert "calls > 0" in src
    assert "NOT LIKE '%pg_stat_statements%'" in src
