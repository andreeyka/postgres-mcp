"""Тесты PlanGuard: проверка по плану запроса в basic (спека basic-followups §4)."""

import json
import re
from typing import Any

import pytest
from psycopg.errors import IndeterminateDatatype, UndefinedTable

from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.postgres.security.plan_catalog import BuiltinTypeNames
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


# Типы pg_catalog, которые встречаются в тестовых выражениях.
_BUILTIN_TYPES = frozenset({"text", "json", "regclass", "bpchar", "char", "int4", "int8"})


def _rule_row(
    kind: str,
    schema: str | None = None,
    name: str | None = None,
    parent_schema: str | None = None,
    relation_schema: str | None = None,
    relation_name: str | None = None,
    definition: str | None = None,
) -> dict[str, Any]:
    """Строка запроса правил и зависимостей (plan_catalog.RULE_DEPENDENCIES_SQL)."""
    return {
        "kind": kind,
        "schema": schema,
        "name": name,
        "parent_schema": parent_schema,
        "relation_schema": relation_schema,
        "relation_name": relation_name,
        "definition": definition,
    }


class _Explain:
    """Исполнитель транзакции в миниатюре: план по тексту EXPLAIN, ответы каталога, журнал отправленного.

    Каталог отвечает именами из заготовленных множеств, которые встречаются в запросе литералом.
    """

    def __init__(
        self,
        plans: dict[str, dict[str, Any]] | None = None,
        *,
        as_text: bool = False,
        pg_catalog_functions: frozenset[str] = frozenset(),
        row_types: frozenset[str] | dict[str, tuple[str, str]] = frozenset(),
        rules: list[dict[str, Any]] | None = None,
        prepare_errors: dict[str, Exception] | None = None,
        implementations: list[dict[str, Any]] | None = None,
    ) -> None:
        self._plans = plans or {}
        self._rules = rules or []
        self.rule_queries: list[str] = []
        self.log: list[str] = []
        self._as_text = as_text
        self._pg_catalog_functions = pg_catalog_functions
        # Строковый тип -> (схема, имя) его отношения; множество — типы таблиц public с тем же именем.
        self._row_types = row_types if isinstance(row_types, dict) else {name: ("public", name) for name in row_types}
        self.sent: list[str] = []
        # Фрагмент текста PREPARE -> ошибка, которую Postgres вернул бы на эту команду.
        self._prepare_errors = prepare_errors or {}
        # PREPARE ... ; DEALLOCATE ..., SAVEPOINT ... и ROLLBACK TO SAVEPOINT ... — в sent не попадают.
        self.prepared: list[str] = []
        self._implementations = implementations or []
        self.implementation_queries: list[str] = []

    async def __call__(self, sql: str) -> list[RowResult] | None:  # noqa: PLR0911
        self.log.append(sql)
        if "pg_catalog.pg_rewrite" in sql:
            self.rule_queries.append(sql)
            return [RowResult(cells=_rule_row(**row)) for row in self._rules]
        if "pg_catalog.pg_aggregate" in sql:
            self.implementation_queries.append(sql)
            return [RowResult(cells=_rule_row(**row)) for row in self._implementations]
        if sql.startswith(("PREPARE ", "SAVEPOINT ", "ROLLBACK TO SAVEPOINT ")):
            self.prepared.append(sql)
            if not sql.startswith("ROLLBACK"):
                for fragment, error in self._prepare_errors.items():
                    if fragment in sql:
                        raise error
            return None
        self.sent.append(sql)
        if "pg_catalog.pg_proc" in sql:
            return self._catalog(sql, self._pg_catalog_functions)
        if "typrelid" in sql:
            return [
                RowResult(cells={"name": name, "relation_schema": schema, "relation_name": relation})
                for name, (schema, relation) in sorted(self._row_types.items())
                if f"'{name}'" in sql
            ]
        if "pg_catalog.pg_type" in sql:
            return [RowResult(cells={"name": name}) for name in sorted(_BUILTIN_TYPES)]
        document: Any = [{"Plan": self._plans.get(sql, _RESULT)}]
        return [RowResult(cells={"QUERY PLAN": json.dumps(document) if self._as_text else document})]

    @staticmethod
    def _catalog(sql: str, names: frozenset[str]) -> list[RowResult]:
        return [RowResult(cells={"name": name}) for name in sorted(names) if f"'{name}'" in sql]

    def catalog_queries(self) -> list[str]:
        return [sql for sql in self.sent if not sql.startswith("EXPLAIN")]


def _guard(
    explain: _Explain, table_prefix: str | None = None, builtin_types: BuiltinTypeNames | None = None
) -> PlanGuard:
    return PlanGuard(explain, allowed_schema="public", table_prefix=table_prefix, builtin_types=builtin_types)


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
    """Исполнитель EXPLAIN, который возвращает заданные строки как есть; правил у представлений нет."""

    def __init__(self, rows: list[RowResult] | None) -> None:
        self._rows = rows

    async def __call__(self, sql: str) -> list[RowResult] | None:
        if "pg_catalog.pg_rewrite" in sql:
            return []
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


def _single_scan(schema: str, function: str, call: str) -> dict[str, Any]:
    """Function Scan одиночной функции с Function Call (VERBOSE): аргументы видны только в нём."""
    return {**_function_scan(schema, function), "Function Call": call}


