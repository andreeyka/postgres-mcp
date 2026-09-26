"""Расширения: список и детали (используется только модулем objects)."""

from typing import Any, cast

from postgres_fastmcp.domains.db_access import DbAccessPort
from postgres_fastmcp.postgres.catalog import QUERY_GET_EXTENSION_DETAILS, QUERY_LIST_EXTENSIONS
from postgres_fastmcp.shared.utils import decode_bytes_to_utf8


class ExtensionsService:
    """Сервис списка расширений и получения их деталей."""

    def __init__(self, db: DbAccessPort) -> None:
        """Инициализация с сервисом доступа к БД.

        Args:
            db: Сервис доступа к БД.
        """
        self.db = db

    async def list_extensions(self) -> list[dict[str, Any]]:
        """Список всех расширений.

        Returns:
            Список словарей с полями name, version, relocatable.
        """
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
        """Получить детали расширения.

        Args:
            object_name: Имя расширения.

        Returns:
            Словарь с полями name, version, relocatable.
        """
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
