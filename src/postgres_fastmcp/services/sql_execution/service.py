"""Сервис выполнения SQL (используется только модулем sql_execution)."""

from typing import Any

from postgres_fastmcp.common.utils import decode_bytes_to_utf8
from postgres_fastmcp.services.db_access_service import DbAccessService


SUCCESS_NO_ROWS = "Statement executed successfully; no rows were returned."


class SqlExecutionService:
    """Сервис для выполнения SQL запросов."""

    def __init__(self, db: DbAccessService) -> None:
        """Инициализация сервиса с подключением к базе данных."""
        self.db = db

    async def execute_sql(self, sql: str) -> list[dict[str, Any]]:
        """Выполнить SQL запрос к базе данных.

        Режим транзакции (только чтение / чтение-запись) определяется write_mode
        сервера, а не вызывающим кодом: при write_mode=True транзакция открывается
        на запись, поэтому DML/DDL реально применяются. В режиме только чтения
        (write_mode=False) запись блокируется на уровне валидатора и транзакции.

        Операторы без результирующего набора (INSERT/UPDATE/DELETE/DDL без RETURNING)
        считаются успешно выполненными и возвращают статусную строку, а не ошибку.

        Args:
            sql: SQL запрос для выполнения.

        Returns:
            Список строк результата (list[dict]) либо список с единственным статусом
            для операторов без результирующего набора.
        """
        sql_driver = self.db.sql_driver
        read_only = not self.db.write_mode
        rows = await sql_driver.execute(sql, params=None, readonly=read_only)
        if rows is None:
            return [{"status": "success", "message": SUCCESS_NO_ROWS}]
        return [decode_bytes_to_utf8(r.cells) for r in rows]
