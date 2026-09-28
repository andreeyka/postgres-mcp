# Проверка по плану в basic — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** С настройкой `plan_check=true` basic отклоняет SQL агента, план которого читает отношения вне `public`/префикса или функции чужих схем — например, через представление в `public` поверх `secret.accounts`.

**Architecture:** Новый модуль `postgres/security/plan_guard.py`: `PlanGuard.check(query)` разбирает запрос pglast, для каждого планируемого оператора выполняет `EXPLAIN (VERBOSE, FORMAT JSON[, GENERIC_PLAN])` через переданный исполнитель и обходит дерево плана. `SafeSqlExecutor._guarded` вызывает его после валидатора и до выполнения, если `SafeSqlConfig.plan_check` и `allowed_schema` заданы; EXPLAIN идёт через делегата с тем же `SET LOCAL`, в read-only транзакции. `DatabaseConfig.plan_check` (по умолчанию `false`) доходит только до исполнителей basic. Валидатор, full и канал сервера (`CatalogSqlExecutor`) не меняются. Спека: `docs/superpowers/specs/2026-09-28-basic-followups-design.md`, §4 (PR 3).

**Tech Stack:** Python 3.12, uv, pglast v8.2 (`pglast.stream.RawStream`), psycopg 3.3, pytest (asyncio auto).

## Spec corrections

- `InitPlan`/`SubPlan` в JSON-плане — не отдельные ключи, а элементы `Plans` с `"Parent Relationship": "InitPlan"|"SubPlan"`; у `ModifyTable` на нескольких таблицах отношения лежат в `"Target Tables"`. `PlanGuard` обходит все вложенные словари и списки плана, а не только `Plans`, — ни одна форма не выпадает.
- `Function Name` (со `Schema` при `VERBOSE`) бывает только у узлов `Function Scan` — функций во `FROM`. Функция в выражении (`SELECT secret.f(x)` внутри представления) в ключах плана не видна, только в тексте `Output`. Проверка функций покрывает табличные функции; функции в выражениях представлений — к §4.3 «Что не покрывается». В SQL агента функции чужих схем и так отклоняет валидатор (список разрешённых функций по имени).
- Опция `generic_plan` у EXPLAIN может иметь значение (`generic_plan false`, `generic_plan 0` — pglast отдаёт `String`/`Integer`): переносится только включённая опция.
- Следствие правила «схема отношения — ровно `allowed_schema`»: с `plan_check=true` запросы к `information_schema.*` отклоняются (представления читают `pg_catalog`). Это совпадает с намерением строгого режима «только `public`»; README это называет.
- Отношение без `Schema` в плане (не бывает при `VERBOSE`, но формат не гарантирован) — отказ с `'?.<имя>'` в тексте ошибки: проверка закрыта по умолчанию.
- Для Task 2: в префиксе исполнителя EXPLAIN у `PlanGuard` закрепить `SET LOCAL standard_conforming_strings = on` — объясняемый текст получен deparse через `RawStream`, который это предполагает.

## Global Constraints

- Всё, что видит агент или внешняя система (ошибки, логи, коммиты, описания тулов), — на английском; docstring и комментарии — по-русски. README — по-русски.
- Нет `from __future__ import annotations`.
- Ломающие изменения разрешены; никаких шимов, deprecation-предупреждений и упоминаний старого поведения в коде. Ломающие изменения — только в заметках к PR (спека §5).
- `shared/` — нижний слой: `shared/errors.py` не импортирует `postgres/`.
- Безопасность: проверка по плану не ослабляет ни одного правила валидатора (`QueryValidator` не меняется и вызывается первым); full и канал сервера (`CatalogSqlExecutor`) не затрагиваются.
- Перед каждым коммитом: `uv run ruff check --fix --show-fixes`, `uv run mypy src/`, `uv run ruff format`; затем `uv run ruff check .` без ошибок.
- Юнит-тесты: `uv run pytest tests/unit -q`. Интеграция: `uv run pytest tests/integration -q` — должна собираться; локально пропускается (нет Docker), в CI — Postgres 15/16 с hypopg. Сверять интеграционные тесты статически с реальным кодом.
- Коммиты: `type(scope): message`, повелительное наклонение, английский.
- **Никогда не запускать `git config` и `git stash`.** Не пушить.
- `_run_concurrently` в `domains/catalog/tables.py` не заменять на TaskGroup.
- Ветка: `claude/basic-plan-check`, создать от вершины `claude/connection-options` перед Task 1: `git switch claude/connection-options && git switch -c claude/basic-plan-check`.

---

### Task 1: `PlanAccessError` и `PlanGuard`

**Files:**
- Modify: `src/postgres_fastmcp/shared/errors.py`
- Create: `src/postgres_fastmcp/postgres/security/plan_guard.py`
- Test: `tests/unit/postgres/test_plan_guard.py` (create), `tests/unit/shared/test_errors.py`