@pytest.mark.parametrize(
    ("schema", "function", "call", "name"),
    [
        ("pg_catalog", "unnest", "unnest(secret.get_secrets())", "secret.get_secrets"),
        ("pg_catalog", "unnest", "unnest(pg_ls_dir('.'::text))", "pg_ls_dir"),
        ("pg_catalog", "generate_series", "generate_series(1, secret.f())", "secret.f"),
        ("public", "my_srf", "my_srf(my_helper(1))", "my_helper"),
    ],
)
async def test_nested_call_of_a_single_function_scan_is_checked(
    schema: str, function: str, call: str, name: str
) -> None:
    explain = _Explain({_EXPLAIN + _SELECT: _single_scan(schema, function, call)})

    with pytest.raises(PlanAccessError) as exc_info:
        await _guard(explain).check(_SELECT)

    assert (exc_info.value.kind, exc_info.value.qualified_name) == ("function", name)


@pytest.mark.parametrize(
    ("schema", "function", "call"),
    [
        ("pg_catalog", "generate_series", "generate_series(1, 10)"),
        ("pg_catalog", "unnest", "unnest('{1,2}'::integer[])"),
        ("pg_catalog", "json_to_recordset", """json_to_recordset('[{"a": 1}]'::json)"""),
        ("public", "my_srf", "my_srf(1)"),
    ],
)
async def test_single_function_scan_with_allowed_calls_passes(schema: str, function: str, call: str) -> None:
    explain = _Explain({_EXPLAIN + _SELECT: _single_scan(schema, function, call)})

    await _guard(explain).check(_SELECT)


@pytest.mark.parametrize(
    "node",
    [
        _rows_from("unnest('{1}'::integer[]) FROM secret.t"),
        _single_scan("pg_catalog", "unnest", "unnest('{1}'::integer[]) FROM secret.t"),
        _single_scan("pg_catalog", "unnest", "unnest('{1}'::integer[]) WHERE true"),
        _single_scan("pg_catalog", "unnest", "unnest('{1}'::integer[]) UNION SELECT 1"),
        _single_scan("pg_catalog", "generate_series", "generate_series(1, (SubPlan 1))"),
    ],
)
async def test_function_call_with_more_than_a_target_list_is_rejected(node: dict[str, Any]) -> None:
    explain = _Explain({_EXPLAIN + _SELECT: node})

    with pytest.raises(PlanUnverifiableError, match="a Function Scan whose functions cannot be verified"):
        await _guard(explain).check(_SELECT)


@pytest.mark.parametrize("node_type", ["Seq Scan", "Result", "Table Function Scan", None])
async def test_function_call_outside_a_function_scan_is_checked_as_an_expression(node_type: str | None) -> None:
    """Имена Function Call пропускает только Function Scan: его вызовы уже проверил строгий путь."""
    node: dict[str, Any] = {"Function Call": "secret.f(1)"}
    if node_type is not None:
        node["Node Type"] = node_type
    explain = _Explain({_EXPLAIN + _SELECT: node})

    with pytest.raises(PlanAccessError) as exc_info:
        await _guard(explain).check(_SELECT)

    assert (exc_info.value.kind, exc_info.value.qualified_name) == ("function", "secret.f")


def _with(**expressions: Any) -> dict[str, Any]:
    """Скан разрешённой таблицы с выражениями плана."""
    return {**_scan("public", "app_t"), **expressions}


@pytest.mark.parametrize(
    ("node", "kind", "name"),
    [
        (_with(Output=["secret.decrypt(app_t.c)"]), "function", "secret.decrypt"),
        (_with(Filter="(pg_catalog.pg_read_file('x'::text) IS NOT NULL)"), "function", "pg_catalog.pg_read_file"),
        (_with(Output=["(app_t.a OPERATOR(secret.+) 1)"]), "function", "secret.+"),
        (_with(**{"Sort Key": ["app_t.a USING OPERATOR(secret.<)"]}), "function", "secret.<"),
        (_with(Output=["(app_t.c)::secret.t"]), "type", "secret.t"),
        (_with(Output=["nextval('secret.s'::regclass)"]), "relation", "secret.s"),
        (_with(**{"Group Keys": [["app_t.a"], ["secret.f(app_t.b)"]]}), "function", "secret.f"),
        (
            {
                "Node Type": "Function Scan",
                "Function Name": "unnest",
                "Schema": "pg_catalog",
                "Alias": "u",
                "Function Call": "unnest(ARRAY[NULL::secret.t])",
            },
            "type",
            "secret.t",
        ),
    ],
)
async def test_expression_outside_basic_is_rejected(node: dict[str, Any], kind: str, name: str) -> None:
    explain = _Explain({_EXPLAIN + _SELECT: node})

    with pytest.raises(PlanAccessError) as exc_info:
        await _guard(explain).check(_SELECT)

    assert (exc_info.value.kind, exc_info.value.qualified_name) == (kind, name)
    assert explain.catalog_queries() == []


@pytest.mark.parametrize(
    "expressions",
    [
        {"Output": ["lower(app_t.name)", "count(*)", "(app_t.a)::text", "'app_t'::regclass", "$0"]},
        {"Filter": "(NOT (hashed SubPlan 1))", "Output": ["(InitPlan 1).col1", "PARTIAL count(*)"]},
        {"Filter": "(ANY (app_t.a = (hashed SubPlan 1).col1))", "Run Condition": "(row_number() OVER (?) <= 10)"},
        {"Sort Key": ["app_t.a DESC NULLS LAST", "app_t.b USING <", '(lower(app_t.name)) COLLATE "C"']},
        {"Cache Key": "app_t.a, app_t.b", "Output": ["public.app_f(app_t.a)", "(app_t.a OPERATOR(public.===) 1)"]},
        {"Output": ["EXTRACT(year FROM app_t.d)", "CURRENT_USER", "COALESCE(app_t.a, 0)"]},
        {"Output": ["nextval('app_t_id_seq'::regclass)", "nextval('app_t_id_seq')"]},
    ],
)
async def test_allowed_expressions_pass_without_catalog_queries(expressions: dict[str, Any]) -> None:
    explain = _Explain({_EXPLAIN + _SELECT: _with(**expressions)})

    await _guard(explain).check(_SELECT)

    assert explain.catalog_queries() == []


