"""Проверка числа соединений и соединений idle-in-transaction в pg_stat_activity."""

from postgres_fastmcp.services.health.base import BaseHealthCalc
from postgres_fastmcp.sql.ports import QueryExecutorPort


QUERY_TOTAL_CONNECTIONS = """
    SELECT COUNT(*) as count
    FROM pg_stat_activity
"""

QUERY_IDLE_IN_TRANSACTION = """
    SELECT COUNT(*) as count
    FROM pg_stat_activity
    WHERE state = 'idle in transaction'
"""

QUERY_MAX_CONNECTIONS = """
    SELECT current_setting('max_connections')::int AS max_connections
"""

# Доля от server max_connections, при превышении которой соединения считаются на пределе.
CONNECTION_WARNING_FRACTION = 0.9


class ConnectionHealthCalc(BaseHealthCalc):
    """Калькулятор для проверок состояния соединений базы данных."""

    def __init__(
        self,
        sql_driver: QueryExecutorPort,
        max_total_connections: int = 500,
        max_idle_connections: int = 100,
    ) -> None:
        super().__init__(sql_driver)
        self.max_total_connections = max_total_connections
        self.max_idle_connections = max_idle_connections

    async def connection_health_check(self) -> str:
        """Run all connection health checks and return combined results.

        The total-connection threshold is derived from the server's actual
        ``max_connections`` (warn at 90%), falling back to the configured
        ``max_total_connections`` cap when that value cannot be read.

        Returns:
            String describing the overall connection health status.
        """
        total = await self._get_total_connections()
        idle = await self._get_idle_connections()
        total_limit = await self._total_connection_limit()

        if total > total_limit:
            return f"High number of connections: {total} (limit {total_limit})"
        if idle > self.max_idle_connections:
            return f"High number of connections idle in transaction: {idle}"
        return f"Connections healthy: {total} total, {idle} idle"

    async def _total_connection_limit(self) -> int:
        """Порог общего числа соединений: 90% от server max_connections или заданный fallback."""
        max_conn = await self._get_max_connections()
        if max_conn is None:
            return self.max_total_connections
        return min(self.max_total_connections, int(max_conn * CONNECTION_WARNING_FRACTION))

    async def _get_max_connections(self) -> int | None:
        """Значение server max_connections (None, если недоступно)."""
        rows = await self._rows(QUERY_MAX_CONNECTIONS)
        return int(rows[0]["max_connections"]) if rows else None

    async def _get_total_connections(self) -> int:
        """Total number of database connections."""
        rows = await self._rows(QUERY_TOTAL_CONNECTIONS)
        return rows[0]["count"] if rows else 0

    async def _get_idle_connections(self) -> int:
        """Number of connections in 'idle in transaction' state."""
        rows = await self._rows(QUERY_IDLE_IN_TRANSACTION)
        return rows[0]["count"] if rows else 0
