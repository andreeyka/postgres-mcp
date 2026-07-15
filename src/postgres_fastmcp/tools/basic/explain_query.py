"""Тул explain_query — план выполнения SQL."""

from typing import Annotated, Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import Field

from postgres_fastmcp.app_context import get_db
from postgres_fastmcp.services.explain.service import ExplainService


async def explain_query(
    sql: Annotated[str, Field(description="SQL query to explain.")],
    *,
    analyze: Annotated[
        bool,
        Field(default=False, description="If True, actually run the query for real stats."),
    ] = False,
    hypothetical_indexes: Annotated[
        list[dict[str, Any]] | None,
        Field(default=None, description="Optional hypothetical indexes to simulate via hypopg."),
    ] = None,
    ctx: Context = CurrentContext(),
) -> str:
    """План выполнения SQL: plain / analyze / гипотетические индексы."""
    db = get_db(ctx)
    service = ExplainService(db=db)
    return await service.explain(sql, analyze=analyze, hypothetical_indexes=hypothetical_indexes)
