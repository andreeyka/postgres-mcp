"""Провайдер TopQueriesService."""

from fastmcp.dependencies import Depends

from postgres_fastmcp.di.db_access_provider import DbAccessServiceProvider
from postgres_fastmcp.services.db_access_service import DbAccessService
from postgres_fastmcp.services.top_queries.service import TopQueriesService


def get_top_queries_service(
    db: DbAccessService = DbAccessServiceProvider,
) -> TopQueriesService:
    """Получить сервис для топ запросов.

    Args:
        db: Сервис доступа к БД.

    Returns:
        Экземпляр TopQueriesService.
    """
    return TopQueriesService(db)


TopQueriesServiceProvider = Depends(get_top_queries_service)
