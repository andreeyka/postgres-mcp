"""Провайдер DbAccessService — получает db из lifespan context."""

from typing import cast

from fastmcp.dependencies import CurrentContext, Depends
from fastmcp.server.context import Context

from postgres_fastmcp.services.db_access_service import DbAccessService


def get_db_access(ctx: Context = CurrentContext()) -> DbAccessService:  # noqa: B008
    """Получить DbAccessService из lifespan context.

    Args:
        ctx: Контекст MCP (lifespan context с ключом db).

    Returns:
        Экземпляр DbAccessService.
    """
    db = ctx.lifespan_context.get("db")
    if db is None:
        msg = "Database access service not available"
        raise RuntimeError(msg)
    return cast("DbAccessService", db)


DbAccessServiceProvider = Depends(get_db_access)
