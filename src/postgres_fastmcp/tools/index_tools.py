"""Регистрация MCP-инструментов анализа индексов."""

from typing import Annotated, Literal

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from fastmcp.server.providers import LocalProvider
from fastmcp.tools.tool import ToolResult
from pydantic import Field

from postgres_fastmcp.common.errors import BaseApplicationError
from postgres_fastmcp.di.index_analysis_provider import DtaIndexAnalysisServiceProvider, LlmIndexAnalysisServiceProvider
from postgres_fastmcp.enums import ToolTag
from postgres_fastmcp.services.index.index_opt_base import MAX_NUM_INDEX_TUNING_QUERIES
from postgres_fastmcp.services.index.service import IndexAnalysisService
from postgres_fastmcp.tools.common import ToolDescriptions


def register_index_tools(provider: LocalProvider, descriptions: ToolDescriptions) -> None:
    """Зарегистрировать инструменты анализа индексов.

    Args:
        provider: Провайдер для регистрации инструментов.
        descriptions: Описания инструментов.
    """

    @provider.tool(
        description=descriptions.analyze_workload_indexes,
        tags={ToolTag.FULL},
        annotations={"readOnlyHint": True},
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
            Literal["dta", "llm"],
            Field(
                default="dta",
                description=(
                    "Analysis method as string value: 'dta' for Database Tuning Advisor algorithm "
                    "or 'llm' for LLM-based optimization"
                ),
            ),
        ] = "dta",
        ctx: Context = CurrentContext(),  # noqa: B008
        dta_index_analysis_service: IndexAnalysisService = DtaIndexAnalysisServiceProvider,
        llm_index_analysis_service: IndexAnalysisService = LlmIndexAnalysisServiceProvider,
    ) -> ToolResult:
        """Проанализировать нагрузку и рекомендовать индексы.

        Returns:
            ToolResult с текстом рекомендаций или сообщением об ошибке.
        """
        try:
            index_analysis_service = dta_index_analysis_service if method == "dta" else llm_index_analysis_service
            content = await index_analysis_service.analyze_workload_indexes(
                max_index_size_mb=max_index_size_mb,
                ctx=ctx,
            )
            return ToolResult(content=content)
        except BaseApplicationError as exc:
            return ToolResult(content=f"Error: {exc}")
        except Exception as exc:  # pragma: no cover - defensive fallback
            return ToolResult(content=f"Error: {exc}")

    @provider.tool(
        description=descriptions.analyze_query_indexes,
        tags={ToolTag.FULL},
        annotations={"readOnlyHint": True},
    )
    async def analyze_query_indexes(  # noqa: PLR0913
        queries: Annotated[
            list[str],
            Field(
                description=(
                    f"List of SQL query strings to analyze (up to {MAX_NUM_INDEX_TUNING_QUERIES} queries allowed)"
                )
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
            Literal["dta", "llm"],
            Field(
                default="dta",
                description=(
                    "Analysis method as string value: 'dta' for Database Tuning Advisor algorithm "
                    "or 'llm' for LLM-based optimization"
                ),
            ),
        ] = "dta",
        ctx: Context = CurrentContext(),  # noqa: B008
        dta_index_analysis_service: IndexAnalysisService = DtaIndexAnalysisServiceProvider,
        llm_index_analysis_service: IndexAnalysisService = LlmIndexAnalysisServiceProvider,
    ) -> ToolResult:
        """Проанализировать указанные запросы и рекомендовать индексы.

        Returns:
            ToolResult с текстом рекомендаций или сообщением об ошибке.
        """
        try:
            index_analysis_service = dta_index_analysis_service if method == "dta" else llm_index_analysis_service
            content = await index_analysis_service.analyze_query_indexes(
                queries=queries,
                max_index_size_mb=max_index_size_mb,
                ctx=ctx,
            )
            return ToolResult(content=content)
        except BaseApplicationError as exc:
            return ToolResult(content=f"Error: {exc}")
        except Exception as exc:  # pragma: no cover - defensive fallback
            return ToolResult(content=f"Error: {exc}")
