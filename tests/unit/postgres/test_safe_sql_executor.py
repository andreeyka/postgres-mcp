# mypy: ignore-errors
"""Unit tests for SafeSqlExecutor."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from psycopg.errors import QueryCanceled

from postgres_fastmcp.shared.errors import QueryTimeoutError, SchemaNotAllowedError
from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.postgres.security.driver import SafeSqlConfig
from postgres_fastmcp.postgres.security.driver import SafeSqlExecutor
from postgres_fastmcp.postgres.security.query_validator import QueryValidator


def _make_executor(
    delegate: MagicMock,
    validator: QueryValidator | MagicMock | None = None,
    config: SafeSqlConfig | None = None,
) -> SafeSqlExecutor:
    """Build SafeSqlExecutor with optional validator and config."""
    if validator is None:
        validator = QueryValidator(read_only=True)
    if config is None:
        config = SafeSqlConfig(query_tag="test-tag")
    return SafeSqlExecutor(delegate=delegate, validator=validator, config=config)


class TestSafeSqlExecutorValidation:
    """Validator is invoked before execute."""

    async def test_validation_called_before_execute(self) -> None:
        """validate(query) is called before delegate.execute; validates final query (with tag when no params)."""
        mock_delegate = AsyncMock(return_value=[])
        validator = MagicMock()
        validator.validate = MagicMock()
        executor = _make_executor(mock_delegate, validator=validator)
        await executor.execute("SELECT 1")
        validator.validate.assert_called_once_with("/* test-tag */ SELECT 1")
        mock_delegate.execute.assert_called_once()

    async def test_invalid_query_raises_before_execute(self) -> None:
        """If validator raises, delegate.execute is not called."""
        mock_delegate = AsyncMock(return_value=[])
        validator = QueryValidator(read_only=True, allowed_schema="public")
        executor = _make_executor(mock_delegate, validator=validator)
        with pytest.raises(SchemaNotAllowedError):
            await executor.execute("SELECT * FROM other_schema.t")
        mock_delegate.execute.assert_not_called()


class TestSafeSqlExecutorSearchPathAndTag:
    """search_path and query_tag are applied."""

    async def test_query_tag_prepended_when_no_params(self) -> None:
        """Query gets comment tag when params is None."""
        mock_delegate = AsyncMock(return_value=[RowResult(cells={"x": 1})])
        executor = _make_executor(mock_delegate)
        await executor.execute("SELECT 1")
        call_args = mock_delegate.execute.call_args
        assert call_args[0][0].startswith("/* test-tag */")
        assert "SELECT 1" in call_args[0][0]

    async def test_search_path_prepended_when_allowed_schema_set(self) -> None:
        """SET LOCAL search_path is prepended when config.allowed_schema is set."""
        mock_delegate = AsyncMock(return_value=[])
        config = SafeSqlConfig(query_tag="t", allowed_schema="public")
        executor = _make_executor(mock_delegate, config=config)
        await executor.execute("SELECT 1")
        call_args = mock_delegate.execute.call_args
        assert "SET LOCAL search_path = public" in call_args[0][0]
        assert "SELECT 1" in call_args[0][0]

    async def test_readonly_from_config_passed_to_delegate(self) -> None:
        """Readonly passed to delegate is from config, not from execute() param."""
        mock_delegate = AsyncMock(return_value=[])
        config = SafeSqlConfig(query_tag="t", read_only=False)
        executor = _make_executor(mock_delegate, config=config)
        await executor.execute("SELECT 1", readonly=True)  # pass True but config is False
        call_args = mock_delegate.execute.call_args
        assert call_args[1]["readonly"] is False

    async def test_statement_timeout_prepended_before_search_path(self) -> None:
        """statement_timeout (ms) is set inside the transaction and precedes search_path."""
        mock_delegate = AsyncMock(return_value=[])
        config = SafeSqlConfig(query_tag="t", allowed_schema="public", timeout=30)
        executor = _make_executor(mock_delegate, config=config)
        await executor.execute("SELECT 1")
        sent = mock_delegate.execute.call_args[0][0]
        assert sent.startswith("SET LOCAL statement_timeout = 30000; SET LOCAL search_path = public; ")
        assert sent.endswith("/* t */ SELECT 1")

    async def test_no_statement_timeout_when_timeout_is_none(self) -> None:
        """Without a configured timeout no statement_timeout is sent."""
        mock_delegate = AsyncMock(return_value=[])
        executor = _make_executor(mock_delegate, config=SafeSqlConfig(query_tag="t"))
        await executor.execute("SELECT 1")
        assert "statement_timeout" not in mock_delegate.execute.call_args[0][0]


class TestSafeSqlExecutorTimeout:
    """Timeout enforcement."""

    async def test_client_timeout_raises_after_grace(self) -> None:
        """When Postgres never answers, the client-side guard (timeout + grace) raises QueryTimeoutError."""

        async def slow_execute(*args: object, **kwargs: object) -> list[RowResult]:
            await asyncio.sleep(1.0)
            return []

        mock_delegate = MagicMock()
        mock_delegate.execute = AsyncMock(side_effect=slow_execute)
        config = SafeSqlConfig(query_tag="t", timeout=0.01, client_timeout_grace=0.0)
        executor = _make_executor(mock_delegate, config=config)
        with pytest.raises(QueryTimeoutError) as exc_info:
            await executor.execute("SELECT 1")
        assert exc_info.value.timeout_seconds == 0.01

    async def test_server_side_cancel_maps_to_query_timeout_error(self) -> None:
        """psycopg QueryCanceled (statement_timeout fired in Postgres) becomes QueryTimeoutError."""
        mock_delegate = MagicMock()
        mock_delegate.execute = AsyncMock(side_effect=QueryCanceled("canceling statement due to statement timeout"))
        config = SafeSqlConfig(query_tag="t", timeout=30)
        executor = _make_executor(mock_delegate, config=config)
        with pytest.raises(QueryTimeoutError) as exc_info:
            await executor.execute("SELECT 1")
        assert exc_info.value.timeout_seconds == 30
        assert isinstance(exc_info.value.__cause__, QueryCanceled)

    async def test_no_timeout_returns_delegate_result(self) -> None:
        """When timeout is None, delegate result is returned."""
        mock_delegate = MagicMock()
        mock_delegate.execute = AsyncMock(return_value=[RowResult(cells={"a": 1})])
        executor = _make_executor(mock_delegate)
        result = await executor.execute("SELECT 1")
        assert result == [RowResult(cells={"a": 1})]