async def test_unqualified_builtin_outside_basic_is_rejected_by_the_catalog() -> None:
    """current_setting печатается без схемы (search_path = public); каталог говорит: это pg_catalog."""
    node = _with(Output=["current_setting('app.jwt_secret'::text)", "my_public_fn(app_t.a)"])
    explain = _Explain({_EXPLAIN + _SELECT: node}, pg_catalog_functions=frozenset({"current_setting"}))

    with pytest.raises(PlanAccessError) as exc_info:
        await _guard(explain).check(_SELECT)

    assert (exc_info.value.kind, exc_info.value.qualified_name) == ("function", "pg_catalog.current_setting")
    [query] = explain.catalog_queries()
    assert "'current_setting'" in query
    assert "'my_public_fn'" in query


async def test_unqualified_public_function_passes_after_one_catalog_query() -> None:
    node = _with(Output=["my_public_fn(app_t.a)", "other_fn(app_t.b)"], Filter="(my_public_fn(app_t.a) > 0)")
    explain = _Explain({_EXPLAIN + _SELECT: node}, pg_catalog_functions=frozenset({"current_setting"}))

    await _guard(explain).check(_SELECT)

    assert len(explain.catalog_queries()) == 1


async def test_nextval_of_an_expression_is_checked_as_a_function() -> None:
    node = _with(Output=["nextval(('x'::text)::regclass)"])
    explain = _Explain({_EXPLAIN + _SELECT: node}, pg_catalog_functions=frozenset({"nextval"}))

    with pytest.raises(PlanAccessError, match=r"function 'pg_catalog\.nextval'"):
        await _guard(explain).check(_SELECT)


@pytest.mark.parametrize("expression", ["nextval('users_id_seq'::regclass)", "nextval('users_id_seq')"])
async def test_sequence_without_the_prefix_is_rejected(expression: str) -> None:
    explain = _Explain({_EXPLAIN + _SELECT: _with(Output=[expression])})

    with pytest.raises(PlanAccessError, match=r"relation 'public\.users_id_seq'"):
        await _guard(explain, table_prefix="app_").check(_SELECT)


@pytest.mark.parametrize(
    "call",
    ["json_populate_record(NULL::users, '{}'::json)", "json_populate_record(NULL::public.users, '{}'::json)"],
)
async def test_row_type_of_a_table_without_the_prefix_is_rejected(call: str) -> None:
    node = {**_function_scan("pg_catalog", "json_populate_record"), "Function Call": call}
    explain = _Explain({_EXPLAIN + _SELECT: node}, row_types=frozenset({"users"}))

    with pytest.raises(PlanAccessError) as exc_info:
        await _guard(explain, table_prefix="app_").check(_SELECT)

    assert (exc_info.value.kind, exc_info.value.qualified_name) == ("relation", "public.users")


@pytest.mark.parametrize(
    ("relation", "name"),
    [(("public", "users"), "public.users"), (("secret", "t"), "secret.t")],
)
async def test_domain_over_a_forbidden_row_type_is_rejected_as_its_relation(
    relation: tuple[str, str], name: str
) -> None:
    """Домен (typrelid = 0) над строковым типом таблицы — оракул её структуры, как сам тип."""
    explain = _Explain({_EXPLAIN + _SELECT: _with(Output=["NULL::users_dom"])}, row_types={"users_dom": relation})

    with pytest.raises(PlanAccessError) as exc_info:
        await _guard(explain, table_prefix="app_").check(_SELECT)

    assert (exc_info.value.kind, exc_info.value.qualified_name) == ("relation", name)


async def test_domain_over_a_prefixed_row_type_passes() -> None:
    explain = _Explain(
        {_EXPLAIN + _SELECT: _with(Output=["NULL::orders_dom"])}, row_types={"orders_dom": ("public", "app_orders")}
    )

    await _guard(explain, table_prefix="app_").check(_SELECT)


async def test_row_types_are_not_checked_without_a_prefix() -> None:
    explain = _Explain({_EXPLAIN + _SELECT: _with(Output=["NULL::users"])}, row_types=frozenset({"users"}))

    await _guard(explain).check(_SELECT)

    assert explain.catalog_queries() == []


async def test_prefixed_builtin_and_reg_types_need_no_row_type_query() -> None:
    node = _with(Output=["NULL::app_users", 'NULL::"APP_Orders"', "(app_t.a)::text", "'app_t'::regclass"])
    explain = _Explain({_EXPLAIN + _SELECT: node}, row_types=frozenset({"users"}))

    await _guard(explain, table_prefix="app_").check(_SELECT)

    assert [q for q in explain.catalog_queries() if "typrelid" in q] == []


async def test_builtin_type_names_are_loaded_once() -> None:
    node = _with(Output=["(app_t.a)::text", "NULL::my_enum"])
    explain = _Explain({_EXPLAIN + _SELECT: node})
    builtin_types = BuiltinTypeNames()

    for _ in range(3):
        await _guard(explain, table_prefix="app_", builtin_types=builtin_types).check(_SELECT)

    queries = explain.catalog_queries()
    assert len([q for q in queries if "pg_catalog.pg_type" in q and "typrelid" not in q]) == 1
    assert len([q for q in queries if "typrelid" in q]) == 3


