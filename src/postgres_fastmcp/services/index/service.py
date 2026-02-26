"""Сервис анализа индексов (фасад модуля index)."""

from typing import Any, Literal

from fastmcp import Context

from postgres_fastmcp.common.errors import ContextRequiredError, EmptyQueriesError, QueriesLimitError
from postgres_fastmcp.services.db_access_service import DbAccessService
from postgres_fastmcp.services.index.dta_calc import DatabaseTuningAdvisor
from postgres_fastmcp.services.index.index_opt_base import MAX_NUM_INDEX_TUNING_QUERIES, IndexTuningBase
from postgres_fastmcp.services.index.llm_opt import LLMOptimizerTool
from postgres_fastmcp.services.index.presentation import TextPresentation


class IndexAnalysisService:
    """Сервис для анализа нагрузки и индексов запросов."""

    def __init__(self, db: DbAccessService, method: Literal["dta", "llm"]) -> None:
        """Инициализация сервиса с подключением к базе данных и методом анализа."""
        self.db = db
        self._method = method

    def _create_index_tuning(self, ctx: Context | None) -> IndexTuningBase:
        """Создать стратегию настройки индексов на основе выбранного метода.

        Args:
            ctx: Контекст MCP для метода LLM (опционально).

        Returns:
            Экземпляр IndexTuningBase (DatabaseTuningAdvisor или LLMOptimizerTool).

        Raises:
            ContextRequiredError: Если метод LLM требует контекст, но он не предоставлен.
        """
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
        """Проанализировать часто выполняемые запросы и рекомендовать оптимальные индексы.

        Args:
            max_index_size_mb: Максимальный размер индексов в МБ (по умолчанию 10000).
            ctx: Контекст MCP для метода LLM (опционально).

        Returns:
            Словарь с результатами анализа и рекомендациями.

        Raises:
            ContextRequiredError: Если метод LLM требует контекст.
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
        """Проанализировать список SQL запросов и рекомендовать оптимальные индексы.

        Args:
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

        sql_driver = self.db.sql_driver
        index_tuning = self._create_index_tuning(ctx)
        dta_tool = TextPresentation(sql_driver, index_tuning)
        return await dta_tool.analyze_queries(queries=queries, max_index_size_mb=max_index_size_mb)
