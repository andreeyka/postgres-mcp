# mypy: ignore-errors
"""Проверка по плану на живом Postgres: представление в public поверх чужой схемы (спека basic-followups §4.4)."""

from collections.abc import AsyncGenerator

import pytest

from postgres_fastmcp.access import EffectiveAccess
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.domains.db_access import DbAccess, DbAccessService
from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.shared.errors import PlanAccessError, PlanUnverifiableError


# public.sum(text) поверх secret.agg_step — намеренно: агрегаты и операторы проверяются по имени, все перегрузки
# сразу, так что в базе этих тестов любой sum(...) под plan_check отклоняется, не только sum(text).
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
CREATE OR REPLACE FUNCTION secret.api_key() RETURNS text LANGUAGE sql IMMUTABLE AS $$SELECT 'k-123'$$;
CREATE OR REPLACE VIEW public.app_api_key_view AS SELECT secret.api_key() AS k;
CREATE OR REPLACE VIEW public.app_limit_setting_view AS
    SELECT id FROM public.app_plan_items LIMIT pg_catalog.current_setting('max_connections')::integer;
CREATE TABLE IF NOT EXISTS public.app_plan_people (id int, name text);
INSERT INTO public.app_plan_people SELECT 1, 'Alice' WHERE NOT EXISTS (SELECT 1 FROM public.app_plan_people);
CREATE OR REPLACE VIEW public.app_plan_people_lower_view AS SELECT lower(name) AS l FROM public.app_plan_people;
DROP VIEW IF EXISTS public.app_setting_operator_view;
DROP OPERATOR IF EXISTS public.!! (text, boolean);
CREATE OPERATOR public.!! (LEFTARG = text, RIGHTARG = boolean, FUNCTION = pg_catalog.current_setting);
CREATE VIEW public.app_setting_operator_view AS SELECT ('max_connections' !! true) AS s FROM public.app_plan_people;
DROP DOMAIN IF EXISTS public.other_users_dom;
CREATE DOMAIN public.other_users_dom AS public.other_users;
CREATE TABLE IF NOT EXISTS public.app_serial_items (id serial PRIMARY KEY, v text);
CREATE TABLE IF NOT EXISTS public.app_identity_items (id int GENERATED ALWAYS AS IDENTITY, v text);
CREATE TABLE IF NOT EXISTS public.app_rule_items (id int);
CREATE OR REPLACE RULE app_rule_items_log AS ON INSERT TO public.app_rule_items DO ALSO SELECT secret.api_key();
CREATE MATERIALIZED VIEW IF NOT EXISTS public.app_matview_setting AS SELECT current_setting('port') AS port;
CREATE OR REPLACE FUNCTION secret.boom() RETURNS text
    LANGUAGE plpgsql IMMUTABLE AS $$BEGIN RAISE EXCEPTION 'secret.boom was executed'; END$$;
CREATE OR REPLACE VIEW public.app_boom_view AS SELECT secret.boom() AS b;
CREATE OR REPLACE FUNCTION secret.agg_step(state text, value text) RETURNS text
    LANGUAGE plpgsql IMMUTABLE AS $$BEGIN RETURN coalesce(state, '') || value; END$$;
CREATE OR REPLACE AGGREGATE public.sum(text) (SFUNC = secret.agg_step, STYPE = text);
CREATE OR REPLACE FUNCTION public.app_close_to(a int, b int) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE AS 'BEGIN RETURN abs(a - b) <= 1; END';
DROP OPERATOR IF EXISTS public.<~> (int, int);
CREATE OPERATOR public.<~> (LEFTARG = int, RIGHTARG = int, FUNCTION = public.app_close_to);
CREATE OR REPLACE FUNCTION secret.audit() RETURNS trigger LANGUAGE plpgsql AS $$BEGIN RETURN NEW; END$$;
CREATE TABLE IF NOT EXISTS public.app_audited (id int);
CREATE OR REPLACE TRIGGER app_audited_audit BEFORE INSERT ON public.app_audited
    FOR EACH ROW EXECUTE FUNCTION secret.audit();
CREATE OR REPLACE FUNCTION secret.valid(n int) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE AS 'BEGIN RETURN n > 0; END';
CREATE TABLE IF NOT EXISTS public.app_checked (id int CHECK (secret.valid(id)));
CREATE TABLE IF NOT EXISTS public.app_generated (id int, flag boolean GENERATED ALWAYS AS (secret.valid(id)) STORED);
CREATE TABLE IF NOT EXISTS public.app_policy_items (id int);
ALTER TABLE public.app_policy_items ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS app_policy_items_check ON public.app_policy_items;
CREATE POLICY app_policy_items_check ON public.app_policy_items WITH CHECK (secret.valid(id));
CREATE OR REPLACE FUNCTION public.app_secret_count() RETURNS bigint
    LANGUAGE sql STABLE AS 'SELECT count(*) FROM secret.accounts';
CREATE OR REPLACE VIEW public.app_secret_count_view AS SELECT public.app_secret_count() AS n;
CREATE OR REPLACE FUNCTION public.app_secret_count_atomic() RETURNS bigint
    LANGUAGE sql STABLE BEGIN ATOMIC SELECT count(*) FROM secret.accounts; END;
CREATE OR REPLACE VIEW public.app_secret_count_atomic_view AS SELECT public.app_secret_count_atomic() AS n;
CREATE OR REPLACE FUNCTION public.app_item_count() RETURNS bigint
    LANGUAGE sql STABLE AS 'SELECT count(*) FROM public.app_plan_items';
CREATE OR REPLACE VIEW public.app_item_count_view AS SELECT public.app_item_count() AS n;
CREATE OR REPLACE FUNCTION public.app_secret_path_count() RETURNS bigint
    LANGUAGE sql STABLE SET search_path = secret AS 'SELECT count(*) FROM accounts';
CREATE OR REPLACE VIEW public.app_secret_path_count_view AS SELECT public.app_secret_path_count() AS n;
CREATE OR REPLACE FUNCTION public.app_writing_count() RETURNS bigint
    LANGUAGE sql VOLATILE AS 'INSERT INTO public.app_plan_items SELECT 1 WHERE false; SELECT 1::bigint';
CREATE OR REPLACE VIEW public.app_writing_count_view AS SELECT public.app_writing_count() AS n;
CREATE OR REPLACE FUNCTION public.app_escaped_text() RETURNS text
    LANGUAGE sql STABLE SET standard_conforming_strings = off
    AS $$SELECT 'p\\' AS a, ' || (SELECT token FROM secret.accounts LIMIT 1) || ' --'$$;
CREATE OR REPLACE VIEW public.app_escaped_text_view AS SELECT public.app_escaped_text() AS n;
CREATE OR REPLACE FUNCTION public.app_shadowed_roles() RETURNS SETOF text LANGUAGE sql VOLATILE AS $$
    SELECT * FROM (WITH pg_authid AS (SELECT 'x'::text AS rolname) SELECT rolname FROM pg_authid) s
    UNION ALL SELECT rolname::text FROM pg_authid$$;
CREATE OR REPLACE VIEW public.app_shadowed_roles_view AS SELECT n FROM public.app_shadowed_roles() AS n;
CREATE OR REPLACE FUNCTION public.app_cte_items() RETURNS SETOF int LANGUAGE sql STABLE AS $$
    WITH app_i AS (SELECT id FROM public.app_plan_items) SELECT id FROM app_i UNION ALL SELECT id FROM app_i$$;
CREATE OR REPLACE VIEW public.app_cte_items_view AS SELECT n FROM public.app_cte_items() AS n;
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


@pytest.fixture
async def db_plan_check_non_sql(
    test_postgres_connection_string: tuple[str, str], db_plan_check: DbAccess
) -> AsyncGenerator[DbAccess, None]:
    """db_plan_check с plan_check_allow_non_sql_functions=true: функции public не на sql проходят непроверенными."""
    connection_string, _ = test_postgres_connection_string
    config = DatabaseConfig.from_uri(
        connection_string,
        access_mode=AccessMode.BASIC,
        write_mode=True,
        table_prefix="app_",
        plan_check=True,
        plan_check_allow_non_sql_functions=True,
    )
    service = DbAccessService(config)
    try:
        yield service.view(EffectiveAccess(AccessMode.BASIC, write_mode=True))
    finally:
        await service.close()


# Операторы public над app_tag, реализованные secret.tag_boom. Имена =, <, >, <=, >= проверяются по имени, все
# перегрузки сразу: пока они есть, под plan_check отклоняется любое сравнение, поэтому они живут только в своих тестах.
_TAG_OPERATORS = """
DO $$BEGIN CREATE TYPE public.app_tag AS ENUM ('a', 'b'); EXCEPTION WHEN duplicate_object THEN NULL; END$$;
CREATE OR REPLACE FUNCTION secret.tag_boom(x public.app_tag, y public.app_tag) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE AS $$BEGIN RAISE EXCEPTION 'secret.tag_boom was executed'; END$$;
CREATE OPERATOR public.= (LEFTARG = public.app_tag, RIGHTARG = public.app_tag, FUNCTION = secret.tag_boom);
CREATE OPERATOR public.< (LEFTARG = public.app_tag, RIGHTARG = public.app_tag, FUNCTION = secret.tag_boom);
CREATE OPERATOR public.> (LEFTARG = public.app_tag, RIGHTARG = public.app_tag, FUNCTION = secret.tag_boom);
CREATE OPERATOR public.<= (LEFTARG = public.app_tag, RIGHTARG = public.app_tag, FUNCTION = secret.tag_boom);
CREATE OPERATOR public.>= (LEFTARG = public.app_tag, RIGHTARG = public.app_tag, FUNCTION = secret.tag_boom);
"""
_DROP_TAG_OPERATORS = """
DROP OPERATOR IF EXISTS public.= (public.app_tag, public.app_tag);
DROP OPERATOR IF EXISTS public.< (public.app_tag, public.app_tag);
DROP OPERATOR IF EXISTS public.> (public.app_tag, public.app_tag);
DROP OPERATOR IF EXISTS public.<= (public.app_tag, public.app_tag);
DROP OPERATOR IF EXISTS public.>= (public.app_tag, public.app_tag);
"""