**Interfaces:**
- Produces (in `postgres_fastmcp.shared.errors`): `PlanAccessError(kind: str, qualified_name: str)` — `UserFacingError`, attributes `kind`, `qualified_name`; message `Access to {kind} '{qualified_name}' is not allowed in basic mode: the query reaches it through a view, rule or function. Only tables in 'public' are permitted.`
- Produces (in `postgres_fastmcp.postgres.security.plan_guard`):
  - `ExplainRunner = Callable[[str], Awaitable[list[RowResult] | None]]` — runs one full `EXPLAIN ...` statement and returns its rows (cell `"QUERY PLAN"`).
  - `RELATION_KIND = "relation"`, `FUNCTION_KIND = "function"`.
  - `class PlanGuard: __init__(self, explain: ExplainRunner, *, allowed_schema: str, table_prefix: str | None)`; `async def check(self, query: str) -> None` — raises `PlanAccessError`; database errors of the EXPLAIN propagate unchanged.
- Rules: plannable = `SelectStmt`, `InsertStmt`, `UpdateStmt`, `DeleteStmt`; `ExplainStmt` → its `query` (carrying an enabled `generic_plan`); `DeclareCursorStmt` → its `query`; everything else is skipped. EXPLAIN text = `EXPLAIN (VERBOSE, FORMAT JSON) <RawStream()(stmt)>` or `EXPLAIN (VERBOSE, FORMAT JSON, GENERIC_PLAN) ...`. Relation: `Schema == allowed_schema`, name not `pg_*`/`_pg_*`/`hypopg*` (`is_system_relation_name`), with prefix — name starts with it (case-insensitive). Function: `Schema` in (`pg_catalog`, `allowed_schema`).
- Facts checked with the installed pglast v8.2: `RawStream()(node)` drops the `/* tag */` comment and keeps quoting (`'O''Brien'`, `"Mixed"`); `ExplainStmt.options` holds `DefElem(defname='generic_plan', arg=None|String|Integer)`; `ExplainStmt.query` and `DeclareCursorStmt.query` are the nested statements (`Node | None`).

- [ ] **Step 1: Write the failing tests**

1. Create `tests/unit/postgres/test_plan_guard.py`:

```python
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
```

2. `tests/unit/shared/test_errors.py`: add to `_SAMPLES`

```python
    "PlanAccessError": lambda: errors.PlanAccessError("relation", "secret.accounts"),
```

and to the `test_correctable_error_ends_with_hint` parameters

```python
        ("PlanAccessError", "Only tables in 'public' are permitted."),
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/postgres/test_plan_guard.py tests/unit/shared/test_errors.py -q`
Expected: FAIL — collection error `ModuleNotFoundError: No module named 'postgres_fastmcp.postgres.security.plan_guard'`; `AttributeError: module 'postgres_fastmcp.shared.errors' has no attribute 'PlanAccessError'`.

- [ ] **Step 3: Add the error**

In `src/postgres_fastmcp/shared/errors.py`, after `SchemataTableAccessError`:

```python
class PlanAccessError(UserFacingError):
    """План запроса basic читает отношение или функцию вне разрешённого (проверка по плану, plan_check)."""

    def __init__(self, kind: str, qualified_name: str) -> None:
        """Инициализация с видом объекта и его полным именем.

        Args:
            kind: Вид объекта из плана: relation или function.
            qualified_name: Имя со схемой из плана (schema.name).
        """
        message = (
            f"Access to {kind} '{qualified_name}' is not allowed in basic mode: the query reaches it through "
            "a view, rule or function. Only tables in 'public' are permitted."
        )
        super().__init__(message)
        self.kind = kind
        self.qualified_name = qualified_name
```

- [ ] **Step 4: Write `plan_guard.py`**

Create `src/postgres_fastmcp/postgres/security/plan_guard.py`:

