# ruff: noqa: E501
"""Инструмент get_top_queries (full)."""

from typing import Annotated

from fastmcp.tools import tool
from pydantic import Field

from postgres_fastmcp.enums import ToolTag
from postgres_fastmcp.providers.top_queries_provider import TopQueriesServiceProvider
from postgres_fastmcp.services.top_queries.service import TopQueriesService
from postgres_fastmcp.tools.constants import PG_STAT_STATEMENTS


_DESC = (
    f"Reports the slowest or most resource-intensive queries using data from the '{PG_STAT_STATEMENTS}' extension. "
    "Input: sort_by (optional, default: 'resources') - ranking criteria: "
    "'total_time' (total execution time across all calls), "
    "'mean_time' (average execution time per call), "
    "'resources' (resource-intensive queries based on I/O, WAL, and execution time). "
    "limit (optional, default: 10) - number of queries to return. "
    "Output: List of top queries with execution statistics, resource usage, and query text. "
    f"\n\nIMPORTANT: The '{PG_STAT_STATEMENTS}' extension must be enabled. "
    "Use this tool to identify slow queries that need optimization. "
    "After identifying slow queries, use explain_query to understand why they're slow, "
    "then use analyze_query_indexes or analyze_workload_indexes to get index recommendations. "
    "Example workflow: 1) get_top_queries to find slow queries, 2) explain_query to analyze plan, 3) analyze_query_indexes to get recommendations."
)


@tool(
    description=_DESC,
    tags={ToolTag.FULL},
    annotations={
        "title": "Get Top Queries",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def get_top_queries(
    sort_by: Annotated[
        str,
        Field(
            default="resources",
            description=(
                "Ranking criteria as string value: 'total_time' for total execution time across all calls, "
                "'mean_time' for mean execution time per call, or 'resources' for resource-intensive queries "
                "based on I/O, WAL, and execution time"
            ),
        ),
    ] = "resources",
    limit: Annotated[
        int,
        Field(
            default=10,
            description=(
                "Number of queries to return as integer value when ranking based on mean_time or total_time "
                "(default 10, must be greater than 0)"
            ),
            ge=1,
        ),
    ] = 10,
    top_queries_service: TopQueriesService = TopQueriesServiceProvider,
) -> str:
    """Отчёт по самым медленным или наиболее ресурсоёмким запросам.

    Returns:
        Строка с отчётом. FastMCP преобразует в ответ. При ошибке — исключение наружу.
    """
    return await top_queries_service.get_top_queries(sort_by=sort_by, limit=limit)
