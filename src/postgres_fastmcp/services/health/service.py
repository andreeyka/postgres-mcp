"""Database health analysis service (used only by health module)."""

from postgres_fastmcp.services.db_access_service import DbAccessService
from postgres_fastmcp.services.health.database_health import DatabaseHealthTool


class HealthService:
    """Service for analyzing database health."""

    def __init__(self, db: DbAccessService) -> None:
        """Initialize with database access service."""
        self.db = db

    async def analyze_db_health(self, health_type: str = "all") -> str:
        """Run database health checks for the specified components."""
        health_tool = DatabaseHealthTool(self.db.sql_driver)
        return await health_tool.health(health_type=health_type)
