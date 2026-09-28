# mypy: ignore-errors
"""Unit tests for SafeSqlExecutor."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from psycopg.errors import QueryCanceled
from psycopg.pq import DiagnosticField

from postgres_fastmcp.postgres.models import RowResult, StatementResult
from postgres_fastmcp.postgres.security.driver import SafeSqlConfig, SafeSqlExecutor, _is_statement_timeout
from postgres_fastmcp.postgres.security.query_validator import QueryValidator
from postgres_fastmcp.shared.errors import (
    PlanAccessError,
    QueryCancelledError,
    QueryTimeoutError,
    SchemaNotAllowedError,
)


def _query_canceled(message_primary: str) -> QueryCanceled:
    """QueryCanceled с diag.message_primary, как его отдаёт сервер."""
    return QueryCanceled(
        f"ERROR: {message_primary}",
        info={DiagnosticField.MESSAGE_PRIMARY: message_primary.encode()},
    )


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
        """Psycopg QueryCanceled (statement_timeout fired in Postgres) becomes QueryTimeoutError."""
        mock_delegate = MagicMock()
        mock_delegate.execute = AsyncMock(side_effect=_query_canceled("canceling statement due to statement timeout"))
        config = SafeSqlConfig(query_tag="t", timeout=30)
        executor = _make_executor(mock_delegate, config=config)
        with pytest.raises(QueryTimeoutError) as exc_info:
            await executor.execute("SELECT 1")
        assert exc_info.value.timeout_seconds == 30
        assert isinstance(exc_info.value.__cause__, QueryCanceled)
        assert "read_only" not in str(exc_info.value)

    async def test_localized_timeout_cancel_after_timeout_maps_to_query_timeout_error(self) -> None:
        """A non-English statement_timeout message is still a timeout once the elapsed time reached it."""
        mock_delegate = MagicMock()
        mock_delegate.execute = AsyncMock(side_effect=_query_canceled("выполнение оператора отменено из-за тайм-аута"))
        executor = _make_executor(mock_delegate, config=SafeSqlConfig(query_tag="t", timeout=30))
        with (
            patch("postgres_fastmcp.postgres.security.driver.monotonic", side_effect=[100.0, 130.0]),
            pytest.raises(QueryTimeoutError) as exc_info,
        ):
            await executor.execute("SELECT 1")
        assert exc_info.value.timeout_seconds == 30

    async def test_quick_cancel_maps_to_query_cancelled_error(self) -> None:
        """A cancel that arrives well before the timeout is not a statement_timeout."""
        mock_delegate = MagicMock()
        mock_delegate.execute = AsyncMock(side_effect=_query_canceled("canceling statement due to user request"))
        executor = _make_executor(mock_delegate, config=SafeSqlConfig(query_tag="t", timeout=30))
        with (
            patch("postgres_fastmcp.postgres.security.driver.monotonic", side_effect=[100.0, 100.5]),
            pytest.raises(QueryCancelledError),
        ):
            await executor.execute("SELECT 1")

    @pytest.mark.parametrize("message_primary", ["canceling statement due to user request", None])
    async def test_other_server_cancel_maps_to_query_cancelled_error(self, message_primary: str | None) -> None:
        """A cancel not caused by statement_timeout (pg_cancel_backend, no diag) is not reported as a timeout."""
        error = _query_canceled(message_primary) if message_primary else QueryCanceled("canceled")
        mock_delegate = MagicMock()
        mock_delegate.execute = AsyncMock(side_effect=error)
        config = SafeSqlConfig(query_tag="t", timeout=30)
        executor = _make_executor(mock_delegate, config=config)
        with pytest.raises(QueryCancelledError) as exc_info:
            await executor.execute("SELECT 1")
        assert not isinstance(exc_info.value, QueryTimeoutError)
        assert isinstance(exc_info.value.__cause__, QueryCanceled)

    async def test_no_timeout_returns_delegate_result(self) -> None:
        """When timeout is None, delegate result is returned."""
        mock_delegate = MagicMock()
        mock_delegate.execute = AsyncMock(return_value=[RowResult(cells={"a": 1})])
        executor = _make_executor(mock_delegate)
        result = await executor.execute("SELECT 1")
        assert result == [RowResult(cells={"a": 1})]


class TestSafeSqlExecutorExecuteStatement:
    """execute_statement идёт тем же путём, что execute, но через delegate.execute_statement."""

    async def test_validates_prefixes_and_delegates(self) -> None:
        """Тег, SET LOCAL и read_only из конфигурации; результат делегата возвращается как есть."""
        expected = StatementResult(rows=None, status="UPDATE 3", affected_rows=3)
        mock_delegate = MagicMock()
        mock_delegate.execute_statement = AsyncMock(return_value=expected)
        config = SafeSqlConfig(query_tag="t", timeout=5, allowed_schema="public", read_only=False)
        executor = _make_executor(mock_delegate, validator=QueryValidator(read_only=False), config=config)

        result = await executor.execute_statement("UPDATE t SET v = 1", readonly=True)

        assert result is expected
        mock_delegate.execute_statement.assert_awaited_once_with(
            "SET LOCAL statement_timeout = 5000; SET LOCAL search_path = public; /* t */ UPDATE t SET v = 1",
            params=None,
            readonly=False,
        )
        mock_delegate.execute.assert_not_called()

    async def test_invalid_query_raises_before_delegate(self) -> None:
        """Валидатор отклоняет запрос до обращения к делегату."""
        mock_delegate = MagicMock()
        mock_delegate.execute_statement = AsyncMock()
        executor = _make_executor(mock_delegate, validator=QueryValidator(read_only=True, allowed_schema="public"))

        with pytest.raises(SchemaNotAllowedError):
            await executor.execute_statement("SELECT * FROM other_schema.t")
        mock_delegate.execute_statement.assert_not_called()

    async def test_statement_timeout_cancel_maps_to_query_timeout_error(self) -> None:
        """Отмена по statement_timeout превращается в QueryTimeoutError, как у execute."""
        mock_delegate = MagicMock()
        mock_delegate.execute_statement = AsyncMock(
            side_effect=_query_canceled("canceling statement due to statement timeout")
        )
        executor = _make_executor(mock_delegate, config=SafeSqlConfig(query_tag="t", timeout=30))

        with pytest.raises(QueryTimeoutError):
            await executor.execute_statement("SELECT 1")

    async def test_client_timeout_raises_after_grace(self) -> None:
        """Клиентская страховка действует и для execute_statement."""

        async def slow(*args: object, **kwargs: object) -> StatementResult:
            await asyncio.sleep(1)
            return StatementResult(rows=None, status="UPDATE 0", affected_rows=0)

        mock_delegate = MagicMock()
        mock_delegate.execute_statement = AsyncMock(side_effect=slow)
        config = SafeSqlConfig(query_tag="t", timeout=0.01, client_timeout_grace=0.01)
        executor = _make_executor(mock_delegate, config=config)

        with pytest.raises(QueryTimeoutError):
            await executor.execute_statement("SELECT 1")


@pytest.mark.parametrize(
    ("message_primary", "elapsed", "timeout", "expected"),
    [
        ("canceling statement due to statement timeout", 0.1, 30.0, True),
        ("выполнение оператора отменено из-за тайм-аута", 30.0, 30.0, True),
        (None, 31.0, 30.0, True),
        ("canceling statement due to user request", 0.5, 30.0, False),
        (None, 0.5, 30.0, False),
        ("canceling statement due to user request", 100.0, None, False),
    ],
    ids=[
        "english-message",
        "localized-at-timeout",
        "no-diag-after-timeout",
        "user-request",
        "no-diag-quick",
        "no-timeout",
    ],
)
def test_is_statement_timeout(
    message_primary: str | None, elapsed: float, timeout: float | None, expected: bool
) -> None:
    """Pure classification: English marker OR elapsed time reached the configured timeout."""
    assert _is_statement_timeout(message_primary, elapsed=elapsed, timeout=timeout) is expected


def _plan_rows(schema: str, relation: str) -> list[RowResult]:
    plan = {"Node Type": "Seq Scan", "Relation Name": relation, "Schema": schema}
    return [RowResult(cells={"QUERY PLAN": [{"Plan": plan}]})]


def _basic_executor(delegate: MagicMock, *, plan_check: bool = True) -> SafeSqlExecutor:
    config = SafeSqlConfig(
        query_tag="t",
        timeout=5,
        allowed_schema="public",
        read_only=False,
        table_prefix="app_",
        plan_check=plan_check,
    )
    validator = QueryValidator(read_only=False, allowed_schema="public", table_prefix="app_")
    return _make_executor(delegate, validator=validator, config=config)


class TestSafeSqlExecutorPlanCheck:
    """plan_check: EXPLAIN (VERBOSE) через делегата после валидации и до выполнения, только с allowed_schema."""

    async def test_plan_is_checked_before_execution(self) -> None:
        delegate = MagicMock()
        delegate.execute = AsyncMock(side_effect=[_plan_rows("public", "app_t"), [RowResult(cells={"x": 1})]])

        result = await _basic_executor(delegate).execute("SELECT * FROM app_t")

        assert result == [RowResult(cells={"x": 1})]
        explain_call, run_call = delegate.execute.await_args_list
        assert explain_call.args[0] == (
            "SET LOCAL statement_timeout = 5000; SET LOCAL search_path = public; "
            "SET LOCAL standard_conforming_strings = on; "
            "/* t */ EXPLAIN (VERBOSE, FORMAT JSON) SELECT * FROM app_t"
        )
        assert explain_call.kwargs["readonly"] is True
        assert run_call.args[0] == (
            "SET LOCAL statement_timeout = 5000; SET LOCAL search_path = public; /* t */ SELECT * FROM app_t"
        )
        assert run_call.kwargs["readonly"] is False

    async def test_plan_violation_stops_execution(self) -> None:
        delegate = MagicMock()
        delegate.execute = AsyncMock(return_value=_plan_rows("secret", "accounts"))

        with pytest.raises(PlanAccessError, match=r"secret\.accounts"):
            await _basic_executor(delegate).execute("SELECT * FROM app_secret_view")

        delegate.execute.assert_awaited_once()

    async def test_execute_statement_is_checked_too(self) -> None:
        delegate = MagicMock()
        delegate.execute = AsyncMock(return_value=_plan_rows("secret", "accounts"))
        delegate.execute_statement = AsyncMock()

        with pytest.raises(PlanAccessError):
            await _basic_executor(delegate).execute_statement("UPDATE app_secret_view SET token = 'x'")

        delegate.execute_statement.assert_not_awaited()

    async def test_validator_runs_before_the_plan_check(self) -> None:
        """Проверка по плану не ослабляет валидатор: отклонённый им запрос не доходит до EXPLAIN."""
        delegate = MagicMock()
        delegate.execute = AsyncMock()

        with pytest.raises(SchemaNotAllowedError):
            await _basic_executor(delegate).execute("SELECT * FROM secret.accounts")

        delegate.execute.assert_not_awaited()

    async def test_plan_check_off_sends_no_explain(self) -> None:
        delegate = MagicMock()
        delegate.execute = AsyncMock(return_value=[])

        await _basic_executor(delegate, plan_check=False).execute("SELECT * FROM app_t")

        delegate.execute.assert_awaited_once()
        assert "EXPLAIN" not in delegate.execute.await_args.args[0]

    async def test_plan_check_is_ignored_without_allowed_schema(self) -> None:
        """Full (allowed_schema=None): plan_check не действует никогда."""
        delegate = MagicMock()
        delegate.execute = AsyncMock(return_value=[])
        config = SafeSqlConfig(query_tag="t", plan_check=True)

        await _make_executor(delegate, config=config).execute("SELECT 1")

        delegate.execute.assert_awaited_once()
        assert "EXPLAIN" not in delegate.execute.await_args.args[0]

    async def test_explain_cancel_maps_to_query_timeout_error(self) -> None:
        delegate = MagicMock()
        delegate.execute = AsyncMock(side_effect=_query_canceled("canceling statement due to statement timeout"))

        with pytest.raises(QueryTimeoutError):
            await _basic_executor(delegate).execute("SELECT * FROM app_t")
