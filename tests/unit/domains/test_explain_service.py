# mypy: ignore-errors
"""Unit tests for ExplainService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import postgres_fastmcp.domains.db_access as db_access_module
from postgres_fastmcp.access import EffectiveAccess
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.domains.db_access import DbAccessService
from postgres_fastmcp.domains.explain.artifacts import ExplainPlanArtifact, PlanNode
from postgres_fastmcp.domains.explain.explain_plan import ExplainPlanBuilder
from postgres_fastmcp.domains.explain.service import ExplainService
from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.shared.errors import (
    ExplainAnalyzeNotSupportedError,
    ExplainAnalyzeWithHypotheticalError,
    ExplainPlanError,
    ExplainPlanExecutionError,
    HypopgNotInstalledError,
    PlanAccessError,
    SchemaNotAllowedError,
    SystemRelationAccessError,
    TablePrefixAccessError,
)


def _make_artifact(text: str = "Plan output") -> ExplainPlanArtifact:
    """Build a minimal ExplainPlanArtifact that returns the given text from to_text()."""
    node = PlanNode(
        node_type="Result",
        total_cost=0.1,
        startup_cost=0.0,
        plan_rows=1,
        plan_width=4,
    )
    return ExplainPlanArtifact(value="{}", plan_tree=node)


class TestExplainServiceDispatch:
    """Tests verifying that ExplainService.explain dispatches to the correct private method."""

    async def test_explain_without_flags_calls_plain(self, mock_db_access: MagicMock) -> None:
        """explain(sql) calls _explain_plain."""
        service = ExplainService(db=mock_db_access)
        service._explain_plain = AsyncMock(return_value="plain-result")
        service._explain_analyze = AsyncMock(return_value="analyze-result")
        service._explain_hypothetical = AsyncMock(return_value="hyp-result")

        result = await service.explain("SELECT 1")
        assert result == "plain-result"
        service._explain_plain.assert_awaited_once_with("SELECT 1")
        service._explain_analyze.assert_not_called()
        service._explain_hypothetical.assert_not_called()

    async def test_explain_with_analyze_calls_analyze(self, mock_db_access: MagicMock) -> None:
        """explain(sql, analyze=True) calls _explain_analyze."""
        service = ExplainService(db=mock_db_access)
        service._explain_plain = AsyncMock(return_value="plain-result")
        service._explain_analyze = AsyncMock(return_value="analyze-result")
        service._explain_hypothetical = AsyncMock(return_value="hyp-result")

        result = await service.explain("SELECT 1", analyze=True)
        assert result == "analyze-result"
        service._explain_analyze.assert_awaited_once_with("SELECT 1")
        service._explain_plain.assert_not_called()
        service._explain_hypothetical.assert_not_called()

    async def test_explain_with_hypothetical_indexes_calls_hypothetical(self, mock_db_access: MagicMock) -> None:
        """explain(sql, hypothetical_indexes=[...]) calls _explain_hypothetical."""
        service = ExplainService(db=mock_db_access)
        service._explain_plain = AsyncMock(return_value="plain-result")
        service._explain_analyze = AsyncMock(return_value="analyze-result")
        service._explain_hypothetical = AsyncMock(return_value="hyp-result")

        indexes = [{"table": "t", "columns": ["id"]}]
        result = await service.explain("SELECT 1", hypothetical_indexes=indexes)
        assert result == "hyp-result"
        service._explain_hypothetical.assert_awaited_once_with("SELECT 1", indexes)
        service._explain_plain.assert_not_called()
        service._explain_analyze.assert_not_called()

    async def test_explain_analyze_with_hypothetical_raises(self, mock_db_access: MagicMock) -> None:
        """explain(sql, analyze=True, hypothetical_indexes=[...]) raises."""
        service = ExplainService(db=mock_db_access)
        with pytest.raises(ExplainAnalyzeWithHypotheticalError):
            await service.explain(
                "SELECT 1",
                analyze=True,
                hypothetical_indexes=[{"table": "t", "columns": ["id"]}],
            )


class TestExplainServicePlainMode:
    """Tests for plain mode behavior."""

    @patch("postgres_fastmcp.domains.explain.service.ExplainPlanBuilder")
    async def test_plain_returns_text_representation_of_plan(
        self,
        mock_tool_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """Plain explain returns string with plan text (EXPLAIN output)."""
        mock_tool = MagicMock()
        artifact = _make_artifact("Plain plan")
        mock_tool.explain = AsyncMock(return_value=artifact)
        mock_tool_cls.return_value = mock_tool

        service = ExplainService(db=mock_db_access)
        result = await service.explain("SELECT 1")
        assert "Result" in result


class TestExplainServiceAnalyzeMode:
    """Tests for analyze mode behavior."""

    @patch("postgres_fastmcp.domains.explain.service.ExplainPlanBuilder")
    async def test_analyze_returns_explain_analyze_plan_text(
        self,
        mock_tool_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """Analyze mode returns string with EXPLAIN ANALYZE plan text."""
        mock_tool = MagicMock()
        artifact = _make_artifact("Analyze plan")
        mock_tool.explain_analyze = AsyncMock(return_value=artifact)
        mock_tool_cls.return_value = mock_tool

        service = ExplainService(db=mock_db_access)
        result = await service.explain("SELECT 1", analyze=True)
        assert "Result" in result

    @patch("postgres_fastmcp.domains.explain.service.ExplainPlanBuilder")
    async def test_analyze_falls_back_to_plain_when_not_supported(
        self,
        mock_tool_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """When EXPLAIN ANALYZE is not supported, fall back to plain EXPLAIN and add a note.

        ExplainAnalyzeNotSupportedError is a UserFacingError: _run_explain_query re-raises it unchanged
        (not wrapped in ExplainPlanExecutionError), so the tool-level mock raises it directly too.
        """
        mock_tool = MagicMock()
        mock_tool.explain_analyze = AsyncMock(side_effect=ExplainAnalyzeNotSupportedError())
        plain_artifact = _make_artifact("Plain fallback")
        mock_tool.explain = AsyncMock(return_value=plain_artifact)
        mock_tool_cls.return_value = mock_tool

        service = ExplainService(db=mock_db_access)
        result = await service.explain("SELECT 1", analyze=True)
        assert "Result" in result
        assert "EXPLAIN ANALYZE is not supported" in result
        assert "plain EXPLAIN result" in result


class TestExplainServiceHypotheticalMode:
    """Tests for hypothetical mode behavior."""

    @patch("postgres_fastmcp.domains.explain.service.ExtensionInspectorAdapter")
    @patch("postgres_fastmcp.domains.explain.service.ExplainPlanBuilder")
    async def test_hypothetical_with_indexes_returns_plan_when_hypopg_installed(
        self,
        mock_tool_cls: MagicMock,
        mock_ext_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """Hypothetical mode with indexes returns plan text when HypoPG is installed."""
        mock_tool = MagicMock()
        artifact = _make_artifact("With hypothetical indexes")
        mock_tool.explain_with_hypothetical_indexes = AsyncMock(return_value=artifact)
        mock_tool_cls.return_value = mock_tool

        mock_inspector = MagicMock()
        mock_inspector.check_hypopg_installation_status = AsyncMock(return_value=(True, "ok"))
        mock_ext_cls.return_value = mock_inspector

        service = ExplainService(db=mock_db_access)
        result = await service.explain("SELECT 1", hypothetical_indexes=[{"table": "t", "columns": ["id"]}])
        assert "Result" in result

    @patch("postgres_fastmcp.domains.explain.service.ExtensionInspectorAdapter")
    @patch("postgres_fastmcp.domains.explain.service.ExplainPlanBuilder")
    async def test_hypothetical_with_indexes_hypopg_not_installed_raises(
        self,
        mock_tool_cls: MagicMock,
        mock_ext_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """Hypothetical mode with indexes when HypoPG not installed raises HypopgNotInstalledError."""
        mock_tool = MagicMock()
        mock_tool_cls.return_value = mock_tool

        mock_inspector = MagicMock()
        mock_inspector.check_hypopg_installation_status = AsyncMock(
            return_value=(False, "HypoPG extension is not installed"),
        )
        mock_ext_cls.return_value = mock_inspector

        service = ExplainService(db=mock_db_access)
        with pytest.raises(HypopgNotInstalledError) as exc_info:
            await service.explain("SELECT 1", hypothetical_indexes=[{"table": "t", "columns": ["id"]}])
        assert "HypoPG" in str(exc_info.value)
        mock_tool.explain_with_hypothetical_indexes.assert_not_called()


class TestExplainServiceErrorPropagation:
    """Errors from underlying tool propagate to caller."""

    @patch("postgres_fastmcp.domains.explain.service.ExplainPlanBuilder")
    async def test_tool_error_propagates_to_caller(
        self,
        mock_tool_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """When underlying tool raises, the same exception propagates to caller."""
        mock_tool = MagicMock()
        mock_tool.explain = AsyncMock(side_effect=ValueError("Parse error"))
        mock_tool_cls.return_value = mock_tool

        service = ExplainService(db=mock_db_access)
        with pytest.raises(ValueError) as exc_info:
            await service.explain("INVALID SQL")
        assert "Parse error" in str(exc_info.value)


_SEQ_SCAN_PLAN = [
    {"Plan": {"Node Type": "Seq Scan", "Total Cost": 1.0, "Startup Cost": 0.0, "Plan Rows": 1, "Plan Width": 4}}
]


async def test_hypothetical_explain_works_in_basic_with_table_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверка hypopg идёт по каналу сервера: валидатор агента с table_prefix её не отклоняет."""

    async def execute(query, params=None, *, readonly=True):
        if "pg_catalog.pg_extension" in query:
            return [RowResult(cells={"extversion": "1.4.1"})]
        if "EXPLAIN" in query:
            return [RowResult(cells={"QUERY PLAN": _SEQ_SCAN_PLAN})]
        return []

    delegate = MagicMock()
    delegate.execute = AsyncMock(side_effect=execute)
    monkeypatch.setattr(db_access_module, "SqlExecutor", lambda conn: delegate)
    config = DatabaseConfig(
        host="h", user="u", password="p", name="d", access_mode=AccessMode.BASIC, write_mode=False, table_prefix="app_"
    )
    db = DbAccessService(config).view(EffectiveAccess(AccessMode.BASIC, write_mode=False))

    result = await ExplainService(db=db).explain(
        "SELECT * FROM app_users WHERE name = 'x'",
        hypothetical_indexes=[{"table": "app_users", "columns": ["name"]}],
    )

    assert "Seq Scan" in result
    sent = [c.args[0] for c in delegate.execute.await_args_list]
    assert any("pg_catalog.pg_extension" in q for q in sent)
    assert any("hypopg_create_index" in q and "EXPLAIN" in q for q in sent)


