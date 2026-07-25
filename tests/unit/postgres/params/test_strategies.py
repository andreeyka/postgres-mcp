# mypy: ignore-errors
"""Unit tests for sql.params.strategies."""

import re

import pytest

from postgres_fastmcp.postgres.params.strategies import (
    context_replace,
    get_bound_values,
    get_generic_replacement,
    get_replacement_value,
    parse_pg_array_value,
)


class TestParsePgArrayValue:
    """Tests for parse_pg_array_value."""

    def test_null_returns_none(self) -> None:
        assert parse_pg_array_value("null") is None
        assert parse_pg_array_value("  null  ") is None

    def test_quoted_string_unquotes(self) -> None:
        assert parse_pg_array_value('"hello"') == "hello"

    def test_integer(self) -> None:
        assert parse_pg_array_value("42") == 42
        assert parse_pg_array_value("  -1  ") == -1

    def test_float(self) -> None:
        assert parse_pg_array_value("3.14") == 3.14
        assert parse_pg_array_value("  2.5  ") == 2.5

    def test_non_numeric_string_returns_as_is(self) -> None:
        assert parse_pg_array_value("abc") == "abc"


class TestGetBoundValues:
    """Tests for get_bound_values."""

    def test_common_vals_float_lower(self) -> None:
        stats = {"common_vals": [1.0, 10.0], "common_freqs": [0.1, 0.5]}
        assert get_bound_values(stats, is_lower=True) == pytest.approx(9.5)

    def test_common_vals_int_upper(self) -> None:
        stats = {"common_vals": [100], "common_freqs": [0.8]}
        assert get_bound_values(stats, is_lower=False) == 105

    def test_histogram_bounds(self) -> None:
        bounds = [1, 5, 10, 15, 20, 25, 30]
        stats = {"histogram_bounds": bounds}
        lower = get_bound_values(stats, is_lower=True)
        upper = get_bound_values(stats, is_lower=False)
        assert lower in bounds
        assert upper in bounds

    def test_most_common_vals_fallback(self) -> None:
        stats = {"most_common_vals": ["x"]}
        assert get_bound_values(stats, is_lower=True) == "x"

    def test_int_type_fallback(self) -> None:
        stats = {"data_type": "integer"}
        assert get_bound_values(stats, is_lower=True) == 10
        assert get_bound_values(stats, is_lower=False) == 20

    def test_boolean_fallback(self) -> None:
        stats = {"data_type": "boolean"}
        assert get_bound_values(stats, is_lower=True) == "true"


class TestGetReplacementValue:
    """Tests for get_replacement_value."""

    def test_text_like(self) -> None:
        stats = {"data_type": "text"}
        assert get_replacement_value(stats, "col LIKE $1") == "'%test%'"

    def test_text_common_vals(self) -> None:
        stats = {"data_type": "varchar", "common_vals": ["hello"]}
        assert get_replacement_value(stats, "col = $1") == "'hello'"

    def test_int_histogram_range(self) -> None:
        stats = {"data_type": "integer", "histogram_bounds": [1, 2, 3, 4, 5]}
        assert get_replacement_value(stats, "col > $1") == "3"

    def test_int_equality_common_vals(self) -> None:
        stats = {"data_type": "bigint", "common_vals": [42]}
        assert get_replacement_value(stats, "col = $1") == "42"

    def test_date_range(self) -> None:
        stats = {"data_type": "date"}
        assert get_replacement_value(stats, "col BETWEEN $1 AND $2") == "'2023-01-15'"

    def test_boolean(self) -> None:
        stats = {"data_type": "boolean"}
        assert get_replacement_value(stats, "col = $1") == "true"


class TestGetGenericReplacement:
    """Tests for get_generic_replacement."""

    def test_date_context(self) -> None:
        assert get_generic_replacement("timestamp column") == "'2023-01-01'"

    def test_id_context(self) -> None:
        assert get_generic_replacement("user_id") == "43"

    def test_like_context(self) -> None:
        assert get_generic_replacement("name like") == "'%sample%'"

    def test_amount_context(self) -> None:
        assert get_generic_replacement("price") == "99.99"

    def test_comparison_context(self) -> None:
        assert get_generic_replacement("x >") == "44"

    def test_default(self) -> None:
        assert get_generic_replacement("unknown") == "'sample_value'"


class TestContextReplace:
    """Tests for context_replace."""

    def _match(self, group1: str) -> re.Match[str]:
        m = re.match(r"(.+)", group1)
        assert m is not None
        return m

    def test_id_column(self) -> None:
        m = self._match("user_id")
        assert context_replace(m, "=") == "user_id = 46"

    def test_date_column(self) -> None:
        m = self._match("created_at")
        assert context_replace(m, ">") == "created_at > '2023-01-01'"

    def test_amount_column(self) -> None:
        m = self._match("price")
        assert context_replace(m, "<=") == "price <= 46.5"

    def test_status_column(self) -> None:
        m = self._match("status")
        assert context_replace(m, "=") == "status = 'active'"

    def test_generic_column(self) -> None:
        m = self._match("name")
        assert context_replace(m, "=") == "name = 'sample_value'"
