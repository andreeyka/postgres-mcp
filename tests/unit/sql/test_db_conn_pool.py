# mypy: ignore-errors
"""Unit tests for DbConnPool."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from postgres_fastmcp.sql.connection.pool import DbConnPool
from postgres_fastmcp.sql.driver.errors import ConnectionFailedError


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

    @patch("postgres_fastmcp.sql.connection.pool.AsyncConnectionPool")
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

    @patch("postgres_fastmcp.sql.connection.pool.AsyncConnectionPool")
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

    @patch("postgres_fastmcp.sql.connection.pool.AsyncConnectionPool")
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

    @patch("postgres_fastmcp.sql.connection.pool.AsyncConnectionPool")
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

    @patch("postgres_fastmcp.sql.connection.pool.AsyncConnectionPool")
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