@pytest.mark.parametrize(
    ("table", "error"),
    [
        ("secret.accounts", SchemaNotAllowedError),
        ("users", TablePrefixAccessError),
        ("pg_stats", SystemRelationAccessError),
    ],
)
async def test_hypothetical_index_on_a_forbidden_table_is_rejected_before_explain(
    monkeypatch: pytest.MonkeyPatch, table: str, error: type[Exception]
) -> None:
    """Определение гипотетического индекса проходит валидатор агента: чужая таблица не доходит до hypopg."""

    async def execute(query, params=None, *, readonly=True):
        if "pg_catalog.pg_extension" in query:
            return [RowResult(cells={"extversion": "1.4.1"})]
        return []

    delegate = MagicMock()
    delegate.execute = AsyncMock(side_effect=execute)
    monkeypatch.setattr(db_access_module, "SqlExecutor", lambda conn: delegate)
    config = DatabaseConfig(
        host="h", user="u", password="p", name="d", access_mode=AccessMode.BASIC, write_mode=False, table_prefix="app_"
    )
    db = DbAccessService(config).view(EffectiveAccess(AccessMode.BASIC, write_mode=False))

    with pytest.raises(error):
        await ExplainService(db=db).explain(
            "SELECT * FROM app_users", hypothetical_indexes=[{"table": table, "columns": ["id"]}]
        )

    sent = [c.args[0] for c in delegate.execute.await_args_list]
    assert not any("hypopg_create_index" in q for q in sent)


