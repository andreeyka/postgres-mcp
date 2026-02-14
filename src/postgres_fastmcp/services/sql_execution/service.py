"""SQL execution service (used only by sql_execution module)."""

from typing import Any

from postgres_fastmcp.common.errors import SqlExecutionError
from postgres_fastmcp.common.utils import decode_bytes_to_utf8
from postgres_fastmcp.services.db_access_service import DbAccessService


class SqlExecutionService:
    """Service for executing SQL queries."""

    def __init__(self, db: DbAccessService) -> None:
        """Initialize with database access service."""
        self.db = db

    async def execute_sql(self, sql: str = "all") -> list[dict[str, Any]]:
        """Execute a SQL query against the database.

        Raises:
            SqlExecutionError: No results or driver error.
        """
        sql_driver = self.db.sql_driver
        rows = await sql_driver.execute(sql, params=None, readonly=True)
        if rows is None:
            msg = "No results"
            raise SqlExecutionError(msg)
        return [decode_bytes_to_utf8(r.cells) for r in rows]
