# mypy: ignore-errors
"""Unit tests for CandidateGenerator internals that must not be SQL-injectable."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from postgres_fastmcp.domains.index_tuning.candidates import CandidateGenerator
from postgres_fastmcp.domains.index_tuning.models import IndexRecommendation


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
