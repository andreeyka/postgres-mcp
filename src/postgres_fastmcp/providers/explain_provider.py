"""Провайдер ExplainService (унифицированный)."""

from fastmcp.dependencies import Depends

from postgres_fastmcp.providers.db_access_provider import DbAccessServiceProvider
from postgres_fastmcp.services.db_access_service import DbAccessService
from postgres_fastmcp.services.explain.service import ExplainService


def get_explain_service(db: DbAccessService = DbAccessServiceProvider) -> ExplainService:
    """Получить унифицированный сервис explain.

    Args:
        db: Сервис доступа к БД.

    Returns:
        Экземпляр ExplainService.
    """
    return ExplainService(db)


ExplainServiceProvider = Depends(get_explain_service)