@pytest.mark.parametrize(
    ("node", "key"),
    [
        (_with(Output=["foo bar ("]), "Output"),
        (_with(Filter="(a) FROM secret.t"), "Filter"),
        (_with(Output=[42]), "Output"),
        (_with(**{"Sort Key": ["a, b"]}), "Sort Key"),
        (_with(Output=["x::a.b.c"]), "Output"),
        ({"Node Type": "Table Function Scan", "Table Function Call": "secret.f()"}, "Table Function Call"),
    ],
)
async def test_unparsable_expression_is_rejected(node: dict[str, Any], key: str) -> None:
    explain = _Explain({_EXPLAIN + _SELECT: node})

    with pytest.raises(PlanUnverifiableError, match=rf"an expression in {key} of a") as exc_info:
        await _guard(explain).check(_SELECT)

    assert exc_info.value.key == key
    assert "secret" not in str(exc_info.value)


async def test_relation_errors_keep_precedence_over_expressions() -> None:
    """Узлы проверяются до выражений: запрещённое отношение отклоняется прежней ошибкой."""
    plan = {
        "Node Type": "Hash Join",
        "Output": ["(c.relname)::information_schema.sql_identifier"],
        "Plans": [_scan("pg_catalog", "pg_class")],
    }
    explain = _Explain({_EXPLAIN + _SELECT: plan})

    with pytest.raises(PlanAccessError, match=r"relation 'pg_catalog\.pg_class'"):
        await _guard(explain).check(_SELECT)


async def test_type_hint_names_the_allowed_schema() -> None:
    explain = _Explain({_EXPLAIN + _SELECT: _with(Output=["NULL::secret.t"])})

    with pytest.raises(PlanAccessError) as exc_info:
        await _guard(explain).check(_SELECT)

    assert "Only types from 'public' or built-in types are permitted." in str(exc_info.value)


@pytest.mark.parametrize(
    ("expression", "kind", "name"),
    [
        ('"PARTIAL public".f(app_t.a)', "function", "PARTIAL public.f"),
        ('NULL::"PARTIAL public".t', "type", "PARTIAL public.t"),
        ('(app_t.a OPERATOR("PARTIAL public".+) 1)', "function", "PARTIAL public.+"),
        ("""nextval('"PARTIAL public".s'::regclass)""", "relation", "PARTIAL public.s"),
    ],
)
async def test_quoted_names_are_checked_as_they_are(expression: str, kind: str, name: str) -> None:
    explain = _Explain({_EXPLAIN + _SELECT: _with(Output=[expression])})

    with pytest.raises(PlanAccessError) as exc_info:
        await _guard(explain).check(_SELECT)

    assert (exc_info.value.kind, exc_info.value.qualified_name) == (kind, name)


async def test_quoted_sequence_without_the_prefix_is_rejected() -> None:
    explain = _Explain({_EXPLAIN + _SELECT: _with(Output=["""nextval('"PARTIAL app_s"'::regclass)"""])})

    with pytest.raises(PlanAccessError, match=r"relation 'public\.PARTIAL app_s'"):
        await _guard(explain, table_prefix="app_").check(_SELECT)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT (NULL::users).secret_note FROM app_t",
        "SELECT '(1,2)'::users",
        "SELECT CAST(NULL AS public.users)",
        "SELECT 1; SELECT NULL::users[]",
        "SELECT * FROM ROWS FROM (json_to_record('{}') AS (u users)) AS r",
    ],
)
async def test_row_type_in_the_agent_sql_is_rejected_before_explain(sql: str) -> None:
    """Ошибка разбора EXPLAIN (нет колонки, неверное число полей) раскрыла бы структуру таблицы без префикса."""
    explain = _Explain(row_types=frozenset({"users"}))

    with pytest.raises(PlanAccessError) as exc_info:
        await _guard(explain, table_prefix="app_").check(sql)

    assert (exc_info.value.kind, exc_info.value.qualified_name) == ("relation", "public.users")
    assert [q for q in explain.sent if q.startswith("EXPLAIN")] == []


async def test_agent_types_need_one_row_type_query_only_when_unresolved() -> None:
    explain = _Explain(row_types=frozenset({"users"}))
    guard = _guard(explain, table_prefix="app_")

    await guard.check("SELECT NULL::app_users, 1::integer, 'x'::text, NULL::app_t[] FROM app_t")

    assert [q for q in explain.catalog_queries() if "typrelid" in q] == []
    assert explain.sent[-1].startswith("EXPLAIN")

    await guard.check("SELECT NULL::my_enum FROM app_t")

    [row_type_query] = [q for q in explain.catalog_queries() if "typrelid" in q]
    assert "'my_enum'" in row_type_query
    assert explain.sent[-1].startswith("EXPLAIN")
    assert explain.sent.index(row_type_query) < len(explain.sent) - 1


async def test_agent_types_are_not_looked_up_without_a_prefix() -> None:
    explain = _Explain(row_types=frozenset({"users"}))

    await _guard(explain).check("SELECT NULL::users FROM app_t")

    assert explain.catalog_queries() == []


def _rule(select: str) -> dict[str, Any]:
    """Строка с текстом правила представления, как его печатает pg_get_ruledef."""
    return {"kind": "rule", "definition": f'CREATE RULE "_RETURN" AS ON SELECT TO public.app_v DO INSTEAD {select};'}


