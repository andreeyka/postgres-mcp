"""Сервис списка схем (используется только модулем schema)."""

from typing import Any

from postgres_fastmcp.common.utils import decode_bytes_to_utf8
from postgres_fastmcp.enums import AccessMode
from postgres_fastmcp.services.db_access_service import DbAccessService
from postgres_fastmcp.sql.catalog.queries import QUERY_LIST_SCHEMAS


class SchemaService:
    """Сервис для получения списка схем базы данных."""

    def __init__(self, db: DbAccessService) -> None:
        """Инициализация с сервисом доступа к БД.

        Args:
            db: Сервис доступа к БД.
        """
        self.db = db

    async def list_schemas(self) -> list[dict[str, Any]]:
        """Список всех схем в базе данных.

        Returns:
            Список словарей с информацией о схемах (schema_name, schema_owner и т.д.).
        """
        if self.db.access_mode == AccessMode.BASIC:
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
