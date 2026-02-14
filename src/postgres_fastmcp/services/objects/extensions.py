"""Extensions — listing and details (used only by objects module)."""

from typing import Any, cast

from postgres_fastmcp.common.utils import decode_bytes_to_utf8
from postgres_fastmcp.services.db_access_service import DbAccessService
from postgres_fastmcp.sql.catalog.queries import QUERY_GET_EXTENSION_DETAILS, QUERY_LIST_EXTENSIONS


class ExtensionsService:
    """Service for listing extensions and getting extension details."""

    def __init__(self, db: DbAccessService) -> None:
        self.db = db

    async def list_extensions(self) -> list[dict[str, Any]]:
        """List all extensions."""
        sql_driver = self.db.sql_driver
        rows = await sql_driver.execute(QUERY_LIST_EXTENSIONS, params=None, readonly=True)
        return (
            [
                {
                    "name": decode_bytes_to_utf8(row.cells["extname"]),
                    "version": decode_bytes_to_utf8(row.cells["extversion"]),
                    "relocatable": decode_bytes_to_utf8(row.cells["extrelocatable"]),
                }
                for row in rows
            ]
            if rows
            else []
        )

    async def get_details(self, object_name: str) -> dict[str, Any]:
        """Get extension details."""
        sql_driver = self.db.sql_driver
        rows = await sql_driver.execute(QUERY_GET_EXTENSION_DETAILS, params=[object_name], readonly=True)
        if rows and rows[0]:
            row = rows[0]
            return {
                "name": cast("str", decode_bytes_to_utf8(row.cells["extname"])),
                "version": cast("str", decode_bytes_to_utf8(row.cells["extversion"])),
                "relocatable": decode_bytes_to_utf8(row.cells["extrelocatable"]),
            }
        return {}
