# mypy: ignore-errors
"""Регрессионный корпус QueryValidator: что обязан блокировать и что обязан пропускать.

Наборы собраны при аудите 2026-09-26. Любое изменение политики должно
отразиться здесь осознанно, а не сломать тест случайно.
"""

import pytest

from postgres_fastmcp.postgres.security.query_validator import QueryValidator
from postgres_fastmcp.shared.errors import UserFacingError


FULL_READ_ONLY = QueryValidator(read_only=True, allow_explain_analyze=True)
BASIC_READ_ONLY = QueryValidator(allowed_schema="public", table_prefix="app_", read_only=True)
BASIC_WRITE = QueryValidator(allowed_schema="public", read_only=False)

# Попытки записи или побочных эффектов: блокируются в ЛЮБОМ ограниченном режиме.
MUST_BLOCK_EVERYWHERE = [
    "SELECT pg_sleep(10)",
    "SELECT pg_terminate_backend(123)",
    "SELECT pg_cancel_backend(123)",
    "SELECT set_config('work_mem', '1GB', false)",
    "SELECT pg_reload_conf()",
    "SELECT pg_advisory_lock(1)",
    "SELECT lo_import('/etc/passwd')",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT nextval('s')",
    "SELECT setval('s', 100)",
    "SELECT * INTO new_t FROM t",
    "CREATE TABLE t AS SELECT 1",
    "CREATE TABLE t (id int)",
    "DROP TABLE t",
    "ALTER TABLE t ADD COLUMN x int",
    "TRUNCATE t",
    "SELECT * FROM t FOR UPDATE",
    "CREATE EXTENSION dblink",
    "CREATE EXTENSION file_fdw",
    "SELECT * FROM dblink('dbname=x', 'DROP TABLE t') AS t(a int)",
    "SET search_path = evil",
    "COMMIT",
    "BEGIN; INSERT INTO t VALUES (1); COMMIT",
    "COPY t TO '/tmp/x'",
    "COPY t FROM PROGRAM 'id'",
    "DO $$ BEGIN PERFORM 1; END $$",
    "CALL proc()",
    "VACUUM t",
    "ANALYZE t",
    "LISTEN c",
    "NOTIFY c",
    "LOCK TABLE t",
    "REFRESH MATERIALIZED VIEW mv",
    "GRANT ALL ON t TO PUBLIC",
    "MERGE INTO t USING u ON t.a = u.a WHEN MATCHED THEN DELETE",
    "SELECT txid_current()",
    "SELECT pg_notify('c', 'p')",
    "SELECT query_to_xml('select 1', true, false, '')",
]

# Запись: блокируется в read-only, разрешена в BASIC_WRITE.
DML_STATEMENTS = [
    "INSERT INTO t VALUES (1)",
    "UPDATE t SET a = 1",
    "DELETE FROM t",
    "INSERT INTO t VALUES (1) RETURNING id",
    "WITH w AS (INSERT INTO t VALUES (1) RETURNING *) SELECT * FROM w",
    "CREATE EXTENSION IF NOT EXISTS hypopg",
]

# Обычный read-only SQL: обязан проходить во всех режимах (нет ссылок на таблицы, чтобы не задеть prefix).
MUST_ALLOW_EVERYWHERE = [
    "SELECT 1",
    "SELECT (SELECT 1)",
    "SELECT 1 UNION ALL SELECT 2",
    "VALUES (1), (2)",
    "SELECT ARRAY[1,2]",
    "SELECT ROW(1,2)",
    "SELECT EXISTS (SELECT 1)",
    "SELECT GREATEST(1, 2)",
    "SELECT extract(epoch FROM now())",
    "SELECT interval '1 day'",
    "SELECT $1",
    "SELECT * FROM generate_series(1, 10)",
    "SELECT * FROM json_to_recordset('[]') AS x(a int)",
    "SELECT jsonb_path_query('{}', '$')",
    "SELECT now() AT TIME ZONE 'UTC'",
    "SELECT 'abc' SIMILAR TO 'a%'",
    "SELECT current_setting('server_version')",
    "SHOW search_path",
    "PREPARE p AS SELECT 1",
    "DECLARE c CURSOR FOR SELECT 1",
    "EXPLAIN SELECT 1",
]

