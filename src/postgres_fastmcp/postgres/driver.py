"""Выполнение SQL запросов и управление политикой транзакций."""

import logging
from typing import Any, LiteralString, NoReturn

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.sql import SQL, Composable, Literal

from postgres_fastmcp.postgres.connection import DbConnPool
from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.shared.errors import ConnectionNotEstablishedError


logger = logging.getLogger(__name__)


class SqlExecutor:
    """Выполнение SQL запросов через пул подключений или прямое подключение. Управление транзакциями."""

    def __init__(
        self,
        conn: DbConnPool | AsyncConnection | None = None,
        engine_url: str | None = None,
    ) -> None:
        """Инициализация с пулом подключений, подключением или URL.

        Args:
            conn: Пул подключений или одиночное асинхронное подключение.
            engine_url: URL подключения; пул будет создан при первом использовании.
        """
        self.conn: DbConnPool | AsyncConnection | None = None
        if conn is not None:
            self.conn = conn
            self._is_pool = isinstance(conn, DbConnPool)
        elif engine_url:
            self.engine_url = engine_url
            self._is_pool = False
        else:
            raise ConnectionNotEstablishedError

    def _ensure_connected(self) -> None:
        """Проверка установки подключения; создание пула из engine_url при необходимости."""
        if self.conn is not None:
            return
        if getattr(self, "engine_url", None):
            self.conn = DbConnPool(self.engine_url)
            self._is_pool = True
            return
        raise ConnectionNotEstablishedError

    def render(self, query: str, params: list[Any]) -> str:
        """Рендер параметризованного запроса (с {} плейсхолдерами) в одну строку."""
        composables = [p if isinstance(p, Composable) else Literal(p) for p in params]
        return SQL(query).format(*composables).as_string()

    async def execute(
        self,
        query: str | LiteralString,
        params: list[Any] | None = None,
        *,
        readonly: bool = True,
    ) -> list[RowResult] | None:
        """Выполнение запроса и возвращение строк, или None для операторов без результата.

        Args:
            query: SQL для выполнения (используйте {} для плейсхолдеров если заданы параметры).
            params: Необязательные параметры; если заданы, запрос рендерится и then выполняется.
            readonly: Если True, использовать транзакцию только для чтения; иначе чтение-запись.

        Returns:
            Список RowResult или None для DDL/командных операторов.
        """
        if params:
            query = self.render(query, params)
            params = None

        def _fail() -> NoReturn:
            raise ConnectionNotEstablishedError

        try:
            self._ensure_connected()
            if self.conn is None:
                _fail()
            if self._is_pool and isinstance(self.conn, DbConnPool):
                pool = await self.conn.pool_connect()
                async with pool.connection() as connection:
                    await connection.set_autocommit(True)
                    return await self._execute_with_connection(connection, query, params, readonly=readonly)
            if isinstance(self.conn, AsyncConnection):
                if hasattr(self.conn, "set_autocommit"):
                    await self.conn.set_autocommit(True)
                return await self._execute_with_connection(self.conn, query, params, readonly=readonly)
            _fail()
        except Exception as e:
            if self.conn and self._is_pool and isinstance(self.conn, DbConnPool):
                self.conn.mark_invalid(str(e))
            elif self.conn and not self._is_pool:
                self.conn = None
            raise

    async def _execute_with_connection(
        self,
        connection: AsyncConnection[Any],
        query: str | LiteralString,
        params: list[Any] | None,
        *,
        readonly: bool,
    ) -> list[RowResult] | None:
        """Выполнение запроса на данном подключении с явной транзакцией."""
        async with connection.cursor(row_factory=dict_row) as cursor:
            if readonly:
                await cursor.execute("BEGIN TRANSACTION READ ONLY")
            else:
                await cursor.execute("BEGIN")
            try:
                if params:
                    await cursor.execute(query, params)
                else:
                    await cursor.execute(query)
                while cursor.nextset():
                    pass
                if cursor.description is None:
                    if readonly:
                        await cursor.execute("ROLLBACK")
                    else:
                        await cursor.execute("COMMIT")
                    return None
                rows = await cursor.fetchall()
                if readonly:
                    await cursor.execute("ROLLBACK")
                else:
                    await cursor.execute("COMMIT")
                return [RowResult(cells=dict(row)) for row in rows]
            except Exception:
                try:
                    await cursor.execute("ROLLBACK")
                except Exception as rollback_error:
                    logger.error("Error rolling back transaction: %s", rollback_error)
                raise
