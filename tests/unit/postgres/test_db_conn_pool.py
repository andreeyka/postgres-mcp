# mypy: ignore-errors
"""Unit tests for DbConnPool."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from psycopg.errors import QueryCanceled, UndefinedFunction

from postgres_fastmcp.postgres.connection import DbConnPool
from postgres_fastmcp.shared.errors import ConnectionFailedError


def _make_mock_pool() -> MagicMock:
    """Build a mock AsyncConnectionPool that passes open() and connection() validation."""
    mock_cursor = MagicMock()
    mock_cursor.execute = AsyncMock()
    cursor_ctx = MagicMock()
    cursor_ctx.__aenter__ = AsyncMock(return_value=mock_cursor)
    cursor_ctx.__aexit__ = AsyncMock(return_value=None)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = cursor_ctx
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=None)
    mock_pool = MagicMock()
    mock_pool.open = AsyncMock()
    mock_pool.connection.return_value = mock_conn
    mock_pool.close = AsyncMock()
    return mock_pool


class TestDbConnPoolLifecycle:
    """pool_connect, close, is_valid."""

    @patch("postgres_fastmcp.postgres.connection.AsyncConnectionPool")
    async def test_pool_connect_success_returns_pool(
        self,
        mock_pool_cls: MagicMock,
    ) -> None:
        """Successful pool_connect returns the pool and sets is_valid."""
        mock_pool = _make_mock_pool()
        mock_pool_cls.return_value = mock_pool

        pool_mgr = DbConnPool(connection_url="postgresql://localhost/test", min_size=1, max_size=2)
        result = await pool_mgr.pool_connect()
        assert result is mock_pool
        assert pool_mgr.is_valid is True
        assert pool_mgr.last_error is None
        mock_pool.open.assert_called_once()

    @patch("postgres_fastmcp.postgres.connection.AsyncConnectionPool")
    async def test_pool_connect_no_url_raises(
        self,
        mock_pool_cls: MagicMock,
    ) -> None:
        """pool_connect with no URL raises ValueError."""
        pool_mgr = DbConnPool(connection_url=None)
        with pytest.raises(ValueError) as exc_info:
            await pool_mgr.pool_connect()
        assert "URL" in str(exc_info.value)
        mock_pool_cls.assert_not_called()

    @patch("postgres_fastmcp.postgres.connection.AsyncConnectionPool")
    async def test_close_clears_pool_and_valid(
        self,
        mock_pool_cls: MagicMock,
    ) -> None:
        """close() clears pool and sets is_valid False."""
        mock_pool = _make_mock_pool()
        mock_pool_cls.return_value = mock_pool

        pool_mgr = DbConnPool(connection_url="postgresql://localhost/test")
        await pool_mgr.pool_connect()
        assert pool_mgr.pool is not None
        await pool_mgr.close()
        assert pool_mgr.pool is None
        assert pool_mgr.is_valid is False

    @patch("postgres_fastmcp.postgres.connection.AsyncConnectionPool")
    async def test_pool_connect_returns_cached_when_valid(
        self,
        mock_pool_cls: MagicMock,
    ) -> None:
        """When already valid, pool_connect returns existing pool without reconnecting."""
        mock_pool = _make_mock_pool()
        mock_pool_cls.return_value = mock_pool

        pool_mgr = DbConnPool(connection_url="postgresql://localhost/test")
        first = await pool_mgr.pool_connect()
        second = await pool_mgr.pool_connect()
        assert first is second
        assert mock_pool_cls.call_count == 1


class TestDbConnPoolMarkInvalid:
    """mark_invalid behavior."""

    def test_mark_invalid_sets_valid_false(self) -> None:
        """mark_invalid sets _is_valid False and stores error."""
        pool_mgr = DbConnPool(connection_url="postgresql://localhost/test")
        pool_mgr._is_valid = True
        pool_mgr.mark_invalid("Connection lost")
        assert pool_mgr.is_valid is False
        assert pool_mgr.last_error == "Connection lost"

    def test_mark_invalid_without_error(self) -> None:
        """mark_invalid(error=None) still sets valid False."""
        pool_mgr = DbConnPool(connection_url="postgresql://localhost/test")
        pool_mgr._is_valid = True
        pool_mgr.mark_invalid()
        assert pool_mgr.is_valid is False


class TestDbConnPoolConnectFailure:
    """pool_connect when connection fails."""

    @patch("postgres_fastmcp.postgres.connection.AsyncConnectionPool")
    async def test_pool_connect_failure_raises_connection_failed_error(
        self,
        mock_pool_cls: MagicMock,
    ) -> None:
        """When pool.open() or validation fails, ConnectionFailedError is raised."""
        mock_pool = _make_mock_pool()
        mock_pool.open = AsyncMock(side_effect=OSError("Connection refused"))
        mock_pool_cls.return_value = mock_pool

        pool_mgr = DbConnPool(connection_url="postgresql://localhost/test")
        with pytest.raises(ConnectionFailedError):
            await pool_mgr.pool_connect()
        assert pool_mgr.is_valid is False
        assert pool_mgr.last_error is not None


class TestDbConnPoolConcurrentConnect:
    """Concurrent first use (asyncio.gather over one service) must open exactly one pool."""

    @patch("postgres_fastmcp.postgres.connection.AsyncConnectionPool")
    async def test_concurrent_pool_connect_opens_one_pool(
        self,
        mock_pool_cls: MagicMock,
    ) -> None:
        """Three callers racing on a fresh DbConnPool share one pool; none is closed under another."""
        created: list[MagicMock] = []

        def _new_pool(*args: object, **kwargs: object) -> MagicMock:
            pool = _make_mock_pool()

            async def _slow_open() -> None:
                # Уступаем цикл событий, как настоящий open() с сетевым подключением.
                await asyncio.sleep(0.01)

            pool.open = AsyncMock(side_effect=_slow_open)
            created.append(pool)
            return pool

        mock_pool_cls.side_effect = _new_pool

        pool_mgr = DbConnPool(connection_url="postgresql://localhost/test")
        results = await asyncio.gather(*(pool_mgr.pool_connect() for _ in range(3)))

        assert len(created) == 1
        assert all(result is created[0] for result in results)
        created[0].close.assert_not_called()
        assert pool_mgr.is_valid is True


class TestDbConnPoolOpenCancelled:
    """Отменённое открытие не оставляет после себя пул, который продолжает подключаться в фоне."""

    @patch("postgres_fastmcp.postgres.connection.AsyncConnectionPool")
    async def test_cancelled_open_closes_the_pool(self, mock_pool_cls: MagicMock) -> None:
        mock_pool = _make_mock_pool()
        mock_pool.open = AsyncMock(side_effect=asyncio.CancelledError)
        mock_pool_cls.return_value = mock_pool
        pool_mgr = DbConnPool(connection_url="postgresql://localhost/test")

        with pytest.raises(asyncio.CancelledError):
            await pool_mgr.pool_connect()

        assert pool_mgr.pool is None
        assert pool_mgr.is_valid is False
        mock_pool.close.assert_awaited_once()

    @patch("postgres_fastmcp.postgres.connection.AsyncConnectionPool")
    async def test_timeout_during_open_closes_the_pool(self, mock_pool_cls: MagicMock) -> None:
        """Клиентский таймаут отменяет висящий open(): пул закрывается."""
        mock_pool = _make_mock_pool()

        async def _hang() -> None:
            await asyncio.Event().wait()

        mock_pool.open = AsyncMock(side_effect=_hang)
        mock_pool_cls.return_value = mock_pool
        pool_mgr = DbConnPool(connection_url="postgresql://localhost/test")

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(pool_mgr.pool_connect(), timeout=0.01)

        assert pool_mgr.pool is None
        mock_pool.close.assert_awaited_once()


async def _reset_callback(pool_mgr: DbConnPool) -> Callable[[MagicMock], Awaitable[None]]:
    """reset-callback, который DbConnPool передаёт в AsyncConnectionPool."""
    with patch("postgres_fastmcp.postgres.connection.AsyncConnectionPool") as mock_pool_cls:
        mock_pool_cls.return_value = _make_mock_pool()
        await pool_mgr.pool_connect()
    return mock_pool_cls.call_args.kwargs["reset"]


def _returned_connection(*, autocommit: bool = True, closed: bool = False) -> MagicMock:
    connection = MagicMock()
    connection.autocommit = autocommit
    connection.closed = closed
    connection.set_autocommit = AsyncMock()
    connection.execute = AsyncMock()
    return connection


class TestDbConnPoolOptions:
    """max_idle и reset-callback доходят до AsyncConnectionPool."""

    @patch("postgres_fastmcp.postgres.connection.AsyncConnectionPool")
    async def test_max_idle_and_reset_reach_the_pool(self, mock_pool_cls: MagicMock) -> None:
        mock_pool_cls.return_value = _make_mock_pool()
        pool_mgr = DbConnPool(connection_url="postgresql://localhost/test", max_idle=42)

        await pool_mgr.pool_connect()

        kwargs = mock_pool_cls.call_args.kwargs
        assert kwargs["max_idle"] == 42
        assert kwargs["reset"] == pool_mgr._reset_connection
        assert kwargs["kwargs"] == {"prepare_threshold": None}


class TestHypopgReset:
    """Гипотетические индексы живут в сессии: помеченное соединение сбрасывается при возврате в пул."""

    async def test_marked_connection_gets_hypopg_reset_then_discard_all(self) -> None:
        """Помеченное соединение: сперва hypopg_reset(), затем DISCARD ALL; пометка снимается сразу."""
        pool_mgr = DbConnPool(connection_url="postgresql://localhost/test")
        reset = await _reset_callback(pool_mgr)
        connection = _returned_connection()
        pool_mgr.mark_hypopg_used(connection)

        await reset(connection)

        assert [call.args[0] for call in connection.execute.await_args_list] == [
            "SELECT hypopg_reset()",
            "DISCARD ALL",
        ]
        connection.set_autocommit.assert_not_awaited()

        # Пометка снята: второй возврат того же соединения больше не трогает hypopg.
        connection.execute.reset_mock()
        await reset(connection)
        connection.execute.assert_awaited_once_with("DISCARD ALL")

    async def test_unmarked_open_connection_gets_exactly_discard_all(self) -> None:
        pool_mgr = DbConnPool(connection_url="postgresql://localhost/test")
        reset = await _reset_callback(pool_mgr)
        connection = _returned_connection()

        await reset(connection)

        connection.execute.assert_awaited_once_with("DISCARD ALL")
        connection.set_autocommit.assert_not_awaited()

    async def test_reset_switches_to_autocommit_first(self) -> None:
        """Pool требует от reset вернуть соединение в IDLE: hypopg_reset() и DISCARD ALL — вне транзакции."""
        pool_mgr = DbConnPool(connection_url="postgresql://localhost/test")
        reset = await _reset_callback(pool_mgr)
        connection = _returned_connection(autocommit=False)
        pool_mgr.mark_hypopg_used(connection)

        await reset(connection)

        connection.set_autocommit.assert_awaited_once()
        assert connection.set_autocommit.await_args.args == (True,)
        assert [call.args[0] for call in connection.execute.await_args_list] == [
            "SELECT hypopg_reset()",
            "DISCARD ALL",
        ]

    async def test_hypopg_reset_failure_is_logged_and_raised_without_discard_all(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Состояние hypopg неизвестно: ошибка поднимается, DISCARD ALL не выполняется, соединение выбрасывается."""
        pool_mgr = DbConnPool(connection_url="postgresql://localhost/test")
        reset = await _reset_callback(pool_mgr)
        connection = _returned_connection()
        connection.execute = AsyncMock(side_effect=QueryCanceled("canceling statement due to statement timeout"))
        pool_mgr.mark_hypopg_used(connection)

        with caplog.at_level(logging.WARNING, logger="postgres_fastmcp.postgres.connection"):
            with pytest.raises(QueryCanceled):
                await reset(connection)

        assert any("Failed to reset hypothetical indexes" in r.getMessage() for r in caplog.records)
        connection.execute.assert_awaited_once_with("SELECT hypopg_reset()")

    async def test_missing_hypopg_reset_is_raised(self, caplog: pytest.LogCaptureFixture) -> None:
        """UndefinedFunction не значит «сбрасывать нечего»: basic создаёт индексы под SET LOCAL search_path = public,
        а сброс идёт на search_path роли по умолчанию. Соединение с непогашенными индексами пул должен выбросить."""
        pool_mgr = DbConnPool(connection_url="postgresql://localhost/test")
        reset = await _reset_callback(pool_mgr)
        connection = _returned_connection()
        connection.execute = AsyncMock(side_effect=UndefinedFunction("function hypopg_reset() does not exist"))
        pool_mgr.mark_hypopg_used(connection)

        with caplog.at_level(logging.WARNING, logger="postgres_fastmcp.postgres.connection"):
            with pytest.raises(UndefinedFunction):
                await reset(connection)

        assert any("Failed to reset hypothetical indexes" in r.getMessage() for r in caplog.records)
        connection.execute.assert_awaited_once_with("SELECT hypopg_reset()")

    async def test_discard_all_failure_is_logged_and_raised(self, caplog: pytest.LogCaptureFixture) -> None:
        """DISCARD ALL может упасть (например, сеть); соединение с неизвестным состоянием сессии выбрасывается."""
        pool_mgr = DbConnPool(connection_url="postgresql://localhost/test")
        reset = await _reset_callback(pool_mgr)
        connection = _returned_connection()
        connection.execute = AsyncMock(side_effect=QueryCanceled("canceling statement due to statement timeout"))

        with caplog.at_level(logging.WARNING, logger="postgres_fastmcp.postgres.connection"):
            with pytest.raises(QueryCanceled):
                await reset(connection)

        assert any("Failed to discard session state" in r.getMessage() for r in caplog.records)
        connection.execute.assert_awaited_once_with("DISCARD ALL")

    async def test_closed_connection_is_left_alone(self) -> None:
        """psycopg_pool вызывает reset и для соединения, которое само же закрыло (ACTIVE/сбойное при возврате):
        запрос на закрытом соединении ничего не даёт и упал бы с вводящим в заблуждение предупреждением."""
        pool_mgr = DbConnPool(connection_url="postgresql://localhost/test")
        reset = await _reset_callback(pool_mgr)
        connection = _returned_connection(closed=True)
        pool_mgr.mark_hypopg_used(connection)

        await reset(connection)

        connection.set_autocommit.assert_not_awaited()
        connection.execute.assert_not_awaited()
