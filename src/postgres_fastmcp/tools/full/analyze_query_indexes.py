"""Тул analyze_query_indexes — рекомендации индексов под список запросов."""

from typing import Annotated, Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import Field

from postgres_fastmcp.app_context import get_db
from postgres_fastmcp.enums import AnalysisMethod
from postgres_fastmcp.services.index.index_opt_base import MAX_NUM_INDEX_TUNING_QUERIES
from postgres_fastmcp.services.index.service import IndexAnalysisService


async def analyze_query_indexes(
    queries: Annotated[
        list[str],
        Field(description=f"SQL queries to analyze (up to {MAX_NUM_INDEX_TUNING_QUERIES})."),
    ],
    max_index_size_mb: Annotated[
        int,
        Field(default=10000, ge=1, description="Max recommended index size (MB)."),
    ] = 10000,
    method: Annotated[
        AnalysisMethod,
        Field(
            default="dta",
            description="Analysis method: 'dta' (cost-based) or 'llm' (LLM-driven).",
        ),
    ] = "dta",
    ctx: Context = CurrentContext(),
) -> dict[str, Any]:
    """Рекомендовать индексы под список запросов."""
    db = get_db(ctx)
    service = IndexAnalysisService(db=db)
    return await service.analyze_query_indexes(
        method=method,
        queries=queries,
        max_index_size_mb=max_index_size_mb,
        ctx=ctx,
    )
