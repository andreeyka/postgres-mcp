# `plan_check`: функции `public` не на SQL (тела PL/pgSQL, C, internal) — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** С `plan_check=true` (basic) отклонять до выполнения запрос, который доходит до функции `public` не на языке `sql` (PL/pgSQL, PL/Python, C, `internal`, любой другой), кроме агрегатов и членов расширений: её тело не проверить. Настройка `plan_check_allow_non_sql_functions` (по умолчанию `false`) возвращает прежнее поведение. Попутно — опорная функция планировщика (`prosupport`) функций `public` проверяется как функция машинерии типа.

**Architecture:** Все функции `public`, до которых доходит проверка (выражения плана, правила, триггеры целей DML, функции операторов и агрегатов, приведения, машинерия типов, умолчания, тела), уже проходят через `PlanGuard._note_implementation` и запрос реализаций `ALLOWED_IMPLEMENTATIONS_SQL` (CTE `functions` — все перегрузки с именем). В этот запрос добавляется фрагмент `plan_catalog._NON_SQL_FUNCTION_ROWS`: строка `non_sql_function` (язык — в `definition`) для функции не на `sql`, не агрегата, не члена расширения, и строка `type_function` для `prosupport` (не у членов расширений). `PlanGuard._check_implementations` проверяет строки `non_sql_function` после остальных строк ответа и при `allow_non_sql_functions=False` бросает `PlanAccessError(..., language=...)`. Настройка идёт `DatabaseConfig` → `DatabaseConfigPort` → `SafeSqlConfig` → `PlanGuard`. Новых обращений к БД нет. Спека: `docs/superpowers/specs/2026-10-02-plan-check-procedural-bodies-design.md`.

**Tech Stack:** Python 3.12, uv, pglast v8, psycopg 3.3.4 (`psycopg.sql`), pydantic-settings, pytest (asyncio auto).

## Spec corrections

Проверено на живом PostgreSQL 17.10 (временный сервер из `.deb` Ubuntu, `localhost:55432`, суперпользователь); PG 15/16 — по документации: `prokind`, `prosupport`, `pg_language.lanname` есть с PG 12. План опробован: код и тесты задачи применены к копии репозитория (`git worktree` в scratchpad от `6f96757`, затем удалён; итоговый diff — `scratchpad/w5/trial-final.diff`):

- `uv run pytest tests/unit -q` — 2177 passed (на `main` — 2164);
- `mypy src/`, `ruff check .`, `ruff format --check .` — чисто;
- `tests/integration/test_plan_check.py` на живом PG 17 (плагин подмены контейнера, `-k "postgres:16"`) — 123 passed, дважды на одной базе (идемпотентно); `uv run pytest tests/integration -q` без Docker — 396 skipped (собирается);
- новые интеграционные тесты на коде до волны (`git archive HEAD src` в `PYTHONPATH`): 8 failed — 4 исключением бросающей функции (`RaiseException public.app_ns_boom|app_ns_opf|app_ns_to_e|app_ns_trg_boom was executed`), 4 `DID NOT RAISE` (представление и триггер, читающие `secret.accounts`, обёртка `internal`, опорная функция `secret.ns_support`); 2 — ошибка фикстуры (нет поля `plan_check_allow_non_sql_functions`); 1 passed (агрегат и `moddatetime` проходят и до волны).
- Стоимость запроса реализаций (оценка планировщика, семь имён функций, два оператора, тип, два отношения): 7749 → 7804; порог JIT — 100000.

Уточнения к спеке, найденные при опробовании:

- **Агрегаты — `prolang = internal`.** `CREATE AGGREGATE public.app_cat2(text) (SFUNC = public.app_cat_ok …)` даёт строку `pg_proc` с `lanname = internal`. Без `prokind <> 'a'` каждый агрегат `public` отклонялся бы (а его опорные функции и так приходят строками `aggregate_function`).
- **Триггерная функция на SQL невозможна.** `CREATE FUNCTION … RETURNS trigger LANGUAGE sql` — `SQL functions cannot return type trigger`. Любой свой триггер в `public` (не из расширения) при настройке по умолчанию отклоняет DML своей таблицы; README называет `moddatetime` (contrib, доверено — живьём `UPDATE` с его триггером проходит).
- **SQL агента функцию `public` сам не называет.** Валидатор basic пускает только функции списка basic: `SELECT app_leak()` и `SELECT public.app_leak()` — `FunctionNotAllowedError`. Интеграционные тесты доходят до функций через представления, триггеры, операторы и приведения.
- **Порядок строк.** Умолчание аргумента PL/pgSQL-функции (`app_boom_arg(a int DEFAULT (1 + secret.boom_int()))`, существующий тест `argument-default`) и её строка `non_sql_function` приходят в одном ответе. Строки `non_sql_function` проверяются после остальных — отказ по-прежнему по `secret.boom_int`.
- **Тесты волны 4 с вводом-выводом `internal` в `public`.** Где у типа есть и такая функция, и функция `secret` (`app_ic_ct`, `app_ic_b`, `app_ic_xt`), отказ остаётся по `secret`: строка машинерии с функцией `secret` приходит в том же круге, где функция `public` только отмечается, а её строка `non_sql_function` — следующим кругом. Цепочки с `app_ic_okt` (у типа только функции `public`) и тесты с `app_double`/`app_close_to` (PL/pgSQL) переводятся на фикстуру с включённой настройкой; по умолчанию цепочка отклоняется по `public.app_ic_okt_(in|out)` — отдельный тест.
- **Опорная функция планировщика в тесте не бросает.** `internal`-функцию с сигнатурой `internal → internal` на PL/pgSQL не написать; `secret.ns_support` — обёртка над `textlike_support` (на `SupportRequestSimplify` возвращает `NULL`). Тест доказывает отказ (`DID NOT RAISE` до волны), а не то, что вызов был бы.

## Global Constraints

