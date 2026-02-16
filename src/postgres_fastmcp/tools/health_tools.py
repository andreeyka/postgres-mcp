"""Регистрация MCP-инструментов проверки состояния БД."""

from typing import Annotated

from fastmcp.server.providers import LocalProvider
from pydantic import Field

from postgres_fastmcp.di.health_provider import HealthServiceProvider
from postgres_fastmcp.enums import ToolTag
from postgres_fastmcp.services.health.service import HealthService
from postgres_fastmcp.tools.common import ToolDescriptions
from postgres_fastmcp.tools.constants import HEALTH_TYPE_VALUES


def register_health_tools(provider: LocalProvider, descriptions: ToolDescriptions) -> None:
    """Зарегистрировать инструменты проверки состояния БД.

    Args:
        provider: Провайдер для регистрации инструментов.
        descriptions: Описания инструментов.
    """

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
    ) -> str:
        """Проверить состояние БД по доступным измерениям.

        Returns:
            Строка с отчётом о состоянии. FastMCP преобразует в ответ. При ошибке — исключение наружу.
        """
        return await health_service.analyze_db_health(health_type=health_type)
