"""Database access service: pool, executor, and safe wrapper."""

from types import TracebackType
from typing import Self

from postgres_fastmcp.config.database import DatabaseConfig
from postgres_fastmcp.enums import AccessMode, UserRole
from postgres_fastmcp.logger import get_logger
from postgres_fastmcp.sql.connection.pool import DbConnPool
from postgres_fastmcp.sql.driver.base import SqlExecutor
from postgres_fastmcp.sql.security.config import SafeSqlConfig
from postgres_fastmcp.sql.security.driver import SafeSqlExecutor
from postgres_fastmcp.sql.validation.query_validator import QueryValidator


logger = get_logger(__name__)

ERROR_DB_NOT_INITIALIZED = "Database connection is not initialized"
ERROR_DB_URL_NOT_SET = "Database connection URL is not set"
LOG_UNRESTRICTED = "Using unrestricted SqlExecutor (UNRESTRICTED mode)"


class DbAccessService:
    """Service for database access: pool and executor (plain or safe)."""

    def __init__(self, config: DatabaseConfig) -> None:
        """Initialize with database config.

        Args:
            config: Database configuration.
        """
        self.config = config
        self.access_mode = config.access_mode
        self.role = config.role
        self.db_connection = DbConnPool(
            connection_url=config.database_uri.get_secret_value(),
            min_size=config.pool_min_size,
            max_size=config.pool_max_size,
        )
        self._executor: SqlExecutor | SafeSqlExecutor | None = None

    async def __aenter__(self) -> Self:
        logger.debug("Entering DbAccessService context manager")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        logger.debug("Exiting DbAccessService context manager, closing database connections")
        if self.db_connection:
            try:
                await self.db_connection.close()
                logger.debug("Database connection pool closed successfully")
            except Exception as e:
                logger.error("Error closing database connection pool: %s", e)

    @property
    def sql_driver(self) -> SqlExecutor | SafeSqlExecutor:
        """Executor for SQL (plain or safe). Lazy-created, reused."""
        if self._executor is not None:
            return self._executor

        if self.db_connection is None:
            raise ValueError(ERROR_DB_NOT_INITIALIZED)
        if not self.db_connection.connection_url:
            raise ValueError(ERROR_DB_URL_NOT_SET)

        base = SqlExecutor(conn=self.db_connection)

        if self.role == UserRole.FULL and self.access_mode == AccessMode.UNRESTRICTED:
            logger.debug(LOG_UNRESTRICTED)
            self._executor = base
        else:
            query_tag = getattr(self.config, "query_tag", None) or "postgres_fastmcp"
            safe_config = SafeSqlConfig(
                timeout=self.config.safe_sql_timeout,
                allowed_schema=self._allowed_schema(),
                read_only=self._is_read_only(),
                query_tag=query_tag,
                table_prefix=self.config.table_prefix if self.role == UserRole.USER else None,
            )
            validator = QueryValidator(
                allowed_schema=safe_config.allowed_schema,
                table_prefix=safe_config.table_prefix,
                read_only=safe_config.read_only,
            )
            logger.debug(
                "Using SafeSqlExecutor (role=%s, access_mode=%s, allowed_schema=%s, "
                "read_only=%s, timeout=%ss, table_prefix=%s)",
                self.role.value,
                self.access_mode.value,
                safe_config.allowed_schema,
                safe_config.read_only,
                safe_config.timeout,
                safe_config.table_prefix,
            )
            self._executor = SafeSqlExecutor(delegate=base, validator=validator, config=safe_config)

        return self._executor

    def _is_read_only(self) -> bool:
        return self.access_mode == AccessMode.RESTRICTED

    def _allowed_schema(self) -> str | None:
        return "public" if self.role == UserRole.USER else None

    @property
    def connection_id(self) -> str:
        """Stable id for this connection (e.g. for version/extension cache)."""
        if not self.db_connection or not self.db_connection.connection_url:
            return ""
        return self.db_connection.connection_url
