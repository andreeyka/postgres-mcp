"""Тесты PlanGuard: проверка по плану запроса в basic (спека basic-followups §4)."""

import json
from typing import Any

import pytest

from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.postgres.security.plan_guard import PlanGuard
from postgres_fastmcp.shared.errors import PlanAccessError


_EXPLAIN = "EXPLAIN (VERBOSE, FORMAT JSON) "
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
