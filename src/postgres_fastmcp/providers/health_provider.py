"""Провайдер HealthService."""

from fastmcp.dependencies import Depends

from postgres_fastmcp.providers.db_access_provider import DbAccessServiceProvider
from postgres_fastmcp.services.db_access_service import DbAccessService
from postgres_fastmcp.services.health.service import HealthService


def get_health_service(db: DbAccessService = DbAccessServiceProvider) -> HealthService:
    """Получить сервис для проверки здоровья БД.

    Args:
        db: Сервис доступа к БД.

    Returns:
        Экземпляр HealthService.
    """
    return HealthService(db)


HealthServiceProvider = Depends(get_health_service)
