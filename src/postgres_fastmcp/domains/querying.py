"""Домен выполнения произвольного SQL (тул execute_sql)."""

from typing import Any

from postgres_fastmcp.domains.db_access import DbAccessPort
from postgres_fastmcp.shared.utils import decode_bytes_to_utf8


SUCCESS_NO_ROWS = "Statement executed successfully; no rows were returned."


async def execute_sql(db: DbAccessPort, sql: str) -> list[dict[str, Any]] | None:
    """Выполнить SQL запрос к базе данных.

    Режим транзакции (только чтение / чтение-запись) определяется write_mode
    сервера, а не вызывающим кодом: при write_mode=True транзакция открывается
    на запись, поэтому DML/DDL реально применяются. В режиме только чтения
    (write_mode=False) запись блокируется на уровне валидатора и транзакции.

    Операторы без результирующего набора (INSERT/UPDATE/DELETE/DDL без RETURNING)
    считаются успешно выполненными и возвращают None, а не ошибку; тул выводит
    для них SUCCESS_NO_ROWS.

    Args:
        db: Сервис доступа к базе данных.
        sql: SQL запрос для выполнения.

    Returns:
        Список строк результата (list[dict]) либо None для оператора без результирующего набора.
    """
    rows = await db.sql_driver.execute(sql, params=None, readonly=not db.write_mode)
    if rows is None:
        return None
    return [decode_bytes_to_utf8(r.cells) for r in rows]
