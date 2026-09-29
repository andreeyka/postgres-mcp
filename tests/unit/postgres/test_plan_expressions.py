"""Тесты разбора выражений плана EXPLAIN (VERBOSE): формы ruleutils PG 15–17 и имена, которые они называют."""

from collections.abc import Callable

import pglast
import pytest
from pglast.ast import Node
from pglast.visitors import Visitor

from postgres_fastmcp.postgres.security.plan_expressions import (
    EXPRESSION_PARSERS,
    ExpressionNames,
    expression_texts,
    parse_definition_expression,
    parse_expression,
    parse_function_body,
    parse_index_definition,
    parse_partition_key_definition,
    parse_rule_definition,
    parse_sort_key,
    parse_table_function,
    parse_trigger_definition,
    parser_operators,
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
        ("nextval('app_t_id_seq')", (None, "app_t_id_seq")),
        ("pg_catalog.nextval('app_t_id_seq')", (None, "app_t_id_seq")),
        ("nextval('secret.s')", ("secret", "s")),
        ("nextval('secret.s'::regclass)", ("secret", "s")),
        ("""nextval('"App"."S q"'::regclass)""", ("App", "S q")),
    ],
)
def test_nextval_of_a_literal_is_a_sequence(text: str, sequence: tuple[str | None, str]) -> None:
    names = parse_expression(text)

    assert names is not None
    assert names.sequences == (sequence,)
    assert names.functions == ()


@pytest.mark.parametrize(
    "text",
    ["nextval(('x'::text)::regclass)", "nextval('x'::text)", "nextval('x'::bigint)", "nextval(1)", "nextval('a', 'b')"],
)
def test_nextval_of_anything_but_a_literal_is_a_function_call(text: str) -> None:
    """Литерал без приведения (identity, NextValueExpr) или '...'::regclass (serial); прочее — вызов функции."""
    names = parse_expression(text)

    assert names is not None
    assert names.functions == ((None, "nextval"),)
    assert names.sequences == ()


def test_other_function_of_a_bare_literal_is_not_a_sequence() -> None:
    names = parse_expression("lower('app_t_id_seq')")

    assert names is not None
    assert names.functions == ((None, "lower"),)
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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('"PARTIAL public".f(t.a)', ExpressionNames(functions=(("PARTIAL public", "f"),))),
        ('NULL::"PARTIAL public".t', ExpressionNames(types=(("PARTIAL public", "t"),))),
        ('(t.a OPERATOR("PARTIAL public".+) t.b)', ExpressionNames(operators=(("PARTIAL public", "+"),))),
        ('"(SubPlan 1)".f(t.a)', ExpressionNames(functions=(("(SubPlan 1)", "f"),))),
        ('"x OVER (?)".f(t.a)', ExpressionNames(functions=(("x OVER (?)", "f"),))),
        ('"a""PARTIAL b".f(t.a)', ExpressionNames(functions=(('a"PARTIAL b', "f"),))),
        (
            """nextval('"PARTIAL public".s'::regclass)""",
            ExpressionNames(types=((None, "regclass"),), sequences=(("PARTIAL public", "s"),)),
        ),
        (
            """nextval('"PARTIAL app_s"'::regclass)""",
            ExpressionNames(types=((None, "regclass"),), sequences=((None, "PARTIAL app_s"),)),
        ),
    ],
)
def test_substitutions_do_not_touch_quoted_names(text: str, expected: ExpressionNames) -> None:
    """Замены ссылок планировщика не заходят в имена в кавычках и литералы: проверяется настоящее имя."""
    assert parse_expression(text) == expected


def test_literal_with_a_planner_reference_hides_nothing() -> None:
    names = parse_expression("secret.f('x''(SubPlan 1) secret.g()'::text)")

    assert names == ExpressionNames(functions=(("secret", "f"),), types=((None, "text"),))


def test_escape_string_keeps_its_boundaries() -> None:
    r"""E'...' с \' внутри: граница литерала не сдвигается, замены внутри него не идут."""
    names = parse_expression(r"""secret.f(E'\' "PARTIAL x".g('::text)""")

    assert names == ExpressionNames(functions=(("secret", "f"),), types=((None, "text"),))


