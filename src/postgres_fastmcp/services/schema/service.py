"""Schema listing service (used only by schema module)."""

from typing import Any

from postgres_fastmcp.common.utils import decode_bytes_to_utf8
from postgres_fastmcp.enums import UserRole
from postgres_fastmcp.services.db_access_service import DbAccessService
from postgres_fastmcp.sql.catalog.queries import QUERY_LIST_SCHEMAS


class SchemaService:
    """Service for listing database schemas."""

    def __init__(self, db: DbAccessService) -> None:
        """Initialize with database access service."""
        self.db = db

    async def list_schemas(self) -> list[dict[str, Any]]:
        """List all schemas in the database."""
        if self.db.role == UserRole.USER:
            return [
                {
                    "schema_name": "public",
                    "schema_owner": "postgres",
                    "schema_type": "User Schema",
                }
            ]

        sql_driver = self.db.sql_driver
        rows = await sql_driver.execute(QUERY_LIST_SCHEMAS, params=None, readonly=True)
        return [decode_bytes_to_utf8(row.cells) for row in rows] if rows else []
