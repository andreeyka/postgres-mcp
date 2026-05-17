"""Lifespan-фабрика: владеет DbAccessService на время жизни сервера."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from postgres_fastmcp.config import Settings
from postgres_fastmcp.services.db_access_service import DbAccessService


def build_lifespan(
    settings: Settings,
) -> Callable[[Any], AbstractAsyncContextManager[dict[str, Any]]]:
    """Собрать async context manager, пригодный для FastMCP(lifespan=...).

    Args:
        settings: Настройки приложения. Используются для конструирования DbAccessService
            и пробрасываются в lifespan_context для тулов.

    Returns:
        Async context manager, который при входе создаёт DbAccessService, выдаёт
        ``{"db": ..., "settings": ...}`` и при выходе закрывает пул подключений.
    """

    @asynccontextmanager
    async def lifespan(server: Any) -> AsyncIterator[dict[str, Any]]:  # noqa: ARG001, ANN401
        db = DbAccessService(settings.database)
        try:
            yield {"db": db, "settings": settings}
        finally:
            await db.close()

    return lifespan
