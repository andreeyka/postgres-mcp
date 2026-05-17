# ruff: noqa: E501, S608
"""Инструмент analyze_query_indexes (full)."""

from typing import Annotated, Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from fastmcp.tools import tool
from pydantic import Field

from postgres_fastmcp.enums import AnalysisMethod, ToolTag
from postgres_fastmcp.providers.index_analysis_provider import IndexAnalysisServiceProvider
from postgres_fastmcp.services.index.index_opt_base import MAX_NUM_INDEX_TUNING_QUERIES
from postgres_fastmcp.services.index.service import IndexAnalysisService


_DESC = (
    f"Analyze a list of SQL queries (up to {MAX_NUM_INDEX_TUNING_QUERIES}) and recommend optimal indexes. "
    "Input: queries (required) - list of SQL SELECT query strings to analyze, "
    "max_index_size_mb (optional, default: 10000) - maximum size for recommended indexes in MB, "
    "method (optional, default: 'dta') - 'dta' for Database Tuning Advisor algorithm or 'llm' for LLM-based optimization. "
    "Output: List of recommended indexes with estimated benefits, sizes, and SQL statements to create them. "
    "\n\nIMPORTANT: Provide actual SQL queries that you want to optimize. "
    "The 'dta' method uses cost-based analysis with hypothetical indexes (requires hypopg extension). "
    "The 'llm' method uses LLM to analyze query patterns and suggest indexes. "
    "Use analyze_workload_indexes if you want to analyze the entire database workload instead of specific queries. "
    "Example Input: queries=['SELECT * FROM users WHERE email = $1', 'SELECT * FROM orders WHERE user_id = $1 AND status = $2']"
)


@tool(
    description=_DESC,
    tags={ToolTag.FULL},
    annotations={
        "title": "Analyze Query Indexes",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def analyze_query_indexes(
    queries: Annotated[
        list[str],
        Field(
            description=(f"List of SQL query strings to analyze (up to {MAX_NUM_INDEX_TUNING_QUERIES} queries allowed)")
        ),
    ],
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
    """Проанализировать указанные запросы и рекомендовать индексы.

    Returns:
        Рекомендации. FastMCP преобразует в ответ. При ошибке — исключение наружу.
    """
    return await index_analysis_service.analyze_query_indexes(
        method=method,
        queries=queries,
        max_index_size_mb=max_index_size_mb,
        ctx=ctx,
    )