_OPERATOR_BANG = {"kind": "operator", "schema": "public", "name": "!!"}
_AGGREGATE = {"kind": "function", "schema": "public", "name": "app_sum"}


@pytest.mark.parametrize(
    ("rules", "kind", "name"),
    [
        ([{"kind": "function", "schema": "secret", "name": "api_key"}], "function", "secret.api_key"),
        (
            [{"kind": "function", "schema": "pg_catalog", "name": "current_setting"}],
            "function",
            "pg_catalog.current_setting",
        ),
        (
            [
                _OPERATOR_BANG,
                {
                    "kind": "operator_function",
                    "schema": "pg_catalog",
                    "name": "current_setting",
                    "parent_schema": "public",
                },
            ],
            "function",
            "pg_catalog.current_setting",
        ),
        ([{"kind": "operator", "schema": "secret", "name": "!!"}], "function", "secret.!!"),
        (
            [_AGGREGATE, {"kind": "aggregate_function", "schema": "secret", "name": "f", "parent_schema": "public"}],
            "function",
            "secret.f",
        ),
        ([{"kind": "type", "schema": "secret", "name": "t"}], "type", "secret.t"),
        (
            [
                {"kind": "type", "schema": "public", "name": "users_dom"},
                {
                    "kind": "type",
                    "schema": "public",
                    "name": "users",
                    "relation_schema": "public",
                    "relation_name": "users",
                },
            ],
            "relation",
            "public.users",
        ),
        ([_rule("SELECT secret.api_key() AS k")], "function", "secret.api_key"),
        (
            [_rule("SELECT id FROM app_t LIMIT (current_setting('max_connections'::text))::integer")],
            "function",
            "pg_catalog.current_setting",
        ),
        ([_rule("SELECT NULL::secret.t AS t")], "type", "secret.t"),
        ([_rule("SELECT nextval('users_id_seq'::regclass) AS n")], "relation", "public.users_id_seq"),
    ],
)
async def test_dependency_of_a_view_or_rule_outside_basic_is_rejected(
    rules: list[dict[str, Any]], kind: str, name: str
) -> None:
    """Свёртка констант, LIMIT, SubPlan PG 15/16 и функции операторов в плане не видны — видны в правиле."""
    explain = _Explain(pg_catalog_functions=frozenset({"current_setting"}), rules=rules)

    with pytest.raises(PlanAccessError) as exc_info:
        await _guard(explain, table_prefix="app_").check(_SELECT)

    assert (exc_info.value.kind, exc_info.value.qualified_name) == (kind, name)


@pytest.mark.parametrize(
    "rules",
    [
        [{"kind": "function", "schema": "pg_catalog", "name": "lower"}],
        [{"kind": "function", "schema": "public", "name": "app_fn"}],
        [
            {"kind": "operator", "schema": "public", "name": "==="},
            {"kind": "operator_function", "schema": "pg_catalog", "name": "lower", "parent_schema": "public"},
        ],
        [
            {"kind": "operator", "schema": "public", "name": "==="},
            {"kind": "operator_function", "schema": "public", "name": "app_eq", "parent_schema": "public"},
        ],
        [{"kind": "aggregate_function", "schema": "pg_catalog", "name": "int4pl", "parent_schema": "pg_catalog"}],
        [
            {"kind": "type", "schema": "public", "name": "app_dom"},
            {
                "kind": "type",
                "schema": "public",
                "name": "app_t",
                "relation_schema": "public",
                "relation_name": "app_t",
            },
            {"kind": "type", "schema": "pg_catalog", "name": "int4"},
        ],
        [
            _rule(
                "SELECT lower(name) AS l, count(*) AS n, (name)::character varying(5) AS v FROM app_t ORDER BY (lower(name))"
            )
        ],
        [_rule("SELECT app_fn(id) AS f, (id OPERATOR(public.===) 1) AS e FROM app_t WHERE (id = 1)")],
    ],
)
async def test_allowed_dependencies_of_views_and_rules_pass(rules: list[dict[str, Any]]) -> None:
    explain = _Explain(pg_catalog_functions=frozenset({"current_setting"}), rules=rules)

    await _guard(explain, table_prefix="app_").check(_SELECT)

    assert len(explain.rule_queries) == 2


@pytest.mark.parametrize(
    "rules",
    [
        [{"kind": "rule", "definition": "CREATE RULE broken ("}],
        [{"kind": "rule", "definition": None}],
        [{"kind": "rule", "definition": "SELECT 1"}],
        [{"kind": "something new", "schema": "public", "name": "x"}],
    ],
)
async def test_unverifiable_rule_rows_are_rejected(rules: list[dict[str, Any]]) -> None:
    explain = _Explain(rules=rules)

    with pytest.raises(PlanUnverifiableError, match="views or rules"):
        await _guard(explain).check(_SELECT)


async def test_missing_rule_rows_are_rejected() -> None:
    async def run(sql: str) -> list[RowResult] | None:
        if "pg_catalog.pg_rewrite" in sql:
            return None
        return [RowResult(cells={"QUERY PLAN": [{"Plan": _RESULT}]})]

    with pytest.raises(PlanUnverifiableError, match="views or rules"):
        await PlanGuard(run, allowed_schema="public", table_prefix=None).check(_SELECT)


_PREPARED = re.compile(r"PREPARE (_pgmcp_check_[0-9a-f]{16}_\d+) AS (.+); DEALLOCATE \1", re.DOTALL)


