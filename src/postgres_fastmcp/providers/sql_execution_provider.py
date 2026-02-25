"""Провайдер SqlExecutionService."""

from fastmcp.dependencies import Depends

from postgres_fastmcp.di.db_access_provider import DbAccessServiceProvider
from postgres_fastmcp.services.db_access_service import DbAccessService
from postgres_fastmcp.services.sql_execution.service import SqlExecutionService


def get_sql_execution_service(
    db: DbAccessService = DbAccessServiceProvider,
) -> SqlExecutionService:
    """Получить сервис для выполнения SQL.

    Args:
        db: Сервис доступа к БД.

    Returns:
        Экземпляр SqlExecutionService.
    """
    return SqlExecutionService(db)


SqlExecutionServiceProvider = Depends(get_sql_execution_service)
