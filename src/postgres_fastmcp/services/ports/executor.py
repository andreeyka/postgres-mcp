"""Порты для выполнения SQL запросов и шаблонизации запросов."""

from typing import Any, Protocol

from postgres_fastmcp.sql.models.row_result import RowResult


class QueryExecutorPort(Protocol):
    """Порт для выполнения SQL запросов (только чтение или чтение-запись)."""

    async def execute(
        self,
        query: str,
        params: list[Any] | None = None,
        *,
        readonly: bool = True,
    ) -> list[RowResult] | None:
        """Выполнение запроса и возвращение строк или None для операторов без результата."""
        ...


class QueryTemplatePort(Protocol):
    """Порт для рендеринга параметризованного запроса в одну строку."""

    def render(self, query: str, params: list[Any]) -> str:
        """Рендер запроса с встроенными параметрами (например, psycopg {} плейсхолдеры)."""
        ...