def _step(sql: str) -> str:
    """Короткое имя шага журнала исполнителя: PREPARE/EXPLAIN с отношением, rules — чтение правил."""
    if "pg_catalog.pg_rewrite" in sql:
        return "rules"
    relation = sql.rsplit(" FROM ", maxsplit=1)[-1].split(";", maxsplit=1)[0]
    return f"{sql.split(' ', maxsplit=1)[0]} {relation}"


async def test_definitions_are_checked_between_prepare_and_explain() -> None:
    """PREPARE блокирует представления без планирования; правила читаются до EXPLAIN и ещё раз после всех."""
    explain = _Explain()

    await _guard(explain).check("SELECT * FROM app_a; SHOW search_path; SELECT * FROM app_b")

    assert [_step(sql) for sql in explain.log] == [
        "PREPARE app_a",
        "PREPARE app_b",
        "rules",
        "EXPLAIN app_a",
        "EXPLAIN app_b",
        "rules",
    ]


async def test_each_statement_is_prepared_and_deallocated_in_one_command() -> None:
    explain = _Explain()

    await _guard(explain).check("SELECT * FROM app_a; SELECT * FROM app_b")

    matches = [_PREPARED.fullmatch(sql) for sql in explain.prepared]
    assert all(match is not None for match in matches)
    assert [match.group(2) for match in matches if match] == ["SELECT * FROM app_a", "SELECT * FROM app_b"]
    assert len({match.group(1) for match in matches if match}) == 2


async def test_prepared_names_differ_between_checks() -> None:
    first, second = _Explain(), _Explain()

    await _guard(first).check(_SELECT)
    await _guard(second).check(_SELECT)

    first_match = _PREPARED.fullmatch(first.prepared[0])
    second_match = _PREPARED.fullmatch(second.prepared[0])
    assert first_match is not None
    assert second_match is not None
    assert first_match.group(1) != second_match.group(1)


@pytest.mark.parametrize(
    "sql",
    ["EXPLAIN SELECT * FROM app_t", "EXPLAIN ANALYZE SELECT * FROM app_t", "DECLARE c CURSOR FOR SELECT * FROM app_t"],
)
async def test_wrappers_prepare_the_inner_query(sql: str) -> None:
    """PREPARE не принимает EXPLAIN и DECLARE: готовится вложенный запрос, тот же, что уходит в EXPLAIN."""
    explain = _Explain()

    await _guard(explain).check(sql)

    [command] = explain.prepared
    match = _PREPARED.fullmatch(command)
    assert match is not None
    assert match.group(2) == "SELECT * FROM app_t"


async def test_definition_rejected_before_planning_sends_no_explain() -> None:
    """IMMUTABLE-вызов представления планировщик выполнил бы при EXPLAIN: отказ приходит раньше."""
    explain = _Explain(rules=[{"kind": "function", "schema": "secret", "name": "api_key"}])

    with pytest.raises(PlanAccessError, match=r"function 'secret\.api_key'"):
        await _guard(explain).check(_SELECT)

    assert len(explain.prepared) == 1
    assert [sql for sql in explain.log if sql.startswith("EXPLAIN")] == []


async def test_rule_rows_seen_before_explain_are_not_checked_again() -> None:
    """Второе чтение правил отдаёт те же строки: имена из них не спрашиваются у каталога повторно."""
    explain = _Explain(rules=[_rule("SELECT my_public_fn(id) AS f FROM app_t")])

    await _guard(explain).check(_SELECT)

    assert len(explain.rule_queries) == 2
    assert len([sql for sql in explain.catalog_queries() if "'my_public_fn'" in sql]) == 1


async def test_generic_statement_is_prepared_in_a_savepoint() -> None:
    explain = _Explain()

    await _guard(explain).check("EXPLAIN (GENERIC_PLAN) SELECT * FROM app_t WHERE id = $1")

    [command] = explain.prepared
    assert command.startswith("SAVEPOINT _pgmcp_check; PREPARE _pgmcp_check_")
    assert command.endswith("; RELEASE SAVEPOINT _pgmcp_check")
    assert "AS SELECT * FROM app_t WHERE id = $1; DEALLOCATE " in command


async def test_undeterminable_parameter_is_prepared_again_with_null() -> None:
    """PREPARE не выводит тип $1 там, где EXPLAIN (GENERIC_PLAN) проходит: точка сохранения откатывается,
    оператор готовится ещё раз с NULL вместо $N, и правила читаются до EXPLAIN (который остаётся с $1)."""
    error = IndeterminateDatatype("could not determine data type of parameter $1")
    explain = _Explain(prepare_errors={"$1 IS NULL": error})

    await _guard(explain).check("EXPLAIN (GENERIC_PLAN) SELECT * FROM app_t WHERE $1 IS NULL")

    first, rollback, retry = explain.prepared
    assert "AS SELECT * FROM app_t WHERE $1 IS NULL; DEALLOCATE " in first
    assert rollback == "ROLLBACK TO SAVEPOINT _pgmcp_check; RELEASE SAVEPOINT _pgmcp_check"
    assert retry.startswith("SAVEPOINT _pgmcp_check; PREPARE _pgmcp_check_")
    assert retry.endswith("; RELEASE SAVEPOINT _pgmcp_check")
    assert "AS SELECT * FROM app_t WHERE NULL IS NULL; DEALLOCATE " in retry
    explain_sql = "EXPLAIN (VERBOSE, FORMAT JSON, GENERIC_PLAN) SELECT * FROM app_t WHERE $1 IS NULL"
    assert explain.sent == [explain_sql]
    rules = [index for index, sql in enumerate(explain.log) if "pg_catalog.pg_rewrite" in sql]
    assert len(rules) == 2
    assert explain.log.index(retry) < rules[0] < explain.log.index(explain_sql)


