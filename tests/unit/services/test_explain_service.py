# mypy: ignore-errors
"""Unit tests for ExplainService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from postgres_fastmcp.common.errors import (
    ExplainAnalyzeNotSupportedError,
    ExplainPlanExecutionError,
    HypopgNotInstalledError,
)
from postgres_fastmcp.services.explain.artifacts import ExplainPlanArtifact, PlanNode
from postgres_fastmcp.services.explain.service import ExplainService


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


class TestExplainServicePlainMode:
    """Tests for ExplainService in plain mode."""

    @patch("postgres_fastmcp.services.explain.service.ExplainPlanTool")
    async def test_plain_mode_returns_text_representation_of_plan(
        self,
        mock_tool_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """Plain mode returns string with plan text (EXPLAIN output)."""
        mock_tool = MagicMock()
        artifact = _make_artifact("Plain plan")
        mock_tool.explain = AsyncMock(return_value=artifact)
        mock_tool_cls.return_value = mock_tool

        service = ExplainService(db=mock_db_access, mode="plain")
        result = await service.explain_query("SELECT 1")
        assert "Plain plan" in result or "Result" in result


class TestExplainServiceAnalyzeMode:
    """Tests for ExplainService in analyze mode."""

    @patch("postgres_fastmcp.services.explain.service.ExplainPlanTool")
    async def test_analyze_mode_returns_explain_analyze_plan_text(
        self,
        mock_tool_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """Analyze mode returns string with EXPLAIN ANALYZE plan text."""
        mock_tool = MagicMock()
        artifact = _make_artifact("Analyze plan")
        mock_tool.explain_analyze = AsyncMock(return_value=artifact)
        mock_tool_cls.return_value = mock_tool

        service = ExplainService(db=mock_db_access, mode="analyze")
        result = await service.explain_query("SELECT 1")
        assert "Analyze plan" in result or "Result" in result

    @patch("postgres_fastmcp.services.explain.service.ExplainPlanTool")
    async def test_analyze_mode_falls_back_to_plain_when_analyze_not_supported(
        self,
        mock_tool_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """When EXPLAIN ANALYZE is not supported, fall back to plain EXPLAIN and add a note."""
        mock_tool = MagicMock()
        mock_tool.explain_analyze = AsyncMock(
            side_effect=ExplainPlanExecutionError(ExplainAnalyzeNotSupportedError())
        )
        plain_artifact = _make_artifact("Plain fallback")
        mock_tool.explain = AsyncMock(return_value=plain_artifact)
        mock_tool_cls.return_value = mock_tool

        service = ExplainService(db=mock_db_access, mode="analyze")
        result = await service.explain_query("SELECT 1")
        assert "Plain fallback" in result or "Result" in result
        assert "EXPLAIN ANALYZE is not supported" in result
        assert "plain EXPLAIN result" in result


class TestExplainServiceHypotheticalMode:
    """Tests for ExplainService in hypothetical mode."""

    @patch("postgres_fastmcp.services.explain.service.ExplainPlanTool")
    async def test_hypothetical_without_indexes_returns_plain_plan_text(
        self,
        mock_tool_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """Hypothetical mode with no hypothetical_indexes returns plain EXPLAIN plan text."""
        mock_tool = MagicMock()
        artifact = _make_artifact("Hypothetical plain")
        mock_tool.explain = AsyncMock(return_value=artifact)
        mock_tool_cls.return_value = mock_tool

        service = ExplainService(db=mock_db_access, mode="hypothetical")
        result = await service.explain_query("SELECT 1")
        assert "Hypothetical plain" in result or "Result" in result

    @patch("postgres_fastmcp.services.explain.service.ExtensionInspectorAdapter")
    @patch("postgres_fastmcp.services.explain.service.ExplainPlanTool")
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

        service = ExplainService(db=mock_db_access, mode="hypothetical")
        result = await service.explain_query("SELECT 1", hypothetical_indexes=[{"table": "t", "columns": ["id"]}])
        assert "With hypothetical indexes" in result or "Result" in result

    @patch("postgres_fastmcp.services.explain.service.ExtensionInspectorAdapter")
    @patch("postgres_fastmcp.services.explain.service.ExplainPlanTool")
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

        service = ExplainService(db=mock_db_access, mode="hypothetical")
        with pytest.raises(HypopgNotInstalledError) as exc_info:
            await service.explain_query("SELECT 1", hypothetical_indexes=[{"table": "t", "columns": ["id"]}])
        assert "HypoPG" in str(exc_info.value)
        mock_tool.explain_with_hypothetical_indexes.assert_not_called()


class TestExplainServiceErrorPropagation:
    """Errors from underlying tool propagate to caller."""

    @patch("postgres_fastmcp.services.explain.service.ExplainPlanTool")
    async def test_tool_error_propagates_to_caller(
        self,
        mock_tool_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """When underlying tool raises, the same exception propagates to caller."""
        mock_tool = MagicMock()
        mock_tool.explain = AsyncMock(side_effect=ValueError("Parse error"))
        mock_tool_cls.return_value = mock_tool

        service = ExplainService(db=mock_db_access, mode="plain")
        with pytest.raises(ValueError) as exc_info:
            await service.explain_query("INVALID SQL")
        assert "Parse error" in str(exc_info.value)
