"""Тесты PlanGuard: проверка по плану запроса в basic (спека basic-followups §4)."""

import json
from typing import Any

import pytest

from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.postgres.security.plan_guard import PlanGuard
from postgres_fastmcp.shared.errors import PlanAccessError, PlanUnverifiableError


_EXPLAIN = "EXPLAIN (VERBOSE, FORMAT JSON) "
_SELECT = "SELECT * FROM app_v"
_RESULT: dict[str, Any] = {"Node Type": "Result"}


def _scan(schema: str | None, relation: str) -> dict[str, Any]:
    node: dict[str, Any] = {"Node Type": "Seq Scan", "Relation Name": relation, "Alias": relation}
    if schema is not None:
        node["Schema"] = schema
    return node


def _function_scan(schema: str, function: str) -> dict[str, Any]:
    return {"Node Type": "Function Scan", "Function Name": function, "Schema": schema, "Alias": function}


class _Explain:
    """Исполнитель EXPLAIN в миниатюре: заготовленный план по тексту EXPLAIN, журнал отправленного."""

    def __init__(self, plans: dict[str, dict[str, Any]] | None = None, *, as_text: bool = False) -> None:
        self._plans = plans or {}
        self._as_text = as_text
        self.sent: list[str] = []

    async def __call__(self, sql: str) -> list[RowResult] | None:
        self.sent.append(sql)
        document: Any = [{"Plan": self._plans.get(sql, _RESULT)}]
        return [RowResult(cells={"QUERY PLAN": json.dumps(document) if self._as_text else document})]


def _guard(explain: _Explain, table_prefix: str | None = None) -> PlanGuard:
    return PlanGuard(explain, allowed_schema="public", table_prefix=table_prefix)


async def test_view_over_a_foreign_schema_is_rejected() -> None:
    explain = _Explain({_EXPLAIN + "SELECT * FROM app_secret_view": _scan("secret", "accounts")})

    with pytest.raises(PlanAccessError, match=r"relation 'secret\.accounts'") as exc_info:
        await _guard(explain).check("/* tag */ SELECT * FROM app_secret_view")

    assert explain.sent == [_EXPLAIN + "SELECT * FROM app_secret_view"]
    assert (exc_info.value.kind, exc_info.value.qualified_name) == ("relation", "secret.accounts")


async def test_prefixed_public_table_passes_case_insensitively() -> None:
    explain = _Explain({_EXPLAIN + 'SELECT * FROM "APP_Orders"': _scan("public", "APP_Orders")})

    await _guard(explain, table_prefix="app_").check('SELECT * FROM "APP_Orders"')


async def test_public_table_without_prefix_is_rejected() -> None:
    explain = _Explain({_EXPLAIN + "SELECT * FROM app_users_view": _scan("public", "users")})

    with pytest.raises(PlanAccessError, match=r"relation 'public\.users'"):
        await _guard(explain, table_prefix="app_").check("SELECT * FROM app_users_view")


@pytest.mark.parametrize("relation", ["pg_stat_statements", "_pg_user_mappings", "hypopg_list_indexes"])
async def test_system_relation_in_public_is_rejected(relation: str) -> None:
    explain = _Explain({_EXPLAIN + "SELECT * FROM v": _scan("public", relation)})

    with pytest.raises(PlanAccessError, match=rf"public\.{relation}"):
        await _guard(explain).check("SELECT * FROM v")


async def test_relation_without_schema_is_rejected() -> None:
    explain = _Explain({_EXPLAIN + "SELECT * FROM v": _scan(None, "t")})

    with pytest.raises(PlanAccessError, match=r"relation '\?\.t'"):
        await _guard(explain).check("SELECT * FROM v")


async def test_function_of_a_foreign_schema_is_rejected() -> None:
    explain = _Explain({_EXPLAIN + "SELECT * FROM v": _function_scan("secret", "f")})

    with pytest.raises(PlanAccessError, match=r"function 'secret\.f'"):
        await _guard(explain).check("SELECT * FROM v")


@pytest.mark.parametrize("schema", ["pg_catalog", "public"])
async def test_builtin_and_public_functions_pass(schema: str) -> None:
    explain = _Explain({_EXPLAIN + "SELECT * FROM now()": _function_scan(schema, "now")})

    await _guard(explain).check("SELECT * FROM now()")


@pytest.mark.parametrize("relationship", ["InitPlan", "SubPlan", "Outer"])
async def test_nested_plans_are_checked(relationship: str) -> None:
    plan = {
        "Node Type": "Result",
        "Plans": [
            {
                "Node Type": "Aggregate",
                "Parent Relationship": relationship,
                "Plans": [_scan("public", "app_t"), _scan("secret", "t")],
            }
        ],
    }
    explain = _Explain({_EXPLAIN + "SELECT (SELECT count(*) FROM v)": plan})

    with pytest.raises(PlanAccessError, match=r"secret\.t"):
        await _guard(explain).check("SELECT (SELECT count(*) FROM v)")