def _plan_check_delegate(scanned_schema: str, scanned_relation: str) -> MagicMock:
    """Делегат: EXPLAIN VERBOSE от PlanGuard получает план со сканом отношения, остальное — как у сервера."""
    scan = {"Node Type": "Seq Scan", "Relation Name": scanned_relation, "Schema": scanned_schema}

    async def execute(query, params=None, *, readonly=True):
        if "pg_catalog.pg_extension" in query:
            return [RowResult(cells={"extversion": "1.4.1"})]
        if "EXPLAIN (VERBOSE, FORMAT JSON) SELECT * FROM" in query:
            return [RowResult(cells={"QUERY PLAN": [{"Plan": scan}]})]
        if "EXPLAIN (VERBOSE" in query:
            return [RowResult(cells={"QUERY PLAN": [{"Plan": {"Node Type": "Result"}}]})]
        if "EXPLAIN" in query:
            return [RowResult(cells={"QUERY PLAN": _SEQ_SCAN_PLAN})]
        return []

    delegate = MagicMock()
    delegate.execute = AsyncMock(side_effect=execute)
    return delegate


def _plan_check_db(monkeypatch: pytest.MonkeyPatch, delegate: MagicMock):
    monkeypatch.setattr(db_access_module, "SqlExecutor", lambda conn: delegate)
    config = DatabaseConfig(
        host="h",
        user="u",
        password="p",
        name="d",
        access_mode=AccessMode.BASIC,
        write_mode=False,
        table_prefix="app_",
        plan_check=True,
    )
    return DbAccessService(config).view(EffectiveAccess(AccessMode.BASIC, write_mode=False))


