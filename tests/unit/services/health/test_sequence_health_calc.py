# mypy: ignore-errors
"""Unit tests for SequenceHealthCalc max-value resolution per column type."""

from postgres_fastmcp.services.health.sequence_health_calc import SequenceHealthCalc


class TestSequenceMaxValueForType:
    """_max_value_for_type must use the correct ceiling per integer width."""

    def test_smallint(self) -> None:
        """smallint sequences top out at 32767, not the bigint ceiling."""
        assert SequenceHealthCalc._max_value_for_type("smallint") == 32767

    def test_integer(self) -> None:
        assert SequenceHealthCalc._max_value_for_type("integer") == 2147483647

    def test_bigint(self) -> None:
        assert SequenceHealthCalc._max_value_for_type("bigint") == 9223372036854775807

    def test_unknown_type_defaults_to_bigint(self) -> None:
        assert SequenceHealthCalc._max_value_for_type("numeric") == 9223372036854775807