async def test_target_tables_of_modify_table_are_checked() -> None:
    plan = {"Node Type": "ModifyTable", "Target Tables": [{"Relation Name": "t", "Schema": "secret"}]}
    explain = _Explain({_EXPLAIN + "UPDATE app_v SET a = 1": plan})

    with pytest.raises(PlanAccessError, match=r"secret\.t"):
        await _guard(explain).check("UPDATE app_v SET a = 1")


async def test_plan_as_json_text_is_parsed() -> None:
    explain = _Explain({_EXPLAIN + "SELECT * FROM v": _scan("secret", "t")}, as_text=True)

    with pytest.raises(PlanAccessError):
        await _guard(explain).check("SELECT * FROM v")


async def test_explain_generic_plan_is_carried_over() -> None:
    explain = _Explain()

    await _guard(explain).check("EXPLAIN (FORMAT JSON, GENERIC_PLAN) SELECT * FROM app_t WHERE id = $1")

    assert explain.sent == ["EXPLAIN (VERBOSE, FORMAT JSON, GENERIC_PLAN) SELECT * FROM app_t WHERE id = $1"]


@pytest.mark.parametrize("option", ["generic_plan false", "generic_plan 0", "generic_plan off"])
async def test_disabled_generic_plan_is_not_carried_over(option: str) -> None:
    explain = _Explain()

    await _guard(explain).check(f"EXPLAIN ({option}) SELECT 1")

    assert explain.sent == [_EXPLAIN + "SELECT 1"]


async def test_cursor_query_is_explained() -> None:
    explain = _Explain()

    await _guard(explain).check("DECLARE c CURSOR FOR SELECT * FROM app_t")

    assert explain.sent == [_EXPLAIN + "SELECT * FROM app_t"]


@pytest.mark.parametrize(
    "sql",
    [
        "SHOW search_path",
        "PREPARE p AS SELECT * FROM app_t",
        "DEALLOCATE p",
        "FETCH NEXT FROM c",
        "CLOSE c",
        "CREATE EXTENSION IF NOT EXISTS hypopg",
    ],
)
async def test_statements_without_a_plan_send_no_explain(sql: str) -> None:
    explain = _Explain()

    await _guard(explain).check(sql)

    assert explain.sent == []


async def test_every_plannable_statement_is_explained() -> None:
    explain = _Explain()

    await _guard(explain).check(
        "INSERT INTO app_t (id) VALUES (1) RETURNING id; UPDATE app_t SET v = 2 WHERE id = 1; "
        "DELETE FROM app_t WHERE id = 1; SHOW search_path; SELECT 1"
    )

    assert explain.sent == [
        _EXPLAIN + "INSERT INTO app_t (id) VALUES (1) RETURNING id",
        _EXPLAIN + "UPDATE app_t SET v = 2 WHERE id = 1",
        _EXPLAIN + "DELETE FROM app_t WHERE id = 1",
        _EXPLAIN + "SELECT 1",
    ]


class _Rows:
    """Исполнитель EXPLAIN, который возвращает заданные строки как есть."""

    def __init__(self, rows: list[RowResult] | None) -> None:
        self._rows = rows

    async def __call__(self, sql: str) -> list[RowResult] | None:
        return self._rows


@pytest.mark.parametrize(
    ("node_type", "extra"),
    [
        ("Foreign Scan", {"Relations": "(secret.ft_a) INNER JOIN (secret.ft_b)"}),
        ("Custom Scan", {"Custom Plan Provider": "x"}),
    ],
)
async def test_scan_without_a_relation_name_is_rejected(node_type: str, extra: dict[str, Any]) -> None:
    explain = _Explain({_EXPLAIN + _SELECT: {"Node Type": node_type, **extra}})

    with pytest.raises(PlanUnverifiableError, match=rf"a {node_type} whose relations cannot be verified") as exc_info:
        await _guard(explain).check(_SELECT)

    assert "secret" not in str(exc_info.value)


async def test_foreign_scan_with_a_relation_name_is_checked_as_a_relation() -> None:
    plan = {"Node Type": "Foreign Scan", "Relation Name": "ft", "Schema": "secret"}
    explain = _Explain({_EXPLAIN + _SELECT: plan})

    with pytest.raises(PlanAccessError, match=r"relation 'secret\.ft'"):
        await _guard(explain).check(_SELECT)


async def test_function_scan_without_a_function_name_is_rejected() -> None:
    explain = _Explain({_EXPLAIN + _SELECT: {"Node Type": "Function Scan", "Alias": "f"}})

    with pytest.raises(PlanUnverifiableError, match="a Function Scan whose functions cannot be verified"):
        await _guard(explain).check(_SELECT)


@pytest.mark.parametrize(
    "function",
    [
        "pg_show_all_settings",
        "pg_show_all_file_settings",
        "pg_hba_file_rules",
        "pg_prepared_statement",
        "pg_cursor",
        "pg_available_extensions",
        "pg_config",
        "pg_ls_dir",
        "pg_stat_get_activity",
        "PG_SHOW_ALL_SETTINGS",
    ],
)
async def test_pg_catalog_function_outside_the_basic_allowlist_is_rejected(function: str) -> None:
    explain = _Explain({_EXPLAIN + _SELECT: _function_scan("pg_catalog", function)})

    with pytest.raises(PlanAccessError, match=rf"function 'pg_catalog\.{function}'") as exc_info:
        await _guard(explain).check(_SELECT)

    assert exc_info.value.kind == "function"


