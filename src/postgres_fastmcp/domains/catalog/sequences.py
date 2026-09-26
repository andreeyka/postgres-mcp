"""Последовательности: список и детали (используется только модулем objects)."""

from typing import Any, cast

from postgres_fastmcp.domains.db_access import DbAccessPort
from postgres_fastmcp.postgres.catalog import QUERY_GET_SEQUENCE_DETAILS, QUERY_LIST_SEQUENCES
from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.shared.utils import decode_bytes_to_utf8


class SequencesService:
    """Сервис списка последовательностей и получения их деталей."""

    def __init__(self, db: DbAccessPort) -> None:
        """Инициализация с сервисом доступа к БД.

        Args:
            db: Сервис доступа к БД.
        """
        self.db = db

    async def list_sequences(self, schema_name: str) -> list[dict[str, Any]]:
        """Список последовательностей в схеме.

        Args:
            schema_name: Имя схемы.

        Returns:
            Список словарей с полями schema, name, data_type.
        """
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
        if self.db.access_mode == AccessMode.BASIC and self.db.table_prefix:
            prefix = self.db.table_prefix.lower()
            objects = [o for o in objects if o["name"].lower().startswith(prefix)]
        return objects

    async def get_details(self, schema_name: str, object_name: str) -> dict[str, Any]:
        """Получить детали последовательности.

        Args:
            schema_name: Имя схемы.
            object_name: Имя последовательности.

        Returns:
            Словарь с полями schema, name, data_type, start_value, increment.
        """
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
