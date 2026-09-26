"""Домен топ-запросов: отчёты по pg_stat_statements (тул get_top_queries)."""

import logging
from typing import Any, Literal

from postgres_fastmcp.domains.db_access import DbAccessService
from postgres_fastmcp.postgres.extensions import ExtensionInspectorAdapter
from postgres_fastmcp.postgres.ports import SqlDriverPort
from postgres_fastmcp.shared.errors import InvalidSortCriteriaError, PgStatStatementsNotInstalledError
from postgres_fastmcp.shared.utils import decode_bytes_to_utf8


logger = logging.getLogger(__name__)

PG_STAT_STATEMENTS = "pg_stat_statements"
# PostgreSQL version where column names changed in pg_stat_statements
PG_VERSION_COLUMN_CHANGE = 13


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

    async def _time_columns(self) -> tuple[str, str]:
        """Имена колонок общего и среднего времени для версии сервера.

        Raises:
            PgStatStatementsNotInstalledError: Если pg_stat_statements не установлено.
        """
        extension_status = await self._ext_inspector.check_extension(PG_STAT_STATEMENTS, include_messages=False)
        if not extension_status.is_installed:
            logger.warning("Extension %s is not installed", PG_STAT_STATEMENTS)
            raise PgStatStatementsNotInstalledError
        pg_version = await self._ext_inspector.get_postgres_version()
        logger.debug("PostgreSQL version: %s", pg_version)
        # Колонки переименованы в PostgreSQL 13
        if pg_version >= PG_VERSION_COLUMN_CHANGE:
            return "total_exec_time", "mean_exec_time"
        return "total_time", "mean_time"

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
        total_time_col, mean_time_col = await self._time_columns()
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
        rows = await self.sql_driver.execute(query, params=[limit], readonly=True)
        result = [decode_bytes_to_utf8(row.cells) for row in rows] if rows else []
        logger.info("Found %s slow queries", len(result))
        return result

    async def get_top_resource_queries(self, limit: int = 10, frac_threshold: float = 0.05) -> list[dict[str, Any]]:
        """Самые ресурсоёмкие запросы: доля времени, буферов и WAL выше порога.

        Args:
            limit: Максимум строк.
            frac_threshold: Порог доли ресурса (по умолчанию 0.05).

        Returns:
            Строки pg_stat_statements с долями ресурсов.
        """
        total_time_col, mean_time_col = await self._time_columns()
        # Имена колонок из проверки версии, frac_threshold — float-параметр, а не SQL от пользователя
        query = f"""
            WITH resource_fractions AS (
                SELECT
                    query,
                    calls,
                    rows,
                    {total_time_col} total_exec_time,
                    {mean_time_col} mean_exec_time,
                    stddev_exec_time,
                    shared_blks_hit,
                    shared_blks_read,
                    shared_blks_dirtied,
                    wal_bytes,
                    total_exec_time / SUM(total_exec_time) OVER () AS total_exec_time_frac,
                    (shared_blks_hit + shared_blks_read) / SUM(shared_blks_hit + shared_blks_read) OVER () AS shared_blks_accessed_frac,
                    shared_blks_read / SUM(shared_blks_read) OVER () AS shared_blks_read_frac,
                    shared_blks_dirtied / SUM(shared_blks_dirtied) OVER () AS shared_blks_dirtied_frac,
                    wal_bytes / SUM(wal_bytes) OVER () AS total_wal_bytes_frac
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
        """  # noqa: E501, S608
        rows = await self.sql_driver.execute(query, params=[limit], readonly=True)
        result = [decode_bytes_to_utf8(row.cells) for row in rows] if rows else []
        logger.info("Found %s resource-intensive queries", len(result))
        return result


async def get_top_queries(
    db: DbAccessService,
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