```python
"""Проверка по плану в basic: отношения и функции, до которых запрос доходит через представления, правила и SQL-функции.

Валидатор видит только текст запроса; представление в public поверх чужой схемы он пропускает. PlanGuard
строит план каждого оператора (EXPLAIN без ANALYZE ничего не выполняет) и проверяет, что читают его узлы.
"""

import json
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

import pglast
from pglast.ast import DeclareCursorStmt, DefElem, DeleteStmt, ExplainStmt, InsertStmt, Node, SelectStmt, UpdateStmt
from pglast.stream import RawStream

from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.postgres.security.schema_guard import is_system_relation_name
from postgres_fastmcp.shared.errors import PlanAccessError


# Выполняет один оператор EXPLAIN целиком и возвращает его строки (ячейка "QUERY PLAN").
ExplainRunner = Callable[[str], Awaitable[list[RowResult] | None]]

RELATION_KIND = "relation"
FUNCTION_KIND = "function"

# Операторы, у которых есть план; EXPLAIN и DECLARE разворачиваются до вложенного запроса.
# SHOW, PREPARE, DEALLOCATE, FETCH, CLOSE, CREATE EXTENSION плана не имеют (EXECUTE запрещён валидатором).
_PLANNABLE_TYPES = (SelectStmt, InsertStmt, UpdateStmt, DeleteStmt)

# Значения, которыми опцию EXPLAIN выключают явно: generic_plan false / off / 0 / no.
_DISABLED_OPTION_VALUES = frozenset({"false", "off", "0", "no"})

# Встроенные функции; функции allowed_schema (расширения в public) тоже допустимы.
_BUILTIN_FUNCTION_SCHEMA = "pg_catalog"


def _option_enabled(arg: Node | None) -> bool:
    """Опция EXPLAIN включена: без значения или со значением, отличным от false/off/0/no."""
    if arg is None:
        return True
    value = getattr(arg, "sval", None)
    if value is None:
        value = getattr(arg, "ival", None)
    if value is None:
        value = getattr(arg, "boolval", None)
    return str(value).lower() not in _DISABLED_OPTION_VALUES


def _generic_plan(explain: ExplainStmt) -> bool:
    """У EXPLAIN агента включена опция generic_plan: без неё план запроса с $1 не построить."""
    return any(
        isinstance(option, DefElem) and option.defname == "generic_plan" and _option_enabled(option.arg)
        for option in explain.options or ()
    )


def _plannable(node: Node | None, *, generic: bool = False) -> tuple[Node, bool] | None:
    """Оператор, план которого проверяется, и нужен ли GENERIC_PLAN; None — у оператора нет плана."""
    if isinstance(node, _PLANNABLE_TYPES):
        return node, generic
    if isinstance(node, ExplainStmt):
        return _plannable(node.query, generic=_generic_plan(node))
    if isinstance(node, DeclareCursorStmt):
        return _plannable(node.query, generic=generic)
    return None


def _plan_nodes(value: object) -> Iterator[dict[str, Any]]:
    """Все словари документа плана: Plans (в том числе InitPlan/SubPlan), Target Tables и прочие вложения."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _plan_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _plan_nodes(child)


def _plan_document(rows: list[RowResult] | None) -> object:
    """JSON-документ EXPLAIN (FORMAT JSON): psycopg отдаёт разобранный список, текст разбирается здесь."""
    value = rows[0].cells.get("QUERY PLAN") if rows else None
    return json.loads(value) if isinstance(value, str) else value


class PlanGuard:
    """Проверяет план каждого оператора: отношения — только allowed_schema (с префиксом), функции — pg_catalog и она."""

    def __init__(self, explain: ExplainRunner, *, allowed_schema: str, table_prefix: str | None) -> None:
        """Инициализация с исполнителем EXPLAIN и правилами basic.

        Args:
            explain: Выполняет оператор EXPLAIN (тот же SET LOCAL, read-only) и возвращает строки.
            allowed_schema: Единственная схема отношений плана (public).
            table_prefix: Если задан, имена отношений плана должны начинаться с него (без учёта регистра).
        """
        self._explain = explain
        self._allowed_schema = allowed_schema
        self._table_prefix = table_prefix.lower() if table_prefix else None

    async def check(self, query: str) -> None:
        """Построить план каждого планируемого оператора запроса и проверить его узлы.

        Запрос уже прошёл валидатор, поэтому разбирается без ошибок. Ошибка планирования (отношения нет)
        приходит из исполнителя как ошибка Postgres — та же, что дало бы выполнение.

        Args:
            query: SQL агента после валидации (с тегом-комментарием).

        Raises:
            PlanAccessError: План читает отношение или функцию вне разрешённого.
        """
        for raw in pglast.parse_sql(query):
            target = _plannable(raw.stmt)
            if target is None:
                continue
            statement, generic = target
            options = "VERBOSE, FORMAT JSON, GENERIC_PLAN" if generic else "VERBOSE, FORMAT JSON"
            rows = await self._explain(f"EXPLAIN ({options}) {RawStream()(statement)}")
            self._check_plan(_plan_document(rows))

    def _check_plan(self, plan: object) -> None:
        """Проверить каждый узел плана, где есть отношение или функция."""
        for node in _plan_nodes(plan):
            if "Relation Name" in node:
                self._check_relation(node.get("Schema"), str(node["Relation Name"]))
            if "Function Name" in node:
                self._check_function(node.get("Schema"), str(node["Function Name"]))

    def _check_relation(self, schema: str | None, name: str) -> None:
        """Отношение плана: ровно allowed_schema, не системное, с префиксом, если он задан."""
        outside_prefix = self._table_prefix is not None and not name.lower().startswith(self._table_prefix)
        if schema != self._allowed_schema or is_system_relation_name(name) or outside_prefix:
            raise PlanAccessError(RELATION_KIND, f"{schema or '?'}.{name}")

    def _check_function(self, schema: str | None, name: str) -> None:
        """Табличная функция плана: встроенная или из allowed_schema."""
        if schema not in (_BUILTIN_FUNCTION_SCHEMA, self._allowed_schema):
            raise PlanAccessError(FUNCTION_KIND, f"{schema or '?'}.{name}")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/postgres/test_plan_guard.py tests/unit/shared/test_errors.py -q`
