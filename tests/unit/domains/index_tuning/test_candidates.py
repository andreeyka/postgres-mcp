# mypy: ignore-errors
"""Unit tests for CandidateGenerator internals that must not be SQL-injectable."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from postgres_fastmcp.domains.index_tuning.candidates import CandidateGenerator
from postgres_fastmcp.domains.index_tuning.models import IndexRecommendation
from postgres_fastmcp.postgres.driver import SqlExecutor
from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.postgres.security.query_validator import QueryValidator


class TestEstimateHypotheticalIndexSizes:
    """Создание гипотетических индексов и чтение их размеров идут одним execute на одном соединении.

    Пул возвращает соединение в исходное состояние (hypopg_reset) сразу после того, как оно
    освободилось: раздельные execute для create и для чтения размеров получили бы для чтения
    уже другое, чистое соединение, и estimated_size_bytes остался бы 0.

    Размеры сопоставляются с кандидатами по позиции (ord из WITH ORDINALITY), а не по имени:
    hypopg называет индекс сам (``<oid>btree_t_a``), и с IndexRecommendation.name оно не совпадает.
    """

    @pytest.mark.asyncio
    async def test_sizes_are_matched_to_candidates_by_position(self) -> None:
        driver = AsyncMock()
        candidate_a = IndexRecommendation("t", ("a",))
        candidate_b = IndexRecommendation("t", ("b",))
        # Строки как у настоящего запроса: только позиция и размер, имён кандидатов в них нет.
        driver.execute = AsyncMock(
            return_value=[
                RowResult(cells={"ord": 1, "index_size": 4096}),
                RowResult(cells={"ord": 2, "index_size": 8192}),
            ]
        )
        generator = CandidateGenerator(driver, MagicMock())

        await generator._estimate_hypothetical_index_sizes([candidate_a, candidate_b])

        driver.execute.assert_awaited_once()
        sent_query = driver.execute.call_args.args[0]
        sent_params = driver.execute.call_args.kwargs["params"]

        assert sent_query.count("{}") == 1
        assert "WITH ORDINALITY" in sent_query
        assert "hypopg_create_index" in sent_query
        # Один statement, а не отдельный сброс/чтение на другом соединении из пула.
        assert "hypopg_reset" not in sent_query
        assert sent_params == [[candidate_a.definition, candidate_b.definition]]
        assert candidate_a.estimated_size_bytes == 4096
        assert candidate_b.estimated_size_bytes == 8192

    @pytest.mark.asyncio
    async def test_rendered_query_passes_the_full_mode_validator(self) -> None:
        """DTA работает в full: SafeSqlExecutor (read-only и запись) пропускает запрос размеров."""
        driver = AsyncMock()
        driver.execute = AsyncMock(return_value=[])
        generator = CandidateGenerator(driver, MagicMock())

        await generator._estimate_hypothetical_index_sizes(
            [IndexRecommendation("t", ("a",)), IndexRecommendation("o'k", ("b", "c"))]
        )

        call = driver.execute.call_args
        rendered = SqlExecutor(engine_url="postgresql://localhost/test").render(call.args[0], call.kwargs["params"])
        assert "hypopg_create_index(d.definition)" in rendered

        for read_only in (True, False):
            QueryValidator(read_only=read_only).validate(f"/* tag */ {rendered}")

    @pytest.mark.asyncio
    async def test_missing_row_keeps_the_candidate_size(self) -> None:
        """Кандидат без строки в результате не меняется."""
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
