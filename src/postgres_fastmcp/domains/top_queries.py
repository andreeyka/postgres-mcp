"""Домен топ-запросов: отчёты по pg_stat_statements (тул get_top_queries)."""

import logging
from typing import Any, Literal

from psycopg.errors import ObjectNotInPrerequisiteState

from postgres_fastmcp.domains.db_access import DbAccessPort
from postgres_fastmcp.postgres.extensions import ExtensionInspectorAdapter
from postgres_fastmcp.postgres.ports import SqlDriverPort
from postgres_fastmcp.shared.errors import (
    ExtensionStatusUnavailableError,
    InvalidSortCriteriaError,
    PgStatStatementsNotInstalledError,
    UnsupportedServerVersionError,
)
from postgres_fastmcp.shared.utils import decode_bytes_to_utf8


logger = logging.getLogger(__name__)

PG_STAT_STATEMENTS = "pg_stat_statements"
# PostgreSQL version where column names changed in pg_stat_statements
PG_VERSION_COLUMN_CHANGE = 13
# Ранжирование по ресурсам читает stddev_exec_time и wal_bytes, которые появились в PostgreSQL 13
_RESOURCES_FEATURE = "sort_by='resources'"
_RESOURCES_HINT = "Use sort_by='total_time' or 'mean_time' instead."