@pytest.mark.parametrize(
    "text",
    [
        "f('unterminated",
        'f("unterminated',
        r"f(E'\')",
        "a -- FROM secret.t",
        "a /* FROM secret.t */",
        "(SELECT x FROM secret.t)",
        "(t.x IN (SELECT y FROM secret.t))",
        "EXISTS (SELECT 1)",
        "ARRAY(SELECT 1)",
        "$q$ PARTIAL $q$",
        "f('a\x00b')",
    ],
)
def test_unverifiable_expression_is_none(text: str) -> None:
    assert parse_expression(text) is None


@pytest.mark.parametrize(
    "text",
    ["(SELECT x FROM secret.t)", "t.a -- FROM secret.t", "(t.x IN (SELECT y FROM secret.t)) DESC", "t.a /* */"],
)
def test_sort_key_with_a_subquery_or_comment_is_none(text: str) -> None:
    assert parse_sort_key(text) is None


def test_comment_marks_inside_quotes_are_text() -> None:
    names = parse_expression("""f('-- /*'::text, "a--b".c)""")

    assert names == ExpressionNames(functions=((None, "f"),), types=((None, "text"),))


@pytest.mark.parametrize(
    "text", ["ONLY app_s", "app_s *", "app_s /* x */", "app_s -- x", "public . s", " app_s", "app_s\n"]
)
def test_sequence_name_accepts_only_what_regclassout_prints(text: str) -> None:
    assert sequence_name(text) is None


class _ParserOperators(Visitor):
    """parser_operators каждого узла дерева подряд."""

    def __init__(self) -> None:
        super().__init__()
        self.found: list[str] = []

    def visit(self, _ancestors: object, node: Node) -> None:
        self.found.extend(parser_operators(node))


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT 1 BETWEEN 0 AND 2", [">=", "<="]),
        ("SELECT 1 BETWEEN SYMMETRIC 0 AND 2", [">=", "<="]),
        ("SELECT 1 NOT BETWEEN 0 AND 2", ["<", ">"]),
        ("SELECT 1 NOT BETWEEN SYMMETRIC 0 AND 2", ["<", ">"]),
        ("SELECT CASE 1 WHEN 2 THEN 3 END", ["="]),
        ("SELECT * FROM a JOIN b USING (c)", ["="]),
        ("SELECT * FROM a LEFT JOIN b USING (c, d)", ["="]),
        ("SELECT * FROM a NATURAL JOIN b", ["="]),
        ("SELECT 1 IN (SELECT 1)", ["="]),
        ("SELECT 1 NOT IN (SELECT 1)", ["="]),
        # Оператор в имени узла или явный: разбор ничего не подставляет.
        ("SELECT 1 = ANY (SELECT 1)", []),
        ("SELECT 1 < ALL (SELECT 1)", []),
        ("SELECT 1 IN (1, 2)", []),
        ("SELECT NULLIF(1, 2)", []),
        ("SELECT 1 IS DISTINCT FROM 2", []),
        ("SELECT (1, 2) < (3, 4)", []),
        ("SELECT CASE WHEN true THEN 1 END", []),
        ("SELECT * FROM a JOIN b ON true", []),
        ("SELECT EXISTS (SELECT 1)", []),
        ("SELECT (SELECT 1)", []),
        ("SELECT ARRAY(SELECT 1)", []),
    ],
)
def test_parser_operators(sql: str, expected: list[str]) -> None:
    collector = _ParserOperators()
    collector(pglast.parse_sql(sql)[0].stmt)
    assert collector.found == expected


@pytest.mark.parametrize(
    ("text", "operators"),
    [
        ("CASE a WHEN 1 THEN 2 ELSE 3 END", ((None, "="),)),
        ("(a BETWEEN 1 AND 2)", ((None, ">="), (None, "<="))),
    ],
)
def test_expression_names_include_parser_operators(text: str, operators: tuple[tuple[None, str], ...]) -> None:
    names = parse_expression(text)
    assert names is not None
    assert names.operators == operators


