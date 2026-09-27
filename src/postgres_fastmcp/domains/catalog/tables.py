"""Таблицы и представления: список и детали (используется только модулем objects)."""

import asyncio
from collections.abc import Awaitable
from typing import Any

from postgres_fastmcp.domains.db_access import DbAccessPort
from postgres_fastmcp.postgres.catalog import (
    QUERY_GET_COLUMNS,
    QUERY_GET_CONSTRAINTS,
    QUERY_GET_INDEXES,
    QUERY_LIST_TABLES_VIEWS,
    QUERY_TABLE_EXISTS,
)
from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.shared.utils import decode_bytes_to_utf8


# object_type тула -> information_schema.tables.table_type
_TABLE_TYPES = {"table": "BASE TABLE", "view": "VIEW"}


async def _run_concurrently[T](*awaitables: Awaitable[T]) -> list[T]:
    """Выполнить awaitable параллельно; при любой ошибке отменить и дождаться остальных.

    Семантика gather: результаты в порядке аргументов, наружу — первое исходное исключение
    (не ExceptionGroup), а при отмене вызывающей задачи — CancelledError. В отличие от
    голого gather, соседи упавшей задачи отменяются и завершаются до выхода, поэтому не
    держат соединения пула, а отмена, пришедшая одновременно с ошибкой ребёнка, не теряется.

    asyncio.TaskGroup здесь не подходит: на Python 3.12, если ребёнок падает, пока родитель
    ждёт в __aexit__, TaskGroup отменяет родителя и не снимает отмену (uncancel). Тогда
    cancelling() у вызывающей задачи остаётся 1: внешнюю отмену запроса не отличить от
    собственной, а лишний запрос отмены утекает к вызывающему коду.

    Args:
        awaitables: Корутины или future для параллельного выполнения.

    Returns:
        Список результатов в порядке аргументов.
    """
    current = asyncio.current_task()
    cancels_before = current.cancelling() if current is not None else 0
    tasks = [asyncio.ensure_future(aw) for aw in awaitables]
    try:
        return await asyncio.gather(*tasks)
    except BaseException as exc:
        # gather не отменяет соседей при ошибке одного из них (при отмене самого gather — отменяет).
        for task in tasks:
            task.cancel()
        # Исключения детей забирает колбэк gather, поэтому wait достаточно, чтобы дождаться завершения.
        await asyncio.wait(tasks)
        # Гонка: вызывающую задачу отменили, а ребёнок в ту же итерацию упал с ошибкой — gather
        # отдаёт ошибку ребёнка, и отмена запроса теряется. Счётчик cancelling() вырос — отмену возвращаем.
        if (
            not isinstance(exc, asyncio.CancelledError)
            and current is not None
            and current.cancelling() > cancels_before
        ):
            raise asyncio.CancelledError from exc
        raise


class TablesService:
    """Сервис списка таблиц/представлений и получения их деталей."""

    def __init__(self, db: DbAccessPort) -> None:
        """Инициализация с сервисом доступа к БД.

        Args:
            db: Доступ к БД для текущего запроса (DbAccessPort).
        """
        self.db = db

    async def list_tables_views(
        self,
        schema_name: str,
        object_type: str = "table",
    ) -> list[dict[str, Any]]:
        """Список таблиц или представлений в схеме.

        Args:
            schema_name: Имя схемы.
            object_type: Тип объекта — "table" или "view" (по умолчанию "table").

        Returns:
            Список словарей с полями schema, name, type.
        """
        sql_driver = self.db.sql_driver
        rows = await sql_driver.execute(
            QUERY_LIST_TABLES_VIEWS,
            params=[schema_name, _TABLE_TYPES[object_type]],
            readonly=True,
        )
        objects = (
            [
                {
                    "schema": decode_bytes_to_utf8(row.cells["table_schema"]),
                    "name": decode_bytes_to_utf8(row.cells["table_name"]),
                    "type": decode_bytes_to_utf8(row.cells["table_type"]),
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

    async def get_details(
        self,
        schema_name: str,
        object_name: str,
        object_type: str = "table",
    ) -> dict[str, Any] | None:
        """Получить столбцы, ограничения и индексы таблицы или представления.

        Существование решает каталог (QUERY_TABLE_EXISTS), а не пустые разделы: таблица
        без столбцов (CREATE TABLE t()) существует. Запрос существования идёт в том же
        параллельном наборе, поэтому ошибка валидатора у остальных запросов (BASIC с
        table_prefix) остаётся той же, что до проверки существования.

        Запросы идут через _run_concurrently: если один падает, остальные отменяются и не
        держат соединения пула; отмена вызывающей задачи не теряется.

        Args:
            schema_name: Имя схемы объекта.
            object_name: Имя таблицы или представления.
            object_type: Тип объекта — "table" или "view" (по умолчанию "table").

        Returns:
            Словарь с ключами basic, columns, constraints, indexes; None, если объекта такого типа нет.
        """
        sql_driver = self.db.sql_driver

        col_rows, con_rows, idx_rows, found = await _run_concurrently(
            sql_driver.execute(QUERY_GET_COLUMNS, params=[schema_name, object_name], readonly=True),
            sql_driver.execute(QUERY_GET_CONSTRAINTS, params=[schema_name, object_name], readonly=True),
            sql_driver.execute(QUERY_GET_INDEXES, params=[schema_name, object_name], readonly=True),
            sql_driver.execute(
                QUERY_TABLE_EXISTS, params=[schema_name, object_name, _TABLE_TYPES[object_type]], readonly=True
            ),
        )
        if not found:
            return None
        columns = (
            [
                {
                    "column": decode_bytes_to_utf8(r.cells["column_name"]),
                    "data_type": decode_bytes_to_utf8(r.cells["data_type"]),
                    "is_nullable": decode_bytes_to_utf8(r.cells["is_nullable"]),
                    "default": decode_bytes_to_utf8(r.cells["column_default"]),
                }
                for r in col_rows
            ]
            if col_rows
            else []
        )

        constraints: dict[str, dict[str, Any]] = {}
        if con_rows:
            for row in con_rows:
                cname = decode_bytes_to_utf8(row.cells["constraint_name"])
                ctype = decode_bytes_to_utf8(row.cells["constraint_type"])
                col = decode_bytes_to_utf8(row.cells["column_name"])
                if isinstance(cname, str) and isinstance(ctype, (str, int, float, bool, type(None))):
                    if cname not in constraints:
                        constraints[cname] = {"type": ctype, "columns": []}
                    if col and isinstance(col, str):
                        constraints[cname]["columns"].append(col)
        constraints_list = [{"name": k, **v} for k, v in constraints.items()]

        indexes = (
            [
                {
                    "name": decode_bytes_to_utf8(r.cells["indexname"]),
                    "definition": decode_bytes_to_utf8(r.cells["indexdef"]),
                }
                for r in idx_rows
            ]
            if idx_rows
            else []
        )

        return {
            "basic": {"schema": schema_name, "name": object_name, "type": object_type},
            "columns": columns,
            "constraints": constraints_list,
            "indexes": indexes,
        }