- Всё, что видит агент или внешняя система (ошибки, логи, коммиты), — на английском; docstring и комментарии — по-русски. README — по-русски.
- Нет `from __future__ import annotations`.
- Ломающие изменения разрешены; описываются только в заметках к PR (спека §8). В коде — никаких шимов и упоминаний старого поведения.
- Безопасность: ни одно правило валидатора и существующей проверки по плану не ослабляется; `full` и `plan_check=false` не затрагиваются. Весь SQL каталога — с `pg_catalog.` у каждого отношения, функции и типа и `OPERATOR(pg_catalog.…)` у каждого оператора; имена — `Literal`/`Identifier`.
- Стоимость запросов каталога — много ниже `jit_above_cost` (100000).
- Перед каждым коммитом: `uv run ruff check --fix --show-fixes`, `uv run mypy src/`, `uv run ruff format`; затем `uv run ruff check .`.
- Юнит-тесты: `uv run pytest tests/unit -q`. Интеграция: `uv run pytest tests/integration -q` — должна собираться; без Docker пропускается, в CI — Postgres 15/16 (суперпользователь `postgres`: функции `LANGUAGE internal` создаёт только он; `moddatetime` — contrib официального образа). Интеграционные тесты сверять статически с кодом.
- Коммиты: `type(scope): message`, повелительное наклонение, английский.
- **Никогда не запускать никакие `git config` и `git stash`.** Не пушить.
- Ветка: `claude/plan-check-procedural-bodies` от `main` (уже создана, на ней — спека и этот план).

---

### Task 1: Отказ по функциям `public` не на SQL и опорным функциям планировщика

**Files:**
- Modify: `src/postgres_fastmcp/postgres/security/plan_catalog.py` (`_NON_SQL_FUNCTION_ROWS`; CTE `functions` и хвост `ALLOWED_IMPLEMENTATIONS_SQL`; комментарий над ним)
- Modify: `src/postgres_fastmcp/postgres/security/plan_guard.py` (docstring модуля; `_NON_SQL_FUNCTION_KIND`; `PlanGuard.__init__(allow_non_sql_functions)`; `_check_implementations`)
- Modify: `src/postgres_fastmcp/shared/errors.py` (`PlanAccessError(language=...)`)
- Modify: `src/postgres_fastmcp/postgres/security/driver.py` (`SafeSqlConfig.plan_check_allow_non_sql_functions`; `_checked_run`)
- Modify: `src/postgres_fastmcp/domains/db_access.py` (`DatabaseConfigPort`; `_executor`)
- Modify: `src/postgres_fastmcp/app/config/database.py` (`DatabaseConfig.plan_check_allow_non_sql_functions`)
- Modify: `README.md` (список полей БД; «Проверка по плану»: новый пункт, «не закрывает», «отклоняет, хотя легитимно»)
- Test: `tests/unit/postgres/test_plan_catalog.py`, `tests/unit/postgres/test_plan_guard.py`, `tests/unit/postgres/test_safe_sql_executor.py`, `tests/unit/domains/test_db_access.py`, `tests/unit/app/test_settings.py`, `tests/integration/test_plan_check.py`

**Interfaces:**
- Consumes (волны 3–4): CTE `functions` запроса реализаций, `_not_extension_member`, `_PG_PROC`, вид строки `type_function` и его правило в `_check_row`, `PlanGuard._check_implementations`, `_check_rule_row`, `PlanAccessError`.
- Produces:
  - `plan_catalog._NON_SQL_FUNCTION_ROWS: str` — ветви `non_sql_function` (`kind, schema, name, parent_schema=NULL, definition=lanname, config=NULL, origin=NULL`) и `type_function` (`prosupport`);
  - `PlanGuard(run, *, allowed_schema, table_prefix, builtin_types=None, allow_non_sql_functions=False)`;
  - `PlanAccessError(kind, qualified_name, *, allowed_schema, table_prefix, binary_cast=None, language=None)`;
  - `SafeSqlConfig.plan_check_allow_non_sql_functions: bool = False`, `DatabaseConfigPort.plan_check_allow_non_sql_functions: bool`, `DatabaseConfig.plan_check_allow_non_sql_functions: bool = False` (env `MCP_DATABASE_PLAN_CHECK_ALLOW_NON_SQL_FUNCTIONS`).

- [ ] **Step 1: Падающие тесты SQL каталога**

В `tests/unit/postgres/test_plan_catalog.py`:

1. В `test_allowed_implementations_return_argument_defaults_of_any_language` последнюю строку

```python
    assert "lanname" not in defaults
```

заменить на (после умолчаний идёт ветвь функций не на sql, в ней `lanname` есть):

```python
    assert "lanname" not in defaults[: defaults.index("UNION ALL")]
```

2. В `test_type_machinery_trusts_extensions_by_the_owning_object` последний цикл

```python
    for member in ("'pg_catalog.pg_proc'", "'pg_catalog.pg_operator'"):
        assert f"e.classid OPERATOR(pg_catalog.=) {member}" not in sql
```

заменить на:

```python
    # Строки функций не на sql (запрос реализаций) доверяют члену расширения по самой функции — это не машинерия.
    machinery = sql[sql.index("type_seed_set(oids)") : sql.rindex("k.conbin IS NOT NULL)")]
    for member in ("'pg_catalog.pg_proc'", "'pg_catalog.pg_operator'"):
        assert f"e.classid OPERATOR(pg_catalog.=) {member}" not in machinery
```

3. Сразу после `test_allowed_implementations_return_argument_defaults_of_any_language` добавить:

```python
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
```

Run: `uv run pytest tests/unit/postgres/test_plan_catalog.py -q`
Expected: FAIL — `ValueError: substring not found` у двух новых тестов (`'non_sql_function'`, `p.prosupport::pg_catalog.oid`) и у теста умолчаний (`UNION ALL` после `'argument_defaults'` есть, он проходит — падают только новые).