async def test_every_parameter_is_replaced_by_null_in_the_retry() -> None:
    error = IndeterminateDatatype("could not determine data type of parameter $2")
    explain = _Explain(prepare_errors={"pg_typeof($2)": error})

    await _guard(explain).check("EXPLAIN (GENERIC_PLAN) SELECT pg_typeof($2), id FROM app_t WHERE id = $1 LIMIT $3")

    retry = explain.prepared[-1]
    assert "AS SELECT pg_typeof(NULL), id FROM app_t WHERE id = NULL LIMIT ALL; DEALLOCATE " in retry
    assert "$" not in retry


@pytest.mark.parametrize(
    "error",
    [
        IndeterminateDatatype("could not determine data type of parameter $1"),
        UndefinedTable('relation "app_missing" does not exist'),
    ],
)
async def test_failed_retry_is_unverifiable_without_explain(error: Exception) -> None:
    """Повторный PREPARE с NULL не прошёл: отказ закрыто, EXPLAIN непроверенного оператора не выполняется."""
    first = IndeterminateDatatype("could not determine data type of parameter $1")
    explain = _Explain(prepare_errors={"$1 IS NULL": first, "NULL IS NULL": error})

    with pytest.raises(PlanUnverifiableError):
        await _guard(explain).check("EXPLAIN (GENERIC_PLAN) SELECT * FROM app_t WHERE $1 IS NULL")

    assert explain.sent == []
    assert explain.rule_queries == []
    assert explain.prepared[-1] == "ROLLBACK TO SAVEPOINT _pgmcp_check; RELEASE SAVEPOINT _pgmcp_check"


async def test_prepare_error_propagates_without_explain() -> None:
    """Ошибка разбора (нет отношения) — та же, что дал бы EXPLAIN; точки сохранения без GENERIC_PLAN нет."""
    explain = _Explain(prepare_errors={"app_missing": UndefinedTable('relation "app_missing" does not exist')})

    with pytest.raises(UndefinedTable):
        await _guard(explain).check("SELECT * FROM app_missing")

    assert explain.sent == []
    assert explain.rule_queries == []
    assert not explain.prepared[0].startswith("SAVEPOINT")


async def test_undeterminable_parameter_without_generic_plan_propagates() -> None:
    error = IndeterminateDatatype("could not determine data type of parameter $1")
    explain = _Explain(prepare_errors={"$1 IS NULL": error})

    with pytest.raises(IndeterminateDatatype):
        await _guard(explain).check("SELECT $1 IS NULL FROM app_t")

    assert explain.sent == []


@pytest.mark.parametrize("sql", ["SHOW search_path", "SET LOCAL search_path = public"])
async def test_rules_are_not_read_without_a_plan(sql: str) -> None:
    explain = _Explain()

    await _guard(explain).check(sql)

    assert explain.rule_queries == []


async def test_rules_are_not_read_again_after_a_rejected_plan() -> None:
    explain = _Explain({_EXPLAIN + _SELECT: _scan("secret", "accounts")})

    with pytest.raises(PlanAccessError):
        await _guard(explain).check(_SELECT)

    assert len(explain.rule_queries) == 1


_BANG_SETTING = [
    {"kind": "operator_function", "schema": "pg_catalog", "name": "current_setting", "parent_schema": "public"}
]


async def test_public_operator_in_the_agent_sql_is_rejected_before_prepare() -> None:
    """Оператор public называет себя, а не current_setting; IMMUTABLE-функцию с константами выполнил бы EXPLAIN."""
    explain = _Explain(implementations=_BANG_SETTING)

    with pytest.raises(PlanAccessError) as exc_info:
        await _guard(explain).check("SELECT 'max_connections' !! true FROM app_t")

    assert (exc_info.value.kind, exc_info.value.qualified_name) == ("function", "pg_catalog.current_setting")
    [query] = explain.implementation_queries
    assert "'!!'" in query
    assert explain.prepared == []
    assert [sql for sql in explain.log if sql.startswith("EXPLAIN")] == []


@pytest.mark.parametrize(
    "expression", ["('max_connections'::text !! true)", "('max_connections'::text OPERATOR(public.!!) true)"]
)
async def test_public_operator_in_the_plan_is_checked_by_its_function(expression: str) -> None:
    """Представление: SQL агента оператора не называет, его печатает план (без схемы или с allowed_schema)."""
    explain = _Explain({_EXPLAIN + _SELECT: _with(Output=[expression])}, implementations=_BANG_SETTING)

    with pytest.raises(PlanAccessError, match=r"function 'pg_catalog\.current_setting'"):
        await _guard(explain).check(_SELECT)

    [query] = explain.implementation_queries
    assert "'!!'" in query


async def test_public_operator_with_a_public_function_passes() -> None:
    implementations = [
        {"kind": "operator_function", "schema": "public", "name": "app_close_to", "parent_schema": "public"}
    ]
    explain = _Explain(implementations=implementations)

    await _guard(explain).check("SELECT id <~> 1 FROM app_t")

    [query] = explain.implementation_queries
    assert "'<~>'" in query


async def test_builtin_operator_rows_are_not_checked_by_their_functions() -> None:
    """Строка оператора pg_catalog (parent_schema) — встроенный int4eq вне списка basic не отклоняется."""
    implementations = [
        {"kind": "operator_function", "schema": "pg_catalog", "name": "int4eq", "parent_schema": "pg_catalog"}
    ]
    explain = _Explain(implementations=implementations)

    await _guard(explain).check("SELECT id FROM app_t WHERE id = 1")


