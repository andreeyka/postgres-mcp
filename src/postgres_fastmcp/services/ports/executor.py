"""Ports for query execution and query templating."""

from typing import Any, Protocol

from postgres_fastmcp.sql.models.row_result import RowResult


class QueryExecutorPort(Protocol):
    """Port for executing SQL queries (read-only or read-write)."""

    async def execute(
        self,
        query: str,
        params: list[Any] | None = None,
        *,
        readonly: bool = True,
    ) -> list[RowResult] | None:
        """Execute query and return rows or None for no-result statements."""
        ...


class QueryTemplatePort(Protocol):
    """Port for rendering a parameterized query to a single string."""

    def render(self, query: str, params: list[Any]) -> str:
        """Render query with params inlined (e.g. psycopg {} placeholders)."""
        ...
