# mypy: ignore-errors
"""Unit tests for SqlExecutor."""

from typing import Self
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from psycopg import AsyncConnection, InterfaceError, OperationalError
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
from postgres_fastmcp.postgres.models import RowResult, StatementResult
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
            mock_exec.return_value = StatementResult(
                rows=[RowResult(cells={"x": 1})], status="SELECT 1", affected_rows=1
            )
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
            mock_exec.return_value = StatementResult(rows=[], status="SELECT 0", affected_rows=0)
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


class _FakeCursor:
    """Курсор psycopg в миниатюре: результаты строки запроса по очереди, как после nextset().

    Как у psycopg 3.3: statusmessage и rowcount относятся к текущему результату,
    а COMMIT/ROLLBACK на том же курсоре заменяет их своими.
    """

    def __init__(self, *results: tuple[str, int, list[dict] | None]) -> None:
        self._pending = list(results)
        self._current: tuple[str, int, list[dict] | None] = ("", -1, None)
        self.executed: list[str] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, query: str, params: object = None) -> None:
        self.executed.append(query)
        if query.startswith(("BEGIN", "COMMIT", "ROLLBACK")):
            self._current = (query.split(maxsplit=1)[0], -1, None)
        else:
            self._current = self._pending.pop(0)

    def nextset(self) -> bool | None:
        if not self._pending:
            return None
        self._current = self._pending.pop(0)
        return True

    @property
    def statusmessage(self) -> str:
        return self._current[0]

    @property
    def rowcount(self) -> int:
        return self._current[1]

    @property
    def description(self) -> list[object] | None:
        return None if self._current[2] is None else [object()]

    async def fetchall(self) -> list[dict]:
        return self._current[2] or []


def _executor_on(cursor: _FakeCursor) -> SqlExecutor:
    """SqlExecutor на одиночном подключении, которое отдаёт cursor."""
    conn = MagicMock(spec=AsyncConnection)
    conn.cursor.return_value = cursor
    return SqlExecutor(conn=conn)


class TestSqlExecutorExecuteStatement:
    """execute_statement: тег команды последнего оператора, снятый до COMMIT/ROLLBACK."""

    async def test_status_of_last_statement_after_set_local_prefix(self) -> None:
        """Префикс SET LOCAL даёт свои результаты; тег и счётчик — от оператора пользователя."""
        cursor = _FakeCursor(("SET", -1, None), ("SET", -1, None), ("UPDATE 3", 3, None))
        executor = _executor_on(cursor)

        result = await executor.execute_statement(
            "SET LOCAL statement_timeout = 1000; SET LOCAL search_path = public; UPDATE t SET v = 1",
            readonly=False,
        )

        assert result == StatementResult(rows=None, status="UPDATE 3", affected_rows=3)
        assert cursor.executed[-1] == "COMMIT"

    async def test_ddl_has_status_without_count(self) -> None:
        """У DDL в теге нет числа: psycopg отдаёт rowcount -1, affected_rows — None."""
        executor = _executor_on(_FakeCursor(("CREATE TABLE", -1, None)))

        result = await executor.execute_statement("CREATE TABLE t ()", readonly=False)

        assert result == StatementResult(rows=None, status="CREATE TABLE", affected_rows=None)

    async def test_rows_and_status_for_select(self) -> None:
        """Оператор с результирующим набором: строки, тег SELECT и их число."""
        cursor = _FakeCursor(("SELECT 1", 1, [{"a": 1}]))
        executor = _executor_on(cursor)

        result = await executor.execute_statement("SELECT 1 AS a")

        assert result == StatementResult(rows=[RowResult(cells={"a": 1})], status="SELECT 1", affected_rows=1)
        assert cursor.executed == [
            "BEGIN TRANSACTION READ ONLY; SET LOCAL standard_conforming_strings = on",
            "SELECT 1 AS a",
            "ROLLBACK",
        ]

    async def test_execute_returns_rows_of_execute_statement(self) -> None:
        """Метод execute остаётся прежним: строки или None, без тега."""
        executor = _executor_on(_FakeCursor(("INSERT 0 2", 2, None)))

        assert await executor.execute("INSERT INTO t VALUES (1), (2)", readonly=False) is None

    async def test_begin_read_only_pins_standard_conforming_strings(self) -> None:
        """readonly=True: первая команда курсора — BEGIN READ ONLY со SET LOCAL в одной строке."""
        cursor = _FakeCursor(("SELECT 1", 1, [{"a": 1}]))
        executor = _executor_on(cursor)

        await executor.execute_statement("SELECT 1 AS a", readonly=True)

        assert cursor.executed[0] == "BEGIN TRANSACTION READ ONLY; SET LOCAL standard_conforming_strings = on"
        assert cursor.executed[1] == "SELECT 1 AS a"

    async def test_begin_read_write_pins_standard_conforming_strings(self) -> None:
        """readonly=False: первая команда курсора — BEGIN со SET LOCAL в одной строке."""
        cursor = _FakeCursor(("UPDATE 1", 1, None))
        executor = _executor_on(cursor)

        await executor.execute_statement("UPDATE t SET v = 1", readonly=False)

        assert cursor.executed[0] == "BEGIN; SET LOCAL standard_conforming_strings = on"
        assert cursor.executed[1] == "UPDATE t SET v = 1"
        assert cursor.executed[-1] == "COMMIT"


