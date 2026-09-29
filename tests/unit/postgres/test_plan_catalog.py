"""Тесты запросов каталога plan_check: полная квалификация имён и разбор ответов."""

import pglast
import pytest
from pglast.ast import A_Expr, FuncCall, Node, RangeVar, String, SubLink, TypeName
from pglast.visitors import Visitor

from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.postgres.security.plan_catalog import (
    ALLOWED_IMPLEMENTATIONS_SQL,
    DEFINITION_DEPENDENCIES_SQL,
    BuiltinTypeNames,
    allowed_implementations,
    pg_catalog_functions,
    row_types,
)


class _Recorder:
    """Исполнитель, который запоминает SQL и отвечает заготовленными строками."""

    def __init__(self, rows: list[RowResult] | None = None) -> None:
        self.rows = rows or []
        self.sent: list[str] = []

    async def __call__(self, sql: str) -> list[RowResult] | None:
        self.sent.append(sql)
        return self.rows


def _parts(names: list[Node] | tuple[Node, ...] | None) -> tuple[str | None, ...]:
    return tuple(part.sval if isinstance(part, String) else None for part in names or ())


class _Unqualified(Visitor):
    """Собирает имена, которые резолвятся по search_path: оператор, функция, тип или отношение без pg_catalog."""

    def __init__(self, ctes: frozenset[str]) -> None:
        super().__init__()
        self.ctes = ctes
        self.found: list[str] = []

    def _check(self, what: str, names: tuple[str | None, ...]) -> None:
        if names and names[0] != "pg_catalog":
            self.found.append(f"{what} {names}")

    def visit_A_Expr(self, _ancestors: object, node: A_Expr) -> None:  # noqa: N802
        self._check("operator", _parts(node.name))

    def visit_SubLink(self, _ancestors: object, node: SubLink) -> None:  # noqa: N802
        self._check("operator", _parts(node.operName))

    def visit_FuncCall(self, _ancestors: object, node: FuncCall) -> None:  # noqa: N802
        self._check("function", _parts(node.funcname))

    def visit_TypeName(self, _ancestors: object, node: TypeName) -> None:  # noqa: N802
        self._check("type", _parts(node.names))

    def visit_RangeVar(self, _ancestors: object, node: RangeVar) -> None:  # noqa: N802
        if node.schemaname != "pg_catalog" and node.relname not in self.ctes:
            self.found.append(f"relation {node.relname}")


def _unqualified_names(sql: str) -> list[str]:
    [statement] = pglast.parse_sql(sql)
    with_clause = getattr(statement.stmt, "withClause", None)
    ctes = frozenset(cte.ctename for cte in (with_clause.ctes if with_clause else ()))
    visitor = _Unqualified(ctes)
    visitor(statement)
    return visitor.found


async def _catalog_sql() -> list[str]:
    recorder = _Recorder()
    await BuiltinTypeNames().load(recorder)
    await pg_catalog_functions(recorder, ["current_setting", "my_fn"])
    await row_types(recorder, "public", ["users", "users_dom"])
    await allowed_implementations(
        recorder, "public", operators=["===", "="], functions=["app_agg"], types=['"app_t"'], relations=['"app_x"']
    )
    return [*recorder.sent, DEFINITION_DEPENDENCIES_SQL]


async def test_catalog_sql_resolves_nothing_through_the_search_path() -> None:
    """search_path = public: оператор public (например <>(oid, integer)) перехватил бы неквалифицированный."""
    for sql in await _catalog_sql():
        assert _unqualified_names(sql) == [], sql


async def test_row_types_follow_domains_and_arrays_to_the_relation() -> None:
    """Домен над строковым типом (typrelid = 0) и массив строкового типа ведут к той же таблице."""
    recorder = _Recorder()

    await row_types(recorder, "public", ["users_dom"])

    [sql] = recorder.sent
    assert "typbasetype" in sql
    assert "typelem" in sql


async def test_row_types_map_each_type_to_its_relation() -> None:
    recorder = _Recorder(
        [
            RowResult(cells={"name": "users", "relation_schema": "public", "relation_name": "users"}),
            RowResult(cells={"name": "users_dom", "relation_schema": "public", "relation_name": "users"}),
        ]
    )

    found = await row_types(recorder, "public", ["users", "users_dom", "status"])

    assert found == {"users": [("public", "users")], "users_dom": [("public", "users")]}


