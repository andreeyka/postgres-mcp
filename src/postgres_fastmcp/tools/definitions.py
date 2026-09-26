"""Все MCP-тулы сервера: тонкие функции над доменными сервисами.

Каждая функция достаёт DbAccessService из lifespan-контекста и делегирует
домену; описания и аннотации задаются при регистрации в ``tools.registry``.
Docstring функций тулов на английском: FastMCP может показать их агенту.
Тулы, которые возвращают строки, принимают ``output`` и отдают ToolResult
через ``tools.rendering``.
"""

from typing import Annotated, Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from fastmcp.tools import ToolResult
from pydantic import Field

from postgres_fastmcp.app.context import get_db
from postgres_fastmcp.domains import querying, top_queries
from postgres_fastmcp.domains.catalog.service import CatalogService
from postgres_fastmcp.domains.explain.service import ExplainService
from postgres_fastmcp.domains.health.database_health import DatabaseHealthAnalyzer, HealthType
from postgres_fastmcp.domains.index_tuning.service import IndexAnalysisService
from postgres_fastmcp.domains.querying import SUCCESS_NO_ROWS
from postgres_fastmcp.shared.errors import ObjectNotFoundError
from postgres_fastmcp.tools.params import (
    HealthTypesParam,
    IndexQueriesParam,
    ObjectTypeParam,
    OutputParam,
    TopQueriesLimitParam,
    TopQueriesSortByParam,
)
from postgres_fastmcp.tools.rendering import rows_result, sections_result


# Поля заголовка, которые get_object_details добавляет сам, без данных каталога.
_TOOL_HEADER_KEYS = frozenset({"schema", "name", "type"})


async def execute_sql(
    sql: Annotated[
        str,
        Field(description="SQL statement to execute, e.g. 'SELECT id, name FROM users WHERE active LIMIT 50'."),
    ],
    output: OutputParam = "table",
    ctx: Context = CurrentContext(),
) -> ToolResult:
    """Execute a SQL statement and return the result rows."""
    rows = await querying.execute_sql(get_db(ctx), sql)
    if rows is None:
        return rows_result([], output, title=SUCCESS_NO_ROWS)
    return rows_result(rows, output)


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
    """Show the execution plan of a SQL query: plain, analyze or with hypothetical indexes."""
    service = ExplainService(db=get_db(ctx))
    return await service.explain(sql, analyze=analyze, hypothetical_indexes=hypothetical_indexes)


async def list_objects(
    schema_name: Annotated[str, Field(description="Schema name to inspect.")],
    object_type: ObjectTypeParam = "table",
    output: OutputParam = "table",
    ctx: Context = CurrentContext(),
) -> ToolResult:
    """List objects of the given kind in a schema."""
    service = CatalogService(db=get_db(ctx))
    return rows_result(await service.list_objects(schema_name=schema_name, object_type=object_type), output)


async def get_object_details(
    schema_name: Annotated[str, Field(description="Schema name.")],
    object_name: Annotated[str, Field(description="Object name.")],
    object_type: ObjectTypeParam = "table",
    output: OutputParam = "table",
    ctx: Context = CurrentContext(),
) -> ToolResult:
    """Show object details: columns, constraints, indexes and other metadata."""
    service = CatalogService(db=get_db(ctx))
    details = await service.get_object_details(
        schema_name=schema_name, object_name=object_name, object_type=object_type
    )
    header: dict[str, Any] = {"schema": schema_name, "name": object_name, "type": object_type}
    sections: dict[str, list[dict[str, Any]]] = {}
    for key, value in details.items():
        if isinstance(value, list):
            sections[key] = value
        elif isinstance(value, dict):
            header.update(value)
        else:
            header[key] = value
    # Каталог не бросает на отсутствующий объект: таблица приходит с пустыми разделами,
    # последовательность и расширение — пустым словарём. Кроме полей самого тула ничего нет — объекта нет.
    if header.keys() <= _TOOL_HEADER_KEYS and not any(sections.values()):
        raise ObjectNotFoundError(header["schema"], object_name, object_type)
    return sections_result(sections, output, header=header)


async def list_schemas(output: OutputParam = "table", ctx: Context = CurrentContext()) -> ToolResult:
    """List database schemas."""
    service = CatalogService(db=get_db(ctx))
    return rows_result(await service.list_schemas(), output)


async def analyze_db_health(
    health_type: HealthTypesParam = (HealthType.ALL,),
    ctx: Context = CurrentContext(),
) -> str:
    """Run database health checks and return a text report."""
    health_tool = DatabaseHealthAnalyzer(get_db(ctx).sql_driver)
    return await health_tool.health(health_type=",".join(health_type))


async def get_top_queries(
    sort_by: TopQueriesSortByParam = "resources",
    limit: TopQueriesLimitParam = 10,
    output: OutputParam = "table",
    ctx: Context = CurrentContext(),
) -> ToolResult:
    """Report top queries from pg_stat_statements by the chosen criteria."""
    rows = await top_queries.get_top_queries(get_db(ctx), sort_by=sort_by, limit=limit)
    return rows_result(rows, output)


async def analyze_query_indexes(
    queries: IndexQueriesParam,
    max_index_size_mb: Annotated[
        int,
        Field(default=10000, ge=1, description="Max recommended index size (MB)."),
    ] = 10000,
    ctx: Context = CurrentContext(),
) -> dict[str, Any]:
    """Recommend indexes for a list of queries (cost-based DTA, requires hypopg)."""
    service = IndexAnalysisService(db=get_db(ctx))
    return await service.analyze_query_indexes(queries=queries, max_index_size_mb=max_index_size_mb)


async def analyze_workload_indexes(
    max_index_size_mb: Annotated[
        int,
        Field(default=10000, ge=1, description="Max recommended index size (MB)."),
    ] = 10000,
    ctx: Context = CurrentContext(),
) -> dict[str, Any]:
    """Recommend indexes for the aggregated database workload (cost-based DTA, requires hypopg)."""
    service = IndexAnalysisService(db=get_db(ctx))
    return await service.analyze_workload_indexes(max_index_size_mb=max_index_size_mb)
