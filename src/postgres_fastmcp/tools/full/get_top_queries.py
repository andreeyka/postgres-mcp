"""Тул get_top_queries — самые тяжёлые/медленные запросы."""

from typing import Annotated

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import Field

from postgres_fastmcp.enums import TopQueriesSortBy
from postgres_fastmcp.services.top_queries.service import TopQueriesService


async def get_top_queries(
    sort_by: Annotated[
        TopQueriesSortBy,
        Field(
            default="resources",
            description="Ranking criteria: 'total_time', 'mean_time', or 'resources'.",
        ),
    ] = "resources",
    limit: Annotated[int, Field(default=10, ge=1, description="Number of queries to return.")] = 10,
    ctx: Context = CurrentContext(),
) -> str:
    """Топ запросов из pg_stat_statements по выбранному критерию."""
    db = ctx.lifespan_context["db"]
    service = TopQueriesService(db=db)
    return await service.get_top_queries(sort_by=sort_by, limit=limit)