Expected: PASS.

Run: `uv run pytest tests/unit -q`
Expected: PASS (nothing uses `PlanGuard` yet).

- [ ] **Step 6: Lint, type-check, commit**

```bash
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
git add src/postgres_fastmcp/shared/errors.py src/postgres_fastmcp/postgres/security/plan_guard.py tests/unit/postgres/test_plan_guard.py tests/unit/shared/test_errors.py
git commit -m "feat(security): add a plan guard that checks relations and functions of a query plan"
```

---

### Task 2: `plan_check` — настройка и исполнитель basic

**Files:**
- Modify: `src/postgres_fastmcp/app/config/database.py` (`DatabaseConfig.plan_check`)
- Modify: `src/postgres_fastmcp/postgres/security/driver.py` (`SafeSqlConfig.plan_check`, `SafeSqlExecutor`)
- Modify: `src/postgres_fastmcp/domains/db_access.py` (`DatabaseConfigPort.plan_check`, `DbAccessService._executor`)
- Test: `tests/unit/postgres/test_safe_sql_executor.py`, `tests/unit/domains/test_db_access.py`, `tests/unit/app/test_settings.py`

**Interfaces:**
- Consumes (Task 1): `PlanGuard(explain, *, allowed_schema, table_prefix)`, `PlanGuard.check(query)`, `PlanAccessError`.
- Consumes (PR 1): `DbAccessService.catalog_driver`.
- Produces: `DatabaseConfig.plan_check: bool = False` (env `MCP_DATABASE_PLAN_CHECK`); `DatabaseConfigPort.plan_check: bool`; `SafeSqlConfig.plan_check: bool = False`; `SafeSqlExecutor._plan_guard: PlanGuard | None` — set only when `config.plan_check and config.allowed_schema`; `DbAccessService` passes `plan_check=basic and config.plan_check`.
- `SafeSqlExecutor` flow after the change: tag → `validator.validate(query)` → (inside the client-timeout block) `PlanGuard.check(query)` → `_run(SET LOCAL ... + query)`. The guard's EXPLAIN runs as `_run(SET LOCAL ... + "/* <tag> */ EXPLAIN (...) ...", delegate.execute, readonly=True)`: same `statement_timeout`/`search_path`, same `QueryCanceled` → `QueryTimeoutError`/`QueryCancelledError` mapping. `_run` gains keyword `readonly: bool`.
- `CatalogSqlExecutor` builds `SafeSqlConfig` without `plan_check` → no guard; it is not modified.

- [ ] **Step 1: Write the failing tests**

1. `tests/unit/postgres/test_safe_sql_executor.py` (extend the errors import with `PlanAccessError`), append:

