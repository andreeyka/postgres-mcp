"""Сервис доступа к базе данных: пул, исполнитель и безопасная обертка."""

from postgres_fastmcp.config.database import DatabaseConfig
from postgres_fastmcp.enums import AccessMode
from postgres_fastmcp.logger import get_logger
from postgres_fastmcp.sql.connection.pool import DbConnPool
from postgres_fastmcp.sql.driver.base import SqlExecutor
from postgres_fastmcp.sql.security.config import SafeSqlConfig
from postgres_fastmcp.sql.security.driver import SafeSqlExecutor
from postgres_fastmcp.sql.validation.query_validator import QueryValidator


logger = get_logger(__name__)

ERROR_DB_NOT_INITIALIZED = "Соединение с базой данных не инициализировано"
ERROR_DB_URL_NOT_SET = "URL подключения к базе данных не задан"
LOG_UNRESTRICTED = "Используется SqlExecutor без ограничений (write_mode=True)"


class DbAccessService:
    """Сервис доступа к базе данных: пул и исполнитель (обычный или безопасный)."""

    def __init__(self, config: DatabaseConfig) -> None:
        """Инициализация с конфигурацией базы данных.

        Args:
            config: Конфигурация базы данных.
        """
        self.config = config
        self.write_mode = config.write_mode
        self.access_mode = config.access_mode
        self.db_connection = DbConnPool(
            connection_url=config.database_uri,
            min_size=config.pool_min_size,
            max_size=config.pool_max_size,
        )
        self._executor: SqlExecutor | SafeSqlExecutor | None = None

    async def close(self) -> None:
        """Закрывает пул подключений к базе данных. Вызывать при завершении жизненного цикла сервиса."""
        logger.debug("Закрытие соединений с базой данных DbAccessService")
        if self.db_connection:
            try:
                await self.db_connection.close()
                logger.debug("Пул подключений к базе данных успешно закрыт")
            except Exception as e:
                logger.error("Ошибка при закрытии пула подключений к базе данных: %s", e)

    @property
    def sql_driver(self) -> SqlExecutor | SafeSqlExecutor:
        """Исполнитель для SQL (обычный или безопасный). Создается лениво, переиспользуется."""
        if self._executor is not None:
            return self._executor

        if self.db_connection is None:
            raise ValueError(ERROR_DB_NOT_INITIALIZED)
        if not self.db_connection.connection_url:
            raise ValueError(ERROR_DB_URL_NOT_SET)

        base = SqlExecutor(conn=self.db_connection)

        if self.access_mode == AccessMode.FULL and self.write_mode:
            logger.debug(LOG_UNRESTRICTED)
            self._executor = base
        else:
            query_tag = getattr(self.config, "query_tag", None) or "postgres_fastmcp"
            safe_config = SafeSqlConfig(
                timeout=self.config.safe_sql_timeout,
                allowed_schema=self._allowed_schema(),
                read_only=self._is_read_only(),
                query_tag=query_tag,
                table_prefix=self.config.table_prefix if self.access_mode == AccessMode.BASIC else None,
            )
            validator = QueryValidator(
                allowed_schema=safe_config.allowed_schema,
                table_prefix=safe_config.table_prefix,
                read_only=safe_config.read_only,
            )
            logger.debug(
                "Using SafeSqlExecutor (access_mode=%s, write_mode=%s, allowed_schema=%s, "
                "read_only=%s, timeout=%ss, table_prefix=%s)",
                self.access_mode,
                self.write_mode,
                safe_config.allowed_schema,
                safe_config.read_only,
                safe_config.timeout,
                safe_config.table_prefix,
            )
            self._executor = SafeSqlExecutor(delegate=base, validator=validator, config=safe_config)

        return self._executor

    def _is_read_only(self) -> bool:
        return not self.write_mode

    def _allowed_schema(self) -> str | None:
        return "public" if self.access_mode == AccessMode.BASIC else None

    @property
    def connection_id(self) -> str:
        """Устойчивый идентификатор для этого соединения (например, для кэша версий/расширений)."""
        if not self.db_connection or not self.db_connection.connection_url:
            return ""
        return self.db_connection.connection_url
