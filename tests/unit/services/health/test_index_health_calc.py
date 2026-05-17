# mypy: ignore-errors
"""Unit tests for IndexHealthCalc."""

from unittest.mock import AsyncMock

import pytest

from postgres_fastmcp.services.health.index_health_calc import IndexHealthCalc
from postgres_fastmcp.sql.models.row_result import RowResult


@pytest.fixture
def mock_sql_driver() -> AsyncMock:
    """Mock SQL driver for IndexHealthCalc."""
    return AsyncMock()


@pytest.fixture
def calc(mock_sql_driver: AsyncMock) -> IndexHealthCalc:
    """IndexHealthCalc instance with mocked driver."""
    return IndexHealthCalc(mock_sql_driver)


def test_cached_indexes_is_instance_level() -> None:
    """Cache must be per-instance to avoid leaking between connections."""
    from postgres_fastmcp.services.health.index_health_calc import IndexHealthCalc

    inst_a = IndexHealthCalc.__new__(IndexHealthCalc)
    inst_b = IndexHealthCalc.__new__(IndexHealthCalc)
    inst_a._cached_indexes = ["index_a"]
    inst_b._cached_indexes = ["index_b"]
    assert inst_a._cached_indexes == ["index_a"]
    assert inst_b._cached_indexes == ["index_b"]
    # Class-level attribute must NOT be set to a list — only a type annotation is acceptable.
    class_attr = IndexHealthCalc.__dict__.get("_cached_indexes")
    assert class_attr is None or not isinstance(class_attr, list)


class TestIndexHealthCalcInvalidIndexCheck:
    """Tests for invalid_index_check."""

    @pytest.mark.asyncio
    async def test_no_invalid_indexes(self, calc: IndexHealthCalc, mock_sql_driver: AsyncMock) -> None:
        """Scenario: catalog returns only valid indexes; report says 'No invalid indexes found.'"""
        mock_sql_driver.execute.return_value = [
            RowResult(
                cells={
                    "schema": "public",
                    "table": "t",
                    "name": "ix",
                    "columns": "id",
                    "using": "btree",
                    "unique": False,
                    "primary": False,
                    "valid": True,
                    "indexprs": None,
                    "indpred": None,
                    "definition": "",
                }
            ),
        ]
        result = await calc.invalid_index_check()
        assert result == "No invalid indexes found."

    @pytest.mark.asyncio
    async def test_invalid_indexes_found(self, calc: IndexHealthCalc, mock_sql_driver: AsyncMock) -> None:
        """Scenario: catalog returns invalid index; report contains 'Invalid indexes found', index and table name."""
        mock_sql_driver.execute.return_value = [
            RowResult(
                cells={
                    "schema": "public",
                    "table": "users",
                    "name": "ix_bad",
                    "columns": "id",
                    "using": "btree",
                    "unique": False,
                    "primary": False,
                    "valid": False,
                    "indexprs": None,
                    "indpred": None,
                    "definition": "CREATE INDEX ...",
                }
            ),
        ]
        result = await calc.invalid_index_check()
        assert "Invalid indexes found" in result
        assert "ix_bad" in result
        assert "users" in result


class TestIndexHealthCalcDuplicateIndexCheck:
    """Tests for duplicate_index_check."""

    @pytest.mark.asyncio
    async def test_no_duplicates(self, calc: IndexHealthCalc, mock_sql_driver: AsyncMock) -> None:
        """Scenario: no duplicate indexes in catalog; report says 'No duplicate indexes found.'"""
        mock_sql_driver.execute.return_value = [
            RowResult(
                cells={
                    "schema": "public",
                    "table": "t",
                    "name": "ix_a",
                    "columns": "a",
                    "using": "btree",
                    "unique": False,
                    "primary": False,
                    "valid": True,
                    "indexprs": None,
                    "indpred": None,
                    "definition": "",
                }
            ),
        ]
        result = await calc.duplicate_index_check()
        assert result == "No duplicate indexes found."


class TestIndexHealthCalcIndexCovers:
    """Tests for _index_covers."""

    def test_prefix_match(self, calc: IndexHealthCalc) -> None:
        """Index columns [a,b,c] cover query columns [a,b] (prefix match)."""
        assert calc._index_covers(["a", "b", "c"], ["a", "b"]) is True

    def test_exact_match(self, calc: IndexHealthCalc) -> None:
        """Index columns [a,b] cover query columns [a,b] (exact match)."""
        assert calc._index_covers(["a", "b"], ["a", "b"]) is True

    def test_no_cover(self, calc: IndexHealthCalc) -> None:
        """Index columns [a,x] do not cover query columns [a,b]."""
        assert calc._index_covers(["a", "x"], ["a", "b"]) is False

    def test_empty_columns_covered(self, calc: IndexHealthCalc) -> None:
        """Empty query columns are covered by any index."""
        assert calc._index_covers(["a"], []) is True


