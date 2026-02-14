from dataclasses import dataclass

from postgres_fastmcp.sql.driver.base import SqlExecutor
from postgres_fastmcp.sql.security.driver import SafeSqlExecutor


@dataclass
class ConnectionHealthMetrics:
    """Metrics for database connection health check.

    Attributes:
        total_connections: Current total number of connections.
        idle_connections: Current number of idle connections.
        max_total_connections: Maximum allowed total connections.
        max_idle_connections: Maximum allowed idle connections.
        is_total_connections_healthy: Whether total connections are within limits.
        is_idle_connections_healthy: Whether idle connections are within limits.
    """

    total_connections: int
    idle_connections: int
    max_total_connections: int
    max_idle_connections: int
    is_total_connections_healthy: bool
    is_idle_connections_healthy: bool

    @property
    def is_healthy(self) -> bool:
        """Check if all connection metrics are healthy.

        Returns:
            True if both total and idle connections are healthy.
        """
        return self.is_total_connections_healthy and self.is_idle_connections_healthy


class ConnectionHealthCalc:
    """Calculator for database connection health checks."""

    def __init__(
        self,
        sql_driver: SqlExecutor | SafeSqlExecutor,
        max_total_connections: int = 500,
        max_idle_connections: int = 100,
    ) -> None:
        self.sql_driver = sql_driver
        self.max_total_connections = max_total_connections
        self.max_idle_connections = max_idle_connections

    async def total_connections_check(self) -> str:
        """Check if total number of connections is within healthy limits.

        Returns:
            String describing the total connections status.
        """
        total = await self._get_total_connections()

        if total <= self.max_total_connections:
            return f"Total connections healthy: {total}"
        return f"High number of connections: {total} (max: {self.max_total_connections})"

    async def idle_connections_check(self) -> str:
        """Check if number of idle connections is within healthy limits.

        Returns:
            String describing the idle connections status.
        """
        idle = await self._get_idle_connections()

        if idle <= self.max_idle_connections:
            return f"Idle connections healthy: {idle}"
        return f"High number of idle connections: {idle} (max: {self.max_idle_connections})"

    async def connection_health_check(self) -> str:
        """Run all connection health checks and return combined results.

        Returns:
            String describing the overall connection health status.
        """
        total = await self._get_total_connections()
        idle = await self._get_idle_connections()

        if total > self.max_total_connections:
            return f"High number of connections: {total}"
        if idle > self.max_idle_connections:
            return f"High number of connections idle in transaction: {idle}"
        return f"Connections healthy: {total} total, {idle} idle"

    async def _get_total_connections(self) -> int:
        """Get the total number of database connections.

        Returns:
            Total number of active database connections.
        """
        result = await self.sql_driver.execute(
            """
            SELECT COUNT(*) as count
            FROM pg_stat_activity
        """,
            params=None,
            readonly=True,
        )
        result_list = [dict(x.cells) for x in result] if result else []
        return result_list[0]["count"] if result_list else 0

    async def _get_idle_connections(self) -> int:
        """Get the number of connections that are idle in transaction.

        Returns:
            Number of connections in 'idle in transaction' state.
        """
        result = await self.sql_driver.execute(
            """
            SELECT COUNT(*) as count
            FROM pg_stat_activity
            WHERE state = 'idle in transaction'
        """,
            params=None,
            readonly=True,
        )
        result_list = [dict(x.cells) for x in result] if result else []
        return result_list[0]["count"] if result_list else 0
