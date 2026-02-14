"""Index analysis service (facade for index module)."""

from typing import Any, Literal

from fastmcp import Context

from postgres_fastmcp.common.errors import ContextRequiredError, EmptyQueriesError, QueriesLimitError
from postgres_fastmcp.services.db_access_service import DbAccessService
from postgres_fastmcp.services.index.dta_calc import DatabaseTuningAdvisor
from postgres_fastmcp.services.index.index_opt_base import MAX_NUM_INDEX_TUNING_QUERIES, IndexTuningBase
from postgres_fastmcp.services.index.llm_opt import LLMOptimizerTool
from postgres_fastmcp.services.index.presentation import TextPresentation


class IndexAnalysisService:
    """Service for analyzing workload and query indexes."""

    def __init__(self, db: DbAccessService, method: Literal["dta", "llm"]) -> None:
        """Initialize with database access service."""
        self.db = db
        self._method = method

    def _create_index_tuning(self, ctx: Context | None) -> IndexTuningBase:
        """Create index tuning strategy based on configured method."""
        sql_driver = self.db.sql_driver
        connection_id = self.db.connection_id
        if self._method == "dta":
            return DatabaseTuningAdvisor(sql_driver, connection_id=connection_id)
        if ctx is None:
            raise ContextRequiredError()
        return LLMOptimizerTool(sql_driver, ctx=ctx, connection_id=connection_id)

    async def analyze_workload_indexes(
        self,
        max_index_size_mb: int = 10000,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Analyze frequently executed queries and recommend optimal indexes.

        Raises:
            ContextRequiredError: LLM method requires context.
        """
        sql_driver = self.db.sql_driver
        index_tuning = self._create_index_tuning(ctx)
        dta_tool = TextPresentation(sql_driver, index_tuning)
        return await dta_tool.analyze_workload(max_index_size_mb=max_index_size_mb)

    async def analyze_query_indexes(
        self,
        queries: list[str],
        max_index_size_mb: int = 10000,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Analyze a list of SQL queries and recommend optimal indexes.

        Raises:
            EmptyQueriesError: Empty list of queries.
            QueriesLimitError: Too many queries.
            ContextRequiredError: LLM method requires context.
        """
        if len(queries) == 0:
            raise EmptyQueriesError()
        if len(queries) > MAX_NUM_INDEX_TUNING_QUERIES:
            raise QueriesLimitError(MAX_NUM_INDEX_TUNING_QUERIES)

        sql_driver = self.db.sql_driver
        index_tuning = self._create_index_tuning(ctx)
        dta_tool = TextPresentation(sql_driver, index_tuning)
        return await dta_tool.analyze_queries(queries=queries, max_index_size_mb=max_index_size_mb)
