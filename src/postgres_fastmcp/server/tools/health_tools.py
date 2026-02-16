"""Database health MCP tools registration."""

from typing import Annotated

from fastmcp.server.providers import LocalProvider
from fastmcp.tools.tool import ToolResult
from postgres_fastmcp.server.tools.common import ToolDescriptions
from postgres_fastmcp.server.tools.constants import HEALTH_TYPE_VALUES
from pydantic import Field

from postgres_fastmcp.common.errors import BaseApplicationError
from postgres_fastmcp.di.health_provider import HealthServiceProvider
from postgres_fastmcp.enums import ToolTag
from postgres_fastmcp.services.health.service import HealthService


def register_health_tools(provider: LocalProvider, descriptions: ToolDescriptions) -> None:
    """Register database health tools."""

    @provider.tool(
        description=descriptions.analyze_db_health,
        tags={ToolTag.FULL},
        annotations={"readOnlyHint": True},
    )
    async def analyze_db_health(
        health_type: Annotated[
            str,
            Field(
                default="all",
                description=(
                    f"Health check type as string value: single check or comma-separated list. "
                    f"Valid values are: {HEALTH_TYPE_VALUES}. Use 'all' for comprehensive health check, "
                    f"or specify individual checks like 'index,connection' for targeted analysis"
                ),
            ),
        ] = "all",
        health_service: HealthService = HealthServiceProvider,
    ) -> ToolResult:
        """Analyze database health across available dimensions."""
        try:
            return ToolResult(content=await health_service.analyze_db_health(health_type=health_type))
        except BaseApplicationError as exc:
            return ToolResult(content=f"Error: {exc}")
        except Exception as exc:  # pragma: no cover - defensive fallback
            return ToolResult(content=f"Error: {exc}")
