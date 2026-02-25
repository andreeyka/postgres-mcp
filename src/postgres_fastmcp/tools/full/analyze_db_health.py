# ruff: noqa: E501
"""Инструмент analyze_db_health (full)."""

from typing import Annotated

from fastmcp.tools import tool
from pydantic import Field

from postgres_fastmcp.enums import ToolTag
from postgres_fastmcp.providers.health_provider import HealthServiceProvider
from postgres_fastmcp.services.health.service import HealthService
from postgres_fastmcp.tools.constants import HEALTH_TYPE_VALUES


_DESC = (
    "Analyzes database health across multiple dimensions. "
    "Input: health_type (optional, default: 'all') - single check or comma-separated list: "
    "'index' (invalid, duplicate, bloated, unused indexes), "
    "'connection' (connection count and utilization), "
    "'vacuum' (vacuum health for transaction ID wraparound), "
    "'sequence' (sequences at risk of exceeding maximum value), "
    "'replication' (replication lag and slots), "
    "'buffer' (buffer cache hit rates), "
    "'constraint' (invalid constraints), "
    "'all' (runs all checks). "
    "Output: Detailed health report with issues found and recommendations. "
    "IMPORTANT: Use this tool regularly to monitor database health and identify potential problems. "
    "Each health check provides actionable recommendations. "
    "Example: Use 'all' for comprehensive health check, or specify individual checks like 'index,connection' for targeted analysis."
)


@tool(
    description=_DESC,
    tags={ToolTag.FULL},
    annotations={
        "title": "Analyze Database Health",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
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