```python
def _plan_rows(schema: str, relation: str) -> list[RowResult]:
    plan = {"Node Type": "Seq Scan", "Relation Name": relation, "Schema": schema}
    return [RowResult(cells={"QUERY PLAN": [{"Plan": plan}]})]


def _basic_executor(delegate: MagicMock, *, plan_check: bool = True) -> SafeSqlExecutor:
    config = SafeSqlConfig(
        query_tag="t",
        timeout=5,
        allowed_schema="public",
        read_only=False,
        table_prefix="app_",
        plan_check=plan_check,
    )
    validator = QueryValidator(read_only=False, allowed_schema="public", table_prefix="app_")
    return _make_executor(delegate, validator=validator, config=config)


class TestSafeSqlExecutorPlanCheck:
    """plan_check: EXPLAIN (VERBOSE) через делегата после валидации и до выполнения, только с allowed_schema."""

    async def test_plan_is_checked_before_execution(self) -> None:
        delegate = MagicMock()
        delegate.execute = AsyncMock(side_effect=[_plan_rows("public", "app_t"), [RowResult(cells={"x": 1})]])

        result = await _basic_executor(delegate).execute("SELECT * FROM app_t")

        assert result == [RowResult(cells={"x": 1})]
        explain_call, run_call = delegate.execute.await_args_list
        assert explain_call.args[0] == (
            "SET LOCAL statement_timeout = 5000; SET LOCAL search_path = public; "
            "/* t */ EXPLAIN (VERBOSE, FORMAT JSON) SELECT * FROM app_t"
        )
        assert explain_call.kwargs["readonly"] is True
        assert run_call.args[0] == (
            "SET LOCAL statement_timeout = 5000; SET LOCAL search_path = public; /* t */ SELECT * FROM app_t"
        )
        assert run_call.kwargs["readonly"] is False

    async def test_plan_violation_stops_execution(self) -> None:
        delegate = MagicMock()
        delegate.execute = AsyncMock(return_value=_plan_rows("secret", "accounts"))

        with pytest.raises(PlanAccessError, match=r"secret\.accounts"):
            await _basic_executor(delegate).execute("SELECT * FROM app_secret_view")

        delegate.execute.assert_awaited_once()

    async def test_execute_statement_is_checked_too(self) -> None:
        delegate = MagicMock()
        delegate.execute = AsyncMock(return_value=_plan_rows("secret", "accounts"))
        delegate.execute_statement = AsyncMock()

        with pytest.raises(PlanAccessError):
            await _basic_executor(delegate).execute_statement("UPDATE app_secret_view SET token = 'x'")

        delegate.execute_statement.assert_not_awaited()

    async def test_validator_runs_before_the_plan_check(self) -> None:
        """Проверка по плану не ослабляет валидатор: отклонённый им запрос не доходит до EXPLAIN."""
        delegate = MagicMock()
        delegate.execute = AsyncMock()

        with pytest.raises(SchemaNotAllowedError):
            await _basic_executor(delegate).execute("SELECT * FROM secret.accounts")

        delegate.execute.assert_not_awaited()

    async def test_plan_check_off_sends_no_explain(self) -> None:
        delegate = MagicMock()
        delegate.execute = AsyncMock(return_value=[])

        await _basic_executor(delegate, plan_check=False).execute("SELECT * FROM app_t")

        delegate.execute.assert_awaited_once()
        assert "EXPLAIN" not in delegate.execute.await_args.args[0]

    async def test_plan_check_is_ignored_without_allowed_schema(self) -> None:
        """Full (allowed_schema=None): plan_check не действует никогда."""
        delegate = MagicMock()
        delegate.execute = AsyncMock(return_value=[])
        config = SafeSqlConfig(query_tag="t", plan_check=True)

        await _make_executor(delegate, config=config).execute("SELECT 1")

        delegate.execute.assert_awaited_once()
        assert "EXPLAIN" not in delegate.execute.await_args.args[0]

    async def test_explain_cancel_maps_to_query_timeout_error(self) -> None:
        delegate = MagicMock()
        delegate.execute = AsyncMock(side_effect=_query_canceled("canceling statement due to statement timeout"))

        with pytest.raises(QueryTimeoutError):
            await _basic_executor(delegate).execute("SELECT * FROM app_t")
```

2. `tests/unit/domains/test_db_access.py`, append:

```python
@pytest.mark.parametrize(
    ("access", "checked"),
    [
        (EffectiveAccess(AccessMode.BASIC, write_mode=False), True),
        (EffectiveAccess(AccessMode.BASIC, write_mode=True), True),
        (EffectiveAccess(AccessMode.FULL, write_mode=False), False),
    ],
)
def test_plan_check_reaches_only_basic_executors(access: EffectiveAccess, *, checked: bool) -> None:
    service = _service(access_mode=AccessMode.FULL, write_mode=True, plan_check=True)
    driver = service.view(access).sql_driver
    assert isinstance(driver, SafeSqlExecutor)
    assert driver._config.plan_check is checked
    assert (driver._plan_guard is not None) is checked


def test_plan_check_is_off_by_default() -> None:
    driver = _service().view(EffectiveAccess(AccessMode.BASIC, write_mode=False)).sql_driver
    assert isinstance(driver, SafeSqlExecutor)
    assert driver._plan_guard is None


def test_catalog_executor_never_checks_plans() -> None:
    service = _service(access_mode=AccessMode.BASIC, plan_check=True)
    catalog = service.catalog_driver
    assert isinstance(catalog, CatalogSqlExecutor)
    assert catalog._inner._plan_guard is None
```

3. `tests/unit/app/test_settings.py`, append:

```python
def test_plan_check_is_off_by_default_and_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    assert DatabaseConfig(**_CONNECTION).plan_check is False
    monkeypatch.setenv("MCP_DATABASE_NAME", "d")
    monkeypatch.setenv("MCP_DATABASE_PLAN_CHECK", "true")
    assert DatabaseSettings().plan_check is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/postgres/test_safe_sql_executor.py tests/unit/domains/test_db_access.py tests/unit/app/test_settings.py -q`
Expected: FAIL — `TypeError: SafeSqlConfig.__init__() got an unexpected keyword argument 'plan_check'`; `ValidationError` "Extra inputs are not permitted" for `plan_check` in `DatabaseConfig`; `AttributeError: ... '_plan_guard'`. `test_validator_runs_before_the_plan_check` fails with the same `TypeError` (it builds the config).

- [ ] **Step 3: `DatabaseConfig.plan_check`**

In `src/postgres_fastmcp/app/config/database.py`, after `table_prefix`:

```python
    plan_check: bool = Field(
        default=False,
        description=(
            "Только для access_mode=basic: перед выполнением SQL агента строить план (EXPLAIN VERBOSE) и отклонять "
            "запросы, которые через представления, правила или SQL-функции читают отношения вне public/table_prefix "
            "или табличные функции чужих схем. Лишний запрос к БД на каждый оператор. Для full игнорируется."
        ),
    )
```

- [ ] **Step 4: `SafeSqlExecutor`**

In `src/postgres_fastmcp/postgres/security/driver.py`:
1. Import: `from postgres_fastmcp.postgres.security.plan_guard import PlanGuard`.
2. `SafeSqlConfig`: add field `plan_check: bool = False` after `table_prefix` and to the docstring `Attributes:` `plan_check: Проверять план запроса перед выполнением (PlanGuard); действует только вместе с allowed_schema.`
3. `SafeSqlExecutor.__init__`, after `self._config = config`:

```python
        # Проверка по плану — только для basic (allowed_schema задан); full и канал сервера её не получают.
        self._plan_guard = (
            PlanGuard(
                self._explain_for_plan_check,
                allowed_schema=config.allowed_schema,
                table_prefix=config.table_prefix,
            )
            if config.plan_check and config.allowed_schema
            else None
        )
```

4. Replace `_guarded` and `_run`, and add two methods:

```python
    async def _guarded[T](
        self,
        query: str,
        params: list[Any] | None,
        run: Callable[..., Awaitable[T]],
    ) -> T:
        """Тег, валидация, проверка по плану, SET LOCAL и клиентская страховка вокруг метода делегата run."""
        query = self.render(query, params) if params else f"/* {self._config.query_tag} */ {query}"
        self._validator.validate(query)
        if self._config.timeout is None:
            return await self._checked_run(query, run)
        try:
            async with asyncio.timeout(self._config.timeout + self._config.client_timeout_grace):
                return await self._checked_run(query, run)
        except TimeoutError as e:
            logger.warning(
                "Client-side timeout after %ss: %s...",
                self._config.timeout,
                query[:100],
            )
            raise QueryTimeoutError(self._config.timeout) from e

    async def _checked_run[T](self, query: str, run: Callable[..., Awaitable[T]]) -> T:
        """Проверка по плану (если включена), затем выполнение с SET LOCAL через делегата."""
        if self._plan_guard is not None:
            await self._plan_guard.check(query)
        return await self._run(self._with_session_settings(query), run, readonly=self._config.read_only)

    async def _explain_for_plan_check(self, explain_sql: str) -> list[RowResult] | None:
        """EXPLAIN для PlanGuard: тот же SET LOCAL и тег, всегда read-only (EXPLAIN без ANALYZE ничего не выполняет)."""
        tagged = f"/* {self._config.query_tag} */ {explain_sql}"
        return await self._run(self._with_session_settings(tagged), self._delegate.execute, readonly=True)

    async def _run[T](self, query: str, run: Callable[..., Awaitable[T]], *, readonly: bool) -> T:
        """Выполнить через делегата; отмену по statement_timeout превратить в QueryTimeoutError.

        Любая другая отмена (pg_cancel_backend, запрос пользователя) становится QueryCancelledError,
        чтобы не выдавать её за таймаут.
        """
        started = monotonic()
        try:
            return await run(query, params=None, readonly=readonly)
        except QueryCanceled as e:
            reason = e.diag.message_primary or ""
            if _is_statement_timeout(reason, elapsed=monotonic() - started, timeout=self._config.timeout):
                logger.warning(
                    "Postgres cancelled the statement (statement_timeout=%ss): %s...",
                    self._config.timeout,
                    query[:100],
                )
                raise QueryTimeoutError(self._config.timeout or 0.0) from e
            logger.warning("Postgres cancelled the statement (%s): %s...", reason or "no reason", query[:100])
            raise QueryCancelledError from e
```

(the `except QueryCanceled` body of `_run` is the existing one; only the signature and the `run(...)` call change.)

5. `execute`/`execute_statement` docstrings: add `PlanAccessError: plan_check в basic, план читает отношение или функцию вне разрешённого.` to `Raises:`.

- [ ] **Step 5: `DbAccessService`**

