"""Тесты разбора выражений плана EXPLAIN (VERBOSE): формы ruleutils PG 15–17 и имена, которые они называют."""

import pytest

from postgres_fastmcp.postgres.security.plan_expressions import (
    EXPRESSION_PARSERS,
    ExpressionNames,
    expression_texts,
    parse_expression,
    parse_sort_key,
    parse_table_function,
    sequence_name,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("secret.f(c)", ExpressionNames(functions=(("secret", "f"),))),
        (
            "current_setting('app.jwt_secret'::text)",
            ExpressionNames(functions=((None, "current_setting"),), types=((None, "text"),)),
        ),
        ("(a OPERATOR(secret.+) b)", ExpressionNames(operators=(("secret", "+"),))),
        ("NULL::secret.t", ExpressionNames(types=(("secret", "t"),))),
        ("NULL::users[]", ExpressionNames(types=((None, "users"),))),
        ("(a)::character varying", ExpressionNames(types=(("pg_catalog", "varchar"),))),
        ("EXTRACT(year FROM d)", ExpressionNames(functions=(("pg_catalog", "extract"),))),
        ("CURRENT_USER", ExpressionNames()),
        ("COALESCE(a, b)", ExpressionNames()),
        ("t.a, t.b", ExpressionNames()),
        ("$1", ExpressionNames()),
    ],
)
def test_names_of_an_expression(text: str, expected: ExpressionNames) -> None:
    assert parse_expression(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "(SubPlan 1)",
        "(NOT (hashed SubPlan 1))",
        "(alternatives: SubPlan 1 or hashed SubPlan 2)",
        "$0",
        "(InitPlan 1).col1",
        "(NOT EXISTS(SubPlan 1))",
        "ARRAY(SubPlan 1)",
        "(rescan SubPlan 1)",
        "(ANY (t.a = (hashed SubPlan 1).col1))",
        "(ALL (t.a > (SubPlan 1).col1))",
        "(ROW(t.a, t.b) < ROW((SubPlan 1).col1, (SubPlan 1).col2))",
    ],
)
def test_planner_references_become_null(text: str) -> None:
    """Ссылки на подпланы и параметры (PG 15–16 и PG 17) разбираются; сами подпланы — узлы плана."""
    names = parse_expression(text)

    assert names is not None
    assert names.functions == ()


@pytest.mark.parametrize(
    ("text", "function"),
    [("PARTIAL count(*)", "count"), ("(row_number() OVER (?) <= 10)", "row_number")],
)
def test_partial_aggregate_and_window_without_definition_are_parsed(text: str, function: str) -> None:
    names = parse_expression(text)

    assert names is not None
    assert names.functions == ((None, function),)


def test_substitution_inside_a_literal_changes_only_its_value() -> None:
    assert parse_expression("secret.f('(SubPlan 1)'::text)") == ExpressionNames(
        functions=(("secret", "f"),), types=((None, "text"),)
    )


@pytest.mark.parametrize(
    ("text", "sequence"),
    [
        ("nextval('app_t_id_seq'::regclass)", (None, "app_t_id_seq")),
        ("nextval('app_t_id_seq'::bigint)", (None, "app_t_id_seq")),
        ("nextval('secret.s'::regclass)", ("secret", "s")),
        ("""nextval('"App"."S q"'::regclass)""", ("App", "S q")),
    ],
)
def test_nextval_of_a_literal_is_a_sequence(text: str, sequence: tuple[str | None, str]) -> None:
    names = parse_expression(text)

    assert names is not None
    assert names.sequences == (sequence,)
    assert names.functions == ()


def test_nextval_of_an_expression_is_a_function_call() -> None:
    names = parse_expression("nextval(('x'::text)::regclass)")

    assert names is not None
    assert names.functions == ((None, "nextval"),)
    assert names.sequences == ()


@pytest.mark.parametrize("text", ["app_t_id_seq", "public.s", '"A""b".c'])
def test_sequence_name_accepts_identifiers(text: str) -> None:
    assert sequence_name(text) is not None


@pytest.mark.parametrize("text", ["a.b.c", "x; SELECT 1", "x AS y", "(SELECT 1) s", ""])
def test_sequence_name_rejects_anything_else(text: str) -> None:
    assert sequence_name(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "not a (call",
        "a FROM secret.t",
        "a; SELECT 1",
        "1 UNION SELECT 2",
        "x::a.b.c",
        "nextval('a.b.c'::regclass)",
    ],
)
def test_unparsable_expression_is_none(text: str) -> None:
    assert parse_expression(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("t.a DESC NULLS LAST", ExpressionNames()),
        ("t.b USING <", ExpressionNames(operators=((None, "<"),))),
        ('(lower(t.name)) COLLATE "C"', ExpressionNames(functions=((None, "lower"),))),
        ("t.a USING OPERATOR(secret.<)", ExpressionNames(operators=(("secret", "<"),))),
        ("(SubPlan 1) DESC", ExpressionNames()),
    ],
)
def test_sort_key(text: str, expected: ExpressionNames) -> None:
    assert parse_sort_key(text) == expected


@pytest.mark.parametrize("text", ["", "a, b", "a LIMIT 1", "a DESC DESC"])
def test_unparsable_sort_key_is_none(text: str) -> None:
    assert parse_sort_key(text) is None


def test_table_function_call_is_parsed_as_a_from_item() -> None:
    names = parse_table_function(
        "XMLTABLE(('/r'::text) PASSING (d.x) COLUMNS id integer PATH ('@id'::text), s secret.t)"
    )

    assert names is not None
    assert ("secret", "t") in names.types


@pytest.mark.parametrize("text", ["", "secret.f()", "XMLTABLE(", "app_t"])
def test_unparsable_table_function_call_is_none(text: str) -> None:
    assert parse_table_function(text) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("a", ["a"]),
        (["a", "b"], ["a", "b"]),
        ([["a", "b"], ["c"]], ["a", "b", "c"]),
        ([], []),
        (1, None),
        (["a", 1], None),
        ({"a": "b"}, None),
    ],
)
def test_expression_texts(value: object, expected: list[str] | None) -> None:
    assert expression_texts(value) == expected


def test_sort_keys_and_table_functions_have_their_own_parsers() -> None:
    assert EXPRESSION_PARSERS["Sort Key"] is parse_sort_key
    assert EXPRESSION_PARSERS["Presorted Key"] is parse_sort_key
    assert EXPRESSION_PARSERS["Table Function Call"] is parse_table_function
    assert EXPRESSION_PARSERS["Output"] is parse_expression
