"""Сервис топ запросов (фасад модуля top_queries)."""

from postgres_fastmcp.common.errors import InvalidSortCriteriaError
from postgres_fastmcp.services.db_access_service import DbAccessService
from postgres_fastmcp.services.top_queries.top_queries_calc import TopQueriesCalc


class TopQueriesService:
    """Сервис для получения медленных запросов."""

    def __init__(self, db: DbAccessService) -> None:
        """Инициализация сервиса с подключением к базе данных."""
        self.db = db

    async def get_top_queries(
        self,
        sort_by: str = "resources",
        limit: int = 10,
    ) -> str:
        """Получить список самых медленных или ресурсоемких запросов.

        Args:
            sort_by: Критерий сортировки (по умолчанию "resources").
            limit: Максимальное количество запросов (по умолчанию 10).

        Returns:
            Строка с отчетом о самых медленных запросах.

        Raises:
            InvalidSortCriteriaError: Если указан недопустимый параметр sort_by.
        """
        top_queries_tool = TopQueriesCalc(
            sql_driver=self.db.sql_driver,
            connection_id=self.db.connection_id,
        )

        if sort_by == "resources":
            return await top_queries_tool.get_top_resource_queries()
        if sort_by in {"mean_time", "total_time"}:
            return await top_queries_tool.get_top_queries_by_time(
                limit=limit,
                sort_by="mean" if sort_by == "mean_time" else "total",
            )
        raise InvalidSortCriteriaError()
