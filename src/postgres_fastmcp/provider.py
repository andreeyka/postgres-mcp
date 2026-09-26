"""PostgresProvider: источник девяти тулов одной базы для любого FastMCP-сервера.

Провайдер владеет DbAccessService (пул открывается лениво при первом запросе,
закрывается в ``lifespan``) и на каждый вызов тула считает права запроса:
токен текущего запроса -> резолвер -> ``DbAccessService.view(права)``.
"""

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
from postgres_fastmcp.shared.enums import AccessMode, ToolTag
from postgres_fastmcp.tools.definitions import ToolSet
from postgres_fastmcp.tools.registry import register_tools


class PostgresProvider(LocalProvider):
    """Тулы одной базы PostgreSQL с правами не выше потолка из ``database``."""

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
            access_policy: Политика сужения прав по claim токена (без resolver).
            access_resolver: Свой резолвер токен -> права; приоритетнее access_policy.
                Результат всегда ограничивается потолком. Вызывается на каждый list_tools
                для каждого full-тула, на каждой проверке доступа full-тула и на каждом
                вызове get_db — должен быть дешёвым и детерминированным.

        Raises:
            ValueError: Если access_policy.enforced=True, а access_resolver не задан.
        """
        super().__init__(on_duplicate="error")
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

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """Закрыть пул подключений при остановке сервера."""
        try:
            yield
        finally:
            await self._db.close()
