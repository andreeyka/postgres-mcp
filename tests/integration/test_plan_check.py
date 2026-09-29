# mypy: ignore-errors
"""Проверка по плану на живом Postgres: представление в public поверх чужой схемы (спека basic-followups §4.4)."""

from collections.abc import AsyncGenerator

import pytest

from postgres_fastmcp.access import EffectiveAccess
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.domains.db_access import DbAccess, DbAccessService
from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.shared.errors import PlanAccessError


_SETUP = """
CREATE SCHEMA IF NOT EXISTS secret;
CREATE TABLE IF NOT EXISTS secret.accounts (id int, token text);
INSERT INTO secret.accounts SELECT 1, 'top-secret' WHERE NOT EXISTS (SELECT 1 FROM secret.accounts);
CREATE OR REPLACE VIEW public.app_secret_view AS SELECT id, token FROM secret.accounts;
CREATE OR REPLACE FUNCTION secret.accounts_rows() RETURNS SETOF secret.accounts
    LANGUAGE sql STABLE AS 'SELECT * FROM secret.accounts';
CREATE OR REPLACE VIEW public.app_secret_fn_view AS SELECT * FROM secret.accounts_rows();
CREATE OR REPLACE VIEW public.app_settings_view AS SELECT name, setting FROM pg_settings;
CREATE OR REPLACE VIEW public.app_secret_rows_from_view AS
    SELECT * FROM ROWS FROM (secret.accounts_rows(), generate_series(1, 1)) AS r(id, token, n);
CREATE OR REPLACE FUNCTION secret.get_tokens() RETURNS text[]
    LANGUAGE plpgsql STABLE AS 'BEGIN RETURN ARRAY(SELECT token FROM secret.accounts); END';
CREATE OR REPLACE VIEW public.app_secret_unnest_view AS SELECT * FROM unnest(secret.get_tokens()) AS t(token);
CREATE TABLE IF NOT EXISTS public.app_plan_items (id int);
INSERT INTO public.app_plan_items SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM public.app_plan_items);
CREATE OR REPLACE FUNCTION secret.reveal(t text) RETURNS text
    LANGUAGE plpgsql IMMUTABLE AS 'BEGIN RETURN upper(t); END';
CREATE OR REPLACE VIEW public.app_expr_secret_fn_view AS SELECT secret.reveal(id::text) AS r FROM public.app_plan_items;
CREATE OR REPLACE VIEW public.app_expr_setting_view AS
    SELECT id, current_setting('application_name') AS s FROM public.app_plan_items;
CREATE OR REPLACE VIEW public.app_expr_lower_view AS SELECT lower(id::text) AS l FROM public.app_plan_items;
CREATE OR REPLACE FUNCTION public.app_double(n int) RETURNS int
    LANGUAGE plpgsql IMMUTABLE AS 'BEGIN RETURN n * 2; END';
CREATE OR REPLACE VIEW public.app_expr_public_fn_view AS SELECT app_double(id) AS d FROM public.app_plan_items;
CREATE TABLE IF NOT EXISTS public.other_users (id int, secret_note text);
CREATE TABLE IF NOT EXISTS public.app_serial_items (id serial PRIMARY KEY, v text);
CREATE TABLE IF NOT EXISTS public.app_identity_items (id int GENERATED ALWAYS AS IDENTITY, v text);
"""


@pytest.fixture
async def db_plan_check(
    test_postgres_connection_string: tuple[str, str], db_full: DbAccess
) -> AsyncGenerator[DbAccess, None]:
    """Basic + app_ + запись + plan_check=true поверх подготовленных объектов."""
    await db_full.sql_driver.execute(_SETUP, readonly=False)
    connection_string, _ = test_postgres_connection_string
    config = DatabaseConfig.from_uri(
        connection_string, access_mode=AccessMode.BASIC, write_mode=True, table_prefix="app_", plan_check=True
    )
    service = DbAccessService(config)
    try:
        yield service.view(EffectiveAccess(AccessMode.BASIC, write_mode=True))
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_view_over_a_foreign_schema_is_rejected_with_plan_check(db_plan_check: DbAccess) -> None:
    with pytest.raises(PlanAccessError, match=r"secret\.accounts"):
        await db_plan_check.sql_driver.execute("SELECT * FROM app_secret_view", readonly=True)