@pytest.fixture
async def db_tag_operators(db_plan_check: DbAccess, db_full: DbAccess) -> AsyncGenerator[DbAccess, None]:
    """db_plan_check, пока в public есть операторы сравнения app_tag над secret.tag_boom."""
    await db_full.sql_driver.execute(_DROP_TAG_OPERATORS + _TAG_OPERATORS, readonly=False)
    try:
        yield db_plan_check
    finally:
        await db_full.sql_driver.execute(_DROP_TAG_OPERATORS, readonly=False)


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
    """Строгий режим: определения представлений information_schema проверяются до плана — их типы вне public."""
    with pytest.raises(PlanAccessError, match=r"type 'information_schema\.sql_identifier'"):
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
async def test_views_with_allowed_expressions_return_data(db_plan_check_non_sql: DbAccess) -> None:
    """app_double — PL/pgSQL: проходит только с plan_check_allow_non_sql_functions=true."""
    lower_rows = await db_plan_check_non_sql.sql_driver.execute("SELECT l FROM app_expr_lower_view", readonly=True)
    public_rows = await db_plan_check_non_sql.sql_driver.execute("SELECT d FROM app_expr_public_fn_view", readonly=True)
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
@pytest.mark.parametrize(
    "sql",
    ["SELECT (NULL::other_users).no_such_column FROM app_plan_items", "SELECT '(1,2,3)'::other_users"],
)
async def test_row_type_error_oracle_is_closed_before_explain(db_plan_check: DbAccess, sql: str) -> None:
    """Без проверки до EXPLAIN ошибка разбора (нет колонки, лишнее поле) раскрыла бы структуру other_users."""
    with pytest.raises(PlanAccessError, match=r"relation 'public\.other_users'"):
        await db_plan_check.sql_driver.execute(sql, readonly=True)


@pytest.mark.asyncio
async def test_domain_over_a_row_type_without_the_prefix_is_rejected(db_plan_check: DbAccess) -> None:
    """У домена typrelid = 0: строковый тип other_users виден только через typbasetype."""
    with pytest.raises(PlanAccessError, match=r"relation 'public\.other_users'"):
        await db_plan_check.sql_driver.execute(
            "SELECT * FROM json_populate_record(NULL::other_users_dom, '{}')", readonly=True
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("view", "name"),
    [
        ("app_api_key_view", r"function 'secret\.api_key'"),
        ("app_limit_setting_view", r"function 'pg_catalog\.current_setting'"),
        ("app_setting_operator_view", r"function 'pg_catalog\.current_setting'"),
    ],
)
async def test_view_dependencies_hidden_from_the_plan_are_rejected(
    db_plan_check: DbAccess, view: str, name: str
) -> None:
    """План их не показывает: IMMUTABLE secret.api_key() свёрнут в константу, LIMIT EXPLAIN не печатает,
    оператор public !! называет себя, а не current_setting. Их видит проверка правил представления."""
    with pytest.raises(PlanAccessError, match=name):
        await db_plan_check.sql_driver.execute(f"SELECT * FROM {view}", readonly=True)


@pytest.mark.asyncio
async def test_view_with_allowed_dependencies_returns_rows(db_plan_check: DbAccess) -> None:
    rows = await db_plan_check.sql_driver.execute("SELECT l FROM app_plan_people_lower_view", readonly=True)
    assert [row.cells["l"] for row in rows] == ["alice"]


@pytest.mark.asyncio
async def test_select_from_a_table_with_a_non_firing_rule_passes(db_plan_check: DbAccess) -> None:
    """SELECT держит только AccessShareLock: правило ON INSERT (ev_type <> '1') не может сработать и не проверяется."""
    rows = await db_plan_check.sql_driver.execute("SELECT count(*) AS n FROM app_rule_items", readonly=True)
    assert rows[0].cells["n"] == 0


@pytest.mark.asyncio
async def test_insert_that_can_fire_a_rule_outside_basic_is_rejected(db_plan_check: DbAccess) -> None:
    """INSERT держит RowExclusiveLock: правило DO ALSO может сработать, его secret.api_key() проверяется."""
    with pytest.raises(PlanAccessError, match=r"secret\.api_key"):
        await db_plan_check.sql_driver.execute("INSERT INTO app_rule_items (id) VALUES (1)", readonly=False)


@pytest.mark.asyncio
async def test_select_from_a_matview_with_a_rejected_definition_passes(db_plan_check: DbAccess) -> None:
    """Relkind = 'm': чтение матвью не выполняет "_RETURN", current_setting('port') в определении не проверяется."""
    rows = await db_plan_check.sql_driver.execute("SELECT port FROM app_matview_setting", readonly=True)
    assert rows[0].cells["port"] is not None


@pytest.mark.asyncio
async def test_folded_function_of_a_view_is_not_executed_before_the_rejection(db_plan_check: DbAccess) -> None:
    """IMMUTABLE secret.boom() с константами планировщик выполнил бы при EXPLAIN (ошибка 'was executed');
    правила читаются после PREPARE, до планирования: приходит отказ по функции, а не её исключение."""
    with pytest.raises(PlanAccessError, match=r"function 'secret\.boom'"):
        await db_plan_check.sql_driver.execute("SELECT * FROM app_boom_view", readonly=True)


@pytest.mark.asyncio
async def test_folded_function_of_a_view_is_not_executed_with_an_undeterminable_parameter(
    db_plan_check: DbAccess,
) -> None:
    """PREPARE не выводит тип $1 в $1 IS NULL (42P18): оператор готовится ещё раз с NULL вместо $1, и правила
    представления читаются до EXPLAIN (GENERIC_PLAN) — secret.boom() не выполняется."""
    with pytest.raises(PlanAccessError, match=r"function 'secret\.boom'"):
        await db_plan_check.sql_driver.execute(
            "EXPLAIN (GENERIC_PLAN) SELECT * FROM app_boom_view WHERE $1 IS NULL", readonly=True
        )


@pytest.mark.asyncio
async def test_generic_statement_whose_null_retry_does_not_parse_is_unverifiable(db_plan_check: DbAccess) -> None:
    """$1 IS NULL — 42P18, повтор с NULL печатает ($2)[1] как NULL[1] (синтаксическая ошибка): текст не
    отправляется, отказ PlanUnverifiableError, а не ошибка точки сохранения."""
    with pytest.raises(PlanUnverifiableError):
        await db_plan_check.sql_driver.execute(
            "EXPLAIN (GENERIC_PLAN) SELECT $1 IS NULL, $2 = ARRAY[1], ($2)[1]", readonly=True
        )


@pytest.mark.asyncio
async def test_public_operator_over_a_builtin_outside_basic_is_rejected_in_the_agent_sql(
    db_plan_check: DbAccess,
) -> None:
    """Оператор public.!! (text, boolean) реализован pg_catalog.current_setting: SQL агента видит только имя оператора."""
    with pytest.raises(PlanAccessError, match=r"function 'pg_catalog\.current_setting'"):
        await db_plan_check.sql_driver.execute(
            "SELECT ('max_connections' !! true) AS s FROM app_plan_people", readonly=True
        )


@pytest.mark.asyncio
async def test_public_aggregate_over_a_foreign_function_is_rejected(db_plan_check: DbAccess) -> None:
    """Валидатор basic пускает только функции из списка basic; public.sum(text) — перегрузка разрешённого имени:
    sum(name) по тексту выглядит встроенным агрегатом, план печатает его без схемы."""
    with pytest.raises(PlanAccessError, match=r"function 'secret\.agg_step'"):
        await db_plan_check.sql_driver.execute("SELECT sum(name) AS c FROM app_plan_people", readonly=True)


