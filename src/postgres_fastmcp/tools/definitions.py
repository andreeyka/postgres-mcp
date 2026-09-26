"""Все MCP-тулы сервера: методы ToolSet, тонкие обёртки над доменными сервисами.

ToolSet получает ``get_db`` — функцию, которая на каждый вызов тула отдаёт
доступ к БД с правами текущего запроса (DbAccessPort). Контекст FastMCP тулам
не нужен. Описания и аннотации задаются при регистрации в ``tools.registry``.
Docstring методов-тулов на английском: FastMCP показывает их агенту.
Тулы, которые возвращают строки, принимают ``output`` и отдают ToolResult
через ``tools.rendering``.
"""

from collections.abc import Callable
from typing import Annotated, Any

from fastmcp.tools import ToolResult
from pydantic import Field

from postgres_fastmcp.domains import querying, top_queries
from postgres_fastmcp.domains.catalog.service import CatalogService
from postgres_fastmcp.domains.db_access import DbAccessPort
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


class ToolSet:
    """Девять тулов как методы; доступ к БД на каждый вызов берётся из get_db."""

    def __init__(self, get_db: Callable[[], DbAccessPort]) -> None:
        """Инициализировать набор тулов.

        Args:
            get_db: Функция, возвращающая доступ к БД с правами текущего запроса.
        """
        self._get_db = get_db

    async def execute_sql(
        self,
        sql: Annotated[
            str,
            Field(description="SQL statement to execute, e.g. 'SELECT id, name FROM users WHERE active LIMIT 50'."),
        ],
        output: OutputParam = "table",
    ) -> ToolResult:
        """Execute a SQL statement and return the result rows."""
        rows = await querying.execute_sql(self._get_db(), sql)
        if rows is None:
            return rows_result([], output, title=SUCCESS_NO_ROWS)
        return rows_result(rows, output)

    async def explain_query(
        self,
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
    ) -> str:
        """Show the execution plan of a SQL query: plain, analyze or with hypothetical indexes."""
        service = ExplainService(db=self._get_db())
        return await service.explain(sql, analyze=analyze, hypothetical_indexes=hypothetical_indexes)

    async def list_objects(
        self,
        schema_name: Annotated[str, Field(description="Schema name to inspect.")],
        object_type: ObjectTypeParam = "table",
        output: OutputParam = "table",
    ) -> ToolResult:
        """List objects of the given kind in a schema."""
        service = CatalogService(db=self._get_db())
        return rows_result(await service.list_objects(schema_name=schema_name, object_type=object_type), output)

    async def get_object_details(
        self,
        schema_name: Annotated[str, Field(description="Schema name.")],
        object_name: Annotated[str, Field(description="Object name.")],
        object_type: ObjectTypeParam = "table",
        output: OutputParam = "table",
    ) -> ToolResult:
        """Show object details: columns, constraints, indexes and other metadata."""
        service = CatalogService(db=self._get_db())
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

    async def list_schemas(self, output: OutputParam = "table") -> ToolResult:
        """List database schemas."""
        service = CatalogService(db=self._get_db())
        return rows_result(await service.list_schemas(), output)

    async def analyze_db_health(self, health_type: HealthTypesParam = (HealthType.ALL,)) -> str:
        """Run database health checks and return a text report."""
        health_tool = DatabaseHealthAnalyzer(self._get_db())
        return await health_tool.health(health_type=",".join(health_type))

    async def get_top_queries(
        self,
        sort_by: TopQueriesSortByParam = "resources",
        limit: TopQueriesLimitParam = 10,
        output: OutputParam = "table",
    ) -> ToolResult:
        """Report top queries from pg_stat_statements by the chosen criteria."""
        rows = await top_queries.get_top_queries(self._get_db(), sort_by=sort_by, limit=limit)
        return rows_result(rows, output)

    async def analyze_query_indexes(
        self,
        queries: IndexQueriesParam,
        max_index_size_mb: Annotated[
            int,
            Field(default=10000, ge=1, description="Max recommended index size (MB)."),
        ] = 10000,
    ) -> dict[str, Any]:
        """Recommend indexes for a list of queries (cost-based DTA, requires hypopg)."""
        service = IndexAnalysisService(db=self._get_db())
        return await service.analyze_query_indexes(queries=queries, max_index_size_mb=max_index_size_mb)

    async def analyze_workload_indexes(
        self,
        max_index_size_mb: Annotated[
            int,
            Field(default=10000, ge=1, description="Max recommended index size (MB)."),
        ] = 10000,
    ) -> dict[str, Any]:
        """Recommend indexes for the aggregated database workload (cost-based DTA, requires hypopg)."""
        service = IndexAnalysisService(db=self._get_db())
        return await service.analyze_workload_indexes(max_index_size_mb=max_index_size_mb)
