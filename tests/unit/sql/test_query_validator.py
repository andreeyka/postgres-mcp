# mypy: ignore-errors
"""Unit tests for QueryValidator (pure logic, no mocks)."""

import pytest

from postgres_fastmcp.sql.validation.query_validator import QueryValidator


class TestQueryValidatorReadOnly:
    """Read-only mode: SELECT allowed, DDL/DML blocked."""

    def test_allows_select(self) -> None:
        """Simple SELECT is allowed."""
        v = QueryValidator(read_only=True)
        v.validate("SELECT 1")
        v.validate("SELECT * FROM t")
        v.validate("SELECT a, b FROM public.t WHERE x = 1")

    def test_blocks_drop_table(self) -> None:
        """DROP TABLE raises TypeError (statement type not allowed)."""
        v = QueryValidator(read_only=True)
        with pytest.raises(TypeError) as exc_info:
            v.validate("DROP TABLE users")
        assert "read-only" in str(exc_info.value).lower() or "DROP" in str(exc_info.value)

    def test_blocks_insert(self) -> None:
        """INSERT raises TypeError in read_only mode."""
        v = QueryValidator(read_only=True)
        with pytest.raises(TypeError):
            v.validate("INSERT INTO t (a) VALUES (1)")

    def test_blocks_create_table(self) -> None:
        """CREATE TABLE raises ValueError (DDL not allowed)."""
        v = QueryValidator(read_only=True)
        with pytest.raises((TypeError, ValueError)):
            v.validate("CREATE TABLE t (id int)")

    def test_parse_error_raises_value_error(self) -> None:
        """Invalid SQL raises ValueError with parse message."""
        v = QueryValidator(read_only=True)
        with pytest.raises(ValueError) as exc_info:
            v.validate("SELEC 1")
        assert "parse" in str(exc_info.value).lower() or "Failed" in str(exc_info.value)


class TestQueryValidatorSchemaGuard:
    """allowed_schema and table_prefix restrictions."""

    def test_allowed_schema_public_rejects_other_schema(self) -> None:
        """allowed_schema=public rejects query referencing other schema."""
        v = QueryValidator(read_only=True, allowed_schema="public")
        with pytest.raises(ValueError) as exc_info:
            v.validate("SELECT * FROM other_schema.t")
        assert "other_schema" in str(exc_info.value) or "not allowed" in str(exc_info.value)

    def test_allowed_schema_public_accepts_public(self) -> None:
        """allowed_schema=public accepts public.t."""
        v = QueryValidator(read_only=True, allowed_schema="public")
        v.validate("SELECT * FROM public.t")

    def test_table_prefix_rejects_non_matching_table(self) -> None:
        """table_prefix filters table names."""
        v = QueryValidator(read_only=True, allowed_schema="public", table_prefix="app_")
        with pytest.raises(ValueError):
            v.validate("SELECT * FROM public.other_table")

    def test_table_prefix_accepts_matching_table(self) -> None:
        """table_prefix accepts tables starting with prefix."""
        v = QueryValidator(read_only=True, allowed_schema="public", table_prefix="app_")
        v.validate("SELECT * FROM public.app_users")


class TestQueryValidatorFunctions:
    """Function whitelist."""

    def test_allows_whitelisted_function(self) -> None:
        """Allowed aggregate/function names pass."""
        v = QueryValidator(read_only=True)
        v.validate("SELECT count(*) FROM t")
        v.validate("SELECT sum(x) FROM t")

    def test_blocks_disallowed_function(self) -> None:
        """Disallowed function raises ValueError."""
        v = QueryValidator(read_only=True)
        with pytest.raises(ValueError) as exc_info:
            v.validate("SELECT pg_sleep(1)")
        assert "not allowed" in str(exc_info.value).lower() or "pg_sleep" in str(exc_info.value)


class TestQueryValidatorExplainAnalyze:
    """EXPLAIN ANALYZE is explicitly blocked."""

    def test_explain_analyze_raises(self) -> None:
        """EXPLAIN (ANALYZE) raises ValueError."""
        v = QueryValidator(read_only=True)
        with pytest.raises(ValueError) as exc_info:
            v.validate("EXPLAIN (ANALYZE) SELECT 1")
        assert "ANALYZE" in str(exc_info.value) or "not supported" in str(exc_info.value).lower()


class TestQueryValidatorCreateExtension:
    """CREATE EXTENSION whitelist."""

    def test_create_extension_disallowed_raises(self) -> None:
        """CREATE EXTENSION with non-whitelisted name raises."""
        v = QueryValidator(read_only=True)
        with pytest.raises(ValueError) as exc_info:
            v.validate("CREATE EXTENSION unknown_ext")
        assert "unknown_ext" in str(exc_info.value) or "not supported" in str(exc_info.value).lower()


class TestQueryValidatorDmlMode:
    """read_only=False allows DML."""

    def test_allows_insert_when_not_read_only(self) -> None:
        """With read_only=False, INSERT is allowed (statement type)."""
        v = QueryValidator(read_only=False)
        v.validate("INSERT INTO t (a) VALUES (1)")

    def test_allows_delete_when_not_read_only(self) -> None:
        """With read_only=False, DELETE is allowed."""
        v = QueryValidator(read_only=False)
        v.validate("DELETE FROM t WHERE id = 1")
