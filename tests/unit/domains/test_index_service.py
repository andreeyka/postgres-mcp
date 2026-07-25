# mypy: ignore-errors
"""Unit tests for IndexAnalysisService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from postgres_fastmcp.shared.errors import ContextRequiredError, EmptyQueriesError, QueriesLimitError
from postgres_fastmcp.domains.index_tuning.index_opt_base import MAX_NUM_INDEX_TUNING_QUERIES
from postgres_fastmcp.domains.index_tuning.service import IndexAnalysisService


class TestIndexAnalysisServiceAnalyzeWorkloadIndexes:
    """Tests for IndexAnalysisService.analyze_workload_indexes."""

    @patch("postgres_fastmcp.domains.index_tuning.service.TextPresentation")
    @patch("postgres_fastmcp.domains.index_tuning.service.DatabaseTuningAdvisor")
    async def test_analyze_workload_indexes_dta_returns_recommendations_dict(
        self,
        mock_dta_cls: MagicMock,  # noqa: ARG002 (required for patch order)
        mock_presentation_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """analyze_workload_indexes(method=dta) returns dict with recommendations."""
        mock_presentation = MagicMock()
        mock_presentation.analyze_workload = AsyncMock(return_value={"recommendations": []})
        mock_presentation_cls.return_value = mock_presentation

        service = IndexAnalysisService(db=mock_db_access)
        result = await service.analyze_workload_indexes(method="dta", max_index_size_mb=1000)
        assert result == {"recommendations": []}

    @patch("postgres_fastmcp.domains.index_tuning.service.TextPresentation")
    @patch("postgres_fastmcp.domains.index_tuning.service.LLMOptimizerTool")
    async def test_analyze_workload_indexes_llm_without_ctx_raises(
        self,
        mock_llm_cls: MagicMock,  # noqa: ARG002 (required for patch order)
        mock_presentation_cls: MagicMock,  # noqa: ARG002 (required for patch order)
        mock_db_access: MagicMock,
    ) -> None:
        """analyze_workload_indexes(method=llm) without ctx raises ContextRequiredError."""
        service = IndexAnalysisService(db=mock_db_access)
        with pytest.raises(ContextRequiredError):
            await service.analyze_workload_indexes(method="llm", ctx=None)


class TestIndexAnalysisServiceAnalyzeQueryIndexes:
    """Tests for IndexAnalysisService.analyze_query_indexes."""

    @patch("postgres_fastmcp.domains.index_tuning.service.TextPresentation")
    @patch("postgres_fastmcp.domains.index_tuning.service.DatabaseTuningAdvisor")
    async def test_analyze_query_indexes_returns_recommendations_dict(
        self,
        mock_dta_cls: MagicMock,  # noqa: ARG002 (required for patch order)
        mock_presentation_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """analyze_query_indexes with valid queries returns dict with recommendations."""
        mock_presentation = MagicMock()
        mock_presentation.analyze_queries = AsyncMock(return_value={"recommendations": []})
        mock_presentation_cls.return_value = mock_presentation

        service = IndexAnalysisService(db=mock_db_access)
        result = await service.analyze_query_indexes(method="dta", queries=["SELECT 1"])
        assert result == {"recommendations": []}

    async def test_analyze_query_indexes_empty_queries_raises(
        self,
        mock_db_access: MagicMock,
    ) -> None:
        """analyze_query_indexes(queries=[]) raises EmptyQueriesError."""
        service = IndexAnalysisService(db=mock_db_access)
        with pytest.raises(EmptyQueriesError):
            await service.analyze_query_indexes(method="dta", queries=[])

    async def test_analyze_query_indexes_over_limit_raises(
        self,
        mock_db_access: MagicMock,
    ) -> None:
        """analyze_query_indexes with more than MAX_NUM_INDEX_TUNING_QUERIES raises QueriesLimitError."""
        service = IndexAnalysisService(db=mock_db_access)
        too_many = ["SELECT 1"] * (MAX_NUM_INDEX_TUNING_QUERIES + 1)
        with pytest.raises(QueriesLimitError) as exc_info:
            await service.analyze_query_indexes(method="dta", queries=too_many)
        assert exc_info.value.limit == MAX_NUM_INDEX_TUNING_QUERIES


class TestIndexAnalysisServiceDispatch:
    """Tests for IndexAnalysisService method dispatch."""

    async def test_analyze_query_indexes_dispatches_to_dta(
        self, monkeypatch: pytest.MonkeyPatch, mock_db_access: MagicMock
    ) -> None:
        """analyze_query_indexes(method=dta) calls _dta_analyze_query."""
        service = IndexAnalysisService(db=mock_db_access)
        dta_mock = AsyncMock(return_value={"via": "dta"})
        llm_mock = AsyncMock(return_value={"via": "llm"})
        monkeypatch.setattr(service, "_dta_analyze_query", dta_mock)
        monkeypatch.setattr(service, "_llm_analyze_query", llm_mock)

        result = await service.analyze_query_indexes(method="dta", queries=["SELECT 1"], max_index_size_mb=10)
        assert result == {"via": "dta"}
        dta_mock.assert_awaited_once_with(queries=["SELECT 1"], max_index_size_mb=10)
        llm_mock.assert_not_awaited()

    async def test_analyze_query_indexes_dispatches_to_llm(
        self, monkeypatch: pytest.MonkeyPatch, mock_db_access: MagicMock
    ) -> None:
        """analyze_query_indexes(method=llm) calls _llm_analyze_query."""
        service = IndexAnalysisService(db=mock_db_access)
        dta_mock = AsyncMock(return_value={"via": "dta"})
        llm_mock = AsyncMock(return_value={"via": "llm"})
        monkeypatch.setattr(service, "_dta_analyze_query", dta_mock)
        monkeypatch.setattr(service, "_llm_analyze_query", llm_mock)
        fake_ctx = MagicMock()

        result = await service.analyze_query_indexes(
            method="llm", queries=["SELECT 1"], max_index_size_mb=10, ctx=fake_ctx
        )
        assert result == {"via": "llm"}
        llm_mock.assert_awaited_once_with(queries=["SELECT 1"], max_index_size_mb=10, ctx=fake_ctx)
        dta_mock.assert_not_awaited()

    async def test_analyze_workload_indexes_dispatches_to_dta(
        self, monkeypatch: pytest.MonkeyPatch, mock_db_access: MagicMock
    ) -> None:
        """analyze_workload_indexes(method=dta) calls _dta_analyze_workload."""
        service = IndexAnalysisService(db=mock_db_access)
        dta_mock = AsyncMock(return_value={"via": "dta"})
        llm_mock = AsyncMock(return_value={"via": "llm"})
        monkeypatch.setattr(service, "_dta_analyze_workload", dta_mock)
        monkeypatch.setattr(service, "_llm_analyze_workload", llm_mock)

        result = await service.analyze_workload_indexes(method="dta", max_index_size_mb=10)
        assert result == {"via": "dta"}
        dta_mock.assert_awaited_once_with(max_index_size_mb=10)
        llm_mock.assert_not_awaited()

    async def test_analyze_workload_indexes_dispatches_to_llm(
        self, monkeypatch: pytest.MonkeyPatch, mock_db_access: MagicMock
    ) -> None:
        """analyze_workload_indexes(method=llm) calls _llm_analyze_workload."""
        service = IndexAnalysisService(db=mock_db_access)
        dta_mock = AsyncMock(return_value={"via": "dta"})
        llm_mock = AsyncMock(return_value={"via": "llm"})
        monkeypatch.setattr(service, "_dta_analyze_workload", dta_mock)
        monkeypatch.setattr(service, "_llm_analyze_workload", llm_mock)
        fake_ctx = MagicMock()

        result = await service.analyze_workload_indexes(method="llm", max_index_size_mb=10, ctx=fake_ctx)
        assert result == {"via": "llm"}
        llm_mock.assert_awaited_once_with(max_index_size_mb=10, ctx=fake_ctx)
        dta_mock.assert_not_awaited()
