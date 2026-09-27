# mypy: ignore-errors
"""Unit tests for QueryValidator (pure logic, no mocks)."""

import pytest

from postgres_fastmcp.postgres.security.query_validator import QueryValidator
from postgres_fastmcp.shared.errors import (
    CreateExtensionNotSupportedError,
    DdlNotAllowedError,
    ExplainAnalyzeNotSupportedError,
    FunctionNotAllowedError,
    SchemaNotAllowedError,
    SqlParseError,
    StatementTypeNotAllowedError,
    TablePrefixAccessError,
    UserFacingError,
)


class TestQueryValidatorReadOnly:
    """Read-only mode: SELECT allowed, DDL/DML blocked."""

    def test_allows_select(self) -> None:
        """Simple SELECT is allowed."""
        v = QueryValidator(read_only=True)
        v.validate("SELECT 1")
        v.validate("SELECT * FROM t")
        v.validate("SELECT a, b FROM public.t WHERE x = 1")

    def test_blocks_drop_table(self) -> None:
        """DROP TABLE raises StatementTypeNotAllowedError."""
        v = QueryValidator(read_only=True)
        with pytest.raises(StatementTypeNotAllowedError) as exc_info:
            v.validate("DROP TABLE users")
        assert "read-only" in str(exc_info.value).lower() or "DROP" in str(exc_info.value)

    def test_blocks_insert(self) -> None:
        """INSERT raises StatementTypeNotAllowedError in read_only mode."""
        v = QueryValidator(read_only=True)
        with pytest.raises(StatementTypeNotAllowedError):
            v.validate("INSERT INTO t (a) VALUES (1)")

    def test_blocks_create_table(self) -> None:
        """CREATE TABLE raises StatementTypeNotAllowedError or DdlNotAllowedError."""
        v = QueryValidator(read_only=True)
        with pytest.raises((StatementTypeNotAllowedError, DdlNotAllowedError)):
            v.validate("CREATE TABLE t (id int)")

    def test_blocks_vacuum(self) -> None:
        """VACUUM writes to disk and must be rejected in read-only mode."""
        v = QueryValidator(read_only=True)
        with pytest.raises(StatementTypeNotAllowedError):
            v.validate("VACUUM users")

    def test_blocks_analyze(self) -> None:
        """ANALYZE (a VacuumStmt in the grammar) writes statistics and is rejected in read-only mode."""
        v = QueryValidator(read_only=True)
        with pytest.raises(StatementTypeNotAllowedError):
            v.validate("ANALYZE users")

    def test_parse_error_raises_sql_parse_error(self) -> None:
        """Invalid SQL raises SqlParseError."""
        v = QueryValidator(read_only=True)
        with pytest.raises(SqlParseError) as exc_info:
            v.validate("SELEC 1")
        assert "parse" in str(exc_info.value).lower() or "Failed" in str(exc_info.value)


class TestQueryValidatorSchemaGuard:
    """allowed_schema and table_prefix restrictions."""

    def test_allowed_schema_public_rejects_other_schema(self) -> None:
        """allowed_schema=public rejects query referencing other schema."""
        v = QueryValidator(read_only=True, allowed_schema="public")
        with pytest.raises(SchemaNotAllowedError) as exc_info:
            v.validate("SELECT * FROM other_schema.t")
        assert "other_schema" in str(exc_info.value) or "not allowed" in str(exc_info.value)

    def test_allowed_schema_public_accepts_public(self) -> None:
        """allowed_schema=public accepts public.t."""
        v = QueryValidator(read_only=True, allowed_schema="public")
        v.validate("SELECT * FROM public.t")

    def test_table_prefix_rejects_non_matching_table(self) -> None:
        """table_prefix filters table names."""
        v = QueryValidator(read_only=True, allowed_schema="public", table_prefix="app_")
        with pytest.raises(TablePrefixAccessError):
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
        """Disallowed function raises FunctionNotAllowedError."""
        v = QueryValidator(read_only=True)
        with pytest.raises(FunctionNotAllowedError) as exc_info:
            v.validate("SELECT pg_sleep(1)")
        assert "not allowed" in str(exc_info.value).lower() or "pg_sleep" in str(exc_info.value)

    def test_blocks_disallowed_table_function(self) -> None:
        """Disallowed function in FROM (RangeFunction) is caught by the pglast traversal."""
        v = QueryValidator(read_only=True)
        with pytest.raises(FunctionNotAllowedError):
            v.validate("SELECT * FROM pg_sleep(1)")

    def test_blocks_disallowed_function_inside_values(self) -> None:
        """Disallowed function inside a VALUES list (nested tuple) is caught."""
        v = QueryValidator(read_only=True)
        with pytest.raises(FunctionNotAllowedError):
            v.validate("SELECT * FROM (VALUES (pg_sleep(1))) AS v(x)")

    def test_allows_values_list(self) -> None:
        """Plain VALUES lists with constants pass validation."""
        v = QueryValidator(read_only=True)
        v.validate("SELECT * FROM (VALUES (1), (2)) AS v(x)")

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM generate_series(1, 10)",
            "SELECT generate_subscripts(ARRAY[1,2], 1)",
            "SELECT created_at AT TIME ZONE 'UTC' FROM t",
            "SELECT * FROM t WHERE name SIMILAR TO 'a%'",
            "SELECT * FROM json_to_recordset('[{\"a\":1}]') AS x(a int)",
            "SELECT * FROM jsonb_to_recordset('[]'::jsonb) AS x(a int, b text)",
        ],
    )
    def test_allows_common_read_only_constructs(self, sql: str) -> None:
        """Everyday SELECT constructs must not be rejected as unsafe."""
        v = QueryValidator(read_only=True)
        v.validate(sql)

    def test_column_def_does_not_unlock_create_table(self) -> None:
        """ColumnDef is allowed as an AST node, but CREATE TABLE is still rejected at statement level."""
        v = QueryValidator(read_only=False)
        with pytest.raises((StatementTypeNotAllowedError, DdlNotAllowedError)):
            v.validate("CREATE TABLE t (id int)")


