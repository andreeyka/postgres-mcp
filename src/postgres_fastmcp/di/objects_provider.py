"""Провайдер ObjectsService."""

from fastmcp.dependencies import Depends

from postgres_fastmcp.di.db_access_provider import DbAccessServiceProvider
from postgres_fastmcp.services.db_access_service import DbAccessService
from postgres_fastmcp.services.objects.service import ObjectsService


def get_objects_service(db: DbAccessService = DbAccessServiceProvider) -> ObjectsService:
    """Получить сервис для работы с объектами БД.

    Returns:
        Экземпляр ObjectsService.
    """
    return ObjectsService(db)


ObjectsServiceProvider = Depends(get_objects_service)
