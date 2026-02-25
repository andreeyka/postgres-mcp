"""Провайдер SchemaService."""

from fastmcp.dependencies import Depends

from postgres_fastmcp.di.db_access_provider import DbAccessServiceProvider
from postgres_fastmcp.services.db_access_service import DbAccessService
from postgres_fastmcp.services.schema.service import SchemaService


def get_schema_service(db: DbAccessService = DbAccessServiceProvider) -> SchemaService:
    """Получить сервис для работы со схемами.

    Args:
        db: Сервис доступа к БД.

    Returns:
        Экземпляр SchemaService.
    """
    return SchemaService(db)


SchemaServiceProvider = Depends(get_schema_service)
