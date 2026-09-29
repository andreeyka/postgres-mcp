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
    await allowed_implementations(recorder, "public", operators=["===", "="], functions=["app_agg"])
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
