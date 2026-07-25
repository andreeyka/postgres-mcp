"""Сервис анализа индексов (фасад модуля index)."""

from typing import Any

from fastmcp import Context

from postgres_fastmcp.domains.db_access import DbAccessService
from postgres_fastmcp.domains.index_tuning.dta_calc import DatabaseTuningAdvisor
from postgres_fastmcp.domains.index_tuning.index_opt_base import MAX_NUM_INDEX_TUNING_QUERIES
from postgres_fastmcp.domains.index_tuning.llm_opt import LLMOptimizerTool
from postgres_fastmcp.domains.index_tuning.presentation import TextPresentation
from postgres_fastmcp.shared.enums import AnalysisMethod
from postgres_fastmcp.shared.errors import ContextRequiredError, EmptyQueriesError, QueriesLimitError


class IndexAnalysisService:
    """Сервис для анализа нагрузки и индексов запросов."""

    def __init__(self, db: DbAccessService) -> None:
        """Инициализация сервиса с подключением к базе данных."""
        self.db = db

    async def analyze_workload_indexes(
        self,
        *,
        method: AnalysisMethod,
        max_index_size_mb: int = 10000,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Проанализировать часто выполняемые запросы и рекомендовать оптимальные индексы.

        Args:
            method: Метод анализа ('dta' или 'llm').
            max_index_size_mb: Максимальный размер индексов в МБ (по умолчанию 10000).
            ctx: Контекст MCP для метода LLM (опционально).

        Returns:
            Словарь с результатами анализа и рекомендациями.

        Raises:
            ContextRequiredError: Если метод LLM требует контекст.
        """
        if method == "dta":
            return await self._dta_analyze_workload(max_index_size_mb=max_index_size_mb)
        return await self._llm_analyze_workload(max_index_size_mb=max_index_size_mb, ctx=ctx)

    async def analyze_query_indexes(
        self,
        *,
        method: AnalysisMethod,
        queries: list[str],
        max_index_size_mb: int = 10000,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Проанализировать список SQL запросов и рекомендовать оптимальные индексы.

        Args:
            method: Метод анализа ('dta' или 'llm').
            queries: Список SQL запросов для анализа.
            max_index_size_mb: Максимальный размер индексов в МБ (по умолчанию 10000).
            ctx: Контекст MCP для метода LLM (опционально).

        Returns:
            Словарь с результатами анализа и рекомендациями.

        Raises:
            EmptyQueriesError: Если список запросов пуст.
            QueriesLimitError: Если слишком много запросов.
            ContextRequiredError: Если метод LLM требует контекст.
        """
        if len(queries) == 0:
            raise EmptyQueriesError()
        if len(queries) > MAX_NUM_INDEX_TUNING_QUERIES:
            raise QueriesLimitError(MAX_NUM_INDEX_TUNING_QUERIES)

        if method == "dta":
            return await self._dta_analyze_query(queries=queries, max_index_size_mb=max_index_size_mb)
        return await self._llm_analyze_query(queries=queries, max_index_size_mb=max_index_size_mb, ctx=ctx)

    async def _dta_analyze_workload(self, *, max_index_size_mb: int) -> dict[str, Any]:
        """Анализ нагрузки методом DTA."""
        sql_driver = self.db.sql_driver
        index_tuning = DatabaseTuningAdvisor(sql_driver, connection_id=self.db.connection_id)
        dta_tool = TextPresentation(sql_driver, index_tuning)
        return await dta_tool.analyze_workload(max_index_size_mb=max_index_size_mb)

    async def _llm_analyze_workload(self, *, max_index_size_mb: int, ctx: Context | None) -> dict[str, Any]:
        """Анализ нагрузки методом LLM."""
        if ctx is None:
            raise ContextRequiredError()
        sql_driver = self.db.sql_driver
        index_tuning = LLMOptimizerTool(sql_driver, ctx=ctx, connection_id=self.db.connection_id)
        dta_tool = TextPresentation(sql_driver, index_tuning)
        return await dta_tool.analyze_workload(max_index_size_mb=max_index_size_mb)

    async def _dta_analyze_query(self, *, queries: list[str], max_index_size_mb: int) -> dict[str, Any]:
        """Анализ запросов методом DTA."""
        sql_driver = self.db.sql_driver
        index_tuning = DatabaseTuningAdvisor(sql_driver, connection_id=self.db.connection_id)
        dta_tool = TextPresentation(sql_driver, index_tuning)
        return await dta_tool.analyze_queries(queries=queries, max_index_size_mb=max_index_size_mb)

    async def _llm_analyze_query(
        self, *, queries: list[str], max_index_size_mb: int, ctx: Context | None
    ) -> dict[str, Any]:
        """Анализ запросов методом LLM."""
        if ctx is None:
            raise ContextRequiredError()
        sql_driver = self.db.sql_driver
        index_tuning = LLMOptimizerTool(sql_driver, ctx=ctx, connection_id=self.db.connection_id)
        dta_tool = TextPresentation(sql_driver, index_tuning)
        return await dta_tool.analyze_queries(queries=queries, max_index_size_mb=max_index_size_mb)