@pytest.mark.asyncio
async def test_public_operator_over_a_public_function_passes(db_plan_check_non_sql: DbAccess) -> None:
    """app_close_to — PL/pgSQL: проходит только с plan_check_allow_non_sql_functions=true."""
    rows = await db_plan_check_non_sql.sql_driver.execute(
        "SELECT (id <~> 2) AS near FROM app_plan_items", readonly=True
    )
    assert rows[0].cells["near"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 'a'::app_tag BETWEEN 'a'::app_tag AND 'b'::app_tag AS r",
        "SELECT 'a'::app_tag NOT BETWEEN 'a'::app_tag AND 'b'::app_tag AS r",
        "SELECT 'a'::app_tag BETWEEN SYMMETRIC 'a'::app_tag AND 'b'::app_tag AS r",
        "SELECT CASE 'a'::app_tag WHEN 'b'::app_tag THEN 1 END AS r",
        "SELECT * FROM (SELECT 'a'::app_tag AS c) AS x JOIN (SELECT 'b'::app_tag AS c) AS y USING (c)",
        "SELECT * FROM (SELECT 'a'::app_tag AS c) AS x NATURAL JOIN (SELECT 'b'::app_tag AS c) AS y",
        "SELECT 'a'::app_tag IN (SELECT 'b'::app_tag) AS r",
        "SELECT 'a'::app_tag NOT IN (SELECT 'b'::app_tag) AS r",
    ],
)
async def test_operator_generated_by_the_parser_is_checked_by_its_function(
    db_tag_operators: DbAccess, sql: str
) -> None:
    """Имени оператора в тексте нет: BETWEEN — сравнения, CASE x WHEN, USING и NATURAL — равенство, IN (подзапрос)
    — = ANY. С константами EXPLAIN выполнил бы secret.tag_boom (IN (подзапрос) — при выполнении): отказ раньше."""
    with pytest.raises(PlanAccessError, match=r"function 'secret\.tag_boom'"):
        await db_tag_operators.sql_driver.execute(sql, readonly=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("table", "function"),
    [
        ("app_audited", r"secret\.audit"),
        ("app_checked", r"secret\.valid"),
        ("app_generated", r"secret\.valid"),
        ("app_policy_items", r"secret\.valid"),
    ],
)
async def test_insert_into_a_table_whose_write_path_calls_a_foreign_function_is_rejected(
    db_plan_check: DbAccess, table: str, function: str
) -> None:
    """Триггер, CHECK, генерируемая колонка и WITH CHECK политики в плане не видны: их читает путь записи."""
    with pytest.raises(PlanAccessError, match=rf"function '{function}'"):
        await db_plan_check.sql_driver.execute(f"INSERT INTO {table} (id) VALUES (1)", readonly=False)


@pytest.mark.asyncio
async def test_select_from_a_table_with_a_foreign_trigger_passes(db_plan_check: DbAccess) -> None:
    """SELECT держит AccessShareLock: путь записи не срабатывает и не проверяется."""
    rows = await db_plan_check.sql_driver.execute("SELECT count(*) AS n FROM app_audited", readonly=True)
    assert rows[0].cells["n"] >= 0


# Определения на secret.boom_int(): IMMUTABLE без аргументов — планировщик сворачивает вызов, то есть выполняет
# его при EXPLAIN. Объекты создаются, пока функция возвращает 0 (CREATE INDEX и PARTITION OF сворачивают выражения
# сами), потом она заменяется на RAISE; после теста — обратно и всё удаляется: CHECK, индекс и политика
# отклоняли бы любое чтение этих таблиц.
_BOOM_INT_RETURNS = """
CREATE OR REPLACE FUNCTION secret.boom_int() RETURNS int LANGUAGE plpgsql IMMUTABLE AS $$BEGIN RETURN 0; END$$;
"""
_BOOM_INT_RAISES = """
CREATE OR REPLACE FUNCTION secret.boom_int() RETURNS int
    LANGUAGE plpgsql IMMUTABLE AS $$BEGIN RAISE EXCEPTION 'secret.boom_int was executed'; END$$;
"""
_DROP_BOOM_DEFINITIONS = """
DROP TABLE IF EXISTS public.app_boom_dom_t, public.app_boom_def_t, public.app_boom_pk, public.app_boom_child,
    public.app_boom_parent, public.app_boom_pp, public.app_boom_rls, public.app_boom_ip, public.app_boom_xi,
    public.app_boom_st, public.app_boom_fx CASCADE;
DROP VIEW IF EXISTS public.app_boom_fn_view, public.app_boom_fn_atomic_view, public.app_boom_arg_view,
    public.app_boom_arg_caller_view;
DROP FUNCTION IF EXISTS public.app_boom_fn_rows(), public.app_boom_fn_rows_atomic(), public.app_boom_arg(int),
    public.app_boom_arg_caller(), public.app_boom_arg_sql(int);
DROP DOMAIN IF EXISTS public.app_boom_text;
DROP FUNCTION IF EXISTS public.app_pass_row();
"""
_BOOM_DEFINITIONS = """
CREATE DOMAIN public.app_boom_text AS text DEFAULT (secret.boom_int())::text;
CREATE TABLE public.app_boom_dom_t (id int, s public.app_boom_text);
CREATE TABLE public.app_boom_def_t (id int, s int DEFAULT secret.boom_int());
CREATE TABLE public.app_boom_pk (id int) PARTITION BY RANGE ((id + secret.boom_int()));
CREATE TABLE public.app_boom_pk1 PARTITION OF public.app_boom_pk FOR VALUES FROM (MINVALUE) TO (MAXVALUE);
CREATE TABLE public.app_boom_parent (id int PRIMARY KEY);
CREATE TABLE public.app_boom_child (
    pid int REFERENCES public.app_boom_parent ON DELETE SET NULL, CHECK (pid > secret.boom_int())
);
INSERT INTO public.app_boom_parent VALUES (1);
INSERT INTO public.app_boom_child VALUES (1);
CREATE FUNCTION public.app_pass_row() RETURNS trigger LANGUAGE plpgsql AS $$BEGIN RETURN NEW; END$$;
CREATE TABLE public.app_boom_pp (id int) PARTITION BY RANGE (id);
CREATE TABLE public.app_boom_pp1 PARTITION OF public.app_boom_pp FOR VALUES FROM (MINVALUE) TO (MAXVALUE);
CREATE TRIGGER app_boom_pp1_when BEFORE INSERT ON public.app_boom_pp1
    FOR EACH ROW WHEN (NEW.id > secret.boom_int()) EXECUTE FUNCTION public.app_pass_row();
CREATE TABLE public.app_boom_rls (id int);
ALTER TABLE public.app_boom_rls ENABLE ROW LEVEL SECURITY;
CREATE POLICY app_boom_rls_read ON public.app_boom_rls FOR SELECT USING (id > secret.boom_int());
CREATE TABLE public.app_boom_ip (id int);
CREATE TABLE public.app_boom_ic (CHECK (id > secret.boom_int())) INHERITS (public.app_boom_ip);
CREATE TABLE public.app_boom_xi (id int);
CREATE INDEX app_boom_xi_expr ON public.app_boom_xi ((id + secret.boom_int()));
CREATE TABLE public.app_boom_st (id int, v int);
CREATE STATISTICS public.app_boom_st_expr ON (id + secret.boom_int()), v FROM public.app_boom_st;
CREATE TABLE public.app_boom_fx (id int);
CREATE INDEX app_boom_fx_expr ON public.app_boom_fx ((id + secret.boom_int()));
CREATE FUNCTION public.app_boom_fn_rows() RETURNS SETOF public.app_boom_fx
    LANGUAGE sql STABLE AS 'SELECT * FROM public.app_boom_fx WHERE id = 1';
CREATE VIEW public.app_boom_fn_view AS SELECT * FROM public.app_boom_fn_rows();
CREATE FUNCTION public.app_boom_fn_rows_atomic() RETURNS SETOF public.app_boom_fx
    LANGUAGE sql STABLE BEGIN ATOMIC SELECT * FROM public.app_boom_fx WHERE id = 1; END;
CREATE VIEW public.app_boom_fn_atomic_view AS SELECT * FROM public.app_boom_fn_rows_atomic();
CREATE FUNCTION public.app_boom_arg(a int DEFAULT (1 + secret.boom_int())) RETURNS int
    LANGUAGE plpgsql IMMUTABLE AS $$BEGIN RETURN a; END$$;
CREATE VIEW public.app_boom_arg_view AS SELECT public.app_boom_arg() AS x;
CREATE FUNCTION public.app_boom_arg_sql(a int DEFAULT secret.boom_int()) RETURNS int
    LANGUAGE sql IMMUTABLE AS 'SELECT a';
CREATE FUNCTION public.app_boom_arg_caller() RETURNS int LANGUAGE sql STABLE AS 'SELECT public.app_boom_arg_sql()';
CREATE VIEW public.app_boom_arg_caller_view AS SELECT public.app_boom_arg_caller() AS x;
"""


