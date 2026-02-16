"""Регистрация MCP-инструментов объяснения планов (EXPLAIN)."""

from typing import Annotated, Any

from fastmcp.server.providers import LocalProvider
from pydantic import Field

from postgres_fastmcp.common.errors import ExplainAnalyzeWithHypotheticalError
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
    """Зарегистрировать инструменты объяснения планов (plain / analyze / hypothetical) в провайдере.

    Args:
        provider: Провайдер для регистрации инструментов.
        descriptions: Описания инструментов.
    """

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
    ) -> str:
        """Объяснить план выполнения SQL-запроса.

        Returns:
            Строка с планом выполнения. FastMCP преобразует в ответ. При ошибке — исключение наружу.
        """
        if analyze and hypothetical_indexes:
            raise ExplainAnalyzeWithHypotheticalError

        if hypothetical_indexes:
            service = explain_hypothetical_service
        elif analyze:
            service = explain_analyze_service
        else:
            service = explain_plain_service

        return await service.explain_query(
            sql,
            hypothetical_indexes=hypothetical_indexes or [],
        )
