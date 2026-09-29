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
async def test_public_operator_over_a_public_function_passes(db_plan_check: DbAccess) -> None:
    rows = await db_plan_check.sql_driver.execute("SELECT (id <~> 2) AS near FROM app_plan_items", readonly=True)
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