- [ ] **Step 2: `_NON_SQL_FUNCTION_ROWS` в `plan_catalog.py`**

1. В CTE `functions` запроса `ALLOWED_IMPLEMENTATIONS_SQL` строку

```python
    "p.proargdefaults, p.prorettype, p.proargtypes, p.proallargtypes "
```

заменить на

```python
    "p.proargdefaults, p.prorettype, p.proargtypes, p.proallargtypes, p.prokind, p.prosupport "
```

2. Сразу после `DEFINITION_DEPENDENCIES_SQL` (перед комментарием «# Реализации операторов и функций allowed_schema по именам.») вставить:

```python
# Функции allowed_schema не на языке sql (plpgsql, plpython, c, internal и любой другой): их тело не проверить —
# PL/pgSQL выполняет динамический SQL (EXECUTE), C-функция — любой код, функция LANGUAGE internal — любую встроенную
# функцию под своим именем (AS 'show_config_by_name' — это current_setting). Строка non_sql_function, definition — имя
# языка; решает PlanGuard (allow_non_sql_functions). Не в счёт агрегаты (prokind 'a': prolang у них internal, их
# опорные функции — строки aggregate_function) и члены расширений (pg_depend, deptype 'e'): код расширения доверен,
# как машинерия его типов. Проверяется каждая перегрузка с именем, как у тел и умолчаний.
#
# type_function — опорная функция планировщика (prosupport, CREATE FUNCTION ... SUPPORT): её вызывает планировщик
# для каждого вызова функции (SupportRequestSimplify, оценки строк и селективности); правило — как у машинерии типов.
# У функции — члена расширения не проверяется (доверие по владельцу).
_NON_SQL_FUNCTION_ROWS = (
    "SELECT 'non_sql_function', {schema}::pg_catalog.name, p.proname, NULL, l.lanname::pg_catalog.text, NULL, NULL "  # noqa: S608
    "FROM functions p JOIN pg_catalog.pg_language l ON l.oid OPERATOR(pg_catalog.=) p.prolang "
    "WHERE l.lanname OPERATOR(pg_catalog.<>) 'sql' AND p.prokind OPERATOR(pg_catalog.<>) 'a' "
    f"AND {_not_extension_member(_PG_PROC, 'p.oid')} "
    "UNION ALL "
    "SELECT 'type_function', fn.nspname, f.proname, NULL, NULL, NULL, NULL FROM functions p "
    "JOIN pg_catalog.pg_proc f ON f.oid OPERATOR(pg_catalog.=) p.prosupport::pg_catalog.oid "
    "JOIN pg_catalog.pg_namespace fn ON fn.oid OPERATOR(pg_catalog.=) f.pronamespace "
    f"WHERE {_not_extension_member(_PG_PROC, 'p.oid')}"
)
```

3. Последнюю строку комментария над `ALLOWED_IMPLEMENTATIONS_SQL`

```python
# функций и операторов, типы состояния агрегатов.
```

заменить на

```python
# функций и операторов, типы состояния агрегатов. Плюс _NON_SQL_FUNCTION_ROWS: функции не на sql и опорные функции
# планировщика.
```

4. В хвосте `ALLOWED_IMPLEMENTATIONS_SQL` (после ветви `type_function` операторов, перед `SELECT DISTINCT m.kind …`) строку `"UNION ALL "` заменить так, чтобы хвост стал:

```python
    "JOIN pg_catalog.pg_namespace fn ON fn.oid OPERATOR(pg_catalog.=) f.pronamespace "
    "UNION ALL " + _NON_SQL_FUNCTION_ROWS + " UNION ALL "
    "SELECT DISTINCT m.kind, m.schema, m.name, m.parent_schema, m.definition, NULL::pg_catalog.text[], m.origin "
    "FROM machinery m"
)
```

(Неявная конкатенация связывает сильнее `+`: получается `"… UNION ALL " + _NON_SQL_FUNCTION_ROWS + " UNION ALL SELECT DISTINCT … FROM machinery m"`.)

Run: `uv run pytest tests/unit/postgres/test_plan_catalog.py -q`
Expected: PASS (в том числе `test_catalog_sql_resolves_nothing_through_the_search_path` — новый SQL квалифицирован).

- [ ] **Step 3: Падающие тесты `PlanGuard`**

В конец `tests/unit/postgres/test_plan_guard.py` добавить (`_VIEW_CALLS`, `_TRIGGER`, `_defaults`, `_EXPLAIN`, `_SELECT` уже есть в файле):

```python
def _non_sql(name: str, language: str = "plpgsql") -> dict[str, Any]:
    """Строка функции public не на языке sql из ALLOWED_IMPLEMENTATIONS_SQL (definition — язык)."""
    return {"kind": "non_sql_function", "schema": "public", "name": name, "definition": language}


@pytest.mark.parametrize(
    ("rules", "name"),
    [
        pytest.param(_VIEW_CALLS, "app_count", id="view-or-trigger-function"),
        pytest.param([{"kind": "type_function", "schema": "public", "name": "app_count"}], "app_count", id="machinery"),
        pytest.param(
            [{"kind": "trigger", "definition": _TRIGGER.format(when="", function="app_count")}],
            "app_count",
            id="trigger",
        ),
    ],
)
async def test_non_sql_public_function_is_rejected_before_explain(rules: list[dict[str, Any]], name: str) -> None:
    """Функция public на PL/pgSQL, до которой доходит запрос (представление, триггер, машинерия типа): тело не
    проверить — отказ до EXPLAIN, с языком и тем, как это исправить."""
    explain = _Explain(rules=rules, implementations={name: [_non_sql(name)]})

    with pytest.raises(PlanAccessError) as exc_info:
        await _guard(explain).check(_SELECT)

    assert (exc_info.value.kind, exc_info.value.qualified_name) == ("function", f"public.{name}")
    message = str(exc_info.value)
    assert "LANGUAGE plpgsql" in message
    assert "rewrite it in LANGUAGE sql" in message
    assert "plan_check_allow_non_sql_functions=true" in message
    assert [sql for sql in explain.log if sql.startswith("EXPLAIN")] == []


async def test_non_sql_function_in_the_agent_sql_is_rejected_before_prepare() -> None:
    """Оператор public в SQL агента реализован функцией на internal (обёртка встроенной функции)."""
    explain = _Explain(
        implementations={
            "<~>": [{"kind": "operator_function", "schema": "public", "name": "app_near", "parent_schema": "public"}],
            "app_near": [_non_sql("app_near", "internal")],
        }
    )

    with pytest.raises(PlanAccessError, match=r"function 'public\.app_near'.*LANGUAGE internal"):
        await _guard(explain).check("SELECT id <~> 1 FROM app_t")

    assert explain.prepared == []


async def test_other_rows_of_a_non_sql_function_are_checked_first() -> None:
    """Умолчание аргумента функции на PL/pgSQL вызывает secret: отказ называет secret, а не язык — в любом порядке
    строк ответа."""
    explain = _Explain(
        rules=_VIEW_CALLS,
        implementations={"app_count": [_non_sql("app_count"), _defaults("app_count", "secret.api_key()")]},
    )

    with pytest.raises(PlanAccessError, match=r"function 'secret\.api_key'"):
        await _guard(explain).check(_SELECT)


async def test_non_sql_functions_pass_when_allowed() -> None:
    """allow_non_sql_functions=True — прежнее поведение: тело не проверяется, остальные строки — как раньше."""
    explain = _Explain(
        rules=_VIEW_CALLS,
        implementations={"app_count": [_non_sql("app_count"), _defaults("app_count", "1")]},
    )

    await PlanGuard(explain, allowed_schema="public", table_prefix=None, allow_non_sql_functions=True).check(_SELECT)

    assert [sql for sql in explain.log if sql.startswith("EXPLAIN")] == [_EXPLAIN + _SELECT]
```

Run: `uv run pytest tests/unit/postgres/test_plan_guard.py -q -k "non_sql or other_rows_of_a_non_sql"`
Expected: FAIL — `PlanUnverifiableError` вместо `PlanAccessError` (вид строки `non_sql_function` неизвестен `_check_row`), `TypeError: … unexpected keyword argument 'allow_non_sql_functions'`.

- [ ] **Step 4: `PlanGuard` и `PlanAccessError`**

`src/postgres_fastmcp/shared/errors.py`, класс `PlanAccessError`:

1. `def __init__(` → `def __init__(  # noqa: PLR0913`; после параметра `binary_cast: str | None = None,` добавить `language: str | None = None,`.
2. В Args после описания `binary_cast` добавить:

```python
            language: Язык функции allowed_schema не на sql (plpgsql, c, internal), тело которой не проверить;
                None — отказ по схеме или списку basic.
```

3. Перед `super().__init__(message)` вставить:

```python
        if language is not None:
            message = (
                f"Access to {kind} '{qualified_name}' is not allowed in basic mode: the query reaches it, and its "
                f"body in LANGUAGE {language} cannot be verified (plan_check verifies only LANGUAGE sql bodies). "
                "A database owner can rewrite it in LANGUAGE sql; the server operator can set "
                "plan_check_allow_non_sql_functions=true, which lets such functions run unchecked."
            )
```

`src/postgres_fastmcp/postgres/security/plan_guard.py`:

1. В docstring модуля после строки «выросло (функции машинерии, их тела), глубину определений не тратят; все круги ограничены _MAX_CATALOG_ROUNDS.» добавить:

```text
Функция allowed_schema не на языке sql (PL/pgSQL, PL/Python, C, internal; не агрегат и не член расширения), до которой
доходит оператор по любому из путей выше, — отказ (allow_non_sql_functions=False): её тело не проверить. Решение —
в том же разборе строк реализаций, что тела и умолчания, после остальных строк ответа.
```

2. После `_TYPE_ORIGIN = "type"`:

```python
# Вид строки реализаций: функция allowed_schema не на языке sql (plan_catalog._NON_SQL_FUNCTION_ROWS).
_NON_SQL_FUNCTION_KIND = "non_sql_function"
```

3. `PlanGuard.__init__`: после `builtin_types: BuiltinTypeNames | None = None,` — параметр `allow_non_sql_functions: bool = False,`; в Args после `builtin_types`:

```python
            allow_non_sql_functions: Пропускать функции allowed_schema не на языке sql (их тела выполняются
                непроверенными); False — отказ.
```

после `self._builtin_types = builtin_types or BuiltinTypeNames()` — `self._allow_non_sql_functions = allow_non_sql_functions`.

4. `_check_implementations`: в конец docstring (перед закрывающими кавычками) добавить

```python

        Функция не на языке sql (строка non_sql_function) отклоняется после остальных строк ответа: отказ по функции
        чужой схемы в умолчании или машинерии той же функции точнее и не зависит от порядка строк.

        Raises:
            PlanAccessError: Строка нарушает правила basic или функция не на sql при allow_non_sql_functions=False.
            PlanUnverifiableError: Ответа нет или строку не проверить.
```

а цикл

```python
        for row in rows:
            self._check_rule_row(row.cells, pending, free_functions)
```

заменить на:

```python
        non_sql: list[dict[str, Any]] = []
        for row in rows:
            if row.cells.get("kind") == _NON_SQL_FUNCTION_KIND:
                non_sql.append(row.cells)
            else:
                self._check_rule_row(row.cells, pending, free_functions)
        if non_sql and not self._allow_non_sql_functions:
            cells = non_sql[0]
            raise PlanAccessError(
                FUNCTION_KIND,
                f"{cells.get('schema')}.{cells.get('name')}",
                allowed_schema=self._allowed_schema,
                table_prefix=self._prefix_for_hint,
                language=str(cells.get("definition")),
            )
```

Run: `uv run pytest tests/unit/postgres/test_plan_guard.py tests/unit/postgres/test_plan_catalog.py -q`
Expected: PASS.

- [ ] **Step 5: Настройка — тесты и проводка**

Тесты (сначала — падают):

`tests/unit/app/test_settings.py`, после `test_plan_check_is_off_by_default_and_read_from_env`:

```python
def test_non_sql_functions_are_rejected_by_default_and_allowed_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    assert DatabaseConfig(**_CONNECTION).plan_check_allow_non_sql_functions is False
    monkeypatch.setenv("MCP_DATABASE_NAME", "d")
    monkeypatch.setenv("MCP_DATABASE_PLAN_CHECK_ALLOW_NON_SQL_FUNCTIONS", "true")
    assert DatabaseSettings().plan_check_allow_non_sql_functions is True
```

`tests/unit/domains/test_db_access.py`, перед `test_plan_check_is_off_by_default`:

```python
@pytest.mark.parametrize("allowed", [False, True])
def test_non_sql_function_setting_reaches_the_basic_executor(*, allowed: bool) -> None:
    service = _service(plan_check=True, plan_check_allow_non_sql_functions=allowed)
    driver = service.view(EffectiveAccess(AccessMode.BASIC, write_mode=False)).sql_driver
    assert isinstance(driver, SafeSqlExecutor)
    assert driver._config.plan_check_allow_non_sql_functions is allowed
```

`tests/unit/postgres/test_safe_sql_executor.py`, в `TestSafeSqlExecutorPlanCheck` перед `test_plan_check_off_keeps_the_prefixed_single_call` (`patch`, `AsyncMock` уже импортированы):

```python
    @pytest.mark.parametrize("allowed", [False, True])
    async def test_non_sql_function_setting_reaches_the_plan_guard(self, *, allowed: bool) -> None:
        delegate = _precheck_delegate(_plan_rows("public", "app_t"))
        config = SafeSqlConfig(
            query_tag="t",
            timeout=5,
            allowed_schema="public",
            read_only=False,
            plan_check=True,
            plan_check_allow_non_sql_functions=allowed,
        )
        validator = QueryValidator(read_only=False, allowed_schema="public")

        with patch("postgres_fastmcp.postgres.security.driver.PlanGuard") as guard:
            guard.return_value.check = AsyncMock()
            await _make_executor(delegate, validator=validator, config=config).execute("SELECT * FROM app_t")

        assert guard.call_args.kwargs["allow_non_sql_functions"] is allowed
```

Run: `uv run pytest tests/unit/app/test_settings.py tests/unit/domains/test_db_access.py tests/unit/postgres/test_safe_sql_executor.py -q`
Expected: FAIL (поля нет: `AttributeError`/`TypeError`/`ValidationError` — `extra` запрещён).

Код:

`src/postgres_fastmcp/app/config/database.py` — сразу после поля `plan_check`:

```python
    plan_check_allow_non_sql_functions: bool = Field(
        default=False,
        description=(
            "Только вместе с plan_check: пропускать функции public не на языке sql (PL/pgSQL, PL/Python, C, internal; "
            "кроме функций расширений), до которых доходит запрос. Их тела plan_check не проверяет, поэтому по "
            "умолчанию (False) такой запрос отклоняется; True — прежнее поведение: тела выполняются непроверенными."
        ),
    )
```

`src/postgres_fastmcp/domains/db_access.py`:
- в `DatabaseConfigPort` после `plan_check: bool` — `plan_check_allow_non_sql_functions: bool`;
- в `_executor`, в `SafeSqlConfig(...)` после `plan_check=basic and self._config.plan_check,` — `plan_check_allow_non_sql_functions=self._config.plan_check_allow_non_sql_functions,`;
- в `logger.debug(...)` строку формата `"read_only=%s, allow_explain_analyze=%s, timeout=%ss, table_prefix=%s, plan_check=%s)",` заменить на две: `"read_only=%s, allow_explain_analyze=%s, timeout=%ss, table_prefix=%s, plan_check=%s, "` и `"plan_check_allow_non_sql_functions=%s)",`, а после аргумента `safe_config.plan_check,` добавить `safe_config.plan_check_allow_non_sql_functions,`.

`src/postgres_fastmcp/postgres/security/driver.py`:
- в docstring `SafeSqlConfig` после строки `plan_check: …`:

```python
        plan_check_allow_non_sql_functions: С plan_check пропускать функции allowed_schema не на языке sql
            (PL/pgSQL, C, internal): их тела выполняются непроверенными. False — отказ.
```

- поле после `plan_check: bool = False`: `plan_check_allow_non_sql_functions: bool = False`;
- в `_checked_run` после `builtin_types = self._builtin_types`: `allow_non_sql_functions = self._config.plan_check_allow_non_sql_functions`; создание `PlanGuard` заменить на:

```python
            guard = PlanGuard(
                run,
                allowed_schema=allowed_schema,
                table_prefix=table_prefix,
                builtin_types=builtin_types,
                allow_non_sql_functions=allow_non_sql_functions,
            )
```

Run: `uv run pytest tests/unit -q`
Expected: PASS (2177).

- [ ] **Step 6: Интеграционные тесты**

`tests/integration/test_plan_check.py`:

1. После фикстуры `db_plan_check` добавить:

```python
@pytest.fixture
async def db_plan_check_non_sql(
    test_postgres_connection_string: tuple[str, str], db_plan_check: DbAccess
) -> AsyncGenerator[DbAccess, None]:
    """db_plan_check с plan_check_allow_non_sql_functions=true: функции public не на sql проходят непроверенными."""
    connection_string, _ = test_postgres_connection_string
    config = DatabaseConfig.from_uri(
        connection_string,
        access_mode=AccessMode.BASIC,
        write_mode=True,
        table_prefix="app_",
        plan_check=True,
        plan_check_allow_non_sql_functions=True,
    )
    service = DbAccessService(config)
    try:
        yield service.view(EffectiveAccess(AccessMode.BASIC, write_mode=True))
    finally:
        await service.close()
```

2. `test_views_with_allowed_expressions_return_data` и `test_public_operator_over_a_public_function_passes` (PL/pgSQL-функции `app_double`, `app_close_to`) перевести на `db_plan_check_non_sql`:

```python
@pytest.mark.asyncio
async def test_views_with_allowed_expressions_return_data(db_plan_check_non_sql: DbAccess) -> None:
    """app_double — PL/pgSQL: проходит только с plan_check_allow_non_sql_functions=true."""
    lower_rows = await db_plan_check_non_sql.sql_driver.execute("SELECT l FROM app_expr_lower_view", readonly=True)
    public_rows = await db_plan_check_non_sql.sql_driver.execute("SELECT d FROM app_expr_public_fn_view", readonly=True)
    assert "1" in [row.cells["l"] for row in lower_rows]
    assert 2 in [row.cells["d"] for row in public_rows]


@pytest.mark.asyncio
async def test_public_operator_over_a_public_function_passes(db_plan_check_non_sql: DbAccess) -> None:
    """app_close_to — PL/pgSQL: проходит только с plan_check_allow_non_sql_functions=true."""
    rows = await db_plan_check_non_sql.sql_driver.execute(
        "SELECT (id <~> 2) AS near FROM app_plan_items", readonly=True
    )
    assert rows[0].cells["near"] is True
```

3. Фикстуру `db_typed_chains` перевести на `db_plan_check_non_sql` (ввод-вывод `app_ic_okt` — `LANGUAGE internal`) и после `test_type_in_the_deepest_body_does_not_cost_depth` добавить тест отказа по умолчанию:

```python
@pytest.fixture
async def db_typed_chains(db_plan_check_non_sql: DbAccess, db_full: DbAccess) -> AsyncGenerator[DbAccess, None]:
    """db_plan_check_non_sql, пока в public есть цепочки SQL-функций глубины 3, 5 и 6 с типом app_ic_okt в самом
    глубоком теле. Ввод-вывод app_ic_okt — LANGUAGE internal: без plan_check_allow_non_sql_functions тип отклонялся бы."""
    setup = _TYPED_CHAIN_TYPE + "\n".join(_typed_chain(depth) for depth in (3, 5, 6))
    await db_full.sql_driver.execute(_DROP_TYPED_CHAINS + setup, readonly=False)
    try:
        yield db_plan_check_non_sql
    finally:
        await db_full.sql_driver.execute(_DROP_TYPED_CHAINS, readonly=False)


# ... test_type_in_the_deepest_body_does_not_cost_depth без изменений ...


@pytest.mark.asyncio
async def test_type_with_non_sql_io_functions_is_rejected_by_default(
    db_typed_chains: DbAccess, db_plan_check: DbAccess
) -> None:
    """По умолчанию ввод-вывод app_ic_okt (обёртки LANGUAGE internal в public) — функции не на sql: отказ."""
    with pytest.raises(PlanAccessError, match=r"function 'public\.app_ic_okt_(in|out)'.*LANGUAGE internal"):
        await db_plan_check.sql_driver.execute("SELECT v FROM app_ic_chain3_view", readonly=True)
```

4. В конец файла добавить:

```python
# Функции public не на языке sql (PL/pgSQL, internal): тело не проверить, по умолчанию — отказ. Бросающие функции
# доказывают «отклонено до выполнения» (EXPLAIN свернул бы IMMUTABLE-вызов, INSERT выполнил бы триггер), чтение
# secret.accounts — «отклонено, хотя без проверки запрос отдал бы секрет». Агрегат (prolang internal) и функция
# расширения (moddatetime, C) не в счёт.
_DROP_NON_SQL = """
DROP VIEW IF EXISTS public.app_ns_boom_view, public.app_ns_leak_view, public.app_ns_setting_view,
    public.app_ns_supported_view, public.app_ns_agg_view;
DROP TABLE IF EXISTS public.app_ns_notes, public.app_ns_trg, public.app_ns_stamped;
DROP OPERATOR IF EXISTS public.#!# (int, int);
DROP AGGREGATE IF EXISTS public.app_ns_agg(text);
DROP TYPE IF EXISTS public.app_ns_e CASCADE;
DROP FUNCTION IF EXISTS public.app_ns_boom(), public.app_ns_leak(), public.app_ns_fill_note(), public.app_ns_trg_boom(),
    public.app_ns_setting(text), public.app_ns_opf(int, int), public.app_ns_supported(int), public.app_ns_cat(text, text),
    secret.ns_support(internal);
DROP EXTENSION IF EXISTS moddatetime;
"""
_NON_SQL = """
CREATE FUNCTION public.app_ns_boom() RETURNS int
    LANGUAGE plpgsql IMMUTABLE AS $$BEGIN RAISE EXCEPTION 'public.app_ns_boom was executed'; END$$;
CREATE VIEW public.app_ns_boom_view AS SELECT public.app_ns_boom() AS b;
CREATE FUNCTION public.app_ns_leak() RETURNS text
    LANGUAGE plpgsql STABLE AS $$BEGIN RETURN (SELECT token FROM secret.accounts LIMIT 1); END$$;
CREATE VIEW public.app_ns_leak_view AS SELECT public.app_ns_leak() AS t;
CREATE TABLE public.app_ns_notes (id int, note text);
CREATE FUNCTION public.app_ns_fill_note() RETURNS trigger LANGUAGE plpgsql
    AS $$BEGIN NEW.note := (SELECT token FROM secret.accounts LIMIT 1); RETURN NEW; END$$;
CREATE TRIGGER app_ns_notes_fill BEFORE INSERT ON public.app_ns_notes
    FOR EACH ROW EXECUTE FUNCTION public.app_ns_fill_note();
CREATE TABLE public.app_ns_trg (id int);
CREATE FUNCTION public.app_ns_trg_boom() RETURNS trigger
    LANGUAGE plpgsql AS $$BEGIN RAISE EXCEPTION 'public.app_ns_trg_boom was executed'; END$$;
CREATE TRIGGER app_ns_trg_boom BEFORE INSERT ON public.app_ns_trg
    FOR EACH ROW EXECUTE FUNCTION public.app_ns_trg_boom();
CREATE FUNCTION public.app_ns_setting(text) RETURNS text LANGUAGE internal STABLE STRICT AS 'show_config_by_name';
CREATE VIEW public.app_ns_setting_view AS SELECT public.app_ns_setting('data_directory') AS s;
CREATE FUNCTION public.app_ns_opf(a int, b int) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE AS $$BEGIN RAISE EXCEPTION 'public.app_ns_opf was executed'; END$$;
CREATE OPERATOR public.#!# (LEFTARG = int, RIGHTARG = int, FUNCTION = public.app_ns_opf);
CREATE TYPE public.app_ns_e AS ENUM ('a');
CREATE FUNCTION public.app_ns_to_e(n int) RETURNS public.app_ns_e
    LANGUAGE plpgsql IMMUTABLE AS $$BEGIN RAISE EXCEPTION 'public.app_ns_to_e was executed'; END$$;
CREATE CAST (int AS public.app_ns_e) WITH FUNCTION public.app_ns_to_e(int);
CREATE FUNCTION secret.ns_support(internal) RETURNS internal LANGUAGE internal AS 'textlike_support';
CREATE FUNCTION public.app_ns_supported(a int) RETURNS int
    LANGUAGE sql IMMUTABLE SUPPORT secret.ns_support AS 'SELECT a';
CREATE VIEW public.app_ns_supported_view AS SELECT public.app_ns_supported(id) AS v FROM public.app_plan_items;
CREATE FUNCTION public.app_ns_cat(s text, v text) RETURNS text LANGUAGE sql IMMUTABLE AS $$SELECT coalesce(s, '') || v$$;
CREATE AGGREGATE public.app_ns_agg(text) (SFUNC = public.app_ns_cat, STYPE = text);
CREATE VIEW public.app_ns_agg_view AS SELECT public.app_ns_agg(id::text) AS c FROM public.app_plan_items;
CREATE EXTENSION moddatetime;
CREATE TABLE public.app_ns_stamped (id int, updated_at timestamp);
CREATE TRIGGER app_ns_stamped_mod BEFORE UPDATE ON public.app_ns_stamped
    FOR EACH ROW EXECUTE FUNCTION moddatetime(updated_at);
INSERT INTO public.app_ns_stamped VALUES (1, NULL);
"""


@pytest.fixture
async def db_non_sql(db_plan_check: DbAccess, db_full: DbAccess) -> AsyncGenerator[DbAccess, None]:
    """db_plan_check, пока в public есть функции не на sql: бросающие, читающие secret.accounts, обёртка internal."""
    await db_full.sql_driver.execute(_DROP_NON_SQL + _NON_SQL, readonly=False)
    try:
        yield db_plan_check
    finally:
        await db_full.sql_driver.execute(_DROP_NON_SQL, readonly=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sql", "function", "language"),
    [
        pytest.param("SELECT b FROM app_ns_boom_view", "app_ns_boom", "plpgsql", id="folded-in-a-view"),
        pytest.param("INSERT INTO app_ns_trg (id) VALUES (1)", "app_ns_trg_boom", "plpgsql", id="trigger-function"),
        pytest.param("SELECT 1 #!# 2 AS b", "app_ns_opf", "plpgsql", id="operator-function"),
        pytest.param("SELECT 1::app_ns_e AS e", "app_ns_to_e", "plpgsql", id="cast-function"),
        pytest.param("SELECT t FROM app_ns_leak_view", "app_ns_leak", "plpgsql", id="secret-read-in-a-view"),
        pytest.param(
            "INSERT INTO app_ns_notes (id) VALUES (1) RETURNING note",
            "app_ns_fill_note",
            "plpgsql",
            id="secret-read-in-a-trigger",
        ),
        pytest.param("SELECT s FROM app_ns_setting_view", "app_ns_setting", "internal", id="internal-builtin-wrapper"),
    ],
)
async def test_non_sql_public_function_is_rejected_before_it_runs(
    db_non_sql: DbAccess, sql: str, function: str, language: str
) -> None:
    """Функция public не на sql, до которой доходит запрос (представление, триггер цели DML, оператор, приведение):
    без проверки EXPLAIN или выполнение вызвали бы её — бросающая дала бы своё исключение, читающая secret.accounts
    отдала бы секрет, обёртка internal над show_config_by_name — настройку сервера в обход списка basic."""
    with pytest.raises(PlanAccessError, match=rf"function 'public\.{function}'.*LANGUAGE {language}"):
        await db_non_sql.sql_driver.execute(sql, readonly=False)


@pytest.mark.asyncio
async def test_planner_support_function_of_a_public_function_is_checked(db_non_sql: DbAccess) -> None:
    """SUPPORT secret.ns_support у SQL-функции public: планировщик вызвал бы её для каждого вызова функции."""
    with pytest.raises(PlanAccessError, match=r"function 'secret\.ns_support'"):
        await db_non_sql.sql_driver.execute("SELECT v FROM app_ns_supported_view", readonly=True)


@pytest.mark.asyncio
async def test_aggregates_and_extension_trigger_functions_pass(db_non_sql: DbAccess) -> None:
    """Агрегат public (prolang internal) проверяется по опорным функциям; moddatetime — функция расширения (C)."""
    aggregated = await db_non_sql.sql_driver.execute("SELECT c FROM app_ns_agg_view", readonly=True)
    updated = await db_non_sql.sql_driver.execute(
        "UPDATE app_ns_stamped SET id = 2 WHERE id = 1 RETURNING updated_at", readonly=False
    )
    assert aggregated[0].cells["c"]
    assert updated[0].cells["updated_at"] is not None


@pytest.mark.asyncio
async def test_non_sql_functions_run_unchecked_when_allowed(
    db_non_sql: DbAccess, db_plan_check_non_sql: DbAccess
) -> None:
    """plan_check_allow_non_sql_functions=true — прежнее поведение и его цена: тело PL/pgSQL читает secret.accounts."""
    rows = await db_plan_check_non_sql.sql_driver.execute("SELECT t FROM app_ns_leak_view", readonly=True)
    assert rows[0].cells["t"] == "top-secret"
```

Run (без Docker): `uv run pytest tests/integration -q` — собирается, пропускается. Со своим Postgres (плагин подмены `create_postgres_container`, как в плане 20): `uv run pytest -p <plugin> tests/integration/test_plan_check.py -k "postgres:16" -q` — 123 passed; дважды подряд — тоже.

- [ ] **Step 7: README**

Применить (из корня репозитория) — текст правок целиком:

```python
p = "README.md"
s = open(p).read()
def rep(old, new):
    global s
    assert s.count(old) == 1, old[:60]
    s = s.replace(old, new)
rep("`table_prefix`, `plan_check`, `sslmode`", "`table_prefix`, `plan_check`, `plan_check_allow_non_sql_functions`, `sslmode`")
marker = "— и те же приведения и сортировки внутри представлений.\n"
rep(marker, marker + """- **Функции `public` не на SQL.** Тело функции на PL/pgSQL, PL/Python, C или `LANGUAGE internal` проверка не видит: PL/pgSQL выполняет и динамический SQL (`EXECUTE`), C-функция — любой код, функция `internal` — любую встроенную функцию под своим именем (`CREATE FUNCTION app_setting(text) … LANGUAGE internal AS 'show_config_by_name'` — это `current_setting` в обход списка basic). Поэтому по умолчанию запрос, который доходит до такой функции `public` — любым путём выше: представление, правило, выражение плана, триггер цели DML, функция оператора или опорная функция агрегата, приведение, машинерия типа, умолчание аргумента, другое тело, — отклоняется до `PREPARE` или `EXPLAIN`: `Access to function 'public.app_touch' is not allowed in basic mode: the query reaches it, and its body in LANGUAGE plpgsql cannot be verified …`. Не в счёт агрегаты (их опорные функции проверяются сами) и функции расширений (`CREATE EXTENSION`: PostGIS, `moddatetime` и прочие — доверены, как их машинерия типов). Исправить: переписать функцию на `LANGUAGE sql` (её тело проверяется) или включить `plan_check_allow_non_sql_functions=true` (env `MCP_DATABASE_PLAN_CHECK_ALLOW_NON_SQL_FUNCTIONS=true`) — тогда такие функции проходят, как до этой настройки, и их тела выполняются непроверенными: PL/pgSQL-функция `public` поверх `secret.*` отдаёт его данные. Опорная функция планировщика (`CREATE FUNCTION … SUPPORT f`: её вызывает планировщик для каждого вызова) проверяется как функция машинерии типа.
""")
rep("- тела триггерных функций (функция триггера из чужой схемы отклоняется, её тело на PL/pgSQL непрозрачно);\n- функции PL/pgSQL и других процедурных языков — их тело непрозрачно; блокировка",
    "- при `plan_check_allow_non_sql_functions=true` — тела функций `public` не на SQL (PL/pgSQL, PL/Python, C, `internal`), в том числе триггерных: они непрозрачны; по умолчанию такие функции отклоняются (см. выше). Функции расширений доверены всегда — и те, что выполняют переданный им текст SQL (`crosstab` из `tablefunc`, `dblink`): их запрос проверка не видит;\n- блокировка")
rep("- SQL-функции `public`, меняющие данные", "- функции `public` не на SQL (PL/pgSQL, PL/Python, C, `internal`), не из расширения, до которых доходит запрос, — по умолчанию (`plan_check_allow_non_sql_functions=false`), даже если тело безобидно. Самые частые случаи: триггер `updated_at` или аудита на PL/pgSQL (отклоняются `INSERT`/`UPDATE`/`DELETE` таблицы и таблиц, ссылающихся на неё каскадом; `SELECT` — нет), PL/pgSQL-функция в представлении, функции приведения, операторов и ввода-вывода базовых типов `public`, созданных без расширения (колонка такого типа отклоняет любой запрос к своей таблице). Проверяются все перегрузки с именем: функция на PL/pgSQL отклоняет и вызов одноимённой SQL-перегрузки, а функция `public`, одноимённая встроенной (`public.lower(app_t)`), — любой вызов этого имени. Триггерную функцию на SQL не написать (Postgres не принимает `LANGUAGE sql … RETURNS trigger`), так что любой свой триггер в `public` — отказ: для `updated_at` есть расширение `moddatetime` (contrib, доверено), иначе — `plan_check_allow_non_sql_functions`; прочие функции можно переписать на SQL;\n- SQL-функции `public`, меняющие данные")
open(p, "w").write(s)
```

И проверить глазами раздел «Проверка по плану»: новый пункт стоит после «Машинерия типов», пункт «не закрывает» про тела PL/pgSQL заменён, в «отклоняет, хотя легитимно» первым идёт пункт о функциях не на SQL.

- [ ] **Step 8: Проверки и коммит**

```bash
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
uv run pytest tests/unit -q
uv run pytest tests/integration -q
git add src tests README.md
git commit -m "feat(security): reject non-SQL public functions reached by plan_check"
```

Статус спеки (`docs/superpowers/specs/2026-10-02-plan-check-procedural-bodies-design.md`, строка «Статус») — «реализовано» — отдельным коммитом `docs(specs): mark the plan_check procedural bodies wave as implemented`.