def _pooled_executor(cursor: _FakeCursor) -> tuple[SqlExecutor, MagicMock, MagicMock]:
    """SqlExecutor на пуле (DbConnPool) с одним соединением, которое отдаёт cursor."""
    connection = MagicMock()
    connection.set_autocommit = AsyncMock()
    connection.cursor.return_value = cursor
    checkout = MagicMock()
    checkout.__aenter__ = AsyncMock(return_value=connection)
    checkout.__aexit__ = AsyncMock(return_value=None)
    inner = MagicMock()
    inner.connection.return_value = checkout
    pool = MagicMock(spec=DbConnPool)
    pool.pool_connect = AsyncMock(return_value=inner)
    return SqlExecutor(conn=pool), pool, connection


class TestSqlExecutorMarksHypopgConnections:
    """Соединение с гипотетическими индексами помечается для сброса при возврате в пул."""

    async def test_hypothetical_index_marks_the_pooled_connection(self) -> None:
        cursor = _FakeCursor(
            ("SELECT 1", 1, [{"hypopg_reset": None}]),
            ("SELECT 1", 1, [{"hypopg_create_index": 1}]),
            ("EXPLAIN", -1, [{"QUERY PLAN": []}]),
        )
        executor, pool, connection = _pooled_executor(cursor)

        await executor.execute(
            "SELECT hypopg_reset();SELECT HYPOPG_CREATE_INDEX('CREATE INDEX ON t (a)');EXPLAIN (FORMAT JSON) SELECT 1"
        )

        pool.mark_hypopg_used.assert_called_once_with(connection)

    async def test_plain_query_does_not_mark(self) -> None:
        executor, pool, _ = _pooled_executor(_FakeCursor(("SELECT 1", 1, [{"a": 1}])))

        await executor.execute("SELECT 1 AS a")

        pool.mark_hypopg_used.assert_not_called()
        pool.mark_hypopg_hidden.assert_not_called()

    async def test_connection_is_marked_even_if_the_statement_fails(self) -> None:
        """Индекс мог создаться до ошибки в том же пакете: пометка — до выполнения."""
        executor, pool, connection = _pooled_executor(_FakeCursor())

        with pytest.raises(IndexError):
            await executor.execute("SELECT hypopg_create_index('CREATE INDEX ON t (a)')")

        pool.mark_hypopg_used.assert_called_once_with(connection)

    async def test_hide_index_marks_the_connection_as_hidden(self) -> None:
        executor, pool, connection = _pooled_executor(_FakeCursor(("SELECT 1", 1, [{"hypopg_hide_index": True}])))

        await executor.execute("SELECT HYPOPG_HIDE_INDEX(16384)")

        pool.mark_hypopg_hidden.assert_called_once_with(connection)
        pool.mark_hypopg_used.assert_not_called()

    async def test_hidden_mark_is_set_even_if_the_statement_fails(self) -> None:
        executor, pool, connection = _pooled_executor(_FakeCursor())

        with pytest.raises(IndexError):
            await executor.execute("SELECT hypopg_hide_index(16384)")

        pool.mark_hypopg_hidden.assert_called_once_with(connection)