async def test_hypothetical_explain_passes_the_plan_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """plan_check: строка hypopg_reset; hypopg_create_index; EXPLAIN … проверяется оператор за оператором.

    Планирование SELECT hypopg_create_index(...) функцию не вызывает, а EXPLAIN агента разворачивается
    до его запроса — гипотетические индексы для проверки по плану не нужны.
    """
    delegate = _plan_check_delegate("public", "app_users")
    db = _plan_check_db(monkeypatch, delegate)

    result = await ExplainService(db=db).explain(
        "SELECT * FROM app_users WHERE name = 'x'",
        hypothetical_indexes=[{"table": "app_users", "columns": ["name"]}],
    )

    assert "Seq Scan" in result
    sent = [c.args[0] for c in delegate.execute.await_args_list]
    checks = [q.split("*/ ", 1)[1] for q in sent if "EXPLAIN (VERBOSE" in q]
    assert checks[0] == "EXPLAIN (VERBOSE, FORMAT JSON) SELECT hypopg_reset()"
    assert checks[1].startswith("EXPLAIN (VERBOSE, FORMAT JSON) SELECT hypopg_create_index(")
    assert checks[2] == "EXPLAIN (VERBOSE, FORMAT JSON) SELECT * FROM app_users WHERE name = 'x'"
    assert len(checks) == 3
    assert not any("standard_conforming_strings" in q for q in sent)
    assert sent[-1].count("hypopg_create_index") == 1
    assert "EXPLAIN (FORMAT JSON, COSTS TRUE)" in sent[-1]


