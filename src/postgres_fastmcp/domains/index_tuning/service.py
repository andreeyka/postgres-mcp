"""Сервис анализа индексов (фасад модуля index_tuning): только DTA."""

from typing import Any

from postgres_fastmcp.domains.db_access import DbAccessService
from postgres_fastmcp.domains.index_tuning.dta_calc import DatabaseTuningAdvisor
from postgres_fastmcp.domains.index_tuning.models import MAX_NUM_INDEX_TUNING_QUERIES
from postgres_fastmcp.domains.index_tuning.presentation import TextPresentation
from postgres_fastmcp.shared.errors import EmptyQueriesError, QueriesLimitError


class IndexAnalysisService:
    """Сервис для анализа нагрузки и индексов запросов."""

    def __init__(self, db: DbAccessService) -> None:
        """Инициализация сервиса с подключением к базе данных."""
        self.db = db

    async def analyze_workload_indexes(self, *, max_index_size_mb: int = 10000) -> dict[str, Any]:
        """Проанализировать часто выполняемые запросы и рекомендовать оптимальные индексы.

        Args:
            max_index_size_mb: Максимальный размер индексов в МБ (по умолчанию 10000).

        Returns:
            Словарь с результатами анализа и рекомендациями.
        """
        return await self._presentation().analyze_workload(max_index_size_mb=max_index_size_mb)

    async def analyze_query_indexes(self, *, queries: list[str], max_index_size_mb: int = 10000) -> dict[str, Any]:
        """Проанализировать список SQL запросов и рекомендовать оптимальные индексы.

        Args:
            queries: Список SQL запросов для анализа.
            max_index_size_mb: Максимальный размер индексов в МБ (по умолчанию 10000).

        Returns:
            Словарь с результатами анализа и рекомендациями.

        Raises:
            EmptyQueriesError: Если список запросов пуст.
            QueriesLimitError: Если запросов больше MAX_NUM_INDEX_TUNING_QUERIES.
        """
        if len(queries) == 0:
            raise EmptyQueriesError()
        if len(queries) > MAX_NUM_INDEX_TUNING_QUERIES:
            raise QueriesLimitError(MAX_NUM_INDEX_TUNING_QUERIES)
        return await self._presentation().analyze_queries(queries=queries, max_index_size_mb=max_index_size_mb)

    def _presentation(self) -> TextPresentation:
        """Собрать DTA-советник и презентацию поверх драйвера сервиса."""
        sql_driver = self.db.sql_driver
        advisor = DatabaseTuningAdvisor(sql_driver, connection_id=self.db.connection_id)
        return TextPresentation(sql_driver, advisor)