@pytest.mark.parametrize("sql", ["SELECT 1 WHERE a <> 0", "SELECT 1 WHERE a IN ('x')", "SELECT NULLIF(a, 0)"])
def test_the_qualification_check_sees_implicit_operators(sql: str) -> None:
    assert _unqualified_names(sql) != []


def test_definition_sql_skips_materialized_views() -> None:
    """Матвью читает уже скопированные данные: чтение не выполняет "_RETURN", определение проверять незачем."""
    assert "k.relkind OPERATOR(pg_catalog.<>) 'm'" in DEFINITION_DEPENDENCIES_SQL


def test_definition_sql_gates_non_select_rules_and_write_targets_on_a_dml_lock() -> None:
    """Правило не ON SELECT и путь записи — только у отношений с блокировкой DML (RowExclusiveLock и строже)."""
    assert "r.ev_type OPERATOR(pg_catalog.=) '1'" in DEFINITION_DEPENDENCIES_SQL
    assert DEFINITION_DEPENDENCIES_SQL.count("k.mode OPERATOR(pg_catalog.=) ANY (") == 2
    for mode in (
        "RowExclusiveLock",
        "ShareUpdateExclusiveLock",
        "ShareLock",
        "ShareRowExclusiveLock",
        "ExclusiveLock",
        "AccessExclusiveLock",
    ):
        assert f"'{mode}'" in DEFINITION_DEPENDENCIES_SQL
    assert "'AccessShareLock'" not in DEFINITION_DEPENDENCIES_SQL
    assert "'RowShareLock'" not in DEFINITION_DEPENDENCIES_SQL


def test_write_targets_include_descendants_and_cascading_references() -> None:
    """PREPARE блокирует только названную таблицу: секции и таблицы каскадных внешних ключей добавляет рекурсия."""
    for fragment in ("pg_catalog.pg_inherits", "i.inhparent", "f.confrelid", "f.confdeltype", "f.confupdtype"):
        assert fragment in DEFINITION_DEPENDENCIES_SQL


def test_write_path_objects_and_their_texts() -> None:
    for fragment in (
        "NOT g.tgisinternal",
        "g.tgenabled OPERATOR(pg_catalog.<>) 'D'",
        "pg_catalog.pg_get_triggerdef(g.oid)",
        "k.contype OPERATOR(pg_catalog.=) 'c'",
        "pg_catalog.pg_get_expr(k.conbin, k.conrelid)",
        "k.contypid",
        "pg_catalog.pg_get_expr(k.conbin, 0::pg_catalog.oid)",
        "pg_catalog.pg_get_expr(d.adbin, d.adrelid)",
        "x.indexprs IS NOT NULL OR x.indpred IS NOT NULL",
        "pg_catalog.pg_get_indexdef(x.oid)",
        "c.relrowsecurity",
        "(p.polqual), (p.polwithcheck)",
    ):
        assert fragment in DEFINITION_DEPENDENCIES_SQL


async def test_allowed_implementations_look_up_operators_and_aggregates_by_name() -> None:
    recorder = _Recorder()

    await allowed_implementations(recorder, "public", operators=["!!"], functions=["app_agg", "count"])

    [sql] = recorder.sent
    assert "'!!'" in sql
    assert "'app_agg'" in sql
    assert "'count'" in sql
    assert "nspname OPERATOR(pg_catalog.=) 'public'" in sql


async def test_allowed_implementations_accept_an_empty_side() -> None:
    """Только операторы или только функции: пустой массив имён — корректный SQL."""
    recorder = _Recorder()

    await allowed_implementations(recorder, "public", operators=[], functions=["app_agg"])

    [sql] = recorder.sent
    assert "ARRAY[]::pg_catalog.name[]" in sql


def test_allowed_implementations_cover_support_functions_and_the_sort_operator() -> None:
    for column in (
        "aggtransfn",
        "aggfinalfn",
        "aggcombinefn",
        "aggserialfn",
        "aggdeserialfn",
        "aggmtransfn",
        "aggminvtransfn",
        "aggmfinalfn",
        "aggsortop",
        "oprcode",
    ):
        assert column in ALLOWED_IMPLEMENTATIONS_SQL


