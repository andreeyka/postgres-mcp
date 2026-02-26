# mypy: ignore-errors
"""Unit tests for IndexAnalysisService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from postgres_fastmcp.common.errors import ContextRequiredError, EmptyQueriesError, QueriesLimitError
from postgres_fastmcp.services.index.index_opt_base import MAX_NUM_INDEX_TUNING_QUERIES
from postgres_fastmcp.services.index.service import IndexAnalysisService


class TestIndexAnalysisServiceAnalyzeWorkloadIndexes:
    """Tests for IndexAnalysisService.analyze_workload_indexes."""

    @patch("postgres_fastmcp.services.index.service.TextPresentation")
    @patch("postgres_fastmcp.services.index.service.DatabaseTuningAdvisor")
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

        service = IndexAnalysisService(db=mock_db_access, method="dta")
        result = await service.analyze_workload_indexes(max_index_size_mb=1000)
        assert result == {"recommendations": []}

    @patch("postgres_fastmcp.services.index.service.TextPresentation")
    @patch("postgres_fastmcp.services.index.service.LLMOptimizerTool")
    async def test_analyze_workload_indexes_llm_without_ctx_raises(
        self,
        mock_llm_cls: MagicMock,  # noqa: ARG002 (required for patch order)
        mock_presentation_cls: MagicMock,  # noqa: ARG002 (required for patch order)
        mock_db_access: MagicMock,
    ) -> None:
        """analyze_workload_indexes(method=llm) without ctx raises ContextRequiredError."""
        service = IndexAnalysisService(db=mock_db_access, method="llm")
        with pytest.raises(ContextRequiredError):
            await service.analyze_workload_indexes(ctx=None)


class TestIndexAnalysisServiceAnalyzeQueryIndexes:
    """Tests for IndexAnalysisService.analyze_query_indexes."""

    @patch("postgres_fastmcp.services.index.service.TextPresentation")
    @patch("postgres_fastmcp.services.index.service.DatabaseTuningAdvisor")
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

        service = IndexAnalysisService(db=mock_db_access, method="dta")
        result = await service.analyze_query_indexes(queries=["SELECT 1"])
        assert result == {"recommendations": []}

    async def test_analyze_query_indexes_empty_queries_raises(
        self,
        mock_db_access: MagicMock,
    ) -> None:
        """analyze_query_indexes(queries=[]) raises EmptyQueriesError."""
        service = IndexAnalysisService(db=mock_db_access, method="dta")
        with pytest.raises(EmptyQueriesError):
            await service.analyze_query_indexes(queries=[])

    async def test_analyze_query_indexes_over_limit_raises(
        self,
        mock_db_access: MagicMock,
    ) -> None:
        """analyze_query_indexes with more than MAX_NUM_INDEX_TUNING_QUERIES raises QueriesLimitError."""
        service = IndexAnalysisService(db=mock_db_access, method="dta")
        too_many = ["SELECT 1"] * (MAX_NUM_INDEX_TUNING_QUERIES + 1)
        with pytest.raises(QueriesLimitError) as exc_info:
            await service.analyze_query_indexes(queries=too_many)
        assert exc_info.value.limit == MAX_NUM_INDEX_TUNING_QUERIES