@pytest.mark.asyncio
async def test_view_over_a_foreign_function_is_rejected_with_plan_check(db_plan_check: DbAccess) -> None:
    """Инлайн SQL-функции даёт отношение secret.accounts, без инлайна — Function Scan secret.accounts_rows."""
    with pytest.raises(PlanAccessError, match="secret"):
        await db_plan_check.sql_driver.execute("SELECT * FROM app_secret_fn_view", readonly=True)


@pytest.mark.asyncio
async def test_without_plan_check_the_view_returns_data(db_plan_check: DbAccess, db_user_prefix: DbAccess) -> None:
    """Задокументированное поведение basic без plan_check: представление отдаёт данные чужой схемы."""
    rows = await db_user_prefix.sql_driver.execute("SELECT token FROM app_secret_view", readonly=True)
    assert rows[0].cells["token"] == "top-secret"


@pytest.mark.asyncio
async def test_prefixed_table_passes_with_and_without_plan_check(
    db_plan_check: DbAccess, db_user_prefix: DbAccess
) -> None:
    for db in (db_plan_check, db_user_prefix):
        rows = await db.sql_driver.execute("SELECT count(*) AS n FROM app_plan_items", readonly=True)
        assert rows[0].cells["n"] >= 1


@pytest.mark.asyncio
async def test_write_statement_is_planned_and_run_once_in_one_transaction(db_plan_check: DbAccess) -> None:
    """EXPLAIN без ANALYZE в пишущей транзакции не исполняет DML: строка вставляется ровно один раз."""
    await db_plan_check.sql_driver.execute("DELETE FROM app_plan_items WHERE id = 2", readonly=False)
    await db_plan_check.sql_driver.execute("INSERT INTO app_plan_items (id) VALUES (2)", readonly=False)
    rows = await db_plan_check.sql_driver.execute(
        "SELECT count(*) AS n FROM app_plan_items WHERE id = 2", readonly=True
    )
    await db_plan_check.sql_driver.execute("DELETE FROM app_plan_items WHERE id = 2", readonly=False)
    assert rows[0].cells["n"] == 1


@pytest.mark.asyncio
async def test_statement_inherits_the_settings_of_the_check(db_plan_check: DbAccess) -> None:
    """Оператор идёт без префикса SET LOCAL: search_path = public он видит, только если выполнен в транзакции проверки."""
    rows = await db_plan_check.sql_driver.execute("SHOW search_path", readonly=True)
    assert rows[0].cells["search_path"] == "public"


@pytest.mark.asyncio
async def test_rejected_write_changes_nothing(db_plan_check: DbAccess, db_full: DbAccess) -> None:
    with pytest.raises(PlanAccessError, match=r"secret\.accounts"):
        await db_plan_check.sql_driver.execute("UPDATE app_secret_view SET token = 'leaked'", readonly=False)
    rows = await db_full.sql_driver.execute("SELECT token FROM secret.accounts WHERE id = 1", readonly=True)
    assert rows[0].cells["token"] == "top-secret"


@pytest.mark.asyncio
async def test_information_schema_is_rejected_with_plan_check(db_plan_check: DbAccess) -> None:
    """Строгий режим: представления information_schema читают pg_catalog."""
    with pytest.raises(PlanAccessError, match="pg_catalog"):
        await db_plan_check.sql_driver.execute("SELECT table_name FROM information_schema.tables", readonly=True)


@pytest.mark.asyncio
async def test_view_over_pg_settings_is_rejected_with_plan_check(db_plan_check: DbAccess) -> None:
    """pg_settings — Function Scan pg_catalog.pg_show_all_settings, которой нет в списке функций basic."""
    with pytest.raises(PlanAccessError, match=r"function 'pg_catalog\.pg_show_all_settings'"):
        await db_plan_check.sql_driver.execute("SELECT * FROM app_settings_view", readonly=True)


@pytest.mark.asyncio
async def test_multi_argument_unnest_passes_with_plan_check(db_plan_check: DbAccess) -> None:
    """unnest(a, b) Postgres переписывает в ROWS FROM без Function Name; вызовы проверяются по Function Call."""
    rows = await db_plan_check.sql_driver.execute(
        "SELECT * FROM unnest(ARRAY[1, 2], ARRAY['a', 'b']) AS u(n, s)", readonly=True
    )
    assert [row.cells["s"] for row in rows] == ["a", "b"]


@pytest.mark.asyncio
async def test_rows_from_with_a_foreign_function_is_rejected_with_plan_check(db_plan_check: DbAccess) -> None:
    with pytest.raises(PlanAccessError, match=r"secret\.accounts_rows"):
        await db_plan_check.sql_driver.execute("SELECT * FROM app_secret_rows_from_view", readonly=True)


