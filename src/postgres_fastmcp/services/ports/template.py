"""Port for rendering parameterized queries to a single string."""

from typing import Any, Protocol


class QueryTemplatePort(Protocol):
    """Protocol for substituting parameters into a query template."""

    def render(self, query: str, params: list[Any]) -> str:
        """Return query string with params inlined (e.g. psycopg {} placeholders)."""
        ...
