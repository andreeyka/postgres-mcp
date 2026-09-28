# mypy: ignore-errors
"""Unit tests for IndexAnalysisService (DTA only)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from postgres_fastmcp.domains.index_tuning.models import MAX_NUM_INDEX_TUNING_QUERIES
from postgres_fastmcp.domains.index_tuning.service import IndexAnalysisService
from postgres_fastmcp.shared.errors import EmptyQueriesError, QueriesLimitError


class TestIndexAnalysisServiceAnalyzeWorkloadIndexes:
    """Tests for IndexAnalysisService.analyze_workload_indexes."""

    @patch("postgres_fastmcp.domains.index_tuning.service.TextPresentation")
    @patch("postgres_fastmcp.domains.index_tuning.service.DatabaseTuningAdvisor")
    async def test_returns_recommendations_dict(
        self,
        mock_dta_cls: MagicMock,
        mock_presentation_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """analyze_workload_indexes returns the presentation result."""
        mock_presentation = MagicMock()
        mock_presentation.analyze_workload = AsyncMock(return_value={"recommendations": []})
        mock_presentation_cls.return_value = mock_presentation

        service = IndexAnalysisService(db=mock_db_access)
        result = await service.analyze_workload_indexes(max_index_size_mb=1000)

        assert result == {"recommendations": []}
        mock_presentation.analyze_workload.assert_awaited_once_with(max_index_size_mb=1000)

    @patch("postgres_fastmcp.domains.index_tuning.service.TextPresentation")
    @patch("postgres_fastmcp.domains.index_tuning.service.DatabaseTuningAdvisor")
    async def test_builds_dta_with_connection_id(
        self,
        mock_dta_cls: MagicMock,
        mock_presentation_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """The advisor is built on the service's driver and connection id."""
        mock_presentation_cls.return_value.analyze_workload = AsyncMock(return_value={})

        await IndexAnalysisService(db=mock_db_access).analyze_workload_indexes()

        mock_dta_cls.assert_called_once_with(
            mock_db_access.sql_driver,
            catalog_driver=mock_db_access.catalog_driver,
            connection_id=mock_db_access.connection_id,
        )


class TestIndexAnalysisServiceAnalyzeQueryIndexes:
    """Tests for IndexAnalysisService.analyze_query_indexes."""

    @patch("postgres_fastmcp.domains.index_tuning.service.TextPresentation")
    @patch("postgres_fastmcp.domains.index_tuning.service.DatabaseTuningAdvisor")
    async def test_returns_recommendations_dict(
        self,
        mock_dta_cls: MagicMock,
        mock_presentation_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """analyze_query_indexes with valid queries returns the presentation result."""
        mock_presentation = MagicMock()
        mock_presentation.analyze_queries = AsyncMock(return_value={"recommendations": []})
        mock_presentation_cls.return_value = mock_presentation

        service = IndexAnalysisService(db=mock_db_access)
        result = await service.analyze_query_indexes(queries=["SELECT 1"], max_index_size_mb=10)

        assert result == {"recommendations": []}
        mock_presentation.analyze_queries.assert_awaited_once_with(queries=["SELECT 1"], max_index_size_mb=10)

    async def test_empty_queries_raises(self, mock_db_access: MagicMock) -> None:
        """analyze_query_indexes(queries=[]) raises EmptyQueriesError."""
        service = IndexAnalysisService(db=mock_db_access)
        with pytest.raises(EmptyQueriesError):
            await service.analyze_query_indexes(queries=[])

    async def test_over_limit_raises(self, mock_db_access: MagicMock) -> None:
        """More than MAX_NUM_INDEX_TUNING_QUERIES queries raises QueriesLimitError."""
        service = IndexAnalysisService(db=mock_db_access)
        too_many = ["SELECT 1"] * (MAX_NUM_INDEX_TUNING_QUERIES + 1)
        with pytest.raises(QueriesLimitError) as exc_info:
            await service.analyze_query_indexes(queries=too_many)
        assert exc_info.value.limit == MAX_NUM_INDEX_TUNING_QUERIES
