"""Lifespan-фабрика: владеет DbAccessService на время жизни сервера."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from postgres_fastmcp.access import EffectiveAccess
from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.context import LifespanContext
from postgres_fastmcp.domains.db_access import DbAccessService


def build_lifespan(
    settings: Settings,
) -> Callable[[Any], AbstractAsyncContextManager[LifespanContext]]:
    """Собрать async context manager, пригодный для FastMCP(lifespan=...).

    Args:
        settings: Настройки приложения. Используются для конструирования DbAccessService
            и пробрасываются в lifespan_context для тулов.

    Returns:
        Async context manager, который при входе создаёт DbAccessService, выдаёт
        ``{"db": <доступ с правами потолка>, "settings": ...}`` и при выходе закрывает пул подключений.
    """

    @asynccontextmanager
    async def lifespan(server: Any) -> AsyncIterator[LifespanContext]:  # noqa: ARG001, ANN401
        db = DbAccessService(settings.database)
        ceiling = EffectiveAccess(settings.database.access_mode, write_mode=settings.database.write_mode)
        context: LifespanContext = {"db": db.view(ceiling), "settings": settings}
        try:
            yield context
        finally:
            await db.close()

    return lifespan