@pytest.mark.asyncio
async def test_nested_call_of_a_single_function_scan_is_rejected_with_plan_check(db_plan_check: DbAccess) -> None:
    """Unnest — разрешённая встроенная, но её аргумент secret.get_tokens() виден только в Function Call."""
    with pytest.raises(PlanAccessError, match=r"secret\.get_tokens"):
        await db_plan_check.sql_driver.execute("SELECT * FROM app_secret_unnest_view", readonly=True)


@pytest.mark.asyncio
async def test_view_calling_a_foreign_function_is_rejected_with_plan_check(db_plan_check: DbAccess) -> None:
    """Функция plpgsql не встраивается: вызов secret.reveal виден только в Output плана."""
    with pytest.raises(PlanAccessError, match=r"function 'secret\.reveal'"):
        await db_plan_check.sql_driver.execute("SELECT r FROM app_expr_secret_fn_view", readonly=True)


@pytest.mark.asyncio
async def test_view_calling_a_builtin_outside_basic_is_rejected_with_plan_check(db_plan_check: DbAccess) -> None:
    """current_setting печатается без схемы; каталог подтверждает, что это функция pg_catalog."""
    with pytest.raises(PlanAccessError, match=r"function 'pg_catalog\.current_setting'"):
        await db_plan_check.sql_driver.execute("SELECT s FROM app_expr_setting_view", readonly=True)


@pytest.mark.asyncio
async def test_views_with_allowed_expressions_return_data(db_plan_check: DbAccess) -> None:
    lower_rows = await db_plan_check.sql_driver.execute("SELECT l FROM app_expr_lower_view", readonly=True)
    public_rows = await db_plan_check.sql_driver.execute("SELECT d FROM app_expr_public_fn_view", readonly=True)
    assert "1" in [row.cells["l"] for row in lower_rows]
    assert 2 in [row.cells["d"] for row in public_rows]


@pytest.mark.asyncio
async def test_row_type_of_a_table_without_the_prefix_is_rejected(db_plan_check: DbAccess) -> None:
    """NULL::other_users — строковый тип таблицы public без префикса: оракул её структуры."""
    with pytest.raises(PlanAccessError, match=r"relation 'public\.other_users'"):
        await db_plan_check.sql_driver.execute(
            "SELECT * FROM json_populate_record(NULL::other_users, '{}')", readonly=True
        )


@pytest.mark.asyncio
async def test_row_type_of_a_prefixed_table_passes(db_plan_check: DbAccess) -> None:
    rows = await db_plan_check.sql_driver.execute(
        """SELECT id FROM json_populate_record(NULL::app_plan_items, '{"id": 7}')""", readonly=True
    )
    assert rows[0].cells["id"] == 7


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM app_plan_items WHERE id = (SELECT max(id) FROM app_plan_items WHERE id < 100)",
        "SELECT id FROM app_plan_items WHERE id NOT IN (SELECT id FROM app_plan_items WHERE id > 100)",
        "SELECT id, count(*) AS n, row_number() OVER (ORDER BY id DESC) AS r "
        "FROM app_plan_items GROUP BY id ORDER BY id DESC NULLS LAST",
    ],
)
async def test_subqueries_sorting_and_windows_pass_with_plan_check(db_plan_check: DbAccess, sql: str) -> None:
    """Реальные формы плана: $0/(InitPlan 1).col1, (hashed SubPlan 1), Sort Key, Group Key, OVER (?)."""
    rows = await db_plan_check.sql_driver.execute(sql, readonly=True)
    assert 1 in [row.cells["id"] for row in rows]


@pytest.mark.asyncio
@pytest.mark.parametrize("table", ["app_serial_items", "app_identity_items"])
async def test_insert_with_a_sequence_default_passes_with_plan_check(db_plan_check: DbAccess, table: str) -> None:
    """DEFAULT serial план печатает как nextval('app_..._seq'::regclass), identity — nextval('app_..._seq')
    (NextValueExpr без приведения): это отношение, а не вызов."""
    rows = await db_plan_check.sql_driver.execute(f"INSERT INTO {table} (v) VALUES ('x') RETURNING id", readonly=False)
    await db_plan_check.sql_driver.execute(f"DELETE FROM {table} WHERE v = 'x'", readonly=False)
    assert rows[0].cells["id"] >= 1
