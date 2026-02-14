"""Explain MCP tools registration."""

from typing import Annotated, Any

from fastmcp.server.providers import LocalProvider
from fastmcp.tools.tool import ToolResult
from pydantic import Field

from postgres_fastmcp.common.errors import BaseApplicationError, ExplainAnalyzeWithHypotheticalError
from postgres_fastmcp.di.explain_provider import (
    ExplainAnalyzeServiceProvider,
    ExplainHypotheticalServiceProvider,
    ExplainPlainServiceProvider,
)
from postgres_fastmcp.enums import ToolTag
from postgres_fastmcp.services.explain.service import ExplainService
from postgres_fastmcp.tools.common import ToolDescriptions
from postgres_fastmcp.tools.descriptions import DESC_HYPOTHETICAL_INDEXES


def register_explain_tools(provider: LocalProvider, descriptions: ToolDescriptions) -> None:
    """Register explain tools (plain / analyze / hypothetical) on the provider."""

    @provider.tool(
        description=descriptions.explain_query,
        tags={ToolTag.BASIC},
        annotations={"readOnlyHint": True},
    )
    async def explain_query(  # noqa: PLR0913
        sql: Annotated[
            str,
            Field(description="SQL query as string value to explain and analyze execution plan"),
        ],
        *,
        analyze: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    "Analyze flag as boolean value: when True, actually runs the query to show real "
                    "execution statistics instead of estimates. Takes longer but provides more accurate "
                    "information. Cannot be used together with hypothetical_indexes"
                ),
            ),
        ] = False,
        hypothetical_indexes: Annotated[
            list[dict[str, Any]] | None,
            Field(default=None, description=DESC_HYPOTHETICAL_INDEXES),
        ] = None,
        explain_plain_service: ExplainService = ExplainPlainServiceProvider,
        explain_analyze_service: ExplainService = ExplainAnalyzeServiceProvider,
        explain_hypothetical_service: ExplainService = ExplainHypotheticalServiceProvider,
    ) -> ToolResult:
        """Explain SQL query execution plan."""
        try:
            if analyze and hypothetical_indexes:
                raise ExplainAnalyzeWithHypotheticalError  # noqa: TRY301

            if hypothetical_indexes:
                service = explain_hypothetical_service
            elif analyze:
                service = explain_analyze_service
            else:
                service = explain_plain_service

            content = await service.explain_query(
                sql,
                hypothetical_indexes=hypothetical_indexes or [],
            )
            return ToolResult(content=content)
        except BaseApplicationError as exc:
            return ToolResult(content=f"Error: {exc}")
        except Exception as exc:  # pragma: no cover - defensive fallback
            return ToolResult(content=f"Error: {exc}")
