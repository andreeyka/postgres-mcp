"""Доступ к базе данных: один пул на сервис и исполнитель под права конкретного запроса."""

from dataclasses import dataclass
from typing import Protocol

from postgres_fastmcp.access import EffectiveAccess, clamp_to_ceiling
from postgres_fastmcp.postgres.connection import DbConnPool, check_connection
from postgres_fastmcp.postgres.driver import SqlExecutor
from postgres_fastmcp.postgres.ports import QueryExecutorPort, SqlDriverPort
from postgres_fastmcp.postgres.security.catalog_driver import CatalogSqlExecutor
from postgres_fastmcp.postgres.security.driver import SafeSqlConfig, SafeSqlExecutor
from postgres_fastmcp.postgres.security.plan_catalog import BuiltinTypeNames
from postgres_fastmcp.postgres.security.query_validator import QueryValidator
from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.shared.logger import get_logger


logger = get_logger(__name__)

DEFAULT_QUERY_TAG = "postgres_fastmcp"


class DatabaseConfigPort(Protocol):
    """Поля конфигурации БД, нужные сервису доступа.

    Структурно реализуется ``app.config.database.DatabaseConfig`` — сам домен
    при этом не зависит от слоя приложения.
    """

    pool_min_size: int
    pool_max_size: int
    max_inactive_connection_lifetime: int
    write_mode: bool
    access_mode: AccessMode
    safe_sql_timeout: int
    table_prefix: str | None
    plan_check: bool
    plan_check_allow_non_sql_functions: bool
    query_tag: str | None

    @property
    def database_uri(self) -> str | None:
        """Строка подключения к БД (None, если не задана)."""
        ...


class DbAccessPort(Protocol):
    """То, что домены получают на один запрос: исполнитель и права этого запроса."""

    @property
    def sql_driver(self) -> SqlDriverPort:
        """Исполнитель SQL под права запроса."""
        ...

    @property
    def catalog_driver(self) -> QueryExecutorPort:
        """Исполнитель служебных запросов каталога: только шаблоны сервера, только чтение."""
        ...

    @property
    def access_mode(self) -> AccessMode:
        """Эффективный уровень доступа запроса."""
        ...

    @property
    def write_mode(self) -> bool:
        """Разрешена ли запись в этом запросе."""
        ...

    @property
    def table_prefix(self) -> str | None:
        """Префикс имён таблиц из конфигурации (действует в BASIC)."""
        ...

    @property
    def connection_id(self) -> str:
        """Устойчивый идентификатор соединения (ключ кэшей версий и расширений)."""
        ...


@dataclass(frozen=True, slots=True)
class DbAccess:
    """Реализация DbAccessPort для одного запроса."""

    sql_driver: SqlDriverPort
    catalog_driver: QueryExecutorPort
    access_mode: AccessMode
    write_mode: bool
    table_prefix: str | None
    connection_id: str


class DbAccessService:
    """Пул подключений, исполнители по эффективным правам (не больше четырёх) и один исполнитель каталога."""

    def __init__(self, config: DatabaseConfigPort) -> None:
        """Инициализация с конфигурацией базы данных; пул открывается лениво при первом запросе.

        Args:
            config: Конфигурация базы данных.
        """
        self._config = config
        self._pool = DbConnPool(
            connection_url=config.database_uri,
            min_size=config.pool_min_size,
            max_size=config.pool_max_size,
            max_idle=config.max_inactive_connection_lifetime,
        )
        self._ceiling = EffectiveAccess(config.access_mode, write_mode=config.write_mode)
        self._executors: dict[EffectiveAccess, SqlDriverPort] = {}
        # Имена типов pg_catalog для plan_check: пул один (одна БД), поэтому и кэш один на все исполнители.
        self._builtin_types = BuiltinTypeNames()
        # Каталог не зависит от прав запроса: схему и префикс проверяет домен до запроса.
        self._catalog = CatalogSqlExecutor(
            SqlExecutor(conn=self._pool),
            timeout=config.safe_sql_timeout,
            query_tag=config.query_tag or DEFAULT_QUERY_TAG,
        )

    def view(self, access: EffectiveAccess) -> DbAccess:
        """Доступ к БД для одного запроса с заданными правами.

        Права запроса дополнительно ограничиваются потолком из конфигурации сервиса,
        независимо от резолвера: запрос не может получить больше, чем разрешено сервису.

        Args:
            access: Эффективные права запроса.

        Returns:
            DbAccess с исполнителем под права, приведённые к потолку.
        """
        access = clamp_to_ceiling(access, self._ceiling)
        return DbAccess(
            sql_driver=self._executor(access),
            catalog_driver=self._catalog,
            access_mode=access.access_mode,
            write_mode=access.write_mode,
            table_prefix=self._config.table_prefix,
            connection_id=self._pool.connection_url or "",
        )

    @property
    def catalog_driver(self) -> QueryExecutorPort:
        """Исполнитель шаблонов каталога (канал сервера), тот же, что в каждом view(); для проверок при старте."""
        return self._catalog

    async def ping(self) -> None:
        """SELECT 1 на отдельном соединении; исключение psycopg, если БД не отвечает."""
        url = self._config.database_uri
        if not url:
            msg = "Database connection URL is not set"
            raise ValueError(msg)
        await check_connection(url)

    async def close(self) -> None:
        """Закрыть пул подключений. Вызывать при завершении жизненного цикла сервиса."""
        logger.debug("Closing the database connection pool")
        try:
            await self._pool.close()
        except Exception as e:
            logger.error("Failed to close the database connection pool: %s", e)

    def _executor(self, access: EffectiveAccess) -> SqlDriverPort:
        """Исполнитель под права: создаётся при первом обращении и переиспользуется."""
        cached = self._executors.get(access)
        if cached is not None:
            return cached

        base = SqlExecutor(conn=self._pool)
        executor: SqlDriverPort
        # Без ограничений — только явная запись (is True) в FULL; всё остальное через SafeSqlExecutor.
        if access.access_mode == AccessMode.FULL and access.write_mode is True:
            logger.debug("Using unrestricted SqlExecutor (access_mode=full, write_mode=True)")
            executor = base
        else:
            basic = access.access_mode == AccessMode.BASIC
            safe_config = SafeSqlConfig(
                timeout=self._config.safe_sql_timeout,
                allowed_schema="public" if basic else None,
                read_only=not access.write_mode,
                query_tag=self._config.query_tag or DEFAULT_QUERY_TAG,
                table_prefix=self._config.table_prefix if basic else None,
                plan_check=basic and self._config.plan_check,
                plan_check_allow_non_sql_functions=self._config.plan_check_allow_non_sql_functions,
            )
            validator = QueryValidator(
                allowed_schema=safe_config.allowed_schema,
                table_prefix=safe_config.table_prefix,
                read_only=safe_config.read_only,
                allow_explain_analyze=not basic,
            )
            logger.debug(
                "Using SafeSqlExecutor (access_mode=%s, write_mode=%s, allowed_schema=%s, "
                "read_only=%s, allow_explain_analyze=%s, timeout=%ss, table_prefix=%s, plan_check=%s, "
                "plan_check_allow_non_sql_functions=%s)",
                access.access_mode,
                access.write_mode,
                safe_config.allowed_schema,
                safe_config.read_only,
                not basic,
                safe_config.timeout,
                safe_config.table_prefix,
                safe_config.plan_check,
                safe_config.plan_check_allow_non_sql_functions,
            )
            executor = SafeSqlExecutor(
                delegate=base, validator=validator, config=safe_config, builtin_types=self._builtin_types
            )
        self._executors[access] = executor
        return executor
