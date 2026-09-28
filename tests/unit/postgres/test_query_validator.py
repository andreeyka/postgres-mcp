# mypy: ignore-errors
"""Unit tests for QueryValidator (pure logic, no mocks)."""

import pytest

from postgres_fastmcp.postgres.security.query_validator import QueryValidator
from postgres_fastmcp.shared.errors import (
    CreateExtensionNotSupportedError,
    DdlNotAllowedError,
    ExplainAnalyzeNotSupportedError,
    ExplainOptionNotAllowedError,
    FunctionNotAllowedError,
    SchemaNotAllowedError,
    ShowParameterNotAllowedError,
    SqlParseError,
    StatementTypeNotAllowedError,
    SystemRelationAccessError,
    TablePrefixAccessError,
    TypeNotAllowedError,
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


BASIC = QueryValidator(allowed_schema="public", read_only=True)
FULL = QueryValidator(read_only=True, allow_explain_analyze=True)


class TestBasicExplainOptions:
    """basic: только опции BASIC_EXPLAIN_OPTIONS; ANALYZE — своей ошибкой и первым; full не меняется."""

    @pytest.mark.parametrize(
        "sql",
        [
            "EXPLAIN (SETTINGS) SELECT 1",
            "EXPLAIN (settings on, format json) SELECT 1",
            "EXPLAIN (SETTINGS false) SELECT 1",
            "EXPLAIN (WAL) SELECT 1",
            "EXPLAIN (SERIALIZE TEXT) SELECT 1",
            'EXPLAIN ("SETTINGS") SELECT 1',
            "EXPLAIN (FUTURE_OPTION) SELECT 1",
        ],
    )
    def test_option_outside_the_list_is_rejected(self, sql: str) -> None:
        with pytest.raises(ExplainOptionNotAllowedError, match="Allowed options: BUFFERS, COSTS, FORMAT"):
            BASIC.validate(sql)

    def test_error_names_the_rejected_option(self) -> None:
        with pytest.raises(ExplainOptionNotAllowedError) as exc_info:
            BASIC.validate("EXPLAIN (FORMAT JSON, SETTINGS) SELECT 1")
        assert exc_info.value.option == "settings"
        assert str(exc_info.value).startswith("EXPLAIN option SETTINGS is not allowed in basic mode.")

    @pytest.mark.parametrize(
        "sql",
        [
            "EXPLAIN SELECT 1",
            "EXPLAIN VERBOSE SELECT 1",
            "EXPLAIN (FORMAT JSON, COSTS false, VERBOSE) SELECT 1",
            "EXPLAIN (SUMMARY, TIMING false, BUFFERS, MEMORY) SELECT 1",
            "EXPLAIN (GENERIC_PLAN) SELECT $1",
            "EXPLAIN (FORMAT JSON, GENERIC_PLAN, COSTS TRUE) SELECT $1",
        ],
    )
    def test_listed_options_pass(self, sql: str) -> None:
        BASIC.validate(sql)

    @pytest.mark.parametrize(
        "sql",
        ["EXPLAIN (ANALYZE) SELECT 1", "EXPLAIN (SETTINGS, ANALYZE) SELECT 1", "EXPLAIN ANALYZE VERBOSE SELECT 1"],
    )
    def test_analyze_keeps_its_own_error_and_is_checked_first(self, sql: str) -> None:
        with pytest.raises(ExplainAnalyzeNotSupportedError):
            BASIC.validate(sql)

    @pytest.mark.parametrize(
        "sql",
        ["EXPLAIN (SETTINGS) SELECT 1", "EXPLAIN (WAL, ANALYZE) SELECT 1", "EXPLAIN (SERIALIZE, ANALYZE) SELECT 1"],
    )
    def test_full_is_unchanged(self, sql: str) -> None:
        FULL.validate(sql)


class TestBasicInformationSchemaSecrets:
    """basic: представления information_schema с секретами и исходниками — SystemRelationAccessError."""

    @pytest.mark.parametrize(
        "view",
        [
            "user_mapping_options",
            "user_mappings",
            "foreign_server_options",
            "foreign_data_wrapper_options",
            "foreign_table_options",
            "column_options",
            "routines",
            "views",
            "triggers",
        ],
    )
    def test_view_is_rejected(self, view: str) -> None:
        with pytest.raises(SystemRelationAccessError, match=rf"'information_schema\.{view}'"):
            BASIC.validate(f"SELECT * FROM information_schema.{view}")

    @pytest.mark.parametrize(
        "sql",
        [
            'SELECT * FROM "information_schema".routines',
            'SELECT * FROM information_schema."ROUTINES"',
            "SELECT * FROM INFORMATION_SCHEMA.Routines",
            "SELECT 1 WHERE EXISTS (SELECT 1 FROM information_schema.views)",
            "WITH x AS (SELECT * FROM information_schema.triggers) SELECT * FROM x",
        ],
    )
    def test_any_spelling_and_position_is_rejected(self, sql: str) -> None:
        with pytest.raises(SystemRelationAccessError):
            BASIC.validate(sql)

    @pytest.mark.parametrize(
        "sql", ["SELECT * FROM information_schema.tables", "SELECT * FROM information_schema.columns"]
    )
    def test_structure_views_still_pass(self, sql: str) -> None:
        BASIC.validate(sql)
        QueryValidator(allowed_schema="public", table_prefix="app_", read_only=True).validate(sql)

    def test_full_is_unchanged(self) -> None:
        FULL.validate("SELECT * FROM information_schema.user_mapping_options")


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


class TestBasicPolicy:
    """basic: системные отношения, интроспекция, SHOW и reg*-приведения (спека basic-confinement §4)."""

    BASIC = QueryValidator(read_only=True, allowed_schema="public")

    @pytest.mark.parametrize(
        "sql", ["SELECT * FROM pg_stats", "SELECT * FROM pg_catalog.pg_stats", "SELECT * FROM public.pg_stats"]
    )
    def test_system_relation_rejected_with_its_name(self, sql: str) -> None:
        with pytest.raises(SystemRelationAccessError, match="'pg_stats'"):
            self.BASIC.validate(sql)

    def test_system_relation_checked_before_prefix(self) -> None:
        validator = QueryValidator(read_only=True, allowed_schema="public", table_prefix="app_")
        with pytest.raises(SystemRelationAccessError):
            validator.validate("SELECT * FROM pg_indexes")

    def test_introspection_function_rejected(self) -> None:
        with pytest.raises(FunctionNotAllowedError, match="current_setting"):
            self.BASIC.validate("SELECT current_setting('app.jwt_secret')")

    @pytest.mark.parametrize(
        ("sql", "func"),
        [("SELECT currval('secret.accounts')", "currval"), ("SELECT lastval()", "lastval")],
    )
    def test_sequence_state_functions_rejected(self, sql: str, func: str) -> None:
        """Аргумент currval приводится через regclass: оракул существования, как 'x'::regclass."""
        with pytest.raises(FunctionNotAllowedError, match=func):
            self.BASIC.validate(sql)

    def test_show_outside_the_list_names_allowed_parameters(self) -> None:
        with pytest.raises(ShowParameterNotAllowedError) as exc_info:
            self.BASIC.validate("SHOW app.jwt_secret")
        assert "SHOW app.jwt_secret is not allowed" in str(exc_info.value)
        assert "search_path" in str(exc_info.value)

    @pytest.mark.parametrize("sql", ["SELECT 't'::regclass", "SELECT 't'::pg_catalog.REGCLASS[]"])
    def test_reg_cast_rejected(self, sql: str) -> None:
        with pytest.raises(TypeNotAllowedError, match="regclass"):
            self.BASIC.validate(sql)

    def test_reg_type_rejected_outside_cast(self) -> None:
        """R4 не должен сводиться к проверке только TypeCast: reg* в типе колонки табличной
        функции тоже отдаёт Postgres имя объекта на вход input-функции типа."""
        with pytest.raises(TypeNotAllowedError, match="regclass"):
            self.BASIC.validate("""SELECT * FROM json_to_record('{"a":"secret.t"}') AS x(a regclass)""")

    @pytest.mark.parametrize(
        ("sql", "type_name"),
        [
            ("SELECT '{secret.t}'::_regclass", "_regclass"),
            ("SELECT '{secret.t}'::pg_catalog._regclass", "_regclass"),
            ("""SELECT * FROM json_to_record('{"a":["secret.t"]}') AS x(a _regclass)""", "_regclass"),
            ("PREPARE p(_regrole) AS SELECT 1", "_regrole"),
            ("SELECT '{x}'::_regnamespace", "_regnamespace"),
            ("SELECT 'secretrole=r/postgres'::aclitem", "aclitem"),
            ("SELECT '{secretrole=r/postgres}'::pg_catalog._aclitem", "_aclitem"),
        ],
    )
    def test_name_lookup_types_rejected_including_arrays(self, sql: str, type_name: str) -> None:
        """Массив reg*-типа и aclitem тоже резолвят имена объектов через функцию ввода типа."""
        with pytest.raises(TypeNotAllowedError) as exc_info:
            self.BASIC.validate(sql)
        assert str(exc_info.value) == (
            f"Type {type_name} is not allowed in basic mode. Rewrite the query without object identifier types."
        )

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT json_populate_record(NULL::secret.accounts, '{}')",
            "SELECT enum_range(NULL::secret.status)",
            "SELECT ROW(1)::secret.accounts",
            "SELECT NULL::secret.accounts[]",
            "SELECT NULL::secret._accounts",
            "SELECT NULL::secret.pg_lsn",
            "SELECT NULL::mydb.secret.accounts",
            "SELECT 'a' COLLATE secret.coll",
            """SELECT * FROM json_to_record('{"a":1}') AS x(a secret.t)""",
        ],
    )
    def test_type_or_collation_from_another_schema_rejected(self, sql: str) -> None:
        with pytest.raises(SchemaNotAllowedError, match="'secret'"):
            self.BASIC.validate(sql)
        with pytest.raises(SchemaNotAllowedError, match="'secret'"):
            self.PREFIXED.validate(sql)

    @pytest.mark.parametrize(
        ("sql", "relation"),
        [
            ("SELECT NULL::pg_authid", "pg_authid"),
            ("SELECT json_populate_record(NULL::pg_class, '{}')", "pg_class"),
            ("SELECT NULL::pg_catalog.pg_class", "pg_class"),
            ("SELECT NULL::PG_AUTHID[]", "pg_authid"),
            ("SELECT NULL::_pg_authid", "_pg_authid"),
            ("SELECT NULL::information_schema._pg_user_mappings", "_pg_user_mappings"),
            ("SELECT NULL::public.pg_stat_statements", "pg_stat_statements"),
        ],
    )
    def test_system_row_type_rejected(self, sql: str, relation: str) -> None:
        """Строковый тип системного отношения — то же, что само отношение (R1)."""
        with pytest.raises(SystemRelationAccessError, match=f"'{relation}'"):
            self.BASIC.validate(sql)
        with pytest.raises(SystemRelationAccessError, match=f"'{relation}'"):
            self.PREFIXED.validate(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT '0/0'::pg_lsn",
            "SELECT NULL::pg_catalog.pg_lsn",
            "SELECT '{0/0}'::pg_lsn[]",
            "SELECT NULL::pg_snapshot",
            "SELECT NULL::public.app_t",
            "SELECT NULL::PUBLIC.app_t",
            "SELECT 'a' COLLATE \"C\"",
            "SELECT 'a' COLLATE pg_catalog.\"default\"",
            "SELECT 'a' COLLATE public.app_coll",
            "SELECT '{1,2}'::int[], 1::bigint, now()::timestamp with time zone, 'a'::pg_catalog.varchar(3)",
        ],
    )
    def test_builtin_and_allowed_schema_types_pass(self, sql: str) -> None:
        self.BASIC.validate(sql)
        self.PREFIXED.validate(sql)

    @pytest.mark.parametrize(
        ("sql", "func"),
        [
            ("SELECT pg_basetype('secret.accounts')", "pg_basetype"),
            ("SELECT pg_typeof(1) = 'secret.accounts'", "pg_typeof"),
            ("SELECT COALESCE(pg_typeof(1), 'secret.accounts')", "pg_typeof"),
        ],
    )
    def test_regtype_functions_rejected(self, sql: str, func: str) -> None:
        """Аргумент или результат regtype приводит строковый литерал через regtypein — оракул типов."""
        with pytest.raises(FunctionNotAllowedError, match=func):
            self.BASIC.validate(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1 OPERATOR(secret.+) 2",
            "SELECT 1 FROM app_t WHERE a OPERATOR(secret.=) ANY (ARRAY[1])",
            "SELECT * FROM app_t ORDER BY name USING OPERATOR(secret.<)",
            "SELECT * FROM app_t TABLESAMPLE secret.m(1)",
            "SELECT 1 FROM app_t WHERE a OPERATOR(secret.=) ANY (SELECT 1)",
            "SELECT 1 FROM app_t WHERE a OPERATOR(secret.=) ALL (SELECT 1)",
            "SELECT 1 FROM app_t WHERE (a, b) OPERATOR(secret.=) ANY (SELECT 1, 2)",
        ],
    )
    def test_operator_or_tablesample_method_from_another_schema_rejected(self, sql: str) -> None:
        with pytest.raises(SchemaNotAllowedError, match="'secret'"):
            self.BASIC.validate(sql)
        with pytest.raises(SchemaNotAllowedError, match="'secret'"):
            self.PREFIXED.validate(sql)

    def test_quantified_subquery_operator_checked_in_write_mode(self) -> None:
        """SubLink.operName в DML: UPDATE ... WHERE a OPERATOR(secret.=) ANY (SELECT ...)."""
        writer = QueryValidator(read_only=False, allowed_schema="public")
        with pytest.raises(SchemaNotAllowedError, match="'secret'"):
            writer.validate("UPDATE app_t SET a = 1 WHERE a OPERATOR(secret.=) ANY (SELECT 1)")

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1 FROM app_t WHERE a = ANY (SELECT 1)",
            "SELECT 1 FROM app_t WHERE a > SOME (SELECT 1)",
            "SELECT 1 FROM app_t WHERE a IN (SELECT 1)",
            "SELECT 1 FROM app_t WHERE EXISTS (SELECT 1)",
            "SELECT 1 FROM app_t WHERE a OPERATOR(pg_catalog.=) ANY (SELECT 1)",
        ],
    )
    def test_quantified_subquery_without_foreign_operator_passes(self, sql: str) -> None:
        self.BASIC.validate(sql)
        self.PREFIXED.validate(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            'SELECT * FROM "PUBLIC".app_t',
            'SELECT NULL::"PUBLIC".t',
            """SELECT 'a' COLLATE "PUBLIC".x""",
            """SELECT hypopg_create_index('CREATE INDEX ON "PUBLIC".app_t (c)')""",
            'SELECT 1 OPERATOR("PG_CATALOG".+) 2',
        ],
    )
    def test_schema_name_matches_exactly(self, sql: str) -> None:
        """Схема в кавычках с другим регистром — отдельная схема; pglast уже свернул имена без кавычек."""
        with pytest.raises(SchemaNotAllowedError):
            self.BASIC.validate(sql)
        with pytest.raises(SchemaNotAllowedError):
            self.PREFIXED.validate(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM PUBLIC.app_t",
            'SELECT * FROM "public".app_t',
            "SELECT NULL::PUBLIC.app_t",
            "SELECT hypopg_create_index('CREATE INDEX ON PUBLIC.app_t (c)')",
            "SELECT * FROM PUBLIC.APP_T",
        ],
    )
    def test_unquoted_schema_folds_and_prefix_stays_case_insensitive(self, sql: str) -> None:
        self.BASIC.validate(sql)
        self.PREFIXED.validate(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT '{secret.t}'::_regclass",
            "SELECT 'secretrole=r/postgres'::aclitem",
            "SELECT json_populate_record(NULL::secret.accounts, '{}')",
            "SELECT 'a' COLLATE secret.coll",
            "SELECT NULL::pg_authid",
        ],
    )
    def test_full_keeps_every_type_name(self, sql: str) -> None:
        QueryValidator(read_only=True).validate(sql)

    def test_full_is_unchanged(self) -> None:
        full = QueryValidator(read_only=True)
        full.validate("SELECT * FROM pg_stats")
        full.validate("SHOW app.jwt_secret")
        full.validate("SELECT 't'::regclass, current_setting('work_mem')")

    PREFIXED = QueryValidator(read_only=True, allowed_schema="public", table_prefix="app_")

    @pytest.mark.parametrize(
        ("sql", "error"),
        [
            ("SELECT hypopg_create_index('CREATE INDEX ON secret.t (c)')", SchemaNotAllowedError),
            ("SELECT hypopg_create_index('CREATE INDEX ON users (c)')", TablePrefixAccessError),
            ("SELECT hypopg_create_index('CREATE INDEX ON pg_class (relname)')", SystemRelationAccessError),
            (
                "SELECT hypopg_create_index('CREATE INDEX ON information_schema.sql_features (feature_id)')",
                SchemaNotAllowedError,
            ),
            ("SELECT hypopg_create_index('CREATE INDEX ON pg_catalog.app_t (c)')", SchemaNotAllowedError),
            ("SELECT hypopg_create_index('CREATE INDEX ON hypopg_list_indexes (c)')", SystemRelationAccessError),
            ("SELECT hypopg_create_index('SELECT 1')", FunctionNotAllowedError),
            (
                "SELECT hypopg_create_index('CREATE INDEX ON app_t (c); CREATE INDEX ON secret.t (c)')",
                FunctionNotAllowedError,
            ),
            ("SELECT hypopg_create_index('not sql')", FunctionNotAllowedError),
            ("SELECT hypopg_create_index(concat('CREATE INDEX ON ', 'secret.t (c)'))", FunctionNotAllowedError),
            ("SELECT hypopg_create_index(stmt) FROM app_defs", FunctionNotAllowedError),
        ],
    )
    def test_hypopg_create_index_target_is_checked(self, sql: str, error: type[Exception]) -> None:
        with pytest.raises(error):
            self.PREFIXED.validate(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT hypopg_create_index('CREATE INDEX ON app_t (c)')",
            "SELECT hypopg_create_index('CREATE INDEX idx ON public.app_t USING btree (a, b)')",
            "SELECT hypopg_create_index('CREATE INDEX ON app_t USING hash (c)')",
            "SELECT hypopg_create_index('CREATE INDEX ON app_t (a DESC NULLS LAST)')",
        ],
    )
    def test_hypopg_create_index_on_allowed_table_passes(self, sql: str) -> None:
        self.PREFIXED.validate(sql)

    @pytest.mark.parametrize(
        ("sql", "relation"),
        [
            ("SELECT * FROM hypopg_list_indexes", "hypopg_list_indexes"),
            ("SELECT * FROM public.hypopg_hidden_indexes", "hypopg_hidden_indexes"),
            ("SELECT * FROM HYPOPG_LIST_INDEXES", "hypopg_list_indexes"),
        ],
    )
    def test_hypopg_views_are_system_relations(self, sql: str, relation: str) -> None:
        """Представления hypopg показывают состояние всей сессии пулового соединения (R1)."""
        with pytest.raises(SystemRelationAccessError, match=f"'{relation}'"):
            self.BASIC.validate(sql)
        with pytest.raises(SystemRelationAccessError, match=f"'{relation}'"):
            self.PREFIXED.validate(sql)

    @pytest.mark.parametrize(
        ("sql", "func"),
        [
            ("SELECT * FROM hypopg_list_indexes()", "hypopg_list_indexes"),
            ("SELECT hypopg_get_indexdef(1)", "hypopg_get_indexdef"),
            ("SELECT hypopg_relation_size(1)", "hypopg_relation_size"),
        ],
    )
    def test_hypopg_session_functions_rejected(self, sql: str, func: str) -> None:
        with pytest.raises(FunctionNotAllowedError, match=func):
            self.BASIC.validate(sql)

    def test_hypopg_reset_stays_in_basic(self) -> None:
        """explain_query склеивает hypopg_reset() с EXPLAIN запроса агента."""
        self.PREFIXED.validate("SELECT hypopg_reset()")

    def test_full_does_not_parse_hypopg_argument(self) -> None:
        QueryValidator(read_only=True).validate("SELECT hypopg_create_index('CREATE INDEX ON secret.t (c)')")

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT hypopg_create_index('CREATE INDEX ON app_t ((''secret.t''::regclass))')",
            "SELECT hypopg_create_index('CREATE INDEX ON app_t (c) WHERE c = ''secret.t''::regclass')",
            "SELECT hypopg_create_index('CREATE INDEX ON app_t ((c::secret.mytype))')",
            "SELECT hypopg_create_index('CREATE INDEX ON app_t ((secret.f(c)))')",
            "SELECT hypopg_create_index('CREATE INDEX ON app_t (c secret.opclass)')",
            "SELECT hypopg_create_index('CREATE INDEX ON app_t (c) TABLESPACE secret')",
            ("SELECT hypopg_create_index('CREATE INDEX ON app_t ((pg_catalog.current_setting(''app.jwt_secret'')))')"),
        ],
    )
    def test_hypopg_create_index_rejects_anything_but_plain_columns(self, sql: str) -> None:
        """Hypopg сам гоняет transformIndexStmt: выражения, WHERE, opclass и TABLESPACE резолвят
        имена объектов и вычисляют входные функции литеральных касто́в — это течь мимо проверки relation."""
        with pytest.raises(FunctionNotAllowedError):
            self.PREFIXED.validate(sql)