def test_rule_names_include_the_equality_of_join_using() -> None:
    names = parse_rule_definition(
        'CREATE RULE "_RETURN" AS ON SELECT TO public.v DO INSTEAD SELECT a.c FROM (a JOIN b USING (c));'
    )
    assert names is not None
    assert names.operators == ((None, "="),)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("secret.valid(id)", ExpressionNames(functions=(("secret", "valid"),))),
        (
            "((VALUE)::integer > 0)",
            ExpressionNames(operators=((None, ">"),), types=(("pg_catalog", "int4"),)),
        ),
        (
            "nextval('app_t_id_seq'::regclass)",
            ExpressionNames(types=((None, "regclass"),), sequences=((None, "app_t_id_seq"),)),
        ),
        (
            "(EXISTS ( SELECT 1\n   FROM secret.t\n  WHERE (t.x = w.id)))",
            ExpressionNames(operators=((None, "="),), relations=(("secret", "t"),)),
        ),
        (
            "(id IN ( SELECT app_owners.id FROM app_owners))",
            ExpressionNames(operators=((None, "="),), relations=((None, "app_owners"),)),
        ),
        (
            "(id < ALL ( SELECT app_owners.id FROM app_owners))",
            ExpressionNames(operators=((None, "<"),), relations=((None, "app_owners"),)),
        ),
        (
            "((WITH x AS (SELECT 1 AS a) SELECT count(*) AS count FROM x) > 0)",
            ExpressionNames(functions=((None, "count"),), operators=((None, ">"),)),
        ),
    ],
)
def test_definition_expression_names(text: str, expected: ExpressionNames) -> None:
    """pg_get_expr: CHECK, DEFAULT, генерируемая колонка, CHECK домена (VALUE), USING/WITH CHECK политики.

    x IN (подзапрос) — = без имени в тексте (как у SQL агента и выражений плана).
    """
    assert parse_definition_expression(text) == expected


@pytest.mark.parametrize("text", [None, 42, "", "1; DROP TABLE x", "a.b.c.f(1)", "(SELECT 1 FROM db.s.t)"])
def test_unparsable_definition_expression_is_rejected(text: object) -> None:
    assert parse_definition_expression(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "CREATE TRIGGER w_tr BEFORE INSERT ON public.w FOR EACH ROW WHEN (secret.valid(new.id)) "
            "EXECUTE FUNCTION secret.audit('a')",
            ExpressionNames(functions=(("secret", "audit"), ("secret", "valid"))),
        ),
        (
            "CREATE TRIGGER t AFTER UPDATE ON public.app_t FOR EACH STATEMENT EXECUTE FUNCTION app_audit()",
            ExpressionNames(functions=((None, "app_audit"),)),
        ),
    ],
)
def test_trigger_definition_names(text: str, expected: ExpressionNames) -> None:
    """Функция триггера и имена WHEN; аргументы триггера — строковые константы."""
    assert parse_trigger_definition(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "CREATE INDEX w_expr ON public.w USING btree (lower(g), ((id + 1)), v secret.myops) WHERE secret.valid(id)",
            ExpressionNames(
                functions=((None, "lower"), ("secret", "valid")), operators=((None, "+"), ("secret", "myops"))
            ),
        ),
        ("CREATE UNIQUE INDEX i ON public.app_t USING btree (v text_pattern_ops)", ExpressionNames()),
    ],
)
def test_index_definition_names(text: str, expected: ExpressionNames) -> None:
    """Выражения ключей, предикат и класс операторов со схемой (без схемы он виден в search_path)."""
    assert parse_index_definition(text) == expected