def test_allowed_implementations_return_sql_bodies_with_their_settings() -> None:
    """Тело SQL-функции: prosrc или (BEGIN ATOMIC/RETURN) pg_get_function_sqlbody; proconfig — для search_path."""
    for fragment in (
        "'sql_body'",
        "'sql_atomic_body'",
        "pg_catalog.pg_get_function_sqlbody(p.oid)",
        "p.prosrc",
        "p.proconfig",
        "p.prosqlbody IS NOT NULL",
        "l.lanname OPERATOR(pg_catalog.=) 'sql'",
    ):
        assert fragment in ALLOWED_IMPLEMENTATIONS_SQL


def test_allowed_implementations_return_argument_defaults_of_any_language() -> None:
    """Умолчания аргументов: для каждой найденной функции любого языка, текст — pg_get_expr(proargdefaults)."""
    for fragment in (
        "'argument_defaults'",
        "pg_catalog.pg_get_expr(p.proargdefaults, 0::pg_catalog.oid)",
        "p.proargdefaults IS NOT NULL",
    ):
        assert fragment in ALLOWED_IMPLEMENTATIONS_SQL
    defaults = ALLOWED_IMPLEMENTATIONS_SQL[ALLOWED_IMPLEMENTATIONS_SQL.index("'argument_defaults'") :]
    assert "lanname" not in defaults[: defaults.index("UNION ALL")]


def test_allowed_implementations_return_non_sql_functions_except_aggregates_and_extension_members() -> None:
    """Функции public не на sql: язык — в definition; агрегаты (prolang internal) и члены расширений не в счёт."""
    rows = ALLOWED_IMPLEMENTATIONS_SQL[ALLOWED_IMPLEMENTATIONS_SQL.index("'non_sql_function'") :]
    rows = rows[: rows.index("UNION ALL")]
    for fragment in (
        "l.lanname::pg_catalog.text",
        "l.lanname OPERATOR(pg_catalog.<>) 'sql'",
        "p.prokind OPERATOR(pg_catalog.<>) 'a'",
        "e.classid OPERATOR(pg_catalog.=) 'pg_catalog.pg_proc'::pg_catalog.regclass::pg_catalog.oid "
        "AND e.objid OPERATOR(pg_catalog.=) p.oid",
    ):
        assert fragment in rows


def test_allowed_implementations_return_planner_support_functions() -> None:
    """Опорная функция планировщика (prosupport) — строка type_function; у членов расширений — нет."""
    assert "p.prokind, p.prosupport" in ALLOWED_IMPLEMENTATIONS_SQL
    support = ALLOWED_IMPLEMENTATIONS_SQL[ALLOWED_IMPLEMENTATIONS_SQL.index("p.prosupport::pg_catalog.oid") :]
    assert "e.objid OPERATOR(pg_catalog.=) p.oid" in support[: support.index("UNION ALL")]


class _Sources(Visitor):
    """Имена отношений и CTE (RangeVar) в тексте: откуда CTE берёт строки."""

    def __init__(self) -> None:
        super().__init__()
        self.names: set[str] = set()

    def visit_RangeVar(self, _ancestors: object, node: RangeVar) -> None:  # noqa: N802
        self.names.add(node.relname if node.schemaname is None else f"{node.schemaname}.{node.relname}")


def _cte_sources(name: str) -> set[str]:
    """Отношения и CTE, которые читает CTE name в DEFINITION_DEPENDENCIES_SQL."""
    [statement] = pglast.parse_sql(DEFINITION_DEPENDENCIES_SQL)
    [cte] = [cte for cte in statement.stmt.withClause.ctes if cte.ctename == name]
    visitor = _Sources()
    visitor(cte.ctequery)
    return visitor.names


@pytest.mark.parametrize("cte", ["checks", "indexes", "partition_keys", "stats"])
def test_definitions_folded_on_select_are_read_for_every_locked_relation_and_its_descendants(cte: str) -> None:
    """CHECK потомков, индексы, ключи секционирования и статистику планировщик сворачивает и для SELECT."""
    assert "relation_set" in _cte_sources(cte)
    assert {"locked", "target_set", "pg_catalog.pg_inherits"} <= _cte_sources("relations")


def test_policies_are_read_for_every_locked_relation_and_write_target() -> None:
    """Политику RLS переписчик подставляет и в SELECT: не только цели DML."""
    assert {"locked", "target_set", "pg_catalog.pg_policy"} <= _cte_sources("policies")


@pytest.mark.parametrize("cte", ["triggers", "defaults", "column_types"])
def test_write_time_definitions_stay_on_write_targets(cte: str) -> None:
    """Триггеры, умолчания и домены колонок при чтении не вычисляются."""
    sources = _cte_sources(cte)
    assert "target_set" in sources
    assert "relation_set" not in sources
    assert "locked" not in sources