class TestIndexHealthCalcIndexes:
    """Tests for _indexes (column parsing)."""

    @pytest.mark.asyncio
    async def test_columns_parsed_and_unquoted(self, calc: IndexHealthCalc, mock_sql_driver: AsyncMock) -> None:
        """Scenario: catalog returns quoted columns string; _indexes parses to list of column names."""
        mock_sql_driver.execute.return_value = [
            RowResult(
                cells={
                    "schema": "public",
                    "table": "t",
                    "name": "ix",
                    "columns": '"col_a", "col_b"',
                    "using": "btree",
                    "unique": False,
                    "primary": False,
                    "valid": True,
                    "indexprs": None,
                    "indpred": None,
                    "definition": "",
                }
            ),
        ]
        indexes = await calc._indexes()
        assert len(indexes) == 1
        assert indexes[0]["columns"] == ["col_a", "col_b"]

    @pytest.mark.asyncio
    async def test_columns_none_becomes_empty_list(self, calc: IndexHealthCalc, mock_sql_driver: AsyncMock) -> None:
        """Scenario: catalog returns None for columns; _indexes yields empty list for that index."""
        mock_sql_driver.execute.return_value = [
            RowResult(
                cells={
                    "schema": "public",
                    "table": "t",
                    "name": "ix",
                    "columns": None,
                    "using": "btree",
                    "unique": False,
                    "primary": False,
                    "valid": True,
                    "indexprs": None,
                    "indpred": None,
                    "definition": "",
                }
            ),
        ]
        indexes = await calc._indexes()
        assert indexes[0]["columns"] == []

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_execute_returns_none(
        self, calc: IndexHealthCalc, mock_sql_driver: AsyncMock
    ) -> None:
        """Scenario: driver returns None; _indexes returns empty list."""
        mock_sql_driver.execute.return_value = None
        indexes = await calc._indexes()
        assert indexes == []


class TestIndexHealthCalcIndexBloat:
    """Tests for index_bloat."""

    @pytest.mark.asyncio
    async def test_no_bloated_indexes(self, calc: IndexHealthCalc, mock_sql_driver: AsyncMock) -> None:
        """Scenario: no bloated indexes above min_size; report says 'No bloated indexes found.'"""
        mock_sql_driver.execute.return_value = []
        result = await calc.index_bloat(min_size=104857600)
        assert result == "No bloated indexes found."


class TestIndexHealthCalcUnusedIndexes:
    """Tests for unused_indexes."""

    @pytest.mark.asyncio
    async def test_no_unused_indexes(self, calc: IndexHealthCalc, mock_sql_driver: AsyncMock) -> None:
        """Scenario: no rarely-used indexes; report says 'No unused indexes found.'"""
        mock_sql_driver.execute.return_value = []
        result = await calc.unused_indexes(max_scans=50)
        assert result == "No unused indexes found."

    @pytest.mark.asyncio
    async def test_skips_primary_in_result(self, calc: IndexHealthCalc, mock_sql_driver: AsyncMock) -> None:
        """Scenario: primary key in result is skipped; report does not list t_pkey."""
        mock_sql_driver.execute.return_value = [
            RowResult(
                cells={
                    "schema": "public",
                    "table": "t",
                    "index": "t_pkey",
                    "size_bytes": 1024,
                    "index_scans": 0,
                    "definition": "",
                    "primary": True,
                }
            ),
        ]
        result = await calc.unused_indexes(max_scans=50)
        assert "Rarely used indexes found:" in result
        # Primary key row is skipped (continue), so no index line
        assert "t_pkey" not in result

    @pytest.mark.asyncio
    async def test_reports_non_primary_unused(self, calc: IndexHealthCalc, mock_sql_driver: AsyncMock) -> None:
        """Scenario: non-primary index with low scans; report lists index name, table, and size (e.g. 2.0MB)."""
        mock_sql_driver.execute.return_value = [
            RowResult(
                cells={
                    "schema": "public",
                    "table": "users",
                    "index": "ix_foo",
                    "size_bytes": 2 * 1024 * 1024,
                    "index_scans": 10,
                    "definition": "",
                    "primary": False,
                }
            ),
        ]
        result = await calc.unused_indexes(max_scans=50)
        assert "ix_foo" in result
        assert "users" in result
        assert "2.0MB" in result
