"""Тул analyze_workload_indexes — рекомендации индексов под нагрузку."""

from typing import Annotated, Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import Field

from postgres_fastmcp.app_context import get_db
from postgres_fastmcp.enums import AnalysisMethod
from postgres_fastmcp.services.index.service import IndexAnalysisService


async def analyze_workload_indexes(
    max_index_size_mb: Annotated[
        int,
        Field(default=10000, ge=1, description="Max recommended index size (MB)."),
    ] = 10000,
    method: Annotated[
        AnalysisMethod,
        Field(default="dta", description="Analysis method: 'dta' or 'llm'."),
    ] = "dta",
    ctx: Context = CurrentContext(),
) -> dict[str, Any]:
    """Рекомендовать индексы по агрегированной нагрузке БД."""
    db = get_db(ctx)
    service = IndexAnalysisService(db=db)
    return await service.analyze_workload_indexes(
        method=method,
        max_index_size_mb=max_index_size_mb,
        ctx=ctx,
    )
