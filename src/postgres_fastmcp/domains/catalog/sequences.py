"""Последовательности: список и детали (используется только модулем objects)."""

from typing import Any, cast

from postgres_fastmcp.domains.db_access import DbAccessPort
from postgres_fastmcp.postgres.catalog import QUERY_GET_SEQUENCE_DETAILS, QUERY_LIST_SEQUENCES
from postgres_fastmcp.shared.utils import decode_bytes_to_utf8

from .prefix import active_prefix, matches_prefix


class SequencesService:
    """Сервис списка последовательностей и получения их деталей."""

    def __init__(self, db: DbAccessPort) -> None:
        """Инициализация с сервисом доступа к БД.

        Args:
            db: Доступ к БД для текущего запроса (DbAccessPort).
        """
        self.db = db

    async def list_sequences(self, schema_name: str) -> list[dict[str, Any]]:
        """Список последовательностей в схеме.

        Args:
            schema_name: Имя схемы.

        Returns:
            Список словарей с полями schema, name, data_type.
        """
        catalog = self.db.catalog_driver
        rows = await catalog.execute(QUERY_LIST_SEQUENCES, params=[schema_name], readonly=True)
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
        prefix = active_prefix(self.db)
        if prefix:
            objects = [o for o in objects if matches_prefix(o["name"], prefix)]
        return objects

    async def get_details(self, schema_name: str, object_name: str) -> dict[str, Any] | None:
        """Получить детали последовательности.

        Args:
            schema_name: Имя схемы.
            object_name: Имя последовательности.

        Returns:
            Словарь с полями schema, name, data_type, start_value, increment; None, если последовательности нет.
        """
        catalog = self.db.catalog_driver
        rows = await catalog.execute(
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
        return None