def test_domain_defaults_partition_keys_and_statistics_and_their_texts() -> None:
    for fragment in (
        "ty.typdefaultbin IS NOT NULL",
        "pg_catalog.pg_get_expr(ty.typdefaultbin, 0::pg_catalog.oid)",
        "'pg_catalog.pg_type'::pg_catalog.regclass::pg_catalog.oid, ty.oid, NULL::pg_catalog.int4 FROM domain_defaults",
        "c.relkind OPERATOR(pg_catalog.=) 'p'",
        "pg_catalog.pg_get_partkeydef(c.oid)",
        "c.oid, 0::pg_catalog.int4 FROM partition_keys",
        "(o.objsubid IS NULL OR d.objsubid OPERATOR(pg_catalog.=) o.objsubid)",
        "s.stxexprs IS NOT NULL",
        "pg_catalog.pg_get_statisticsobjdef_expressions(s.oid)",
    ):
        assert fragment in DEFINITION_DEPENDENCIES_SQL
    assert {"column_types"} <= _cte_sources("type_set")
    assert "type_set" in _cte_sources("domain_defaults")


_BOTH_QUERIES = pytest.mark.parametrize(
    "sql", [ALLOWED_IMPLEMENTATIONS_SQL, DEFINITION_DEPENDENCIES_SQL], ids=["implementations", "definitions"]
)


@_BOTH_QUERIES
def test_type_machinery_covers_io_casts_operator_classes_domains_and_ranges(sql: str) -> None:
    """Всё, что Postgres вызывает для значения типа без имени функции в тексте (plan_catalog._type_machinery)."""
    for fragment in (
        "(t.typinput), (t.typoutput), (t.typreceive), (t.typsend), (t.typmodin), (t.typmodout), (t.typanalyze), "
        "(t.typsubscript)",
        "(r.rngcanonical), (r.rngsubdiff)",
        "r.rngsubopc",
        "pg_catalog.pg_amproc",
        "pg_catalog.pg_amop",
        "(o.oprrest), (o.oprjoin)",
        "k.castsource",
        "k.casttarget",
        "k.castfunc",
        "k.contypid",
    ):
        assert fragment in sql


@_BOTH_QUERIES
def test_type_closure_follows_what_calls_nested_machinery(sql: str) -> None:
    """Домен -> база, массив -> элемент, составной -> атрибуты, диапазон -> подтип, мультидиапазон -> диапазон.

    Типы и семейства операторов pg_catalog не раскрываются: встроенная машинерия.
    """
    for fragment in (
        "SELECT t.typbasetype UNION ALL SELECT t.typelem",
        "a.attrelid OPERATOR(pg_catalog.=) t.typrelid",
        "r.rngsubtype",
        "r.rngmultitypid OPERATOR(pg_catalog.=) t.oid",
        "t.typnamespace OPERATOR(pg_catalog.<>) 'pg_catalog'::pg_catalog.regnamespace::pg_catalog.oid",
        "f.opfnamespace OPERATOR(pg_catalog.<>) 'pg_catalog'::pg_catalog.regnamespace::pg_catalog.oid",
    ):
        assert fragment in sql


@_BOTH_QUERIES
def test_type_machinery_trusts_extensions_by_the_owning_object(sql: str) -> None:
    """Машинерию, которую поставил скрипт расширения (deptype 'e'), определяет владелец: тип, семейство операторов,
    строка pg_cast. Членство самой функции или оператора не в счёт: приведение DBA над функцией расширения
    проверяется по обычным правилам."""
    assert "e.deptype OPERATOR(pg_catalog.=) 'e'" in sql
    for owner in (
        "'pg_catalog.pg_type'::pg_catalog.regclass::pg_catalog.oid AND e.objid OPERATOR(pg_catalog.=) m.oid",
        "'pg_catalog.pg_opfamily'::pg_catalog.regclass::pg_catalog.oid AND e.objid OPERATOR(pg_catalog.=) y.oid",
        "'pg_catalog.pg_cast'::pg_catalog.regclass::pg_catalog.oid AND e.objid OPERATOR(pg_catalog.=) k.oid",
    ):
        assert f"e.classid OPERATOR(pg_catalog.=) {owner}" in sql
    # Строки функций не на sql (запрос реализаций) доверяют члену расширения по самой функции — это не машинерия.
    machinery = sql[sql.index("type_seed_set(oids)") : sql.rindex("k.conbin IS NOT NULL)")]
    for member in ("'pg_catalog.pg_proc'", "'pg_catalog.pg_operator'"):
        assert f"e.classid OPERATOR(pg_catalog.=) {member}" not in machinery