@pytest.mark.parametrize("function", ["generate_series", "unnest", "jsonb_each", "Generate_Series"])
async def test_pg_catalog_function_in_the_basic_allowlist_passes(function: str) -> None:
    explain = _Explain({_EXPLAIN + _SELECT: _function_scan("pg_catalog", function)})

    await _guard(explain).check(_SELECT)


def _rows_from(call: str) -> dict[str, Any]:
    """Function Scan для ROWS FROM из нескольких функций: без Function Name, с Function Call (VERBOSE)."""
    return {"Node Type": "Function Scan", "Alias": "f", "Function Call": call}


@pytest.mark.parametrize(
    "call",
    [
        "unnest('{1,2}'::integer[]), unnest('{a,b}'::text[])",
        "generate_series(1, 3), unnest(ARRAY['a'::text, 'b'::text])",
        "pg_catalog.unnest('{1,2}'::integer[]), public.app_f()",
        "unnest('{1,2}'::integer[])",
    ],
)
async def test_multi_function_scan_with_verifiable_calls_passes(call: str) -> None:
    explain = _Explain({_EXPLAIN + _SELECT: _rows_from(call)})

    await _guard(explain).check(_SELECT)


@pytest.mark.parametrize(
    ("call", "name"),
    [
        ("secret.f(), generate_series(1, 1)", "secret.f"),
        ("pg_catalog.pg_show_all_settings(), generate_series(1, 1)", "pg_catalog.pg_show_all_settings"),
        ("generate_series(1, 1), pg_show_all_settings()", "pg_show_all_settings"),
        ("unnest(secret.g()), generate_series(1, 1)", "secret.g"),
        ("app_f(), generate_series(1, 1)", "app_f"),
    ],
)
async def test_multi_function_scan_with_a_forbidden_call_is_rejected(call: str, name: str) -> None:
    explain = _Explain({_EXPLAIN + _SELECT: _rows_from(call)})

    with pytest.raises(PlanAccessError) as exc_info:
        await _guard(explain).check(_SELECT)

    assert (exc_info.value.kind, exc_info.value.qualified_name) == ("function", name)


@pytest.mark.parametrize("call", ["", "unnest(", "not a (call", 42])
async def test_multi_function_scan_with_an_unparsable_call_is_rejected(call: object) -> None:
    explain = _Explain({_EXPLAIN + _SELECT: {"Node Type": "Function Scan", "Alias": "f", "Function Call": call}})

    with pytest.raises(PlanUnverifiableError, match="a Function Scan whose functions cannot be verified"):
        await _guard(explain).check(_SELECT)


@pytest.mark.parametrize(
    "rows",
    [
        None,
        [],
        [RowResult(cells={})],
        [RowResult(cells={"QUERY PLAN": 42})],
        [RowResult(cells={"QUERY PLAN": None})],
        [RowResult(cells={"QUERY PLAN": []})],
        [RowResult(cells={"QUERY PLAN": [{"Planning": {}}]})],
        [RowResult(cells={"QUERY PLAN": '"text"'})],
        [RowResult(cells={"QUERY PLAN": "not json"})],
    ],
)
async def test_missing_or_malformed_plan_is_rejected(rows: list[RowResult] | None) -> None:
    guard = PlanGuard(_Rows(rows), allowed_schema="public", table_prefix=None)

    with pytest.raises(PlanUnverifiableError, match="EXPLAIN returned no plan"):
        await guard.check(_SELECT)


@pytest.mark.parametrize(
    "sql",
    [
        "WITH ins AS (INSERT INTO app_t (id) VALUES (1) RETURNING id) SELECT id FROM ins",
        "WITH src AS (SELECT 1 AS id) INSERT INTO app_t (id) SELECT id FROM src RETURNING id",
    ],
)
async def test_data_modifying_cte_is_explained(sql: str) -> None:
    explain = _Explain()

    await _guard(explain).check(sql)

    assert explain.sent == [_EXPLAIN + sql]


@pytest.mark.parametrize(
    ("table_prefix", "hint"),
    [(None, "Only tables in 'public' are permitted."), ("app_", "Only tables in 'public' starting with 'app_'")],
)
async def test_relation_hint_names_the_schema_and_prefix(table_prefix: str | None, hint: str) -> None:
    explain = _Explain({_EXPLAIN + _SELECT: _scan("secret", "t")})

    with pytest.raises(PlanAccessError) as exc_info:
        await _guard(explain, table_prefix=table_prefix).check(_SELECT)

    assert hint in str(exc_info.value)


async def test_function_hint_names_the_allowed_schemas() -> None:
    explain = _Explain({_EXPLAIN + _SELECT: _function_scan("secret", "f")})

    with pytest.raises(PlanAccessError) as exc_info:
        await _guard(explain).check(_SELECT)

    assert "Only functions from 'public' or built-in functions allowed in basic mode are permitted." in str(
        exc_info.value
    )