class TopQueriesCalc:
    """Строки pg_stat_statements: самые медленные и самые ресурсоёмкие запросы."""

    def __init__(
        self,
        sql_driver: SqlDriverPort,
        connection_id: str = "",
    ) -> None:
        """Инициализация.

        Args:
            sql_driver: SQL-драйвер для запросов (и шаблон для проверки расширений).
            connection_id: Стабильный id подключения для кэша расширений и версии.
        """
        self.sql_driver = sql_driver
        self._ext_inspector = ExtensionInspectorAdapter(sql_driver, sql_driver, connection_id)

    async def _server_version(self) -> int:
        """Мажорная версия PostgreSQL после проверки, что pg_stat_statements установлено.

        Raises:
            ExtensionStatusUnavailableError: Каталог расширений ответил ошибкой, статус неизвестен.
            PgStatStatementsNotInstalledError: Если pg_stat_statements не установлено.
        """
        extension_status = await self._ext_inspector.check_extension(PG_STAT_STATEMENTS, include_messages=False)
        if extension_status.catalog_error:
            logger.warning("Extension catalog error while checking %s", PG_STAT_STATEMENTS)
            raise ExtensionStatusUnavailableError(PG_STAT_STATEMENTS, extension_status.catalog_error)
        if not extension_status.is_installed:
            logger.warning("Extension %s is not installed", PG_STAT_STATEMENTS)
            raise PgStatStatementsNotInstalledError
        pg_version = await self._ext_inspector.get_postgres_version()
        logger.debug("PostgreSQL version: %s", pg_version)
        return pg_version

    async def _fetch(self, query: str, limit: int) -> list[dict[str, Any]]:
        """Выполнить запрос к pg_stat_statements и декодировать строки.

        Raises:
            PgStatStatementsNotInstalledError: Расширение создано, но библиотека не загружена
                через shared_preload_libraries: представление недоступно.
        """
        try:
            rows = await self.sql_driver.execute(query, params=[limit], readonly=True)
        except ObjectNotInPrerequisiteState as e:
            logger.warning("Extension %s is not preloaded: %s", PG_STAT_STATEMENTS, e)
            raise PgStatStatementsNotInstalledError from e
        return [decode_bytes_to_utf8(row.cells) for row in rows] if rows else []

    async def get_top_queries_by_time(
        self, limit: int = 10, sort_by: Literal["total", "mean"] = "mean"
    ) -> list[dict[str, Any]]:
        """Самые медленные запросы по общему или среднему времени выполнения.

        Args:
            limit: Максимум строк.
            sort_by: 'total' — по общему времени, 'mean' — по среднему на вызов.

        Returns:
            Строки pg_stat_statements: query, calls, время, rows.
        """
        # Колонки переименованы в PostgreSQL 13
        if await self._server_version() >= PG_VERSION_COLUMN_CHANGE:
            total_time_col, mean_time_col = "total_exec_time", "mean_exec_time"
        else:
            total_time_col, mean_time_col = "total_time", "mean_time"
        order_by_column = total_time_col if sort_by == "total" else mean_time_col
        # Имена колонок берутся из проверки версии, а не из ввода пользователя
        query = f"""
            SELECT
                query,
                calls,
                {total_time_col},
                {mean_time_col},
                rows
            FROM pg_stat_statements
            WHERE calls > 0
              AND query NOT LIKE '%pg_stat_statements%'
            ORDER BY {order_by_column} DESC
            LIMIT {{}};
        """  # noqa: S608
        result = await self._fetch(query, limit)
        logger.info("Found %s slow queries", len(result))
        return result

    async def get_top_resource_queries(self, limit: int = 10, frac_threshold: float = 0.05) -> list[dict[str, Any]]:
        """Самые ресурсоёмкие запросы: доля времени, буферов и WAL выше порога.

        Args:
            limit: Максимум строк.
            frac_threshold: Порог доли ресурса (по умолчанию 0.05).

        Returns:
            Строки pg_stat_statements с долями ресурсов.

        Raises:
            UnsupportedServerVersionError: PostgreSQL ниже 13: нет stddev_exec_time и wal_bytes.
        """
        pg_version = await self._server_version()
        if pg_version < PG_VERSION_COLUMN_CHANGE:
            raise UnsupportedServerVersionError(
                _RESOURCES_FEATURE, PG_VERSION_COLUMN_CHANGE, pg_version, hint=_RESOURCES_HINT
            )
        # Доли делятся на NULLIF(сумма, 0): на нагрузке без WAL или чтений сумма равна нулю,
        # доля становится NULL и не проходит фильтр по порогу вместо division by zero.
        # frac_threshold — float-параметр, а не SQL от пользователя
        query = f"""
            WITH resource_fractions AS (
                SELECT
                    query,
                    calls,
                    rows,
                    total_exec_time,
                    mean_exec_time,
                    stddev_exec_time,
                    shared_blks_hit,
                    shared_blks_read,
                    shared_blks_dirtied,
                    wal_bytes,
                    total_exec_time / NULLIF(SUM(total_exec_time) OVER (), 0) AS total_exec_time_frac,
                    (shared_blks_hit + shared_blks_read)
                        / NULLIF(SUM(shared_blks_hit + shared_blks_read) OVER (), 0) AS shared_blks_accessed_frac,
                    shared_blks_read / NULLIF(SUM(shared_blks_read) OVER (), 0) AS shared_blks_read_frac,
                    shared_blks_dirtied / NULLIF(SUM(shared_blks_dirtied) OVER (), 0) AS shared_blks_dirtied_frac,
                    wal_bytes / NULLIF(SUM(wal_bytes) OVER (), 0) AS total_wal_bytes_frac
                FROM pg_stat_statements
                WHERE calls > 0
                  AND query NOT LIKE '%pg_stat_statements%'
            )
            SELECT
                query,
                calls,
                rows,
                total_exec_time,
                mean_exec_time,
                stddev_exec_time,
                total_exec_time_frac,
                shared_blks_accessed_frac,
                shared_blks_read_frac,
                shared_blks_dirtied_frac,
                total_wal_bytes_frac,
                shared_blks_hit,
                shared_blks_read,
                shared_blks_dirtied,
                wal_bytes
            FROM resource_fractions
            WHERE
                total_exec_time_frac > {frac_threshold}
                OR shared_blks_accessed_frac > {frac_threshold}
                OR shared_blks_read_frac > {frac_threshold}
                OR shared_blks_dirtied_frac > {frac_threshold}
                OR total_wal_bytes_frac > {frac_threshold}
            ORDER BY total_exec_time DESC
            LIMIT {{}};
        """  # noqa: S608
        result = await self._fetch(query, limit)
        logger.info("Found %s resource-intensive queries", len(result))
        return result


async def get_top_queries(
    db: DbAccessPort,
    sort_by: str = "resources",
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Самые медленные или ресурсоёмкие запросы из pg_stat_statements.

    Args:
        db: Сервис доступа к базе данных.
        sort_by: Критерий: 'resources', 'mean_time' или 'total_time'.
        limit: Максимум строк (по умолчанию 10).

    Returns:
        Строки pg_stat_statements.

    Raises:
        InvalidSortCriteriaError: Если указан недопустимый sort_by.
        PgStatStatementsNotInstalledError: Если pg_stat_statements не установлено.
    """
    calc = TopQueriesCalc(sql_driver=db.sql_driver, connection_id=db.connection_id)

    if sort_by == "resources":
        return await calc.get_top_resource_queries(limit=limit)
    if sort_by in {"mean_time", "total_time"}:
        return await calc.get_top_queries_by_time(
            limit=limit,
            sort_by="mean" if sort_by == "mean_time" else "total",
        )
    raise InvalidSortCriteriaError(sort_by)
