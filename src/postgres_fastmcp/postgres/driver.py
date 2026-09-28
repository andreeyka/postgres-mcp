"""Выполнение SQL запросов и управление политикой транзакций."""

import logging
from typing import Any, LiteralString, NoReturn

from psycopg import (
    AsyncConnection,
    DatabaseError,
    Error as PsycopgError,
    InterfaceError,
    OperationalError,
)
from psycopg.rows import dict_row
from psycopg.sql import SQL, Composable, Literal
from psycopg_pool import PoolClosed, PoolTimeout, TooManyRequests

from postgres_fastmcp.postgres.connection import DbConnPool
from postgres_fastmcp.postgres.models import RowResult, StatementResult
from postgres_fastmcp.shared.errors import ConnectionNotEstablishedError


logger = logging.getLogger(__name__)


# SQLSTATE, означающие, что сервер рвёт или не принимает соединение (кроме класса 08).
# 57P01/57P02/57P03 — admin/crash shutdown, cannot connect now; 57P04 — база удалена;
# 57P05 — сессия завершена по idle_session_timeout. Все они рвут текущее соединение.
_CONNECTION_SQLSTATES = frozenset({"57P01", "57P02", "57P03", "57P04", "57P05"})

# Валидатор (pglast) и проверка по плану разбирают SQL так, как если бы у сервера был
# standard_conforming_strings = on. SET LOCAL в той же строке, что и запрос, не помог бы:
# Postgres лексит всю строку простого протокола целиком до выполнения. Поэтому параметр
# ставится отдельной командой до запроса, но в одной транзакции с ним — в той же команде,
# что и BEGIN, чтобы не тратить лишний круг к БД на отдельный execute.
_BEGIN_READ_ONLY = "BEGIN TRANSACTION READ ONLY; SET LOCAL standard_conforming_strings = on"
_BEGIN_READ_WRITE = "BEGIN; SET LOCAL standard_conforming_strings = on"


def _is_connection_error(error: Exception) -> bool:
    """Отличить ошибку соединения (пул надо пересоздать) от ошибки самого SQL (пул исправен).

    psycopg относит к OperationalError и обычные ошибки выполнения (55000, 40P01, 40001,
    55P03, 53xxx, 57014), поэтому решаем по SQLSTATE, а не по классу исключения.
    Соединением считаем: не-psycopg исключения (консервативно), InterfaceError, ошибки
    psycopg_pool, SQLSTATE класса 08, коды из _CONNECTION_SQLSTATES и ошибку без SQLSTATE —
    как OperationalError (клиент потерял соединение), так и точный тип DatabaseError:
    именно его конструирует psycopg.errors.error_from_result, когда libpq отдаёт
    FATAL_ERROR (например, "server closed the connection unexpectedly") без кода SQLSTATE.
    QueryCanceled остаётся исключением: у него всегда есть SQLSTATE (57014), поэтому сюда
    он не попадает.
    """
    if not isinstance(error, PsycopgError):
        return True
    if isinstance(error, (InterfaceError, PoolTimeout, PoolClosed, TooManyRequests)):
        return True
    sqlstate = error.sqlstate
    if sqlstate is None:
        return isinstance(error, OperationalError) or type(error) is DatabaseError
    return sqlstate.startswith("08") or sqlstate in _CONNECTION_SQLSTATES


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
        return (await self.execute_statement(query, params, readonly=readonly)).rows

    async def execute_statement(
        self,
        query: str | LiteralString,
        params: list[Any] | None = None,
        *,
        readonly: bool = True,
    ) -> StatementResult:
        """Выполнение запроса: строки и тег команды Postgres ("UPDATE 3", "CREATE TABLE").

        Args:
            query: SQL для выполнения (используйте {} для плейсхолдеров если заданы параметры).
            params: Необязательные параметры; если заданы, запрос рендерится перед выполнением.
            readonly: Если True, использовать транзакцию только для чтения; иначе чтение-запись.

        Returns:
            StatementResult последнего оператора строки запроса.
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
            if _is_connection_error(e):
                self._invalidate(e)
            raise

    def _invalidate(self, error: Exception) -> None:
        """Пометить пул невалидным или сбросить одиночное подключение после ошибки соединения."""
        if self.conn and self._is_pool and isinstance(self.conn, DbConnPool):
            self.conn.mark_invalid(str(error))
        elif self.conn and not self._is_pool:
            self.conn = None

    async def _execute_with_connection(
        self,
        connection: AsyncConnection[Any],
        query: str | LiteralString,
        params: list[Any] | None,
        *,
        readonly: bool,
    ) -> StatementResult:
        """Выполнение запроса на данном подключении с явной транзакцией.

        Строка может содержать несколько операторов (префикс SET LOCAL от SafeSqlExecutor):
        после nextset() текущим становится результат последнего, то есть оператора пользователя.

        SQL с hypopg_create_index помечает соединение пула до выполнения: пул сбросит гипотетические
        индексы при возврате соединения, даже если пакет упал после их создания.

        Транзакция открывается одной командой BEGIN[...]; SET LOCAL standard_conforming_strings = on:
        запрос пользователя выполняется отдельным execute() после неё, уже под on.
        """
        if isinstance(self.conn, DbConnPool) and "hypopg_create_index" in str(query).lower():
            self.conn.mark_hypopg_used(connection)
        async with connection.cursor(row_factory=dict_row) as cursor:
            if readonly:
                await cursor.execute(_BEGIN_READ_ONLY)
            else:
                await cursor.execute(_BEGIN_READ_WRITE)
            try:
                if params:
                    await cursor.execute(query, params)
                else:
                    await cursor.execute(query)
                while cursor.nextset():
                    pass
                # Тег и счётчик читаются до COMMIT/ROLLBACK: после них statusmessage станет "COMMIT"/"ROLLBACK"
                status = cursor.statusmessage
                affected_rows = cursor.rowcount if cursor.rowcount >= 0 else None
                rows = None
                if cursor.description is not None:
                    rows = [RowResult(cells=dict(row)) for row in await cursor.fetchall()]
                if readonly:
                    await cursor.execute("ROLLBACK")
                else:
                    await cursor.execute("COMMIT")
                return StatementResult(rows=rows, status=status, affected_rows=affected_rows)
            except Exception:
                try:
                    await cursor.execute("ROLLBACK")
                except Exception as rollback_error:
                    logger.error("Error rolling back transaction: %s", rollback_error)
                raise
