"""Top queries MCP tools registration."""

from typing import Annotated

from fastmcp.server.providers import LocalProvider
from fastmcp.tools.tool import ToolResult
from pydantic import Field

from postgres_fastmcp.common.errors import BaseApplicationError
from postgres_fastmcp.di.top_queries_provider import TopQueriesServiceProvider
from postgres_fastmcp.enums import ToolTag
from postgres_fastmcp.services.top_queries.service import TopQueriesService
from postgres_fastmcp.tools.common import ToolDescriptions


def register_top_queries_tools(provider: LocalProvider, descriptions: ToolDescriptions) -> None:
    """Register top queries tools."""

    @provider.tool(
        description=descriptions.get_top_queries,
        tags={ToolTag.FULL},
        annotations={"readOnlyHint": True},
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
    ) -> ToolResult:
        """Report slowest or most resource-intensive queries."""
        try:
            return ToolResult(content=await top_queries_service.get_top_queries(sort_by=sort_by, limit=limit))
        except BaseApplicationError as exc:
            return ToolResult(content=f"Error: {exc}")
        except Exception as exc:  # pragma: no cover - defensive fallback
            return ToolResult(content=f"Error: {exc}")