@pytest.mark.parametrize(
    ("parse", "text"),
    [
        (parse_trigger_definition, "SELECT 1"),
        (parse_trigger_definition, None),
        (parse_index_definition, "CREATE TRIGGER t AFTER UPDATE ON t EXECUTE FUNCTION f()"),
        (parse_index_definition, "CREATE INDEX i ON t (x); CREATE INDEX j ON t (y)"),
    ],
)
def test_foreign_statement_text_is_rejected(parse: Callable[[object], ExpressionNames | None], text: object) -> None:
    assert parse(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "RANGE (((id + secret.boomi())))",
            ExpressionNames(functions=(("secret", "boomi"),), operators=((None, "+"),)),
        ),
        ("LIST (id)", ExpressionNames()),
        (
            'HASH (id, lower(v) COLLATE "C" secret.ops, w text_pattern_ops)',
            ExpressionNames(functions=((None, "lower"),), operators=(("secret", "ops"),)),
        ),
    ],
)
def test_partition_key_definition_names(text: str, expected: ExpressionNames) -> None:
    """pg_get_partkeydef: выражения ключей и класс операторов со схемой (collation пропускается)."""
    assert parse_partition_key_definition(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "RANGE (id); DROP TABLE x",
        "RANGE (id)) ; CREATE TABLE y (a int",
        "RANGE (id) WITH (fillfactor = 1)",
        "RANGE (id) TABLESPACE t",
        "RANGE (id) USING heap",
        "RANGE (a.b.c.f(id))",
    ],
)
def test_unparsable_partition_key_definition_is_rejected(text: object) -> None:
    assert parse_partition_key_definition(text) is None


@pytest.mark.parametrize(
    ("text", "atomic", "expected"),
    [
        (
            "SELECT count(*) FROM secret.accounts",
            False,
            ExpressionNames(functions=((None, "count"),), relations=(("secret", "accounts"),)),
        ),
        (
            "SELECT x FROM app_t WHERE id = $1; SELECT 1",
            False,
            ExpressionNames(operators=((None, "="),), relations=((None, "app_t"),)),
        ),
        ("", False, ExpressionNames()),
        (
            "WITH w AS (SELECT id FROM app_t) SELECT count(*) FROM w",
            False,
            ExpressionNames(functions=((None, "count"),), relations=((None, "app_t"),)),
        ),
        (
            "SELECT * FROM app_rows() WHERE id IN (SELECT id FROM app_t)",
            False,
            ExpressionNames(functions=((None, "app_rows"),), operators=((None, "="),), relations=((None, "app_t"),)),
        ),
        (
            "RETURN ((secret.valid($1))::integer + 1)",
            True,
            ExpressionNames(
                functions=(("secret", "valid"),), operators=((None, "+"),), types=(("pg_catalog", "int4"),)
            ),
        ),
        (
            "BEGIN ATOMIC\n SELECT t.x\n    FROM secret.t\n  LIMIT 1;\nEND",
            True,
            ExpressionNames(relations=(("secret", "t"),)),
        ),
        ("BEGIN ATOMIC\nEND", True, ExpressionNames()),
    ],
)
def test_function_body_names(text: str, atomic: bool, expected: ExpressionNames) -> None:
    """Тело prosrc (операторы через ;) и pg_get_function_sqlbody (BEGIN ATOMIC / RETURN)."""
    assert parse_function_body(text, atomic=atomic) == expected


@pytest.mark.parametrize(
    ("text", "atomic"),
    [
        (None, False),
        ("SELEC 1", False),
        ("INSERT INTO app_t VALUES (1)", False),
        ("SELECT 1; DELETE FROM app_t", False),
        ("WITH d AS (DELETE FROM app_t RETURNING id) SELECT count(*) FROM d", False),
        ("CREATE TABLE app_x (id int)", False),
        ("SELECT 1 INTO app_x", False),
        ("SELECT id FROM app_t FOR UPDATE", False),
        ("SELECT id FROM secret.a.t", False),
        ("BEGIN ATOMIC\n INSERT INTO app_t VALUES (1);\nEND", True),
        ("BEGIN ATOMIC\n UPDATE app_t SET id = 2;\nEND", True),
        ("BEGIN ATOMIC\n SELECT 1;\n CREATE TABLE app_x (id int);\nEND", True),
        ("SELECT 1", True),
        ("RETURN 1; SELECT 2", True),
    ],
)
def test_data_modifying_or_unparsable_body_is_rejected(text: object, atomic: bool) -> None:
    """Изменение данных в теле — свои цели записи, их путь записи не проверить; служебные команды — тоже."""
    assert parse_function_body(text, atomic=atomic) is None
