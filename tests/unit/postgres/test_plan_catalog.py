"""Тесты запросов каталога plan_check: полная квалификация имён и разбор ответов."""

import pglast
import pytest
from pglast.ast import A_Expr, FuncCall, Node, RangeVar, String, SubLink, TypeName
from pglast.visitors import Visitor

from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.postgres.security.plan_catalog import BuiltinTypeNames, pg_catalog_functions, row_types


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
    return recorder.sent


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
