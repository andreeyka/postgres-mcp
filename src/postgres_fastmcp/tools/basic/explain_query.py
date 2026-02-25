# ruff: noqa: E501
"""Обработчик explain_query (фиксированное описание)."""

from typing import Annotated, Any

from fastmcp.tools import tool
from pydantic import Field

from postgres_fastmcp.common.errors import ExplainAnalyzeWithHypotheticalError
from postgres_fastmcp.enums import ToolTag
from postgres_fastmcp.providers.explain_provider import (
    ExplainAnalyzeServiceProvider,
    ExplainHypotheticalServiceProvider,
    ExplainPlainServiceProvider,
)
from postgres_fastmcp.services.explain.service import ExplainService


_DESC_EXPLAIN_QUERY = (
    "Explains the execution plan for a SQL query, showing how PostgreSQL will execute it "
    "and provides detailed cost estimates, index usage, and join strategies. "
    "Input: sql (required) - a SQL SELECT query, analyze (optional, default: False) - if True, actually runs the query "
    "to show real execution statistics instead of estimates, hypothetical_indexes (optional) - list of hypothetical indexes to test. "
    "Output: Detailed execution plan with costs, actual times (if analyze=True), and recommendations. "
    "\n\nIMPORTANT: Use this tool to optimize queries before executing them. "
    "If analyze=True, the query will actually run - use with caution on large tables. "
    "You can test hypothetical indexes without creating them using the hypothetical_indexes parameter. "
    "Example workflow: 1) explain_query to check plan, 2) optimize query or add indexes, 3) execute_sql to run the final query."
)
_DESC_HYPOTHETICAL_INDEXES = (
    "A list of hypothetical indexes to simulate when explaining a query. "
    "Each index must be a dictionary with these keys:\n"
    "    - 'table': The table name to add the index to (e.g., 'users')\n"
    "    - 'columns': List of column names to include in the index "
    "(e.g., ['email'] or ['last_name', 'first_name'])\n"
    "    - 'using': Optional index method (default: 'btree', other options include 'hash', 'gist', 'gin', etc.)\n\n"
    "Examples: [\n"
    '    {"table": "users", "columns": ["email"], "using": "btree"},\n'
    '    {"table": "orders", "columns": ["user_id", "created_at"]}\n'
    "]"
    "\n\nIMPORTANT: Hypothetical indexes are created using the hypopg extension and are automatically cleaned up. "
    "They allow you to test index impact without actually creating indexes. "
    "If there are no hypothetical indexes to test, pass an empty list []."
)


@tool(
    description=_DESC_EXPLAIN_QUERY,
    tags={ToolTag.BASIC},
    annotations={
        "title": "Explain Query",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
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
        Field(default=None, description=_DESC_HYPOTHETICAL_INDEXES),
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