@_BOTH_QUERIES
def test_type_closure_follows_implicit_binary_coercible_casts(sql: str) -> None:
    """Класс операторов по умолчанию берётся и у типа, к которому значение неявно двоично приводится; такие
    приведения от типов pg_catalog — семена любого запроса."""
    assert (
        "SELECT k.casttarget FROM pg_catalog.pg_cast k WHERE k.castsource OPERATOR(pg_catalog.=) t.oid "
        "AND k.castmethod OPERATOR(pg_catalog.=) 'b' AND k.castcontext OPERATOR(pg_catalog.=) 'i'"
    ) in sql
    assert "ks.typnamespace OPERATOR(pg_catalog.=) 'pg_catalog'::pg_catalog.regnamespace::pg_catalog.oid" in sql
    assert "kt.typnamespace OPERATOR(pg_catalog.<>) 'pg_catalog'::pg_catalog.regnamespace::pg_catalog.oid" in sql


@_BOTH_QUERIES
def test_binary_casts_of_builtin_types_reach_only_default_btree_and_hash_families(sql: str) -> None:
    """От типа pg_catalog — только семейства классов по умолчанию btree и hash типа-цели и только для метода, у
    которого у источника нет своего класса по умолчанию; тип-цель не становится семенем (ввод-вывод, приведения,
    CHECK двоичное приведение не вызывает). Строки несут текст приведения (origin)."""
    type_closure = sql[sql.index("type_closure(oid) AS (") : sql.index("machinery_set(oids) AS (")]
    assert "ks.typnamespace" not in type_closure
    for fragment in (
        "oc.opcintype OPERATOR(pg_catalog.=) k.casttarget AND oc.opcdefault",
        "am.amname OPERATOR(pg_catalog.=) ANY (ARRAY['btree', 'hash']::pg_catalog.name[])",
        "NOT EXISTS (SELECT FROM pg_catalog.pg_opclass so WHERE so.opcintype OPERATOR(pg_catalog.=) k.castsource "
        "AND so.opcmethod OPERATOR(pg_catalog.=) oc.opcmethod AND so.opcdefault)",
        "pg_catalog.format_type(k.castsource, NULL::pg_catalog.int4), ' -> '",
        "m.origin",
    ):
        assert fragment in sql


@_BOTH_QUERIES
def test_type_machinery_is_seeded_by_row_types_of_relations(sql: str) -> None:
    """Ссылка на всю строку (r::int, abs(r)) вызывает приведение строкового типа отношения (pg_class.reltype)."""
    assert "SELECT c.reltype FROM" in sql


async def test_allowed_implementations_seed_types_and_relation_columns() -> None:
    """Имена SQL агента разрешаются по search_path, как их разрешит PREPARE; к ним — типы найденных функций."""
    recorder = _Recorder()

    await allowed_implementations(
        recorder, "public", operators=[], functions=[], types=['"app_t"'], relations=['"public"."app_x"']
    )

    [sql] = recorder.sent
    assert "pg_catalog.to_regtype(n.name)" in sql
    assert "pg_catalog.to_regclass(n.name)" in sql
    assert """ARRAY['"app_t"']::pg_catalog.text[]""" in sql
    assert """ARRAY['"public"."app_x"']::pg_catalog.text[]""" in sql
    for column in ("p.prorettype", "p.proargtypes", "p.proallargtypes", "o.oprleft", "o.oprright", "o.oprresult"):
        assert column in sql
    assert "a.aggtranstype" in sql


async def test_allowed_implementations_default_to_no_seeds() -> None:
    recorder = _Recorder()

    await allowed_implementations(recorder, "public", operators=["="], functions=[])

    [sql] = recorder.sent
    assert sql.count("ARRAY[]::pg_catalog.text[]") == 3


def test_definition_machinery_is_seeded_by_locked_relations_and_dependency_types() -> None:
    """Колонки заблокированных отношений, целей DML и их потомков, типы из pg_depend определений."""
    assert {"relation_set", "types", "pg_catalog.pg_attribute"} <= _cte_sources("type_seed_set")
