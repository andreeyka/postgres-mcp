"""SQL executor: run queries and manage transaction policy."""

import logging
from typing import Any, LiteralString, NoReturn

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.sql import SQL, Composable, Literal

from postgres_fastmcp.sql.connection.pool import DbConnPool
from postgres_fastmcp.sql.models.row_result import RowResult


logger = logging.getLogger(__name__)


class SqlExecutor:
    """Executes SQL via a connection pool or direct connection. Manages transactions."""

    def __init__(
        self,
        conn: DbConnPool | AsyncConnection | None = None,
        engine_url: str | None = None,
    ) -> None:
        """Initialize with pool, connection, or URL.

        Args:
            conn: Connection pool or single async connection.
            engine_url: Connection URL; pool will be created on first use.
        """
        self.conn: DbConnPool | AsyncConnection | None = None
        if conn is not None:
            self.conn = conn
            self._is_pool = isinstance(conn, DbConnPool)
        elif engine_url:
            self.engine_url = engine_url
            self._is_pool = False
        else:
            raise ValueError("Either conn or engine_url must be provided")

    def _ensure_connected(self) -> None:
        """Ensure conn is set; create pool from engine_url if needed."""
        if self.conn is not None:
            return
        if getattr(self, "engine_url", None):
            self.conn = DbConnPool(self.engine_url)
            self._is_pool = True
            return
        raise ValueError("Connection not established. Either conn or engine_url must be provided")

    def render(self, query: str, params: list[Any]) -> str:
        """Render parameterized query ({} placeholders) to a single string."""
        composables = [p if isinstance(p, Composable) else Literal(p) for p in params]
        return SQL(query).format(*composables).as_string()

    async def execute(
        self,
        query: str | LiteralString,
        params: list[Any] | None = None,
        *,
        readonly: bool = True,
    ) -> list[RowResult] | None:
        """Execute query and return rows, or None for no-result statements.

        Args:
            query: SQL to execute (use {} for placeholders if params given).
            params: Optional parameters; if set, query is rendered then executed.
            readonly: If True, use read-only transaction; else read-write.

        Returns:
            List of RowResult or None for DDL/command-only.
        """
        if params:
            query = self.render(query, params)
            params = None

        def _fail() -> NoReturn:
            raise ValueError("Connection not established")

        try:
            self._ensure_connected()
            if self.conn is None:
                _fail()
            if self._is_pool and isinstance(self.conn, DbConnPool):
                pool = await self.conn.pool_connect()
                async with pool.connection() as connection:
                    await connection.set_autocommit(True)
                    return await self._execute_with_connection(connection, query, params, readonly=readonly)
            if isinstance(self.conn, AsyncConnection):
                if hasattr(self.conn, "set_autocommit"):
                    await self.conn.set_autocommit(True)
                return await self._execute_with_connection(self.conn, query, params, readonly=readonly)
            _fail()
        except Exception as e:
            if self.conn and self._is_pool and isinstance(self.conn, DbConnPool):
                self.conn.mark_invalid(str(e))
            elif self.conn and not self._is_pool:
                self.conn = None
            raise

    async def _execute_with_connection(
        self,
        connection: AsyncConnection[Any],
        query: str | LiteralString,
        params: list[Any] | None,
        *,
        readonly: bool,
    ) -> list[RowResult] | None:
        """Run query on the given connection with explicit transaction."""
        async with connection.cursor(row_factory=dict_row) as cursor:
            if readonly:
                await cursor.execute("BEGIN TRANSACTION READ ONLY")
            else:
                await cursor.execute("BEGIN")
            try:
                if params:
                    await cursor.execute(query, params)
                else:
                    await cursor.execute(query)
                while cursor.nextset():
                    pass
                if cursor.description is None:
                    if readonly:
                        await cursor.execute("ROLLBACK")
                    else:
                        await cursor.execute("COMMIT")
                    return None
                rows = await cursor.fetchall()
                if readonly:
                    await cursor.execute("ROLLBACK")
                else:
                    await cursor.execute("COMMIT")
                return [RowResult(cells=dict(row)) for row in rows]
            except Exception:
                try:
                    await cursor.execute("ROLLBACK")
                except Exception as rollback_error:
                    logger.error("Error rolling back transaction: %s", rollback_error)
                raise
