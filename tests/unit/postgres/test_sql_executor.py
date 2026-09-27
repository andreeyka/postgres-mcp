# mypy: ignore-errors
"""Unit tests for SqlExecutor."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from psycopg import InterfaceError, OperationalError
from psycopg.errors import (
    AdminShutdown,
    ConnectionFailure,
    DatabaseDropped,
    DeadlockDetected,
    IdleSessionTimeout,
    LockNotAvailable,
    ObjectNotInPrerequisiteState,
    QueryCanceled,
    SerializationFailure,
    UndefinedTable,
    error_from_result,
)
from psycopg_pool import PoolTimeout

from postgres_fastmcp.postgres.connection import DbConnPool
from postgres_fastmcp.postgres.driver import SqlExecutor
from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.shared.errors import ConnectionNotEstablishedError


class _NoSqlstateResult:
    """Стаб PGresult без SQLSTATE — как при libpq FATAL_ERROR без кода ошибки."""

    def error_field(self, fieldcode: int) -> bytes | None:
        return None

    def get_error_message(self, encoding: str = "utf-8") -> str:
        return "server closed the connection unexpectedly"


def _database_error_without_sqlstate() -> Exception:
    """Собрать исключение так же, как это делает psycopg на пути FATAL_ERROR без SQLSTATE."""
    return error_from_result(_NoSqlstateResult())


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

    @pytest.mark.parametrize(
        "error",
        [
            UndefinedTable('relation "t" does not exist'),
            QueryCanceled("canceling statement due to statement timeout"),
            ObjectNotInPrerequisiteState('pg_stat_statements must be loaded via "shared_preload_libraries"'),
            DeadlockDetected("deadlock detected"),
            SerializationFailure("could not serialize access due to concurrent update"),
            LockNotAvailable('could not obtain lock on relation "t"'),
        ],
        ids=[
            "programming-error",
            "statement-timeout",
            "object-not-in-prerequisite-state",
            "deadlock",
            "serialization-failure",
            "lock-not-available",
        ],
    )
    async def test_sql_errors_do_not_invalidate_pool(self, error: Exception) -> None:
        """A failed statement is the client's problem, not the pool's: the pool stays valid."""
        mock_pool = self._mock_pool_for_execute()
        executor = SqlExecutor(conn=mock_pool)
        with patch.object(executor, "_execute_with_connection", new_callable=AsyncMock) as mock_exec:
            mock_exec.side_effect = error
            with pytest.raises(type(error)):
                await executor.execute("SELECT 1", readonly=True)
            mock_pool.mark_invalid.assert_not_called()

    @pytest.mark.parametrize(
        "error",
        [
            AdminShutdown("terminating connection due to administrator command"),
            ConnectionFailure("connection failure"),
            InterfaceError("the connection is closed"),
            OperationalError("server closed the connection unexpectedly"),
            PoolTimeout("couldn't get a connection after 30.00 sec"),
            _database_error_without_sqlstate(),
            DatabaseDropped("database is being dropped"),
            IdleSessionTimeout("terminating connection due to idle-session timeout"),
        ],
        ids=[
            "admin-shutdown",
            "class-08",
            "interface-error",
            "operational-without-sqlstate",
            "pool-timeout",
            "database-error-without-sqlstate-from-result",
            "database-dropped-57p04",
            "idle-session-timeout-57p05",
        ],
    )
    async def test_connection_errors_invalidate_pool(self, error: Exception) -> None:
        """Real connection failures invalidate the pool."""
        mock_pool = self._mock_pool_for_execute()
        executor = SqlExecutor(conn=mock_pool)
        with patch.object(executor, "_execute_with_connection", new_callable=AsyncMock) as mock_exec:
            mock_exec.side_effect = error
            with pytest.raises(type(error)):
                await executor.execute("SELECT 1", readonly=True)
            mock_pool.mark_invalid.assert_called_once()
