# ruff: noqa: E501
"""Инструмент analyze_workload_indexes (full)."""

from typing import Annotated, Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from fastmcp.tools import tool
from pydantic import Field

from postgres_fastmcp.enums import AnalysisMethod, ToolTag
from postgres_fastmcp.providers.index_analysis_provider import IndexAnalysisServiceProvider
from postgres_fastmcp.services.index.service import IndexAnalysisService
from postgres_fastmcp.tools.constants import PG_STAT_STATEMENTS


_DESC = (
    "Analyze frequently executed queries in the database and recommend optimal indexes. "
    f"Uses data from the '{PG_STAT_STATEMENTS}' extension to identify slow queries and suggest indexes. "
    "Input: max_index_size_mb (optional, default: 10000) - maximum size for recommended indexes in MB, "
    "method (optional, default: 'dta') - 'dta' for Database Tuning Advisor algorithm or 'llm' for LLM-based optimization. "
    "Output: List of recommended indexes with estimated benefits, sizes, and SQL statements to create them. "
    f"\n\nIMPORTANT: The '{PG_STAT_STATEMENTS}' extension must be enabled and the database must have query statistics. "
    "This tool analyzes actual query patterns from pg_stat_statements, not hypothetical queries. "
    "Use analyze_query_indexes if you want to analyze specific queries instead of the workload. "
    "Example: Use this tool periodically to identify missing indexes that would improve query performance."
)


@tool(
    description=_DESC,
    tags={ToolTag.FULL},
    annotations={
        "title": "Analyze Workload Indexes",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def analyze_workload_indexes(
    max_index_size_mb: Annotated[
        int,
        Field(
            default=10000,
            description=(
                "Maximum index size in megabytes as integer value for limiting recommended index sizes "
                "(default 10000, must be greater than 0)"
            ),
            ge=1,
        ),
    ] = 10000,
    method: Annotated[
        AnalysisMethod,
        Field(
            default="dta",
            description=(
                "Analysis method as string value: 'dta' for Database Tuning Advisor algorithm "
                "or 'llm' for LLM-based optimization"
            ),
        ),
    ] = "dta",
    ctx: Context = CurrentContext(),
    index_analysis_service: IndexAnalysisService = IndexAnalysisServiceProvider,
) -> dict[str, Any]:
    """Проанализировать нагрузку и рекомендовать индексы.

    Returns:
        Рекомендации. FastMCP преобразует в ответ. При ошибке — исключение наружу.
    """
    return await index_analysis_service.analyze_workload_indexes(
        method=method,
        max_index_size_mb=max_index_size_mb,
        ctx=ctx,
    )
