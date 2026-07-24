# mypy: ignore-errors
"""Unit tests for SqlExecutor."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from postgres_fastmcp.common.errors import ConnectionNotEstablishedError
from postgres_fastmcp.sql.connection import DbConnPool
from postgres_fastmcp.sql.driver import SqlExecutor
from postgres_fastmcp.sql.models import RowResult


class TestSqlExecutorRender:
    """Parameter rendering (no I/O)."""

    def test_render_substitutes_params(self) -> None:
        """render() substitutes {} placeholders with literal values."""
        pool = MagicMock(spec=DbConnPool)
        executor = SqlExecutor(conn=pool)
        out = executor.render("SELECT * FROM t WHERE id = {}", [42])
        assert "42" in out
        assert "{}" not in out

    def test_init_requires_conn_or_engine_url(self) -> None:
        """Constructor raises if neither conn nor engine_url provided."""
        with pytest.raises(ConnectionNotEstablishedError) as exc_info:
            SqlExecutor(conn=None, engine_url=None)
        assert "conn or engine_url" in str(exc_info.value)


class TestSqlExecutorExecuteWithPool:
    """execute() behavior: returns rows, marks pool invalid on exception, renders params before run."""

    def _mock_pool_for_execute(self) -> MagicMock:
        """Build a mock DbConnPool so that async with pool.connection() completes."""
        mock_connection = MagicMock()
        mock_connection.set_autocommit = AsyncMock()
        ctx_mgr = MagicMock()
        ctx_mgr.__aenter__ = AsyncMock(return_value=mock_connection)
        ctx_mgr.__aexit__ = AsyncMock(return_value=None)
        mock_pool_inner = MagicMock()
        mock_pool_inner.connection.return_value = ctx_mgr
        mock_pool = MagicMock(spec=DbConnPool)
        mock_pool.pool_connect = AsyncMock(return_value=mock_pool_inner)
        return mock_pool

    async def test_execute_returns_row_results(self) -> None:
        """execute(sql, readonly=True) returns list of RowResult from the driver."""
        mock_pool = self._mock_pool_for_execute()
        executor = SqlExecutor(conn=mock_pool)
        with patch.object(executor, "_execute_with_connection", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = [RowResult(cells={"x": 1})]
            result = await executor.execute("SELECT 1", readonly=True)
            assert result == [RowResult(cells={"x": 1})]
            call_kw = mock_exec.call_args[1]
            assert call_kw["readonly"] is True

    async def test_execute_exception_marks_pool_invalid(self) -> None:
        """When execute fails (e.g. connection lost), pool is marked invalid."""
        mock_pool = self._mock_pool_for_execute()
        executor = SqlExecutor(conn=mock_pool)
        with patch.object(executor, "_execute_with_connection", new_callable=AsyncMock) as mock_exec:
            mock_exec.side_effect = RuntimeError("Connection lost")
            with pytest.raises(RuntimeError):
                await executor.execute("SELECT 1", readonly=True)
            mock_pool.mark_invalid.assert_called_once()

    async def test_execute_with_params_renders_query_before_run(self) -> None:
        """When params are given, query is rendered (placeholders replaced) before execution."""
        mock_pool = self._mock_pool_for_execute()
        executor = SqlExecutor(conn=mock_pool)
        with patch.object(executor, "_execute_with_connection", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = []
            await executor.execute("SELECT * FROM t WHERE id = {}", [10])
            call_args = mock_exec.call_args
            assert "10" in str(call_args[0][1])
            assert "{}" not in str(call_args[0][1])
