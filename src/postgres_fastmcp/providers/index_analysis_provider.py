"""Провайдер IndexAnalysisService."""

from fastmcp.dependencies import Depends

from postgres_fastmcp.providers.db_access_provider import DbAccessServiceProvider
from postgres_fastmcp.services.db_access_service import DbAccessService
from postgres_fastmcp.services.index.service import IndexAnalysisService


def get_index_analysis_service(
    db: DbAccessService = DbAccessServiceProvider,
) -> IndexAnalysisService:
    """Получить сервис для анализа индексов.

    Args:
        db: Сервис доступа к БД.

    Returns:
        Экземпляр IndexAnalysisService.
    """
    return IndexAnalysisService(db)


IndexAnalysisServiceProvider = Depends(get_index_analysis_service)
