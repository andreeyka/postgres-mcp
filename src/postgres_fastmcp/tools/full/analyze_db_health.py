"""Тул analyze_db_health — комплексная проверка состояния БД."""

from typing import Annotated

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import Field

from postgres_fastmcp.services.health.service import HealthService
from postgres_fastmcp.tools.constants import HEALTH_TYPE_VALUES


async def analyze_db_health(
    health_type: Annotated[
        str,
        Field(
            default="all",
            description=f"Single check or comma-separated list. Valid: {HEALTH_TYPE_VALUES}.",
        ),
    ] = "all",
    ctx: Context = CurrentContext(),
) -> str:
    """Запустить набор health-проверок и вернуть отчёт."""
    db = ctx.lifespan_context["db"]
    service = HealthService(db=db)
    return await service.analyze_db_health(health_type=health_type)
