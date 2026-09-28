# mypy: ignore-errors
"""Unit tests for CandidateGenerator internals that must not be SQL-injectable."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from postgres_fastmcp.domains.index_tuning.candidates import CandidateGenerator
from postgres_fastmcp.domains.index_tuning.models import IndexRecommendation
from postgres_fastmcp.postgres.models import RowResult


class TestEstimateHypotheticalIndexSizes:
    """Создание гипотетических индексов и чтение их размеров идут одним execute на одном соединении.

    Пул возвращает соединение в исходное состояние (hypopg_reset) сразу после того, как оно
    освободилось: раздельные execute для create и для hypopg_list_indexes получили бы для чтения
    уже другое, чистое соединение, и estimated_size_bytes остался бы 0.
    """

    @pytest.mark.asyncio
    async def test_creates_and_size_query_are_sent_as_one_statement(self) -> None:
        driver = AsyncMock()
        candidate_a = IndexRecommendation("t", ("a",))
        candidate_b = IndexRecommendation("t", ("b",))
        driver.execute = AsyncMock(
            return_value=[
                RowResult(cells={"index_name": candidate_a.name, "index_size": 4096}),
                RowResult(cells={"index_name": candidate_b.name, "index_size": 8192}),
            ]
        )
        generator = CandidateGenerator(driver, MagicMock())

        await generator._estimate_hypothetical_index_sizes([candidate_a, candidate_b])

        driver.execute.assert_awaited_once()
        sent_query = driver.execute.call_args.args[0]
        sent_params = driver.execute.call_args.kwargs["params"]

        assert sent_query.count("hypopg_create_index({})") == 2
        assert "hypopg_list_indexes" in sent_query
        # Один statement, а не отдельный сброс/чтение на другом соединении из пула.
        assert "hypopg_reset" not in sent_query
        assert sent_params == [candidate_a.definition, candidate_b.definition]
        assert candidate_a.estimated_size_bytes == 4096
        assert candidate_b.estimated_size_bytes == 8192

    @pytest.mark.asyncio
    async def test_unmatched_candidate_keeps_its_size(self) -> None:
        """Кандидат, не попавший в hypopg_list_indexes (сборка индекса hypopg не удалась), не меняется."""
        driver = AsyncMock()
        candidate = IndexRecommendation("t", ("a",), estimated_size_bytes=0)
        driver.execute = AsyncMock(return_value=[])
        generator = CandidateGenerator(driver, MagicMock())

        await generator._estimate_hypothetical_index_sizes([candidate])

        assert candidate.estimated_size_bytes == 0

    @pytest.mark.asyncio
    async def test_no_candidates_does_not_call_the_driver(self) -> None:
        driver = AsyncMock()
        generator = CandidateGenerator(driver, MagicMock())

        await generator._estimate_hypothetical_index_sizes([])

        driver.execute.assert_not_awaited()


class TestFilterLongTextColumnsParameterization:
    """_filter_long_text_columns must pass table/column names as parameters, never interpolate them."""

    @pytest.mark.asyncio
    async def test_table_name_is_parameterized_not_interpolated(self) -> None:
        """A table name from a parsed query must not be embedded in the SQL text."""
        driver = AsyncMock()
        driver.execute = AsyncMock(return_value=[])
        generator = CandidateGenerator(driver, MagicMock())

        malicious = "users'); DROP TABLE secrets; --"
        await generator._filter_long_text_columns([IndexRecommendation(malicious, ("id",))])

        driver.execute.assert_awaited_once()
        sent_query = driver.execute.call_args.args[0]
        params = driver.execute.call_args.kwargs["params"]

        # The name is carried as a parameter, and the query uses placeholders (ANY({})).
        assert malicious not in sent_query
        assert "ANY({})" in sent_query
        assert malicious in params[1]
