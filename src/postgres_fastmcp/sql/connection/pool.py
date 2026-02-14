"""Database connection pool (lifecycle only)."""

import logging

from psycopg_pool import AsyncConnectionPool

from postgres_fastmcp.common.utils import obfuscate_password
from postgres_fastmcp.sql.driver.errors import ConnectionFailedError


logger = logging.getLogger(__name__)


class DbConnPool:
    """Database connection manager using psycopg's connection pool."""

    def __init__(
        self,
        connection_url: str | None = None,
        min_size: int = 1,
        max_size: int = 5,
    ) -> None:
        """Initialize database connection pool.

        Args:
            connection_url: Database connection URL.
            min_size: Minimum number of connections in the pool.
            max_size: Maximum number of connections in the pool.
        """
        self.connection_url = connection_url
        self.min_size = min_size
        self.max_size = max_size
        self.pool: AsyncConnectionPool | None = None
        self._is_valid = False
        self._last_error: str | None = None

    async def pool_connect(self, connection_url: str | None = None) -> AsyncConnectionPool:
        """Initialize connection pool; returns existing pool if already valid."""
        if self.pool and self._is_valid:
            return self.pool

        url = connection_url or self.connection_url
        self.connection_url = url
        if not url:
            self._is_valid = False
            self._last_error = "Database connection URL not provided"
            raise ValueError(self._last_error)

        await self.close()

        try:
            self.pool = AsyncConnectionPool(
                conninfo=url,
                min_size=self.min_size,
                max_size=self.max_size,
                open=False,
            )
            await self.pool.open()
            async with self.pool.connection() as conn, conn.cursor() as cursor:
                await cursor.execute("SELECT 1")
            self._is_valid = True
            self._last_error = None
        except Exception as e:
            self._is_valid = False
            self._last_error = str(e)
            await self.close()
            obfuscated_error = obfuscate_password(str(e))
            raise ConnectionFailedError(obfuscated_error) from e
        else:
            return self.pool

    async def close(self) -> None:
        """Close the connection pool."""
        if self.pool:
            try:
                await self.pool.close()
            except Exception as e:
                logger.warning("Error closing connection pool: %s", e)
            finally:
                self.pool = None
                self._is_valid = False

    @property
    def is_valid(self) -> bool:
        """Whether the connection pool is valid."""
        return self._is_valid

    @property
    def last_error(self) -> str | None:
        """Last error message if any."""
        return self._last_error

    def mark_invalid(self, error: str | None = None) -> None:
        """Mark pool as invalid (e.g. after connection failure).

        Args:
            error: Optional error message.
        """
        self._is_valid = False
        self._last_error = error
