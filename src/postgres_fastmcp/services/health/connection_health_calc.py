"""Проверка числа соединений и соединений idle-in-transaction в pg_stat_activity."""

from postgres_fastmcp.services.health.base import BaseHealthCalc, HealthSqlDriver


QUERY_TOTAL_CONNECTIONS = """
    SELECT COUNT(*) as count
    FROM pg_stat_activity
"""

QUERY_IDLE_IN_TRANSACTION = """
    SELECT COUNT(*) as count
    FROM pg_stat_activity
    WHERE state = 'idle in transaction'
"""


class ConnectionHealthCalc(BaseHealthCalc):
    """Калькулятор для проверок состояния соединений базы данных."""

    def __init__(
        self,
        sql_driver: HealthSqlDriver,
        max_total_connections: int = 500,
        max_idle_connections: int = 100,
    ) -> None:
        super().__init__(sql_driver)
        self.max_total_connections = max_total_connections
        self.max_idle_connections = max_idle_connections

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
        """Total number of database connections."""
        rows = await self._rows(QUERY_TOTAL_CONNECTIONS)
        return rows[0]["count"] if rows else 0

    async def _get_idle_connections(self) -> int:
        """Number of connections in 'idle in transaction' state."""
        rows = await self._rows(QUERY_IDLE_IN_TRANSACTION)
        return rows[0]["count"] if rows else 0