In `src/postgres_fastmcp/domains/db_access.py`:
- `DatabaseConfigPort`: add `plan_check: bool` after `table_prefix: str | None`.
- `_executor`: the `SafeSqlConfig(...)` call gains `plan_check=basic and self._config.plan_check,`; the debug log format gains `, plan_check=%s` with argument `safe_config.plan_check` (last).

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit -q`
Expected: PASS. Existing `SafeSqlExecutor` tests keep passing: without `plan_check` the delegate sees exactly the same strings and `readonly` values as before.

- [ ] **Step 7: Lint, type-check, commit**

```bash
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
git add src/postgres_fastmcp/app/config/database.py src/postgres_fastmcp/postgres/security/driver.py src/postgres_fastmcp/domains/db_access.py tests/unit/postgres/test_safe_sql_executor.py tests/unit/domains/test_db_access.py tests/unit/app/test_settings.py
git commit -m "feat(security): check query plans in basic mode when plan_check is on"
```

---

### Task 3: интеграция, README, env.example, спеки

**Files:**
- Test: `tests/integration/test_plan_check.py` (create)
- Modify: `README.md`, `env.example`
- Modify: `docs/superpowers/specs/2026-09-28-basic-followups-design.md`, `docs/superpowers/specs/2026-09-28-basic-confinement-design.md`

**Interfaces:**
- Consumes: `DatabaseConfig(plan_check=True)` (Task 2), `PlanAccessError` (Task 1), fixtures `db_full` (FULL+write) and `db_user_prefix` (BASIC read-only, `table_prefix="app_"`, `plan_check` off) from `tests/integration/conftest.py`.

- [ ] **Step 1: Integration tests**

Create `tests/integration/test_plan_check.py`:

```python
# mypy: ignore-errors
"""Проверка по плану на живом Postgres: представление в public поверх чужой схемы (спека basic-followups §4.4)."""

from collections.abc import AsyncGenerator

import pytest

from postgres_fastmcp.access import EffectiveAccess
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.domains.db_access import DbAccess, DbAccessService
from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.shared.errors import PlanAccessError


