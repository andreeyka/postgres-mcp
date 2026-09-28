"""PostgresProvider: источник девяти тулов одной базы для любого FastMCP-сервера.

Провайдер владеет DbAccessService (пул открывается лениво при первом запросе,
закрывается в ``lifespan``; там же при достижимом basic запускается фоновая проверка прав роли)
и на каждый вызов тула считает права запроса: токен текущего запроса -> резолвер ->
``DbAccessService.view(права)``.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastmcp.server.dependencies import get_access_token
from fastmcp.server.providers import LocalProvider

from postgres_fastmcp.access import (
    AccessPolicy,
    AccessResolver,
    EffectiveAccess,
    bounded_resolver,
    build_resolver,
    full_access_check,
)
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.domains.db_access import DbAccessPort, DbAccessService
from postgres_fastmcp.domains.role_check import warn_about_basic_role
from postgres_fastmcp.shared.enums import AccessMode, ToolTag
from postgres_fastmcp.shared.logger import get_logger
from postgres_fastmcp.tools.definitions import ToolSet
from postgres_fastmcp.tools.registry import register_tools


logger = get_logger(__name__)


class PostgresProvider(LocalProvider):
    """Тулы одной базы PostgreSQL с правами не выше потолка из ``database``.

    ``DatabaseConfig`` не читает env и .env: потолок прав — ровно то, что передано (по умолчанию
    ``basic`` без записи). Конфиг из окружения ``MCP_DATABASE_*`` — ``DatabaseSettings`` или
    ``Settings().database``.
    """

    def __init__(
        self,
        database: DatabaseConfig,
        *,
        access_policy: AccessPolicy | None = None,
        access_resolver: AccessResolver | None = None,
    ) -> None:
        """Создать провайдер и зарегистрировать тулы.

        Args:
            database: Подключение и серверный потолок прав (access_mode, write_mode).
            access_policy: Политика сужения прав по claim токена (``resolve_access``);
                без неё права запроса равны потолку.
            access_resolver: Свой резолвер токен -> права; приоритетнее access_policy.
                Результат всегда ограничивается потолком. Вызывается на каждый list_tools
                для каждого full-тула, на каждой проверке доступа full-тула и на каждом
                вызове get_db — должен быть дешёвым и детерминированным.
        """
        super().__init__(on_duplicate="error")
        # basic достижим: потолок basic или права сужаются по токену до basic
        self._basic_reachable = (
            database.access_mode == AccessMode.BASIC
            or access_resolver is not None
            or (access_policy is not None and access_policy.enforced)
        )
        self._table_prefix = database.table_prefix
        ceiling = EffectiveAccess(database.access_mode, write_mode=database.write_mode)
        if access_resolver is None:
            access_resolver = build_resolver(ceiling, access_policy or AccessPolicy())
        self._resolve = bounded_resolver(access_resolver, ceiling)
        self._db = DbAccessService(database)
        register_tools(
            self,
            ToolSet(get_db=self._current_db),
            ceiling=database,
            full_tool_auth=full_access_check(self._resolve),
        )
        if database.access_mode == AccessMode.BASIC:
            self.disable(tags={ToolTag.FULL.value})

    def _current_db(self) -> DbAccessPort:
        """Доступ к БД с правами текущего запроса (токен None в stdio и без auth)."""
        return self._db.view(self._resolve(get_access_token()))

    async def ping(self) -> None:
        """Проверить, что база отвечает на SELECT 1 (для /health); бросает ошибку psycopg, если нет.

        Своего общего таймаута нет: вызывающий оборачивает вызов в ``asyncio.timeout``.
        """
        await self._db.ping()

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """Фоновая проверка прав роли для basic на старте; при остановке — отменить её и закрыть пул.

        Проверка не задерживает старт: одна строка WARNING о правах шире basic или INFO, если БД недоступна.
        """
        role_check = asyncio.create_task(self._check_basic_role()) if self._basic_reachable else None
        try:
            yield
        finally:
            if role_check is not None:
                role_check.cancel()
                await asyncio.wait({role_check})
            await self._db.close()

    async def _check_basic_role(self) -> None:
        """Обёртка фоновой задачи: программная ошибка проверки не должна ронять сервер.

        Сама проверка гасит ошибки БД; ValueError/TypeError (баг в шаблоне каталога) доходят
        до неё — здесь они ловятся отдельно и логируются, чтобы задача не упала незаметно.
        """
        try:
            await warn_about_basic_role(self._db.catalog_driver, self._table_prefix)
        except Exception:
            logger.exception("Basic role check crashed")
