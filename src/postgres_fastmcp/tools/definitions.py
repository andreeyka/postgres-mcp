"""Все MCP-тулы сервера: тонкие функции над доменными сервисами.

Каждая функция достаёт DbAccessService из lifespan-контекста и делегирует
домену; описания и аннотации задаются при регистрации в ``tools.registry``.
"""

from typing import Annotated, Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import Field

from postgres_fastmcp.app.context import get_db
from postgres_fastmcp.domains import querying, top_queries
from postgres_fastmcp.domains.catalog.service import CatalogService
from postgres_fastmcp.domains.explain.service import ExplainService
from postgres_fastmcp.domains.health.database_health import DatabaseHealthAnalyzer, HealthType
from postgres_fastmcp.domains.index_tuning.index_opt_base import MAX_NUM_INDEX_TUNING_QUERIES
from postgres_fastmcp.domains.index_tuning.service import IndexAnalysisService
from postgres_fastmcp.shared.enums import AnalysisMethod, ObjectType, TopQueriesSortBy


HEALTH_TYPE_VALUES = ", ".join(sorted(ht.value for ht in HealthType))


async def execute_sql(
    sql: Annotated[str, Field(description="SQL statement to execute.")],
    ctx: Context = CurrentContext(),
) -> list[dict[str, Any]]:
    """Выполнить SQL-запрос. Область доступа (access_mode) и режим записи (write_mode) берутся из конфигурации БД."""
    return await querying.execute_sql(get_db(ctx), sql)


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
    service = ExplainService(db=get_db(ctx))
    return await service.explain(sql, analyze=analyze, hypothetical_indexes=hypothetical_indexes)


async def list_objects(
    schema_name: Annotated[str, Field(description="Schema name to inspect.")],
    object_type: Annotated[
        ObjectType,
        Field(default="table", description="Object kind: 'table', 'view', 'sequence', 'extension'."),
    ] = "table",
    ctx: Context = CurrentContext(),
) -> list[dict[str, Any]]:
    """Получить список объектов указанного типа в схеме."""
    service = CatalogService(db=get_db(ctx))
    return await service.list_objects(schema_name=schema_name, object_type=object_type)


async def get_object_details(
    schema_name: Annotated[str, Field(description="Schema name.")],
    object_name: Annotated[str, Field(description="Object name.")],
    object_type: Annotated[
        ObjectType,
        Field(default="table", description="Object kind: 'table', 'view', 'sequence', 'extension'."),
    ] = "table",
    ctx: Context = CurrentContext(),
) -> dict[str, Any]:
    """Детали объекта: колонки, констрейнты, индексы и пр."""
    service = CatalogService(db=get_db(ctx))
    return await service.get_object_details(schema_name=schema_name, object_name=object_name, object_type=object_type)


async def list_schemas(ctx: Context = CurrentContext()) -> list[dict[str, Any]]:
    """Список схем БД."""
    service = CatalogService(db=get_db(ctx))
    return await service.list_schemas()


async def analyze_db_health(
    health_type: Annotated[
        str,
        Field(
            default="all",
            description=f"Single check or comma-separated list. Valid: {HEALTH_TYPE_VALUES}.",
        ),
    ] = "all",
    ctx: Context = CurrentContext(),
) -> str:
    """Запустить набор health-проверок и вернуть отчёт."""
    health_tool = DatabaseHealthAnalyzer(get_db(ctx).sql_driver)
    return await health_tool.health(health_type=health_type)


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
    return await top_queries.get_top_queries(get_db(ctx), sort_by=sort_by, limit=limit)


async def analyze_query_indexes(
    queries: Annotated[
        list[str],
        Field(description=f"SQL queries to analyze (up to {MAX_NUM_INDEX_TUNING_QUERIES})."),
    ],
    max_index_size_mb: Annotated[
        int,
        Field(default=10000, ge=1, description="Max recommended index size (MB)."),
    ] = 10000,
    method: Annotated[
        AnalysisMethod,
        Field(
            default="dta",
            description="Analysis method: 'dta' (cost-based) or 'llm' (LLM-driven).",
        ),
    ] = "dta",
    ctx: Context = CurrentContext(),
) -> dict[str, Any]:
    """Рекомендовать индексы под список запросов."""
    service = IndexAnalysisService(db=get_db(ctx))
    return await service.analyze_query_indexes(
        method=method,
        queries=queries,
        max_index_size_mb=max_index_size_mb,
        ctx=ctx,
    )


async def analyze_workload_indexes(
    max_index_size_mb: Annotated[
        int,
        Field(default=10000, ge=1, description="Max recommended index size (MB)."),
    ] = 10000,
    method: Annotated[
        AnalysisMethod,
        Field(default="dta", description="Analysis method: 'dta' or 'llm'."),
    ] = "dta",
    ctx: Context = CurrentContext(),
) -> dict[str, Any]:
    """Рекомендовать индексы по агрегированной нагрузке БД."""
    service = IndexAnalysisService(db=get_db(ctx))
    return await service.analyze_workload_indexes(
        method=method,
        max_index_size_mb=max_index_size_mb,
        ctx=ctx,
    )