# Read-only SQL с таблицами: проходит в full, в basic требует префикс app_.
TABLE_QUERIES_FULL = [
    "SELECT * FROM secret.t",
    "SELECT * FROM pg_catalog.pg_class",
    "SELECT * FROM information_schema.schemata",
    "SELECT a::int FROM t",
    "SELECT string_agg(a, ',' ORDER BY a) FROM t",
    "SELECT count(*) FILTER (WHERE a > 1) FROM t",
    "SELECT a, row_number() OVER (PARTITION BY b) FROM t",
    "SELECT * FROM t GROUP BY GROUPING SETS ((a), ())",
    "SELECT * FROM t, LATERAL (SELECT 1) x",
    "SELECT 1 FROM t TABLESAMPLE SYSTEM (10)",
    "SELECT * FROM t WHERE a IN (SELECT b FROM u)",
    "EXPLAIN ANALYZE SELECT * FROM t",
]

TABLE_QUERIES_BASIC_ALLOWED = [
    "SELECT * FROM app_users",
    "SELECT * FROM public.app_users WHERE id = 1",
    "SELECT * FROM information_schema.tables",
]

TABLE_QUERIES_BASIC_BLOCKED = [
    "SELECT * FROM users",
    "SELECT * FROM secret.app_users",
    "SELECT * FROM pg_catalog.pg_class",
    "SELECT * FROM information_schema.schemata",
    "EXPLAIN ANALYZE SELECT * FROM app_users",
    "SELECT * FROM app_users WHERE name LIKE other_col",
]


@pytest.mark.parametrize(
    "validator", [FULL_READ_ONLY, BASIC_READ_ONLY, BASIC_WRITE], ids=["full-ro", "basic-ro", "basic-rw"]
)
@pytest.mark.parametrize("sql", MUST_BLOCK_EVERYWHERE)
def test_blocks_side_effects_in_every_mode(validator: QueryValidator, sql: str) -> None:
    with pytest.raises(UserFacingError):
        validator.validate(sql)


@pytest.mark.parametrize("validator", [FULL_READ_ONLY, BASIC_READ_ONLY], ids=["full-ro", "basic-ro"])
@pytest.mark.parametrize("sql", DML_STATEMENTS)
def test_read_only_blocks_dml(validator: QueryValidator, sql: str) -> None:
    with pytest.raises(UserFacingError):
        validator.validate(sql)


@pytest.mark.parametrize("sql", DML_STATEMENTS)
def test_write_mode_allows_dml(sql: str) -> None:
    QueryValidator(read_only=False).validate(sql)


@pytest.mark.parametrize(
    "validator", [FULL_READ_ONLY, BASIC_READ_ONLY, BASIC_WRITE], ids=["full-ro", "basic-ro", "basic-rw"]
)
@pytest.mark.parametrize("sql", MUST_ALLOW_EVERYWHERE)
def test_allows_plain_read_only_sql(validator: QueryValidator, sql: str) -> None:
    validator.validate(sql)


@pytest.mark.parametrize("sql", TABLE_QUERIES_FULL)
def test_full_allows_any_schema(sql: str) -> None:
    FULL_READ_ONLY.validate(sql)


@pytest.mark.parametrize("sql", TABLE_QUERIES_BASIC_ALLOWED)
def test_basic_allows_prefixed_public_tables(sql: str) -> None:
    BASIC_READ_ONLY.validate(sql)


@pytest.mark.parametrize("sql", TABLE_QUERIES_BASIC_BLOCKED)
def test_basic_blocks_foreign_schemas_and_prefix_mismatch(sql: str) -> None:
    with pytest.raises(UserFacingError):
        BASIC_READ_ONLY.validate(sql)
