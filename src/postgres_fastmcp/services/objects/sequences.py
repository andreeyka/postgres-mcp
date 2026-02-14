"""Sequences — listing and details (used only by objects module)."""

from typing import Any, cast

from postgres_fastmcp.common.utils import decode_bytes_to_utf8
from postgres_fastmcp.enums import UserRole
from postgres_fastmcp.services.db_access_service import DbAccessService
from postgres_fastmcp.sql.catalog.queries import QUERY_GET_SEQUENCE_DETAILS, QUERY_LIST_SEQUENCES


class SequencesService:
    """Service for listing sequences and getting sequence details."""

    def __init__(self, db: DbAccessService) -> None:
        self.db = db

    async def list_sequences(self, schema_name: str) -> list[dict[str, Any]]:
        """List sequences in a schema."""
        sql_driver = self.db.sql_driver
        rows = await sql_driver.execute(QUERY_LIST_SEQUENCES, params=[schema_name], readonly=True)
        objects = (
            [
                {
                    "schema": decode_bytes_to_utf8(row.cells["sequence_schema"]),
                    "name": decode_bytes_to_utf8(row.cells["sequence_name"]),
                    "data_type": decode_bytes_to_utf8(row.cells["data_type"]),
                }
                for row in rows
            ]
            if rows
            else []
        )
        if self.db.role == UserRole.USER and self.db.config.table_prefix:
            prefix = self.db.config.table_prefix.lower()
            objects = [o for o in objects if o["name"].lower().startswith(prefix)]
        return objects

    async def get_details(self, schema_name: str, object_name: str) -> dict[str, Any]:
        """Get sequence details."""
        sql_driver = self.db.sql_driver
        rows = await sql_driver.execute(
            QUERY_GET_SEQUENCE_DETAILS,
            params=[schema_name, object_name],
            readonly=True,
        )
        if rows and rows[0]:
            row = rows[0]
            return {
                "schema": cast("str", decode_bytes_to_utf8(row.cells["sequence_schema"])),
                "name": cast("str", decode_bytes_to_utf8(row.cells["sequence_name"])),
                "data_type": cast("str", decode_bytes_to_utf8(row.cells["data_type"])),
                "start_value": decode_bytes_to_utf8(row.cells["start_value"]),
                "increment": decode_bytes_to_utf8(row.cells["increment"]),
            }
        return {}