class TestQueryValidatorExplainAnalyze:
    """EXPLAIN ANALYZE is blocked by default, allowed when allow_explain_analyze=True."""

    def test_explain_analyze_raises_when_not_allowed(self) -> None:
        """EXPLAIN (ANALYZE) raises ExplainAnalyzeNotSupportedError when allow_explain_analyze=False."""
        v = QueryValidator(read_only=True)
        with pytest.raises(ExplainAnalyzeNotSupportedError) as exc_info:
            v.validate("EXPLAIN (ANALYZE) SELECT 1")
        assert "ANALYZE" in str(exc_info.value) or "not supported" in str(exc_info.value).lower()

    def test_explain_analyze_allowed_when_flag_true(self) -> None:
        """EXPLAIN (ANALYZE) passes validation when allow_explain_analyze=True."""
        v = QueryValidator(read_only=True, allow_explain_analyze=True)
        v.validate("EXPLAIN (ANALYZE) SELECT 1")


class TestQueryValidatorCreateExtension:
    """CREATE EXTENSION: only in write mode, only hypopg / pg_stat_statements."""

    @pytest.mark.parametrize("extname", ["hypopg", "pg_stat_statements", "dblink"])
    def test_read_only_rejects_any_create_extension(self, extname: str) -> None:
        """In read-only mode CREATE EXTENSION is rejected by statement type before touching the DB."""
        v = QueryValidator(read_only=True)
        with pytest.raises(StatementTypeNotAllowedError):
            v.validate(f"CREATE EXTENSION {extname}")

    @pytest.mark.parametrize("extname", ["hypopg", "pg_stat_statements"])
    def test_write_mode_allows_whitelisted_extension(self, extname: str) -> None:
        """Write mode allows the two extensions the server itself relies on."""
        v = QueryValidator(read_only=False)
        v.validate(f"CREATE EXTENSION IF NOT EXISTS {extname}")

    @pytest.mark.parametrize("extname", ["dblink", "file_fdw", "plpython3u", "unknown_ext"])
    def test_write_mode_rejects_other_extensions(self, extname: str) -> None:
        """Any other extension is rejected with CreateExtensionNotSupportedError."""
        v = QueryValidator(read_only=False)
        with pytest.raises(CreateExtensionNotSupportedError) as exc_info:
            v.validate(f"CREATE EXTENSION {extname}")
        assert extname in str(exc_info.value)

    def test_write_mode_with_allowed_schema_rejects_schema_option(self) -> None:
        """With allowed_schema set, SCHEMA would place the extension outside it: rejected, option named."""
        v = QueryValidator(allowed_schema="public", read_only=False)
        with pytest.raises(CreateExtensionNotSupportedError) as exc_info:
            v.validate("CREATE EXTENSION hypopg SCHEMA secret")
        assert "SCHEMA" in str(exc_info.value)

    def test_write_mode_without_allowed_schema_allows_schema_option(self) -> None:
        """Without a schema restriction the SCHEMA option is not a policy violation."""
        QueryValidator(read_only=False).validate("CREATE EXTENSION hypopg SCHEMA secret")

    @pytest.mark.parametrize("allowed_schema", [None, "public"], ids=["no-schema", "public"])
    def test_write_mode_rejects_cascade(self, allowed_schema: str | None) -> None:
        """CASCADE could install dependencies outside the allowlist, so it is always rejected."""
        v = QueryValidator(allowed_schema=allowed_schema, read_only=False)
        with pytest.raises(CreateExtensionNotSupportedError) as exc_info:
            v.validate("CREATE EXTENSION hypopg CASCADE")
        assert "CASCADE" in str(exc_info.value)

    def test_write_mode_with_allowed_schema_allows_plain_if_not_exists(self) -> None:
        """The form the server itself uses still passes in basic write mode."""
        QueryValidator(allowed_schema="public", read_only=False).validate("CREATE EXTENSION IF NOT EXISTS hypopg")


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

    def test_vacuum_rejected_even_in_write_mode(self) -> None:
        """VACUUM cannot run in the executor's wrapped transaction, so it is rejected in every mode."""
        v = QueryValidator(read_only=False)
        with pytest.raises(StatementTypeNotAllowedError):
            v.validate("VACUUM users")

    @pytest.mark.parametrize(
        "sql",
        [
            "INSERT INTO t (a) VALUES (1) RETURNING id",
            "UPDATE t SET a = 1 WHERE id = 1 RETURNING *",
            "WITH w AS (INSERT INTO t VALUES (1) RETURNING *) SELECT * FROM w",
        ],
    )
    def test_returning_allowed_in_write_mode(self, sql: str) -> None:
        """RETURNING is part of DML and must pass when DML is allowed."""
        QueryValidator(read_only=False).validate(sql)

    def test_returning_still_blocked_in_read_only(self) -> None:
        """A data-modifying CTE stays rejected in read-only mode."""
        with pytest.raises(UserFacingError):
            QueryValidator(read_only=True).validate("WITH w AS (INSERT INTO t VALUES (1) RETURNING *) SELECT * FROM w")