@pytest.mark.parametrize(
    ("row", "name"),
    [
        ({"kind": "aggregate_function", "schema": "secret", "name": "f", "parent_schema": "public"}, "secret.f"),
        (
            {"kind": "aggregate_function", "schema": "pg_catalog", "name": "int4pl", "parent_schema": "public"},
            "pg_catalog.int4pl",
        ),
        ({"kind": "operator", "schema": "secret", "name": "<"}, "secret.<"),
        (
            {"kind": "operator_function", "schema": "secret", "name": "lt", "parent_schema": "public"},
            "secret.lt",
        ),
    ],
)
async def test_public_aggregate_with_a_forbidden_implementation_is_rejected(row: dict[str, Any], name: str) -> None:
    """Опорные функции агрегата public и оператор сортировки (aggsortop, для min/max) — по правилам basic."""
    explain = _Explain(implementations=[row])

    with pytest.raises(PlanAccessError) as exc_info:
        await _guard(explain).check("SELECT app_agg(id) FROM app_t")

    assert (exc_info.value.kind, exc_info.value.qualified_name) == ("function", name)
    [query] = explain.implementation_queries
    assert "'app_agg'" in query


async def test_builtin_named_function_is_looked_up_as_a_possible_public_aggregate() -> None:
    """Имя count из списка basic тоже может быть агрегатом public (count(mytype)): его спрашивают у каталога."""
    explain = _Explain()

    await _guard(explain).check("SELECT count(*) FROM app_t")

    [query] = explain.implementation_queries
    assert "'count'" in query


async def test_names_are_looked_up_once_per_check() -> None:
    sql = "SELECT app_agg(id) FROM app_t WHERE id <~> 1"
    node = _with(Filter="(app_t.id <~> 1)", Output=["app_agg(app_t.id)"])
    explain = _Explain({_EXPLAIN + sql: node})

    await _guard(explain).check(sql)

    [query] = explain.implementation_queries
    assert "'<~>'" in query
    assert "'app_agg'" in query


async def test_plan_only_names_get_a_second_lookup() -> None:
    node = _with(Output=["app_agg(app_t.id)"])
    explain = _Explain({_EXPLAIN + _SELECT: node})

    await _guard(explain).check(_SELECT)

    [query] = explain.implementation_queries
    assert "'app_agg'" in query


async def test_no_lookup_without_names_of_the_allowed_schema() -> None:
    node = _with(Output=["pg_catalog.lower(app_t.name)", "(app_t.a OPERATOR(pg_catalog.=) 1)"])
    explain = _Explain({_EXPLAIN + _SELECT: node})

    await _guard(explain).check(_SELECT)

    assert explain.implementation_queries == []


async def test_types_of_non_planned_statements_are_checked_but_their_names_are_not_looked_up() -> None:
    explain = _Explain()

    await _guard(explain).check("PREPARE p AS SELECT app_agg(id) FROM app_t WHERE id <~> 1")

    assert explain.implementation_queries == []


_TAG_BOOM = [{"kind": "operator_function", "schema": "secret", "name": "tag_boom", "parent_schema": "public"}]


@pytest.mark.parametrize(
    ("sql", "operators"),
    [
        ("SELECT id BETWEEN 1 AND 2 FROM app_t", ["'>='", "'<='"]),
        ("SELECT id BETWEEN SYMMETRIC 1 AND 2 FROM app_t", ["'>='", "'<='"]),
        ("SELECT id NOT BETWEEN 1 AND 2 FROM app_t", ["'<'", "'>'"]),
        ("SELECT id NOT BETWEEN SYMMETRIC 1 AND 2 FROM app_t", ["'<'", "'>'"]),
        ("SELECT CASE id WHEN 1 THEN 2 END FROM app_t", ["'='"]),
        ("SELECT * FROM app_t AS a JOIN app_t AS b USING (id)", ["'='"]),
        ("SELECT * FROM app_t AS a FULL JOIN app_t AS b USING (id)", ["'='"]),
        ("SELECT * FROM app_t AS a NATURAL JOIN app_t AS b", ["'='"]),
        ("SELECT id IN (SELECT id FROM app_t) FROM app_t", ["'='"]),
        ("SELECT id NOT IN (SELECT id FROM app_t) FROM app_t", ["'='"]),
        ("SELECT (id, id) IN (SELECT id, id FROM app_t) FROM app_t", ["'='"]),
    ],
)
async def test_operator_generated_by_the_parser_is_rejected_before_prepare(sql: str, operators: list[str]) -> None:
    """Имени оператора в тексте нет — его подставляет разбор Postgres; с константами EXPLAIN выполнил бы его функцию."""
    explain = _Explain(implementations=_TAG_BOOM)

    with pytest.raises(PlanAccessError, match=r"function 'secret\.tag_boom'"):
        await _guard(explain).check(sql)

    [query] = explain.implementation_queries
    assert all(operator in query for operator in operators)
    assert "BETWEEN" not in query
    assert explain.prepared == []


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT CASE WHEN true THEN 1 END FROM app_t",
        "SELECT * FROM app_t AS a JOIN app_t AS b ON true",
        "SELECT EXISTS (SELECT id FROM app_t) FROM app_t",
        "SELECT (SELECT id FROM app_t LIMIT 1) FROM app_t",
    ],
)
async def test_constructs_without_an_operator_do_not_look_up_implementations(sql: str) -> None:
    explain = _Explain(implementations=_TAG_BOOM)

    await _guard(explain).check(sql)

    assert explain.implementation_queries == []