@pytest.fixture
async def db_boom_definitions(db_plan_check: DbAccess, db_full: DbAccess) -> AsyncGenerator[DbAccess, None]:
    """db_plan_check, пока в public есть таблицы, чьи определения вызывают бросающую secret.boom_int()."""
    await db_full.sql_driver.execute(
        _BOOM_INT_RETURNS + _DROP_BOOM_DEFINITIONS + _BOOM_DEFINITIONS + _BOOM_INT_RAISES, readonly=False
    )
    try:
        yield db_plan_check
    finally:
        await db_full.sql_driver.execute(_BOOM_INT_RETURNS + _DROP_BOOM_DEFINITIONS, readonly=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("INSERT INTO app_boom_dom_t (id) VALUES (1)", id="domain-default"),
        pytest.param("INSERT INTO app_boom_def_t (id) VALUES (1)", id="column-default"),
        pytest.param("INSERT INTO app_boom_pk (id) VALUES (1)", id="partition-key-insert"),
        pytest.param("SELECT * FROM app_boom_pk WHERE id = 1", id="partition-key-select"),
        pytest.param("DELETE FROM app_boom_parent WHERE id = 1", id="cascade-child-check"),
        pytest.param("INSERT INTO app_boom_pp (id) VALUES (1)", id="partition-trigger"),
        pytest.param("SELECT * FROM app_boom_rls WHERE id = 1", id="policy-select"),
        pytest.param("SELECT * FROM app_boom_ip WHERE id = 1", id="inheritance-child-check-select"),
        pytest.param("SELECT * FROM app_boom_xi WHERE id = 1", id="expression-index-select"),
        pytest.param("SELECT * FROM app_boom_st WHERE id = 1", id="statistics-select"),
        pytest.param("SELECT * FROM app_boom_fn_view", id="inlined-function-table-index"),
        pytest.param("SELECT * FROM app_boom_fn_atomic_view", id="inlined-atomic-function-table-index"),
        pytest.param("SELECT * FROM app_boom_arg_view", id="argument-default"),
        pytest.param("SELECT * FROM app_boom_arg_caller_view", id="sql-body-argument-default"),
    ],
)
async def test_definition_calling_a_foreign_function_is_rejected_before_it_runs(
    db_boom_definitions: DbAccess, sql: str
) -> None:
    """Умолчание домена и колонки, ключ секционирования, CHECK каскадной таблицы, триггер секции, политика, CHECK
    наследника, индекс и статистика: EXPLAIN (или выполнение) вызвал бы secret.boom_int() и получил бы его
    исключение. Отказ по функции приходит раньше — определения читаются после PREPARE, до EXPLAIN. Таблицу,
    которую читает только тело встраиваемой SQL-функции, блокирует PREPARE по телу — её индекс читается тоже.
    Умолчание аргумента функции public (вызов без него — в представлении или в теле SQL-функции) планировщик
    подставляет и сворачивает — оно проверяется вместе с реализацией функции."""
    with pytest.raises(PlanAccessError, match=r"function 'secret\.boom_int'"):
        await db_boom_definitions.sql_driver.execute(sql, readonly=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("view", ["app_secret_count_view", "app_secret_count_atomic_view"])
async def test_view_over_a_public_sql_function_reading_a_foreign_table_is_rejected(
    db_plan_check: DbAccess, view: str
) -> None:
    """Скалярная SQL-функция с FROM не встраивается: план видит только app_secret_count(), тело — проверка тел."""
    with pytest.raises(PlanAccessError, match=r"relation 'secret\.accounts'"):
        await db_plan_check.sql_driver.execute(f"SELECT n FROM {view}", readonly=True)


@pytest.mark.asyncio
async def test_view_over_a_public_sql_function_reading_a_prefixed_table_passes(db_plan_check: DbAccess) -> None:
    rows = await db_plan_check.sql_driver.execute("SELECT n FROM app_item_count_view", readonly=True)
    assert rows[0].cells["n"] >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize("view", ["app_secret_path_count_view", "app_writing_count_view", "app_escaped_text_view"])
async def test_view_over_an_unverifiable_sql_function_body_is_rejected(db_plan_check: DbAccess, view: str) -> None:
    """Собственный SET search_path, изменение данных в теле и SET standard_conforming_strings = off — не проверить.

    При off обратный слэш экранирует кавычку: Postgres выполняет подзапрос, который pglast видит литералом.
    """
    with pytest.raises(PlanUnverifiableError, match="definitions of views"):
        await db_plan_check.sql_driver.execute(f"SELECT n FROM {view}", readonly=True)


@pytest.mark.asyncio
async def test_table_named_like_a_cte_of_another_scope_is_checked(db_plan_check: DbAccess) -> None:
    """CTE pg_authid видна только в своём подзапросе: вторая ветвь UNION читает pg_catalog.pg_authid.

    VOLATILE-функция не встраивается: план видит только её вызов, pg_authid — только проверка тела.
    """
    with pytest.raises(PlanAccessError, match=r"relation 'public\.pg_authid'"):
        await db_plan_check.sql_driver.execute("SELECT n FROM app_shadowed_roles_view", readonly=True)


@pytest.mark.asyncio
async def test_cte_used_in_every_branch_of_its_statement_passes(db_plan_check: DbAccess) -> None:
    rows = await db_plan_check.sql_driver.execute("SELECT n FROM app_cte_items_view", readonly=True)
    assert len(rows) >= 2


# Неявные вызовы типов (машинерия): функции ввода-вывода, приведения, классы операторов, CHECK доменов. Бросающие
# функции доказывают «отклонено до выполнения»: без проверки PREPARE, EXPLAIN или выполнение получили бы их
# исключение. Функции ввода-вывода — обёртки LANGUAGE internal (нужен суперпользователь): ввод int4in бросает на
# 'abc', вывод enum_out бросает на любом значении, которое не метка перечисления. Операторы класса названы #<, #=
# и т. д.: операторы public с именами =, < проверяются по имени, все перегрузки сразу, и затронули бы любые тесты.
_DROP_IMPLICIT_CALLS = """
DROP VIEW IF EXISTS public.app_ic_dom_view, public.app_ic_hidden_dom_view, public.app_ic_sorted_view,
    public.app_ic_body_view, public.app_ic_atomic_view, public.app_ic_def_view;
DROP TABLE IF EXISTS public.app_ic_cast_t, public.app_ic_io_t, public.app_ic_ct_t, public.app_ic_ok_t,
    public.app_ic_bt, public.app_ic_xt_t CASCADE;
DROP TABLE IF EXISTS public.app_ic_row_t CASCADE;
DROP OPERATOR IF EXISTS public.#<<< (int, int);
DROP FUNCTION IF EXISTS public.app_ic_body(), public.app_ic_atomic(), public.app_ic_def(int), public.app_ic_opf(int, int);
DROP DOMAIN IF EXISTS public.app_ic_dc CASCADE;
DROP TYPE IF EXISTS public.app_ic_pair, public.app_ic_rr, public.app_ic_e, public.app_ic_ok CASCADE;
DROP DOMAIN IF EXISTS public.app_ic_rd CASCADE;
DROP TYPE IF EXISTS public.app_ic_t, public.app_ic_ct, public.app_ic_b, public.app_ic_xt CASCADE;
DROP OPERATOR IF EXISTS public.#~ (int, int);
DROP FUNCTION IF EXISTS secret.ic_sel(internal, oid, internal, integer);
"""
_IC_BOOM_RETURNS = """
CREATE OR REPLACE FUNCTION secret.ic_boom() RETURNS int LANGUAGE plpgsql IMMUTABLE AS $$BEGIN RETURN 0; END$$;
"""
_IC_BOOM_RAISES = """
CREATE OR REPLACE FUNCTION secret.ic_boom() RETURNS int
    LANGUAGE plpgsql IMMUTABLE AS $$BEGIN RAISE EXCEPTION 'secret.ic_boom was executed'; END$$;
"""
_IMPLICIT_CALLS = """
CREATE TYPE public.app_ic_e AS ENUM ('a');
CREATE OR REPLACE FUNCTION secret.ic_to_e(n int) RETURNS public.app_ic_e
    LANGUAGE plpgsql IMMUTABLE AS $$BEGIN RAISE EXCEPTION 'secret.ic_to_e was executed'; END$$;
CREATE CAST (int AS public.app_ic_e) WITH FUNCTION secret.ic_to_e(int) AS ASSIGNMENT;
CREATE TABLE public.app_ic_cast_t (e public.app_ic_e);
CREATE TYPE public.app_ic_t;
CREATE FUNCTION secret.ic_t_in(cstring) RETURNS public.app_ic_t AS 'int4in' LANGUAGE internal IMMUTABLE STRICT;
CREATE FUNCTION secret.ic_t_out(public.app_ic_t) RETURNS cstring AS 'enum_out' LANGUAGE internal IMMUTABLE STRICT;
CREATE TYPE public.app_ic_t (INPUT = secret.ic_t_in, OUTPUT = secret.ic_t_out, LIKE = int4);
CREATE TABLE public.app_ic_io_t (t public.app_ic_t);
CREATE TYPE public.app_ic_pair AS (t public.app_ic_t);
CREATE TYPE public.app_ic_ct;
CREATE FUNCTION public.app_ic_ct_in(cstring) RETURNS public.app_ic_ct AS 'int4in' LANGUAGE internal IMMUTABLE STRICT;
CREATE FUNCTION public.app_ic_ct_out(public.app_ic_ct) RETURNS cstring
    AS 'int4out' LANGUAGE internal IMMUTABLE STRICT;
CREATE TYPE public.app_ic_ct (INPUT = public.app_ic_ct_in, OUTPUT = public.app_ic_ct_out, LIKE = int4);
CREATE CAST (public.app_ic_ct AS int4) WITHOUT FUNCTION;
CREATE FUNCTION public.app_ic_lt(a public.app_ic_ct, b public.app_ic_ct) RETURNS boolean
    LANGUAGE sql IMMUTABLE AS 'SELECT a::int4 < b::int4';
CREATE FUNCTION public.app_ic_le(a public.app_ic_ct, b public.app_ic_ct) RETURNS boolean
    LANGUAGE sql IMMUTABLE AS 'SELECT a::int4 <= b::int4';
CREATE FUNCTION public.app_ic_eq(a public.app_ic_ct, b public.app_ic_ct) RETURNS boolean
    LANGUAGE sql IMMUTABLE AS 'SELECT a::int4 = b::int4';
CREATE FUNCTION public.app_ic_ge(a public.app_ic_ct, b public.app_ic_ct) RETURNS boolean
    LANGUAGE sql IMMUTABLE AS 'SELECT a::int4 >= b::int4';
CREATE FUNCTION public.app_ic_gt(a public.app_ic_ct, b public.app_ic_ct) RETURNS boolean
    LANGUAGE sql IMMUTABLE AS 'SELECT a::int4 > b::int4';
CREATE OPERATOR public.#< (LEFTARG = public.app_ic_ct, RIGHTARG = public.app_ic_ct, FUNCTION = public.app_ic_lt);
CREATE OPERATOR public.#<= (LEFTARG = public.app_ic_ct, RIGHTARG = public.app_ic_ct, FUNCTION = public.app_ic_le);
CREATE OPERATOR public.#= (LEFTARG = public.app_ic_ct, RIGHTARG = public.app_ic_ct, FUNCTION = public.app_ic_eq);
CREATE OPERATOR public.#>= (LEFTARG = public.app_ic_ct, RIGHTARG = public.app_ic_ct, FUNCTION = public.app_ic_ge);
CREATE OPERATOR public.#> (LEFTARG = public.app_ic_ct, RIGHTARG = public.app_ic_ct, FUNCTION = public.app_ic_gt);
CREATE OR REPLACE FUNCTION secret.ic_cmp(a public.app_ic_ct, b public.app_ic_ct) RETURNS int
    LANGUAGE plpgsql IMMUTABLE AS $$BEGIN RAISE EXCEPTION 'secret.ic_cmp was executed'; END$$;
CREATE OPERATOR CLASS public.app_ic_ct_ops DEFAULT FOR TYPE public.app_ic_ct USING btree AS
    OPERATOR 1 public.#<, OPERATOR 2 public.#<=, OPERATOR 3 public.#=, OPERATOR 4 public.#>=, OPERATOR 5 public.#>,
    FUNCTION 1 secret.ic_cmp(public.app_ic_ct, public.app_ic_ct);
CREATE TABLE public.app_ic_ct_t (c public.app_ic_ct);
INSERT INTO public.app_ic_ct_t VALUES ('1'), ('2');
CREATE DOMAIN public.app_ic_rd AS int CHECK (VALUE > secret.ic_boom());
CREATE TYPE public.app_ic_rr AS RANGE (subtype = public.app_ic_rd);
CREATE VIEW public.app_ic_dom_view AS SELECT 1::public.app_ic_rd AS d;
CREATE VIEW public.app_ic_hidden_dom_view AS SELECT 1 AS n WHERE 1::public.app_ic_rd IS NOT NULL;
CREATE VIEW public.app_ic_sorted_view AS SELECT 1 AS n FROM public.app_ic_ct_t ORDER BY c;
CREATE FUNCTION secret.ic_sel(internal, oid, internal, integer) RETURNS float8 AS 'eqsel' LANGUAGE internal STABLE;
CREATE OPERATOR public.#~ (LEFTARG = int, RIGHTARG = int, FUNCTION = public.app_close_to, RESTRICT = secret.ic_sel);
CREATE TYPE public.app_ic_ok AS ENUM ('x', 'y');
CREATE TABLE public.app_ic_ok_t (e public.app_ic_ok);
INSERT INTO public.app_ic_ok_t VALUES ('y'), ('x');
CREATE DOMAIN public.app_ic_dc AS int CHECK (VALUE > 0 AND (1::public.app_ic_e) IS NOT NULL);
CREATE FUNCTION public.app_ic_body() RETURNS int LANGUAGE sql IMMUTABLE
    AS $$SELECT CASE WHEN 1::public.app_ic_e IS NULL THEN 1 ELSE 2 END$$;
CREATE FUNCTION public.app_ic_atomic() RETURNS int LANGUAGE sql IMMUTABLE
    BEGIN ATOMIC SELECT CASE WHEN 1::public.app_ic_e IS NULL THEN 1 ELSE 2 END; END;
CREATE FUNCTION public.app_ic_def(a int DEFAULT (CASE WHEN 1::public.app_ic_e IS NULL THEN 1 ELSE 2 END))
    RETURNS int LANGUAGE sql IMMUTABLE AS $$SELECT a$$;
CREATE FUNCTION public.app_ic_opf(a int, b int) RETURNS boolean LANGUAGE sql IMMUTABLE
    AS $$SELECT a < b AND (1::public.app_ic_e) IS NOT NULL$$;
CREATE OPERATOR public.#<<< (LEFTARG = int, RIGHTARG = int, FUNCTION = public.app_ic_opf);
CREATE VIEW public.app_ic_body_view AS SELECT public.app_ic_body() AS v;
CREATE VIEW public.app_ic_atomic_view AS SELECT public.app_ic_atomic() AS v;
CREATE VIEW public.app_ic_def_view AS SELECT public.app_ic_def() AS v;
CREATE TABLE public.app_ic_row_t (x int);
INSERT INTO public.app_ic_row_t VALUES (1);
CREATE FUNCTION secret.ic_row_to_int(r public.app_ic_row_t) RETURNS int
    LANGUAGE plpgsql IMMUTABLE AS $$BEGIN RAISE EXCEPTION 'secret.ic_row_to_int was executed'; END$$;
CREATE CAST (public.app_ic_row_t AS int) WITH FUNCTION secret.ic_row_to_int(public.app_ic_row_t) AS IMPLICIT;
CREATE TYPE public.app_ic_b;
CREATE FUNCTION public.app_ic_b_in(cstring) RETURNS public.app_ic_b AS 'int4in' LANGUAGE internal IMMUTABLE STRICT;
CREATE FUNCTION public.app_ic_b_out(public.app_ic_b) RETURNS cstring AS 'int4out' LANGUAGE internal IMMUTABLE STRICT;
CREATE TYPE public.app_ic_b (INPUT = public.app_ic_b_in, OUTPUT = public.app_ic_b_out, LIKE = int4);
CREATE CAST (public.app_ic_b AS public.app_ic_ct) WITHOUT FUNCTION AS IMPLICIT;
CREATE TABLE public.app_ic_bt (b public.app_ic_b);
INSERT INTO public.app_ic_bt VALUES ('1'), ('2');
CREATE TYPE public.app_ic_xt;
CREATE FUNCTION public.app_ic_xt_in(cstring) RETURNS public.app_ic_xt AS 'int4in' LANGUAGE internal IMMUTABLE STRICT;
CREATE FUNCTION public.app_ic_xt_out(public.app_ic_xt) RETURNS cstring
    AS 'int4out' LANGUAGE internal IMMUTABLE STRICT;
CREATE TYPE public.app_ic_xt (INPUT = public.app_ic_xt_in, OUTPUT = public.app_ic_xt_out, LIKE = int4);
CREATE FUNCTION public.app_ic_xt_hash(public.app_ic_xt) RETURNS int AS 'hashint4' LANGUAGE internal IMMUTABLE STRICT;
CREATE OR REPLACE FUNCTION secret.ic_xt_eq(a public.app_ic_xt, b public.app_ic_xt) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE AS $$BEGIN RAISE EXCEPTION 'secret.ic_xt_eq was executed'; END$$;
CREATE OPERATOR public.#== (
    LEFTARG = public.app_ic_xt, RIGHTARG = public.app_ic_xt, FUNCTION = secret.ic_xt_eq, COMMUTATOR = #==
);
CREATE OPERATOR CLASS public.app_ic_xt_ops DEFAULT FOR TYPE public.app_ic_xt USING hash AS
    OPERATOR 1 public.#==, FUNCTION 1 public.app_ic_xt_hash(public.app_ic_xt);
CREATE TABLE public.app_ic_xt_t (x public.app_ic_xt, EXCLUDE USING hash (x WITH #==));
INSERT INTO public.app_ic_xt_t VALUES ('1');
"""


@pytest.fixture
async def db_implicit_calls(db_plan_check: DbAccess, db_full: DbAccess) -> AsyncGenerator[DbAccess, None]:
    """db_plan_check, пока в public есть типы, чья машинерия вызывает функции secret (большинство — бросающие)."""
    await db_full.sql_driver.execute(
        _IC_BOOM_RETURNS + _DROP_IMPLICIT_CALLS + _IMPLICIT_CALLS + _IC_BOOM_RAISES, readonly=False
    )
    try:
        yield db_plan_check
    finally:
        await db_full.sql_driver.execute(_DROP_IMPLICIT_CALLS, readonly=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sql", "function"),
    [
        pytest.param("SELECT 1::app_ic_e AS e", "ic_to_e", id="explicit-cast"),
        pytest.param("INSERT INTO app_ic_cast_t (e) VALUES (1)", "ic_to_e", id="assignment-cast"),
        pytest.param("SELECT '5'::app_ic_t AS t", "ic_t_(in|out)", id="type-output-at-explain"),
        pytest.param("INSERT INTO app_ic_io_t (t) VALUES ('abc')", "ic_t_(in|out)", id="type-input-at-prepare"),
        pytest.param("SELECT '(abc)'::app_ic_pair AS p", "ic_t_(in|out)", id="composite-attribute-input"),
        pytest.param("SELECT c FROM app_ic_ct_t ORDER BY c", "ic_cmp", id="opclass-order-by"),
        pytest.param("SELECT GREATEST(c, c) AS g FROM app_ic_ct_t", "ic_cmp", id="opclass-greatest"),
        pytest.param("SELECT GREATEST('1'::app_ic_ct, '2'::app_ic_ct) AS g", "ic_cmp", id="opclass-folded"),
        pytest.param("SELECT 1::app_ic_rd AS d", "ic_boom", id="domain-cast"),
        pytest.param("SELECT d FROM app_ic_dom_view", "ic_boom", id="domain-cast-in-view"),
        pytest.param("SELECT n FROM app_ic_hidden_dom_view", "ic_boom", id="domain-cast-hidden-in-view"),
        pytest.param("SELECT n FROM app_ic_sorted_view", "ic_cmp", id="opclass-of-a-view-base-table"),
        pytest.param("SELECT '[1,2]'::app_ic_rr AS r", "ic_boom", id="range-over-domain-input"),
        pytest.param("SELECT id FROM app_plan_items WHERE id #~ 1", "ic_sel", id="operator-estimator"),
        pytest.param("SELECT 1::app_ic_dc AS d", "ic_to_e", id="type-in-domain-check"),
        pytest.param("SELECT v FROM app_ic_body_view", "ic_to_e", id="type-in-sql-body"),
        pytest.param("SELECT v FROM app_ic_atomic_view", "ic_to_e", id="type-in-atomic-body"),
        pytest.param("SELECT v FROM app_ic_def_view", "ic_to_e", id="type-in-argument-default"),
        pytest.param("SELECT 1 #<<< 2 AS b", "ic_to_e", id="type-in-operator-function-body"),
        pytest.param("SELECT r::int AS n FROM app_ic_row_t r", "ic_row_to_int", id="row-type-cast"),
        pytest.param("SELECT r.*::int AS n FROM app_ic_row_t r", "ic_row_to_int", id="row-type-star-cast"),
        pytest.param("SELECT pg_catalog.abs(r) AS n FROM app_ic_row_t r", "ic_row_to_int", id="row-type-implicit"),
        pytest.param("SELECT b FROM app_ic_bt ORDER BY b", "ic_cmp", id="binary-coercible-order-by"),
        pytest.param("SELECT b FROM app_ic_bt GROUP BY b", "ic_cmp", id="binary-coercible-group-by"),
        pytest.param("INSERT INTO app_ic_xt_t (x) VALUES ('1')", "ic_xt_eq", id="exclusion-constraint-operator"),
    ],
)
async def test_implicit_call_of_a_type_is_rejected_before_it_runs(
    db_implicit_calls: DbAccess, sql: str, function: str
) -> None:
    """Приведение (явное и присваивания), ввод-вывод типа и атрибута составного типа, сравнение класса операторов
    (ORDER BY, GREATEST), CHECK домена (в том числе в представлении и подтипа диапазона), оценка селективности
    оператора: ни одного имени функции в тексте. Без проверки PREPARE (ввод), EXPLAIN (свёртка, печать констант)
    или выполнение вызвали бы функцию secret и получили бы её ошибку; отказ приходит раньше.

    Типы, которых нет ни в SQL агента, ни в колонках названных отношений (приведение внутри представления, колонка
    базовой таблицы представления), находит чтение определений после PREPARE — по зависимостям правила и колонкам
    заблокированных отношений. Тип, названный в тексте определения (CHECK домена, тело SQL-функции строкой и
    BEGIN ATOMIC, умолчание аргумента, тело функции оператора), — тоже семя: (1)::app_ic_e печатается без имени
    функции, и EXPLAIN свернул бы IMMUTABLE secret.ic_to_e. Ссылка на всю строку вызывает приведение строкового типа
    таблицы; ORDER BY и GROUP BY по типу, неявно двоично приводимому к app_ic_ct, — класс операторов app_ic_ct;
    ограничение-исключение — оператор семейства типа колонки."""
    with pytest.raises(PlanAccessError, match=rf"function 'secret\.{function}'"):
        await db_implicit_calls.sql_driver.execute(sql, readonly=False)


@pytest.mark.asyncio
async def test_type_with_builtin_machinery_passes(db_implicit_calls: DbAccess) -> None:
    """Перечисление public: ввод-вывод и сравнение — встроенные функции pg_catalog."""
    rows = await db_implicit_calls.sql_driver.execute("SELECT e FROM app_ic_ok_t ORDER BY e", readonly=True)
    assert [row.cells["e"] for row in rows] == ["x", "y"]


# Неявное двоично-совместимое приведение встроенного типа к типу public: у json нет классов btree и hash, и класс
# app_ic_vt по умолчанию сравнивает и значения json (ORDER BY j вызывает secret.ic_vt_cmp). Семейство этого класса
# проверяется в любом запросе, поэтому приведение живёт только в своих тестах. Приведение jsonb его не делает:
# у jsonb свои классы btree и hash по умолчанию.
_DROP_BINARY_CAST = """
DROP TABLE IF EXISTS public.app_ic_jt;
DROP TYPE IF EXISTS public.app_ic_vt CASCADE;
"""
_BINARY_CAST = """
CREATE TYPE public.app_ic_vt;
CREATE FUNCTION public.app_ic_vt_in(cstring) RETURNS public.app_ic_vt AS 'textin' LANGUAGE internal IMMUTABLE STRICT;
CREATE FUNCTION public.app_ic_vt_out(public.app_ic_vt) RETURNS cstring AS 'textout' LANGUAGE internal IMMUTABLE STRICT;
CREATE TYPE public.app_ic_vt (INPUT = public.app_ic_vt_in, OUTPUT = public.app_ic_vt_out, LIKE = text);
CREATE FUNCTION secret.ic_vt_cmp(a public.app_ic_vt, b public.app_ic_vt) RETURNS int
    LANGUAGE plpgsql IMMUTABLE AS $$BEGIN RAISE EXCEPTION 'secret.ic_vt_cmp was executed'; END$$;
CREATE FUNCTION public.app_ic_vt_lt(a public.app_ic_vt, b public.app_ic_vt) RETURNS boolean
    LANGUAGE sql IMMUTABLE AS 'SELECT a::text < b::text';
CREATE FUNCTION public.app_ic_vt_eq(a public.app_ic_vt, b public.app_ic_vt) RETURNS boolean
    LANGUAGE sql IMMUTABLE AS 'SELECT a::text = b::text';
CREATE OPERATOR public.#<< (LEFTARG = public.app_ic_vt, RIGHTARG = public.app_ic_vt, FUNCTION = public.app_ic_vt_lt);
CREATE OPERATOR public.#=# (LEFTARG = public.app_ic_vt, RIGHTARG = public.app_ic_vt, FUNCTION = public.app_ic_vt_eq);
CREATE OPERATOR CLASS public.app_ic_vt_ops DEFAULT FOR TYPE public.app_ic_vt USING btree AS
    OPERATOR 1 public.#<<, OPERATOR 3 public.#=#, FUNCTION 1 secret.ic_vt_cmp(public.app_ic_vt, public.app_ic_vt);
CREATE CAST ({source} AS public.app_ic_vt) WITHOUT FUNCTION AS IMPLICIT;
CREATE TABLE public.app_ic_jt (j json, jb jsonb, x int);
INSERT INTO public.app_ic_jt VALUES ('1', '1', 1), ('2', '2', 2);
"""


@pytest.fixture
async def db_binary_cast(
    request: pytest.FixtureRequest, db_plan_check: DbAccess, db_full: DbAccess
) -> AsyncGenerator[DbAccess, None]:
    """db_plan_check, пока встроенный тип request.param неявно двоично приводится к public.app_ic_vt."""
    setup = _BINARY_CAST.replace("{source}", request.param)
    await db_full.sql_driver.execute(_DROP_BINARY_CAST + setup, readonly=False)
    try:
        yield db_plan_check
    finally:
        await db_full.sql_driver.execute(_DROP_BINARY_CAST, readonly=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("db_binary_cast", ["json"], indirect=True)
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT j FROM app_ic_jt ORDER BY j",
        "SELECT DISTINCT j FROM app_ic_jt",
        "SELECT DISTINCT pg_catalog.to_json(x) AS t FROM app_ic_jt",
    ],
)
async def test_binary_coercible_cast_of_a_builtin_type_is_rejected_before_it_runs(
    db_binary_cast: DbAccess, sql: str
) -> None:
    """У json нет классов btree и hash: ORDER BY и DISTINCT берут класс app_ic_vt (GetDefaultOpClass принимает класс
    типа, к которому json неявно двоично приводится) и вызвали бы secret.ic_vt_cmp при выполнении — и для json,
    которого запрос не называет (to_json). Отказ называет приведение."""
    with pytest.raises(
        PlanAccessError, match=r"function 'secret\.ic_vt_cmp'.*implicit binary cast json -> public\.app_ic_vt"
    ):
        await db_binary_cast.sql_driver.execute(sql, readonly=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("db_binary_cast", ["jsonb"], indirect=True)
@pytest.mark.parametrize(
    "sql", ["SELECT 1 AS n", "SELECT jb FROM app_ic_jt ORDER BY jb", "SELECT DISTINCT jb FROM app_ic_jt"]
)
async def test_binary_coercible_cast_of_a_type_with_its_own_classes_passes(db_binary_cast: DbAccess, sql: str) -> None:
    """У jsonb свои классы btree и hash по умолчанию: класс app_ic_vt ему не нужен, приведение ничего не отклоняет."""
    rows = await db_binary_cast.sql_driver.execute(sql, readonly=True)
    assert rows


# Цепочка представление -> SQL-функции public глубины n; самое глубокое тело называет тип app_ic_okt с классом
# операторов над функциями public на языке sql. Тип в теле — семя машинерии, а не уровень вложенности: глубина та же,
# что без него (до пяти — проходит, шесть — непроверяемо).
_DROP_TYPED_CHAINS = """
DROP VIEW IF EXISTS public.app_ic_chain3_view, public.app_ic_chain5_view, public.app_ic_chain6_view;
DROP FUNCTION IF EXISTS public.app_ic_chain3_1(), public.app_ic_chain3_2(), public.app_ic_chain3_3(),
    public.app_ic_chain5_1(), public.app_ic_chain5_2(), public.app_ic_chain5_3(), public.app_ic_chain5_4(),
    public.app_ic_chain5_5(), public.app_ic_chain6_1(), public.app_ic_chain6_2(), public.app_ic_chain6_3(),
    public.app_ic_chain6_4(), public.app_ic_chain6_5(), public.app_ic_chain6_6();
DROP TYPE IF EXISTS public.app_ic_okt CASCADE;
"""
_TYPED_CHAIN_TYPE = """
CREATE TYPE public.app_ic_okt;
CREATE FUNCTION public.app_ic_okt_in(cstring) RETURNS public.app_ic_okt AS 'int4in' LANGUAGE internal IMMUTABLE STRICT;
CREATE FUNCTION public.app_ic_okt_out(public.app_ic_okt) RETURNS cstring
    AS 'int4out' LANGUAGE internal IMMUTABLE STRICT;
CREATE TYPE public.app_ic_okt (INPUT = public.app_ic_okt_in, OUTPUT = public.app_ic_okt_out, LIKE = int4);
CREATE CAST (public.app_ic_okt AS int4) WITHOUT FUNCTION;
CREATE FUNCTION public.app_ic_okt_lt(a public.app_ic_okt, b public.app_ic_okt) RETURNS boolean
    LANGUAGE sql IMMUTABLE AS 'SELECT a::int4 < b::int4';
CREATE FUNCTION public.app_ic_okt_eq(a public.app_ic_okt, b public.app_ic_okt) RETURNS boolean
    LANGUAGE sql IMMUTABLE AS 'SELECT a::int4 = b::int4';
CREATE FUNCTION public.app_ic_okt_cmp(a public.app_ic_okt, b public.app_ic_okt) RETURNS int LANGUAGE sql IMMUTABLE
    AS 'SELECT CASE WHEN a::int4 < b::int4 THEN -1 WHEN a::int4 > b::int4 THEN 1 ELSE 0 END';
CREATE OPERATOR public.#~< (LEFTARG = public.app_ic_okt, RIGHTARG = public.app_ic_okt, FUNCTION = public.app_ic_okt_lt);
CREATE OPERATOR public.#~= (LEFTARG = public.app_ic_okt, RIGHTARG = public.app_ic_okt, FUNCTION = public.app_ic_okt_eq);
CREATE OPERATOR CLASS public.app_ic_okt_ops DEFAULT FOR TYPE public.app_ic_okt USING btree AS
    OPERATOR 1 public.#~<, OPERATOR 3 public.#~=, FUNCTION 1 public.app_ic_okt_cmp(public.app_ic_okt, public.app_ic_okt);
"""


def _typed_chain(depth: int) -> str:
    """Представление app_ic_chain<depth>_view -> app_ic_chain<depth>_1() -> ... -> _<depth>() с типом в теле."""
    prefix = f"public.app_ic_chain{depth}"
    statements = [
        f"CREATE FUNCTION {prefix}_{depth}() RETURNS int LANGUAGE sql IMMUTABLE "
        "AS $$SELECT CASE WHEN '1'::public.app_ic_okt IS NULL THEN 1 ELSE 2 END$$;"
    ]
    statements += [
        f"CREATE FUNCTION {prefix}_{level}() RETURNS int LANGUAGE sql IMMUTABLE AS $$SELECT {prefix}_{level + 1}()$$;"
        for level in range(depth - 1, 0, -1)
    ]
    statements.append(f"CREATE VIEW {prefix}_view AS SELECT {prefix}_1() AS v;")
    return "\n".join(statements)


@pytest.fixture
async def db_typed_chains(db_plan_check_non_sql: DbAccess, db_full: DbAccess) -> AsyncGenerator[DbAccess, None]:
    """db_plan_check_non_sql, пока в public есть цепочки SQL-функций глубины 3, 5 и 6 с типом app_ic_okt в самом
    глубоком теле. Ввод-вывод app_ic_okt — LANGUAGE internal: без plan_check_allow_non_sql_functions тип отклонялся бы."""
    setup = _TYPED_CHAIN_TYPE + "\n".join(_typed_chain(depth) for depth in (3, 5, 6))
    await db_full.sql_driver.execute(_DROP_TYPED_CHAINS + setup, readonly=False)
    try:
        yield db_plan_check_non_sql
    finally:
        await db_full.sql_driver.execute(_DROP_TYPED_CHAINS, readonly=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("depth", [3, 5])
async def test_type_in_the_deepest_body_does_not_cost_depth(db_typed_chains: DbAccess, depth: int) -> None:
    """Семя типа и тела функций его класса операторов идут кругами, которые глубину определений не тратят."""
    rows = await db_typed_chains.sql_driver.execute(f"SELECT v FROM app_ic_chain{depth}_view", readonly=True)
    assert [row.cells["v"] for row in rows] == [2]


@pytest.mark.asyncio
async def test_type_with_non_sql_io_functions_is_rejected_by_default(
    db_typed_chains: DbAccess, db_plan_check: DbAccess
) -> None:
    """По умолчанию ввод-вывод app_ic_okt (обёртки LANGUAGE internal в public) — функции не на sql: отказ."""
    with pytest.raises(PlanAccessError, match=r"function 'public\.app_ic_okt_(in|out)'.*LANGUAGE internal"):
        await db_plan_check.sql_driver.execute("SELECT v FROM app_ic_chain3_view", readonly=True)


@pytest.mark.asyncio
async def test_chain_deeper_than_the_budget_stays_unverifiable(db_typed_chains: DbAccess) -> None:
    with pytest.raises(PlanUnverifiableError, match="definitions of views"):
        await db_typed_chains.sql_driver.execute("SELECT v FROM app_ic_chain6_view", readonly=True)


# Расширение citext в схеме вне public (как extensions у Supabase): его машинерию поставил скрипт расширения,
# ей доверяют по владельцу (тип, семейство операторов, приведение). Приведение, созданное DBA поверх функции
# расширения, — не член расширения и проверяется по обычным правилам.
_DROP_EXTENSION_TYPE = """
DROP TABLE IF EXISTS public.app_ic_cit_t;
DROP EXTENSION IF EXISTS citext CASCADE;
DROP SCHEMA IF EXISTS ic_ext CASCADE;
"""
_EXTENSION_TYPE = """
CREATE SCHEMA ic_ext;
CREATE EXTENSION citext SCHEMA ic_ext;
CREATE TABLE public.app_ic_cit_t (id int, c ic_ext.citext);
INSERT INTO public.app_ic_cit_t VALUES (1, 'b'), (2, 'A');
"""
_DBA_CAST_OVER_AN_EXTENSION_FUNCTION = """
CREATE CAST (cidr AS ic_ext.citext) WITH FUNCTION ic_ext.citext(inet) AS ASSIGNMENT;
"""


@pytest.fixture
async def db_extension_type(db_plan_check: DbAccess, db_full: DbAccess) -> AsyncGenerator[DbAccess, None]:
    """db_plan_check, пока в public есть таблица с колонкой ic_ext.citext."""
    await db_full.sql_driver.execute(_DROP_EXTENSION_TYPE + _EXTENSION_TYPE, readonly=False)
    try:
        yield db_plan_check
    finally:
        await db_full.sql_driver.execute(_DROP_EXTENSION_TYPE, readonly=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id, c FROM app_ic_cit_t ORDER BY c",
        "SELECT c FROM app_ic_cit_t GROUP BY c ORDER BY c",
        "SELECT id FROM app_ic_cit_t WHERE c = 'A'",
    ],
)
async def test_machinery_of_an_extension_type_outside_public_passes(db_extension_type: DbAccess, sql: str) -> None:
    """Ввод-вывод, сравнение и хеш citext — функции ic_ext, члены расширения: доверены по типу и семейству."""
    rows = await db_extension_type.sql_driver.execute(sql, readonly=True)
    assert rows


@pytest.mark.asyncio
async def test_dba_cast_over_an_extension_function_is_checked(db_extension_type: DbAccess, db_full: DbAccess) -> None:
    """Функция приведения следует обычному правилу basic: членство функции в расширении не в счёт, если сама строка
    pg_cast создана DBA, — ic_ext.citext(inet) вне public отклоняется."""
    await db_full.sql_driver.execute(_DBA_CAST_OVER_AN_EXTENSION_FUNCTION, readonly=False)

    with pytest.raises(PlanAccessError, match=r"function 'ic_ext\.citext'"):
        await db_extension_type.sql_driver.execute("SELECT id, c FROM app_ic_cit_t ORDER BY c", readonly=True)


# Функции public не на языке sql (PL/pgSQL, internal): тело не проверить, по умолчанию — отказ. Бросающие функции
# доказывают «отклонено до выполнения» (EXPLAIN свернул бы IMMUTABLE-вызов, INSERT выполнил бы триггер), чтение
# secret.accounts — «отклонено, хотя без проверки запрос отдал бы секрет». Агрегат (prolang internal) и функция
# расширения (moddatetime, C) не в счёт.
_DROP_NON_SQL = """
DROP VIEW IF EXISTS public.app_ns_boom_view, public.app_ns_leak_view, public.app_ns_setting_view,
    public.app_ns_supported_view, public.app_ns_agg_view;
DROP TABLE IF EXISTS public.app_ns_notes, public.app_ns_trg, public.app_ns_stamped;
DROP OPERATOR IF EXISTS public.#!# (int, int);
DROP AGGREGATE IF EXISTS public.app_ns_agg(text);
DROP TYPE IF EXISTS public.app_ns_e CASCADE;
DROP FUNCTION IF EXISTS public.app_ns_boom(), public.app_ns_leak(), public.app_ns_fill_note(), public.app_ns_trg_boom(),
    public.app_ns_setting(text), public.app_ns_opf(int, int), public.app_ns_supported(int), public.app_ns_cat(text, text),
    secret.ns_support(internal);
DROP EXTENSION IF EXISTS moddatetime;
"""
_NON_SQL = """
CREATE FUNCTION public.app_ns_boom() RETURNS int
    LANGUAGE plpgsql IMMUTABLE AS $$BEGIN RAISE EXCEPTION 'public.app_ns_boom was executed'; END$$;
CREATE VIEW public.app_ns_boom_view AS SELECT public.app_ns_boom() AS b;
CREATE FUNCTION public.app_ns_leak() RETURNS text
    LANGUAGE plpgsql STABLE AS $$BEGIN RETURN (SELECT token FROM secret.accounts LIMIT 1); END$$;
CREATE VIEW public.app_ns_leak_view AS SELECT public.app_ns_leak() AS t;
CREATE TABLE public.app_ns_notes (id int, note text);
CREATE FUNCTION public.app_ns_fill_note() RETURNS trigger LANGUAGE plpgsql
    AS $$BEGIN NEW.note := (SELECT token FROM secret.accounts LIMIT 1); RETURN NEW; END$$;
CREATE TRIGGER app_ns_notes_fill BEFORE INSERT ON public.app_ns_notes
    FOR EACH ROW EXECUTE FUNCTION public.app_ns_fill_note();
CREATE TABLE public.app_ns_trg (id int);
CREATE FUNCTION public.app_ns_trg_boom() RETURNS trigger
    LANGUAGE plpgsql AS $$BEGIN RAISE EXCEPTION 'public.app_ns_trg_boom was executed'; END$$;
CREATE TRIGGER app_ns_trg_boom BEFORE INSERT ON public.app_ns_trg
    FOR EACH ROW EXECUTE FUNCTION public.app_ns_trg_boom();
CREATE FUNCTION public.app_ns_setting(text) RETURNS text LANGUAGE internal STABLE STRICT AS 'show_config_by_name';
CREATE VIEW public.app_ns_setting_view AS SELECT public.app_ns_setting('data_directory') AS s;
CREATE FUNCTION public.app_ns_opf(a int, b int) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE AS $$BEGIN RAISE EXCEPTION 'public.app_ns_opf was executed'; END$$;
CREATE OPERATOR public.#!# (LEFTARG = int, RIGHTARG = int, FUNCTION = public.app_ns_opf);
CREATE TYPE public.app_ns_e AS ENUM ('a');
CREATE FUNCTION public.app_ns_to_e(n int) RETURNS public.app_ns_e
    LANGUAGE plpgsql IMMUTABLE AS $$BEGIN RAISE EXCEPTION 'public.app_ns_to_e was executed'; END$$;
CREATE CAST (int AS public.app_ns_e) WITH FUNCTION public.app_ns_to_e(int);
CREATE FUNCTION secret.ns_support(internal) RETURNS internal LANGUAGE internal AS 'textlike_support';
CREATE FUNCTION public.app_ns_supported(a int) RETURNS int
    LANGUAGE sql IMMUTABLE SUPPORT secret.ns_support AS 'SELECT a';
CREATE VIEW public.app_ns_supported_view AS SELECT public.app_ns_supported(id) AS v FROM public.app_plan_items;
CREATE FUNCTION public.app_ns_cat(s text, v text) RETURNS text LANGUAGE sql IMMUTABLE AS $$SELECT coalesce(s, '') || v$$;
CREATE AGGREGATE public.app_ns_agg(text) (SFUNC = public.app_ns_cat, STYPE = text);
CREATE VIEW public.app_ns_agg_view AS SELECT public.app_ns_agg(id::text) AS c FROM public.app_plan_items;
CREATE EXTENSION moddatetime;
CREATE TABLE public.app_ns_stamped (id int, updated_at timestamp);
CREATE TRIGGER app_ns_stamped_mod BEFORE UPDATE ON public.app_ns_stamped
    FOR EACH ROW EXECUTE FUNCTION moddatetime(updated_at);
INSERT INTO public.app_ns_stamped VALUES (1, NULL);
"""


@pytest.fixture
async def db_non_sql(db_plan_check: DbAccess, db_full: DbAccess) -> AsyncGenerator[DbAccess, None]:
    """db_plan_check, пока в public есть функции не на sql: бросающие, читающие secret.accounts, обёртка internal."""
    await db_full.sql_driver.execute(_DROP_NON_SQL + _NON_SQL, readonly=False)
    try:
        yield db_plan_check
    finally:
        await db_full.sql_driver.execute(_DROP_NON_SQL, readonly=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sql", "function", "language"),
    [
        pytest.param("SELECT b FROM app_ns_boom_view", "app_ns_boom", "plpgsql", id="folded-in-a-view"),
        pytest.param("INSERT INTO app_ns_trg (id) VALUES (1)", "app_ns_trg_boom", "plpgsql", id="trigger-function"),
        pytest.param("SELECT 1 #!# 2 AS b", "app_ns_opf", "plpgsql", id="operator-function"),
        pytest.param("SELECT 1::app_ns_e AS e", "app_ns_to_e", "plpgsql", id="cast-function"),
        pytest.param("SELECT t FROM app_ns_leak_view", "app_ns_leak", "plpgsql", id="secret-read-in-a-view"),
        pytest.param(
            "INSERT INTO app_ns_notes (id) VALUES (1) RETURNING note",
            "app_ns_fill_note",
            "plpgsql",
            id="secret-read-in-a-trigger",
        ),
        pytest.param("SELECT s FROM app_ns_setting_view", "app_ns_setting", "internal", id="internal-builtin-wrapper"),
    ],
)
async def test_non_sql_public_function_is_rejected_before_it_runs(
    db_non_sql: DbAccess, sql: str, function: str, language: str
) -> None:
    """Функция public не на sql, до которой доходит запрос (представление, триггер цели DML, оператор, приведение):
    без проверки EXPLAIN или выполнение вызвали бы её — бросающая дала бы своё исключение, читающая secret.accounts
    отдала бы секрет, обёртка internal над show_config_by_name — настройку сервера в обход списка basic."""
    with pytest.raises(PlanAccessError, match=rf"function 'public\.{function}'.*LANGUAGE {language}"):
        await db_non_sql.sql_driver.execute(sql, readonly=False)


@pytest.mark.asyncio
async def test_planner_support_function_of_a_public_function_is_checked(db_non_sql: DbAccess) -> None:
    """SUPPORT secret.ns_support у SQL-функции public: планировщик вызвал бы её для каждого вызова функции."""
    with pytest.raises(PlanAccessError, match=r"function 'secret\.ns_support'"):
        await db_non_sql.sql_driver.execute("SELECT v FROM app_ns_supported_view", readonly=True)


@pytest.mark.asyncio
async def test_aggregates_and_extension_trigger_functions_pass(db_non_sql: DbAccess) -> None:
    """Агрегат public (prolang internal) проверяется по опорным функциям; moddatetime — функция расширения (C)."""
    aggregated = await db_non_sql.sql_driver.execute("SELECT c FROM app_ns_agg_view", readonly=True)
    updated = await db_non_sql.sql_driver.execute(
        "UPDATE app_ns_stamped SET id = 2 WHERE id = 1 RETURNING updated_at", readonly=False
    )
    assert aggregated[0].cells["c"]
    assert updated[0].cells["updated_at"] is not None


@pytest.mark.asyncio
async def test_non_sql_functions_run_unchecked_when_allowed(
    db_non_sql: DbAccess, db_plan_check_non_sql: DbAccess
) -> None:
    """plan_check_allow_non_sql_functions=true — прежнее поведение и его цена: тело PL/pgSQL читает secret.accounts."""
    rows = await db_plan_check_non_sql.sql_driver.execute("SELECT t FROM app_ns_leak_view", readonly=True)
    assert rows[0].cells["t"] == "top-secret"