async def test_hypothetical_explain_over_a_foreign_view_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """plan_check: представление с префиксом поверх чужой схемы отклоняется до выполнения со hypopg."""
    delegate = _plan_check_delegate("secret", "accounts")
    db = _plan_check_db(monkeypatch, delegate)

    with pytest.raises(PlanAccessError, match=r"secret\.accounts"):
        await ExplainService(db=db).explain(
            "SELECT * FROM app_secret_view",
            hypothetical_indexes=[{"table": "app_secret_view", "columns": ["id"]}],
        )

    sent = [c.args[0] for c in delegate.execute.await_args_list]
    assert not any("hypopg_create_index" in q and "EXPLAIN (FORMAT JSON" in q for q in sent)


class TestExplainQueryAccessErrorsAreNotWrapped:
    """_run_explain_query re-raises UserFacingError as-is: the agent sees the real reason, not a generic error."""

    async def test_explain_query_on_a_system_relation_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """explain_query без hypothetical_indexes: валидатор basic отклоняет pg_stats до обращения к БД."""
        delegate = MagicMock()
        delegate.execute = AsyncMock()
        monkeypatch.setattr(db_access_module, "SqlExecutor", lambda conn: delegate)
        config = DatabaseConfig(
            host="h",
            user="u",
            password="p",
            name="d",
            access_mode=AccessMode.BASIC,
            write_mode=False,
            table_prefix="app_",
        )
        db = DbAccessService(config).view(EffectiveAccess(AccessMode.BASIC, write_mode=False))

        with pytest.raises(SystemRelationAccessError):
            await ExplainService(db=db).explain("SELECT * FROM pg_stats")

        delegate.execute.assert_not_awaited()

    async def test_explain_query_with_plan_check_on_a_foreign_relation_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """explain_query без hypothetical_indexes: plan_check отклоняет план, читающий secret.t."""
        delegate = _plan_check_delegate("secret", "t")
        db = _plan_check_db(monkeypatch, delegate)

        with pytest.raises(PlanAccessError, match=r"secret\.t"):
            await ExplainService(db=db).explain("SELECT * FROM app_secret_view")

    async def test_explain_analyze_falls_back_to_plain_in_basic_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """basic: валидатор отклоняет EXPLAIN ANALYZE с ExplainAnalyzeNotSupportedError, сервис делает обычный EXPLAIN."""
        delegate = MagicMock()
        delegate.execute = AsyncMock(return_value=[RowResult(cells={"QUERY PLAN": _SEQ_SCAN_PLAN})])
        monkeypatch.setattr(db_access_module, "SqlExecutor", lambda conn: delegate)
        config = DatabaseConfig(
            host="h",
            user="u",
            password="p",
            name="d",
            access_mode=AccessMode.BASIC,
            write_mode=False,
            table_prefix="app_",
        )
        db = DbAccessService(config).view(EffectiveAccess(AccessMode.BASIC, write_mode=False))

        result = await ExplainService(db=db).explain("SELECT * FROM app_users", analyze=True)

        assert "Seq Scan" in result
        assert "EXPLAIN ANALYZE is not supported" in result


@pytest.mark.parametrize(
    "rows",
    [[], [RowResult(cells={})], [RowResult(cells={"QUERY PLAN": [{"Plan": {}}]})]],
    ids=["no-rows", "no-query-plan-cell", "plan-without-node-type"],
)
async def test_unexpected_explain_result_is_wrapped(rows: list[RowResult]) -> None:
    """IndexError/KeyError разбора результата EXPLAIN не уходят наружу сырыми, а оборачиваются в ошибку плана."""
    sql_driver = MagicMock()
    sql_driver.execute = AsyncMock(return_value=rows)
    builder = ExplainPlanBuilder(sql_driver, catalog_driver=MagicMock())

    with pytest.raises(ExplainPlanError):
        await builder.explain("SELECT 1")


async def test_missing_query_plan_cell_is_an_execution_error() -> None:
    sql_driver = MagicMock()
    sql_driver.execute = AsyncMock(return_value=[RowResult(cells={})])
    builder = ExplainPlanBuilder(sql_driver, catalog_driver=MagicMock())

    with pytest.raises(ExplainPlanExecutionError, match="QUERY PLAN"):
        await builder.explain("SELECT 1")
