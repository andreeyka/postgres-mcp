"""Safe SQL executor: validation + timeout + search_path around a delegate executor."""

import asyncio
import logging
from typing import Any, cast

from psycopg.sql import SQL, Composable, Literal

from postgres_fastmcp.sql.models.row_result import RowResult
from postgres_fastmcp.sql.security.config import SafeSqlConfig
from postgres_fastmcp.sql.validation.query_validator import QueryValidator


logger = logging.getLogger(__name__)


class SafeSqlExecutor:
    """Compositional wrapper: validates SQL, sets search_path/timeout, then delegates execution."""

    def __init__(
        self,
        delegate: Any,  # QueryExecutorPort-compatible: execute(query, params?, readonly) -> list[RowResult]|None
        validator: QueryValidator,
        config: SafeSqlConfig,
    ) -> None:
        """Initialize with delegate executor, validator, and config.

        Args:
            delegate: Executor with async execute(query, params=..., readonly=...) -> list[RowResult]|None.
            validator: Validator used to validate each query before execution.
            config: Safe SQL config (tag, timeout, schema, read_only, prefix).
        """
        self._delegate = delegate
        self._validator = validator
        self._config = config

    async def execute(
        self,
        query: str,
        params: list[Any] | None = None,
        *,
        readonly: bool = True,
    ) -> list[RowResult] | None:
        """Validate query then execute via delegate (with search_path and optional timeout).

        Args:
            query: SQL to execute.
            params: Optional parameters (will be rendered into query before execution).
            readonly: Ignored; self._config.read_only is used.

        Returns:
            Rows or None for no-result statements.
        """
        self._validator.validate(query)
        readonly_effective = self._config.read_only
        if params:
            query = self.render(query, params)
        else:
            query = f"/* {self._config.query_tag} */ {query}"
        if self._config.allowed_schema:
            query = f"SET LOCAL search_path = {self._config.allowed_schema}; {query}"
        if self._config.timeout is not None:
            try:
                async with asyncio.timeout(self._config.timeout):
                    return cast(
                        "list[RowResult] | None",
                        await self._delegate.execute(query, params=None, readonly=readonly_effective),
                    )
            except TimeoutError as e:
                logger.warning(
                    "Query execution timed out after %s seconds: %s...",
                    self._config.timeout,
                    query[:100],
                )
                raise ValueError(
                    f"Query execution timed out after {self._config.timeout} seconds in restricted mode. "
                    "Consider simplifying your query or increasing the timeout."
                ) from e
        return cast(
            "list[RowResult] | None",
            await self._delegate.execute(query, params=None, readonly=readonly_effective),
        )

    def render(self, query: str, params: list[Any]) -> str:
        """Render parameterized query to a single string (for execution without server-side params).

        Args:
            query: Query with {} placeholders (psycopg style).
            params: Values to substitute.

        Returns:
            Query string with values inlined (tagged).
        """
        composables = [p if isinstance(p, Composable) else Literal(p) for p in params]
        rendered = SQL(query).format(*composables).as_string()
        return f"/* {self._config.query_tag} */ {rendered}"
