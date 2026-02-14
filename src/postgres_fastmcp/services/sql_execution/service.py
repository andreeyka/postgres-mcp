"""Сервис выполнения SQL (используется только модулем sql_execution)."""

from typing import Any

from postgres_fastmcp.common.errors import SqlExecutionError
from postgres_fastmcp.common.utils import decode_bytes_to_utf8
from postgres_fastmcp.services.db_access_service import DbAccessService


class SqlExecutionService:
    """Сервис для выполнения SQL запросов."""

    def __init__(self, db: DbAccessService) -> None:
        """Инициализация сервиса с подключением к базе данных."""
        self.db = db

    async def execute_sql(self, sql: str = "all") -> list[dict[str, Any]]:
        """Выполнить SQL запрос к базе данных.

        Args:
            sql: SQL запрос для выполнения (по умолчанию "all").

        Returns:
            Список результатов запроса в виде списка словарей.

        Raises:
            SqlExecutionError: Если нет результатов или ошибка драйвера.
        """
        sql_driver = self.db.sql_driver
        rows = await sql_driver.execute(sql, params=None, readonly=True)
        if rows is None:
            msg = "No results"
            raise SqlExecutionError(msg)
        return [decode_bytes_to_utf8(r.cells) for r in rows]
