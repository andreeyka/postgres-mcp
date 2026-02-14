"""Сервис анализа состояния базы данных (используется только в модуле здоровья)."""

from postgres_fastmcp.services.db_access_service import DbAccessService
from postgres_fastmcp.services.health.database_health import DatabaseHealthTool


class HealthService:
    """Сервис для анализа состояния базы данных."""

    def __init__(self, db: DbAccessService) -> None:
        """Инициализация с сервисом доступа к базе данных."""
        self.db = db

    async def analyze_db_health(self, health_type: str = "all") -> str:
        """Запуск проверок состояния базы данных для указанных компонентов."""
        health_tool = DatabaseHealthTool(self.db.sql_driver)
        return await health_tool.health(health_type=health_type)
