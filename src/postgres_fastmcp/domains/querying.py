"""Домен выполнения произвольного SQL (тул execute_sql)."""

from typing import Any

from postgres_fastmcp.domains.db_access import DbAccessPort
from postgres_fastmcp.postgres.models import StatementResult
from postgres_fastmcp.shared.utils import decode_bytes_to_utf8


async def execute_sql(db: DbAccessPort, sql: str) -> list[dict[str, Any]] | StatementResult:
    """Выполнить SQL запрос к базе данных.

    Режим транзакции (только чтение / чтение-запись) определяется правами
    текущего запроса (``db.write_mode``), а не вызывающим кодом: при записи
    транзакция открывается на запись, поэтому DML/DDL реально применяются.
    Без права записи запись блокируется на уровне валидатора и транзакции.

    Оператор без результирующего набора (INSERT/UPDATE/DELETE без RETURNING, DDL)
    возвращает StatementResult с тегом команды Postgres ("UPDATE 3", "CREATE TABLE"):
    агент видит, сколько строк затронуто, а не пустой список.

    Args:
        db: Доступ к БД для текущего запроса (DbAccessPort).
        sql: SQL запрос для выполнения.

    Returns:
        Строки результата (list[dict]) либо StatementResult (rows=None) для оператора без результирующего набора.
    """
    result = await db.sql_driver.execute_statement(sql, params=None, readonly=not db.write_mode)
    if result.rows is None:
        return result
    return [decode_bytes_to_utf8(r.cells) for r in result.rows]