_SETUP = """
CREATE SCHEMA IF NOT EXISTS secret;
CREATE TABLE IF NOT EXISTS secret.accounts (id int, token text);
INSERT INTO secret.accounts SELECT 1, 'top-secret' WHERE NOT EXISTS (SELECT 1 FROM secret.accounts);
CREATE OR REPLACE VIEW public.app_secret_view AS SELECT id, token FROM secret.accounts;
CREATE OR REPLACE FUNCTION secret.accounts_rows() RETURNS SETOF secret.accounts
    LANGUAGE sql STABLE AS 'SELECT * FROM secret.accounts';
CREATE OR REPLACE VIEW public.app_secret_fn_view AS SELECT * FROM secret.accounts_rows();
CREATE TABLE IF NOT EXISTS public.app_plan_items (id int);
INSERT INTO public.app_plan_items SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM public.app_plan_items);
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
async def test_write_statement_is_planned_in_a_read_only_transaction(db_plan_check: DbAccess) -> None:
    """EXPLAIN без ANALYZE не исполняет DML: план INSERT строится в read-only транзакции, сам INSERT проходит."""
    await db_plan_check.sql_driver.execute("INSERT INTO app_plan_items (id) VALUES (2)", readonly=False)
    await db_plan_check.sql_driver.execute("DELETE FROM app_plan_items WHERE id = 2", readonly=False)


@pytest.mark.asyncio
async def test_information_schema_is_rejected_with_plan_check(db_plan_check: DbAccess) -> None:
    """Строгий режим: представления information_schema читают pg_catalog."""
    with pytest.raises(PlanAccessError, match="pg_catalog"):
        await db_plan_check.sql_driver.execute("SELECT table_name FROM information_schema.tables", readonly=True)
```

Check statically and record in the report: `db_full` runs `_SETUP` through the unrestricted `SqlExecutor` (multi-statement string, no formatting); `app_secret_view`/`app_secret_fn_view` pass the basic validator (public, prefix `app_`), so without `plan_check` they reach Postgres; `EXPLAIN` of `INSERT` does not trip `READ ONLY` (Postgres skips `ExecCheckXactReadOnly` for `EXEC_FLAG_EXPLAIN_ONLY`) — the test proves it on both Postgres versions in CI.

- [ ] **Step 2: README**

1. In `#### 2. Конфигурационный файл`, append `plan_check` to the optional `database` fields list.
2. In the paragraph `**Что закрыто в access_mode=basic для SQL агента:**`, replace `Статически basic закрыть не может всё: представление в \`public\` поверх таблиц другой схемы отдаёт данные этой схемы, а` with `Статически basic закрыть не может всё: представление в \`public\` поверх таблиц другой схемы отдаёт данные этой схемы (закрывается настройкой \`plan_check\`, см. ниже), а`.
3. After the section `#### Роль для basic` (added in PR 1), insert:

```markdown
#### Проверка по плану (`plan_check`)

`plan_check=true` (env `MCP_DATABASE_PLAN_CHECK=true`, по умолчанию `false`) — строгий режим basic: перед выполнением каждого оператора SQL агента сервер строит его план (`EXPLAIN (VERBOSE, FORMAT JSON)`, без выполнения) и отклоняет запрос, если план читает отношение вне `public` (или без `table_prefix`), системное отношение `pg_*` или табличную функцию схемы, отличной от `pg_catalog` и `public`. Ответ — ошибка `Access to relation 'secret.accounts' is not allowed in basic mode: the query reaches it through a view, rule or function. ...`.

- Закрывает представления, правила и SQL-функции поверх чужих схем, которые валидатор по тексту запроса не видит.
- Не закрывает функции `SECURITY DEFINER` и PL/pgSQL: их тело плану непрозрачно; функции в выражениях представлений (`SELECT secret.f(x)`) в плане тоже не видны.
- Отклоняет и запросы к `information_schema`: её представления читают `pg_catalog`.
- Цена — лишний запрос к БД на каждый оператор. Для `access_mode=full` настройка игнорируется.

По умолчанию выключено: представление в `public` поверх другой схемы обычно создаёт DBA намеренно и выдаёт роли через `GRANT`.
```

- [ ] **Step 3: env.example**

After the `MCP_DATABASE_TABLE_PREFIX=` block (its comment line) add:

```bash
# MCP_DATABASE_PLAN_CHECK=false
#   Для access_mode=basic: проверять план запроса агента (EXPLAIN VERBOSE) и отклонять чтение вне public через представления и функции.
```

- [ ] **Step 4: Specs**

1. `docs/superpowers/specs/2026-09-28-basic-followups-design.md`:
- status line → `Статус: реализовано (PR 1–3).`
- §4.2, first bullet list: after `- \`DeclareCursorStmt\` — вложенный запрос;` keep as is; replace `- По всему дереву плана (вложенные \`Plans\`, \`InitPlan\`/\`SubPlan\`) собираются пары ...` with `- По всему документу плана (все вложенные словари и списки: \`Plans\` с \`InitPlan\`/\`SubPlan\` в \`Parent Relationship\`, \`Target Tables\` у \`ModifyTable\`) собираются пары \`Schema\`/\`Relation Name\` и \`Schema\`/\`Function Name\`:`; after `если у EXPLAIN есть опция \`generic_plan\`, она переносится` add `(только включённая: \`generic_plan false\`/\`0\`/\`off\` не переносится)`.
- §4.3 → append: `\`Function Name\` бывает только у \`Function Scan\` (функции во \`FROM\`): функция в выражении представления (\`SELECT secret.f(x)\`) в плане не видна. С \`plan_check\` запросы к \`information_schema\` отклоняются — её представления читают \`pg_catalog\`.`
2. `docs/superpowers/specs/2026-09-28-basic-confinement-design.md`, §6, the bullet `- (i) Представление в \`public\` поверх таблиц другой схемы остаётся доступным: ...` → append the sentence: `Опционально закрывается настройкой \`plan_check\` (спека \`2026-09-28-basic-followups-design.md\`, §4): basic строит план каждого оператора агента и отклоняет отношения вне \`public\`/префикса.`

- [ ] **Step 5: Full verification, commit**

```bash
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
git add tests/integration/test_plan_check.py README.md env.example docs/superpowers/specs/2026-09-28-basic-followups-design.md docs/superpowers/specs/2026-09-28-basic-confinement-design.md
git commit -m "docs(security): describe plan_check and pin it against Postgres"
```

---

## Self-review

- Spec §4.1 (`plan_check` in `DatabaseConfig`/env, basic only, `SafeSqlConfig.plan_check`, `_guarded` after validation and before execution, `plan_guard.py`) → Task 2 Steps 3–5, Task 1 Step 4. §4.2 (statement kinds, `generic_plan`, `RawStream`, `EXPLAIN (VERBOSE, FORMAT JSON[, GENERIC_PLAN])` via the delegate with the same `SET LOCAL`, `readonly=True`, relation/function rules, `PlanAccessError` text, planning errors as Postgres errors) → Task 1, Task 2 Step 4. §4.3 → Task 3 Steps 2, 4. §4.4 unit (view, `public.app_t`, `secret.f`, `pg_catalog.now`, nested plans, `GENERIC_PLAN`, `SHOW`/`PREPARE`, prefix; `plan_check=false` and full send no EXPLAIN) → Task 1 Step 1, Task 2 Step 1; integration (`secret` schema, `public.app_secret_view`, with/without `plan_check`, `app_` table in both) → Task 3 Step 1. §5 PR 3 bullet already matches.
- Security constraint: `QueryValidator` untouched and runs first (`test_validator_runs_before_the_plan_check`); full (`test_plan_check_is_ignored_without_allowed_schema`, `test_plan_check_reaches_only_basic_executors`) and `CatalogSqlExecutor` (`test_catalog_executor_never_checks_plans`) never get a guard.
- Names across tasks: `PlanGuard`, `ExplainRunner`, `PlanAccessError(kind, qualified_name)`, `SafeSqlConfig.plan_check`, `SafeSqlExecutor._plan_guard`, `_checked_run`, `_explain_for_plan_check`, `_run(..., readonly=)`, `DatabaseConfig.plan_check` — consistent.
