# `plan_check`: определения до планирования, операторы и агрегаты `public` — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** С `plan_check=true` (basic) правила и зависимости представлений проверяются до того, как планировщик что-либо выполнит (`PREPARE` → правила → `EXPLAIN`), а операторы и агрегаты `public` — в SQL агента и в выражениях плана — проверяются по функциям, которые их реализуют.

**Architecture:** `PlanGuard.check` получает новый порядок: типы и реализации операторов/агрегатов SQL агента → `PREPARE x AS <оператор>; DEALLOCATE x` каждого планируемого оператора (разбор и переписывание берут блокировки, план не строится) → запрос правил (`RULE_DEPENDENCIES_SQL`, как сейчас) → `EXPLAIN` и проверка плана каждого оператора → повторный запрос правил (представления, до которых дошёл только планировщик). Новый запрос каталога `plan_catalog.ALLOWED_IMPLEMENTATIONS_SQL` по именам операторов и функций без схемы (или со схемой `allowed_schema`) отдаёт строки того же вида, что и запрос правил (`operator_function`, `aggregate_function`, `operator`), и их проверяет тот же `_check_rule_row`. Спека: `docs/superpowers/specs/2026-09-30-plan-check-wave-3-design.md` §2–§3.

**Tech Stack:** Python 3.12, uv, pglast v8.2 (`parse_sql`, `Visitor`, `RawStream`), psycopg 3.3.4 (`psycopg.sql`, `psycopg.errors.IndeterminateDatatype`), pytest (asyncio auto).

## Spec corrections

Проверено на живом PostgreSQL 17.10 (тот же временный сервер из `.deb`, что и для прошлой волны, `localhost:55432`); PG 15/16 — по исходникам (`ruleutils.c` 15/16 в scratchpad) и документации. План опробован: код и тесты задач применены к копии репозитория — `pytest tests/unit/postgres` проходит, `mypy src/` чист, `tests/integration/test_plan_check.py` проходит на живом PG 17 (PR A: 36, PR A + PR B: 44).

- **(a) `PREPARE` ничего не выполняет, блокировки берёт.** Живьём: `PREPARE p AS SELECT * FROM v` для `v AS SELECT secret.api_key()` (IMMUTABLE, `RAISE NOTICE`) — уведомления нет, на `v` — `AccessShareLock`; `EXPLAIN (VERBOSE)` того же — уведомление есть, в `Output` уже `'k'::text`. `PREPARE INSERT`/`UPDATE` — `RowExclusiveLock` на цель, `AccessShareLock` на читаемые таблицы. Подтверждено для 15–17: `PREPARE` = `parse_analyze_varparams` + `pg_rewrite_query` + `CachedPlanSource` без плана.
- **`DEALLOCATE` не в `finally`, а той же командой.** Живьём: подготовленный оператор переживает `ROLLBACK` (`pg_prepared_statements` его показывает), а `DEALLOCATE` в прерванной транзакции падает `InFailedSqlTransaction` — в `finally` он подменил бы исходную ошибку. Поэтому команда одна: `PREPARE _pgmcp_check_<16 hex>_<n> AS <оператор>; DEALLOCATE _pgmcp_check_<16 hex>_<n>`. Блокировки держит транзакция — после `DEALLOCATE` они остаются (живьём: `AccessShareLock` на `v` на месте). Упал `PREPARE` — `DEALLOCATE` той же строки не выполняется, снимать нечего, транзакцию откатывает `SqlExecutor`. Имя уникально на проверку (`secrets.token_hex(8)`) и на оператор (счётчик); `DISCARD ALL` пула — страховка, не основа.
- **Порядок — не «на оператор», а на строку, и правила читаются дважды.** Живьём: `SELECT * FROM srf2()` (SQL-функция `STABLE`, тело `SELECT * FROM public.v_sec`) после `PREPARE` не держит ни одной блокировки; `v_sec` блокирует и её `secret.api_key()` выполняет только планировщик (встраивание SQL-функции). Поэтому: `PREPARE` всех операторов → одно чтение правил → `EXPLAIN` всех → ещё одно чтение правил (как сейчас). Второе чтение сохраняет всё, что проверялось раньше, — ослабления нет; уже проверенные строки не проверяются повторно. Свёртка во встраиваемой SQL-функции до отказа остаётся (закрывается в PR B: тела функций, названных в SQL агента, проверяются до `PREPARE`).
- **`$N` и `GENERIC_PLAN`.** Живьём: `PREPARE` без типов выводит их из контекста (`id = $1`, `$1 || 'a'`), но там, где `EXPLAIN (GENERIC_PLAN)` проходит, падает `42P18 could not determine data type of parameter $1` (`$1 IS NULL`, `pg_typeof($1)`, `k = $1` над представлением); явный тип `unknown` не помогает. Ошибка прерывает транзакцию, поэтому запасной путь — точка сохранения: оператор с `GENERIC_PLAN` готовится как `SAVEPOINT _pgmcp_check; PREPARE …; DEALLOCATE …; RELEASE SAVEPOINT _pgmcp_check`, при `IndeterminateDatatype` — `ROLLBACK TO SAVEPOINT _pgmcp_check; RELEASE SAVEPOINT _pgmcp_check`, и оператор готовится ещё раз (тоже в точке сохранения) с `NULL` вместо каждого `$N` — правила читаются до `EXPLAIN`, как у прочих операторов (исправление 95191c4: прежний путь «правила после `EXPLAIN`» выполнял свёрнутые функции представлений до отказа). Не прошёл повтор или текст с `NULL` не разбирается (`($2)[1]` → `NULL[1]`) — `PlanUnverifiableError`, `EXPLAIN` не выполняется. Любая другая ошибка первого `PREPARE` — ошибка Postgres, как ошибка `EXPLAIN` раньше (та же ошибка разбора).
- **Текст отказа у представлений меняется.** Правила теперь проверяются раньше узлов плана, и представление, запрещённое и по узлам, и по определению, отклоняется по определению. Живьём (интеграция на PG 17 с прототипом этого плана): `SELECT table_name FROM information_schema.tables` → `Access to type 'information_schema.sql_identifier' …` вместо `relation 'pg_catalog.…'`. Отказ остаётся `PlanAccessError`; интеграционный тест `test_information_schema_is_rejected_with_plan_check` правится (Task 1 Step 6), в заметках к PR — изменение текста.
- **Обёртки.** Живьём: `PREPARE p AS EXPLAIN …` и `PREPARE p AS DECLARE …` — синтаксическая ошибка. Готовится вложенный запрос (`_plannable`) — тот же текст, что уходит в `EXPLAIN`.
- **(b) Операторы без схемы.** `generate_operator_name` (одинаково в 15/16/17) печатает имя без схемы, если `oper()` по имени и типам аргументов в `search_path` находит тот же оператор; иначе `OPERATOR(схема.op)`. Живьём: `public.===(int, text)` печатается `(app_t.id === app_t.v)`. Типов в тексте нет, поэтому правило спеки «если оператора с таким именем и типами нет среди встроенных» не вычислимо, а «нет встроенного с таким именем» пропустило бы `public.=(int, text)` над `current_setting`. Решение: каждое имя оператора без схемы или со схемой `allowed_schema` ищется в `allowed_schema` целиком (все перегрузки). Так же — каждое имя функции без схемы или `allowed_schema`, включая имена из списка basic (`count`): агрегат `public.count(mytype)` печатается так же, как встроенный. Цена — один запрос на фазу (до `PREPARE` и после всех `EXPLAIN`, второй — только для новых имён), практически для любого запроса с `=`.
- **Операторы и агрегаты SQL агента — до `PREPARE`.** Спека §3 говорит только о выражениях плана; но IMMUTABLE-функция оператора с константами сворачивается при `EXPLAIN` так же, как в представлении. Имена операторов (`A_Expr`, `SubLink.operName`, `SortBy.useOp`, а также операторы, которые подставляет разбор: `BETWEEN` — `>=`/`<=`, `NOT BETWEEN` — `<`/`>`, `CASE x WHEN`, `JOIN … USING`, `NATURAL JOIN`, `x IN (подзапрос)` — `=`) и функций планируемых операторов SQL агента проверяются тем же запросом до `PREPARE`. Валидатор basic пускает в SQL агента только функции из списка basic (`FunctionNotAllowedError` для прочих), поэтому агрегат `public` агент может вызвать только как перегрузку разрешённого имени (`public.sum(text)`) — на этом и строится интеграционный тест; операторы валидатор ограничивает только по схеме.
- **`aggsortop`.** Не в спеке: для `min`/`max` планировщик заменяет агрегат индексным сканом с оператором сортировки агрегата. Оператор `aggsortop` проверяется как строка `operator`, его `oprcode` — как `operator_function`. Живьём: `CREATE AGGREGATE public.mymin(int) (SFUNC = int4smaller, STYPE = int, SORTOP = OPERATOR(public.<<<))` даёт строки `operator public.<<<` и `operator_function public.mylt`.
- **Колонки `pg_aggregate`** в 15–17 одинаковы (`aggtransfn`, `aggfinalfn`, `aggcombinefn`, `aggserialfn`, `aggdeserialfn`, `aggmtransfn`, `aggminvtransfn`, `aggmfinalfn` — `regproc`, `0` = нет; `aggsortop` — `oid`), проверено живьём на 17.
- **Встроенные функции реализации.** Оператор или агрегат `public` поверх функции `pg_catalog` вне списка basic (`SFUNC = int4pl`, `FUNCTION = pg_catalog.textcat`) отклоняется — то же правило, что уже действует в проверке зависимостей представлений; в README — в «отклоняет, хотя легитимно».

## Global Constraints

- Всё, что видит агент или внешняя система (ошибки, логи, коммиты), — на английском; docstring и комментарии — по-русски. README — по-русски.
- Нет `from __future__ import annotations`.
- Ломающие изменения разрешены; описываются только в заметках к PR. В коде — никаких шимов и упоминаний старого поведения.
- Безопасность: ни одно правило валидатора и существующей проверки по плану не ослабляется (узлы, выражения, правила после `EXPLAIN` проверяются как раньше); `full` и `plan_check=false` не затрагиваются (`PlanGuard` создаётся только в `precheck` при `plan_check` с `allowed_schema`). Весь SQL каталога — с `pg_catalog.` у каждого отношения, функции и типа и `OPERATOR(pg_catalog.…)` у каждого оператора (в том числе на смешанных типах); имена — `Literal`.
- Перед каждым коммитом: `uv run ruff check --fix --show-fixes`, `uv run mypy src/`, `uv run ruff format`; затем `uv run ruff check .`.
- Юнит-тесты: `uv run pytest tests/unit -q`. Интеграция: `uv run pytest tests/integration -q` — должна собираться; без Docker пропускается, в CI — Postgres 15/16. Сверять интеграционные тесты статически с кодом.
- Коммиты: `type(scope): message`, повелительное наклонение, английский.
- **Никогда не запускать никакие `git config` и `git stash`.** Не пушить.
- Ветка: `claude/plan-check-prepare-first` (от `main` e403e2b, на ней коммит спеки 0e8cfc9 и этот план).

---

### Task 1: `PREPARE` → правила → `EXPLAIN`

**Files:**
- Modify: `src/postgres_fastmcp/postgres/security/plan_guard.py` (docstring модуля, импорты, константы, `PlanGuard.__init__`, `check`, новый `_prepare`, `_check_rules`, новая `_row_key`)
- Modify: `src/postgres_fastmcp/postgres/security/driver.py` (docstring `_checked_run`, комментарий в `precheck`)
- Modify: `src/postgres_fastmcp/postgres/driver.py` (docstring `_execute_with_connection`)
- Modify: `README.md` (раздел «Проверка по плану»)
- Test: `tests/unit/postgres/test_plan_guard.py`, `tests/unit/postgres/test_safe_sql_executor.py`, `tests/integration/test_plan_check.py`

**Interfaces:**
- Consumes: `plan_catalog.RULE_DEPENDENCIES_SQL` (без изменений), `_plannable`, `_check_rule_row`, `_check_catalog_names` (без изменений).
- Produces (в `plan_guard.py`):
  - `_PREPARED_PREFIX = "_pgmcp_check"`, `_SAVEPOINT = "_pgmcp_check"`;
  - `PlanGuard._prepare(self, text: str, *, generic: bool) -> None` — `PREPARE`+`DEALLOCATE` одной командой; при `generic` — в точке сохранения с откатом на `IndeterminateDatatype`;
  - `PlanGuard._prepared_tag: str` (16 hex), `PlanGuard._prepared_count: int`, `PlanGuard._seen_rows: set[tuple[object, ...]]`;
  - `_row_key(cells: dict[str, Any]) -> tuple[object, ...]`;
  - в тестах: `_Explain(..., prepare_errors: dict[str, Exception] | None = None)`, `_Explain.prepared: list[str]` (команды `PREPARE`/`SAVEPOINT`/`ROLLBACK TO SAVEPOINT` не попадают в `sent`).

- [ ] **Step 1: Научить фальшивый исполнитель `PREPARE` и точкам сохранения**

В `tests/unit/postgres/test_plan_guard.py` добавить импорты:

```python
import re

from psycopg.errors import IndeterminateDatatype, UndefinedTable
```

В `_Explain.__init__` добавить параметр `prepare_errors: dict[str, Exception] | None = None,` (последним) и поля:

```python
        # Фрагмент текста PREPARE -> ошибка, которую Postgres вернул бы на эту команду.
        self._prepare_errors = prepare_errors or {}
        # PREPARE ... ; DEALLOCATE ..., SAVEPOINT ... и ROLLBACK TO SAVEPOINT ... — в sent не попадают.
        self.prepared: list[str] = []
```

В `_Explain.__call__` (у объявления — `# noqa: PLR0911`: веток ответа становится больше шести) сразу после ветки `pg_catalog.pg_rewrite` вставить:

```python
        if sql.startswith(("PREPARE ", "SAVEPOINT ", "ROLLBACK TO SAVEPOINT ")):
            self.prepared.append(sql)
            if not sql.startswith("ROLLBACK"):
                for fragment, error in self._prepare_errors.items():
                    if fragment in sql:
                        raise error
            return None
```

`_Rows` отвечает на запрос правил пустым списком (иначе `None`/мусорная строка теперь падала бы на чтении правил до `EXPLAIN`, а тест проверяет ответ `EXPLAIN`):

```python
class _Rows:
    """Исполнитель EXPLAIN, который возвращает заданные строки как есть; правил у представлений нет."""

    def __init__(self, rows: list[RowResult] | None) -> None:
        self._rows = rows

    async def __call__(self, sql: str) -> list[RowResult] | None:
        if "pg_catalog.pg_rewrite" in sql:
            return []
        return self._rows
```

- [ ] **Step 2: Написать падающие тесты порядка**

Заменить `test_rules_are_read_once_after_every_explain` на тесты ниже; `test_rules_are_not_read_after_a_rejected_plan` — на `test_rules_are_not_read_again_after_a_rejected_plan`; в `test_allowed_dependencies_of_views_and_rules_pass` заменить `assert len(explain.rule_queries) == 1` на `assert len(explain.rule_queries) == 2`.

```python
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
    "sql", ["EXPLAIN SELECT * FROM app_t", "EXPLAIN ANALYZE SELECT * FROM app_t", "DECLARE c CURSOR FOR SELECT * FROM app_t"]
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


async def test_undeterminable_parameter_falls_back_to_rules_after_explain() -> None:
    """PREPARE не выводит тип $1 там, где EXPLAIN (GENERIC_PLAN) проходит: точка сохранения откатывается."""
    error = IndeterminateDatatype("could not determine data type of parameter $1")
    explain = _Explain(prepare_errors={"$1 IS NULL": error})

    await _guard(explain).check("EXPLAIN (GENERIC_PLAN) SELECT $1 IS NULL FROM app_t")

    assert explain.prepared[-1] == "ROLLBACK TO SAVEPOINT _pgmcp_check; RELEASE SAVEPOINT _pgmcp_check"
    assert explain.sent == ["EXPLAIN (VERBOSE, FORMAT JSON, GENERIC_PLAN) SELECT $1 IS NULL FROM app_t"]
    assert len(explain.rule_queries) == 2


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


async def test_rules_are_not_read_again_after_a_rejected_plan() -> None:
    explain = _Explain({_EXPLAIN + _SELECT: _scan("secret", "accounts")})

    with pytest.raises(PlanAccessError):
        await _guard(explain).check(_SELECT)

    assert len(explain.rule_queries) == 1
```

В `tests/unit/postgres/test_safe_sql_executor.py` (`import re` в импорты) в `TestSafeSqlExecutorPlanCheck.test_plan_is_checked_in_the_statement_transaction` четыре строки от `assert delegate.sent[:2] == …` до `assert delegate.sent[3:] == …` заменить на:

```python
        assert delegate.sent[0] == _SETTINGS
        prepared = re.fullmatch(
            r"/\* t \*/ PREPARE (_pgmcp_check_[0-9a-f]{16}_0) AS SELECT \* FROM app_t; DEALLOCATE \1", delegate.sent[1]
        )
        assert prepared is not None
        # Правила представлений читаются после PREPARE (он их заблокировал, план не строился), с тегом, ...
        assert delegate.sent[2].startswith("/* t */ WITH RECURSIVE rules AS")
        assert "pg_catalog.pg_rewrite" in delegate.sent[2]
        assert delegate.sent[3] == "/* t */ EXPLAIN (VERBOSE, FORMAT JSON) SELECT * FROM app_t"
        # ... и ещё раз после EXPLAIN, до оператора.
        assert "pg_catalog.pg_rewrite" in delegate.sent[4]
        assert delegate.sent[5:] == ["/* t */ SELECT * FROM app_t"]
```

Остальные тесты `TestSafeSqlExecutorPlanCheck` не меняются: фальшивый исполнитель отвечает на `PREPARE` пустым списком, как на запросы каталога.

- [ ] **Step 3: Запустить тесты — падают**

Run: `uv run pytest tests/unit/postgres/test_plan_guard.py tests/unit/postgres/test_safe_sql_executor.py -q`
Expected: FAIL — `explain.prepared == []` (PREPARE не отправляется), `test_definitions_are_checked_between_prepare_and_explain` видит `EXPLAIN app_a, EXPLAIN app_b, rules`, `rule_queries` длины 1 вместо 2.

- [ ] **Step 4: Реализовать новый порядок в `plan_guard.py`**

Docstring модуля: абзац «До первого EXPLAIN проверяются типы … функции операторов и агрегатов public).» заменить на:

```python
"""...
Порядок. До всего, что разбирает SQL агента на сервере, проверяются его типы (ошибка разбора раскрыла бы структуру
таблицы без префикса). Затем каждый планируемый оператор готовится и тут же снимается (PREPARE; DEALLOCATE одной
командой): разбор и переписывание берут блокировки представлений и таблиц до конца транзакции, но план не строится
и функции не выполняются. После этого читаются правила заблокированного — текст правила и его зависимости
(pg_depend), чего план не показывает (свёртка констант, LIMIT, функции операторов и агрегатов public). Только потом
EXPLAIN: планировщик сворачивает IMMUTABLE-вызовы, то есть выполняет их, и отклонённое представление до него не
доходит. После всех EXPLAIN правила читаются ещё раз — представления, до которых дошёл только планировщик
(встраивание SQL-функций, оператор GENERIC_PLAN, тип параметра которого PREPARE не вывел).
..."""
```

(остальные абзацы docstring без изменений). Импорты — добавить:

```python
import secrets
```

```python
from psycopg.errors import IndeterminateDatatype
```

После `_FUNCTION_SCAN_TYPE = "Function Scan"`:

```python
# Служебные имена проверки: подготовленный оператор (_pgmcp_check_<метка проверки>_<номер>) и точка сохранения
# для оператора с GENERIC_PLAN. Метка — случайная на каждую проверку: подготовленный оператор переживает ROLLBACK,
# и имя, оставшееся в соединении после сбоя, не должно совпасть со следующим.
_PREPARED_PREFIX = "_pgmcp_check"
_SAVEPOINT = "_pgmcp_check"
```

После `_plan_document` добавить:

```python
def _row_key(cells: dict[str, Any]) -> tuple[object, ...]:
    """Строка каталога как ключ множества: списки (массивы Postgres) — кортежами."""
    return tuple((key, tuple(value) if isinstance(value, list) else value) for key, value in sorted(cells.items()))
```

В конец `PlanGuard.__init__`:

```python
        self._prepared_tag = secrets.token_hex(8)
        self._prepared_count = 0
        # Строки правил, уже проверенные в этой проверке: второе чтение после EXPLAIN их не повторяет.
        self._seen_rows: set[tuple[object, ...]] = set()
```

`check` целиком:

```python
    async def check(self, query: str) -> None:
        """Проверить определения, до которых доходит запрос, затем план каждого планируемого оператора.

        Запрос уже прошёл валидатор, поэтому разбирается без ошибок. Ошибка разбора (отношения нет) приходит
        из исполнителя как ошибка Postgres — та же, что дало бы выполнение.

        Args:
            query: SQL агента после валидации (с тегом-комментарием).

        Raises:
            PlanAccessError: Запрос доходит до отношения, функции или типа вне разрешённого.
            PlanUnverifiableError: Плана нет, узел сканирования не называет, что читает, или выражение
                не разбирается.
        """
        statements = [raw.stmt for raw in pglast.parse_sql(query)]
        await self._check_statement_types(statements)
        plannable = [target for target in (_plannable(statement) for statement in statements) if target is not None]
        if not plannable:
            return
        targets = [(RawStream()(statement), generic) for statement, generic in plannable]
        for text, generic in targets:
            await self._prepare(text, generic=generic)
        await self._check_rules()
        for text, generic in targets:
            options = "VERBOSE, FORMAT JSON, GENERIC_PLAN" if generic else "VERBOSE, FORMAT JSON"
            rows = await self._run(f"EXPLAIN ({options}) {text}")
            await self._check_plan(_plan_document(rows))
        await self._check_rules()

    async def _prepare(self, text: str, *, generic: bool) -> None:
        """Разобрать и переписать оператор без планирования: блокировки представлений и таблиц до конца транзакции.

        PREPARE строит дерево запроса (разбор, переписывание) и берёт AccessShareLock на представления и таблицы,
        RowExclusiveLock на цели DML, но план не строит: IMMUTABLE-вызовы не сворачиваются, функции не выполняются.
        DEALLOCATE — той же командой: блокировки держит транзакция, а подготовленный оператор пережил бы ROLLBACK.
        Упал PREPARE — DEALLOCATE строки не выполняется: снимать нечего.

        GENERIC_PLAN: PREPARE без типов выводит типы $N из контекста, но, в отличие от EXPLAIN (GENERIC_PLAN),
        не принимает невыводимый ($1 IS NULL, pg_typeof($1)) — 42P18. Такой оператор готовится в точке сохранения;
        при 42P18 она откатывается, и его представления проверяет чтение правил после EXPLAIN.
        """
        name = f"{_PREPARED_PREFIX}_{self._prepared_tag}_{self._prepared_count}"
        self._prepared_count += 1
        command = f"PREPARE {name} AS {text}; DEALLOCATE {name}"
        if not generic:
            await self._run(command)
            return
        try:
            await self._run(f"SAVEPOINT {_SAVEPOINT}; {command}; RELEASE SAVEPOINT {_SAVEPOINT}")
        except IndeterminateDatatype:
            await self._run(f"ROLLBACK TO SAVEPOINT {_SAVEPOINT}; RELEASE SAVEPOINT {_SAVEPOINT}")
```

`_check_rules` целиком:

```python
    async def _check_rules(self) -> None:
        """Представления и правила, которые заблокировала транзакция: текст правила и его зависимости.

        План не показывает всего, что вычисляет правило: IMMUTABLE-вызов с константами свёрнут в результат,
        LIMIT/OFFSET и смещения рамки окна EXPLAIN не печатает, как и проверку SubPlan в PG 15/16; оператор или
        агрегат public называет себя, а не функции, которые вызывает. Читается дважды: после PREPARE всех
        операторов (до планирования) и после всех EXPLAIN (планировщик блокирует представления встраиваемых
        SQL-функций). Строка, проверенная при первом чтении, при втором пропускается. Текст правила проверяется
        как выражение плана, зависимости из pg_depend — по правилам basic с функцией оператора и опорными
        функциями агрегата. Тела SQL-функций, политики RLS и триггеры не проверяются.

        Raises:
            PlanAccessError: Правило вызывает функцию, оператор или тип вне разрешённого.
            PlanUnverifiableError: Ответа нет, текст правила не разбирается или строка незнакомого вида.
        """
        rows = await self._run(RULE_DEPENDENCIES_SQL)
        if rows is None:
            raise PlanUnverifiableError(rules=True)
        pending = _CatalogNames()
        for row in rows:
            key = _row_key(row.cells)
            if key in self._seen_rows:
                continue
            self._seen_rows.add(key)
            self._check_rule_row(row.cells, pending)
        await self._check_catalog_names(pending)
```

- [ ] **Step 5: Запустить юнит-тесты — проходят**

Run: `uv run pytest tests/unit/postgres/test_plan_guard.py -q`
Expected: PASS (все, включая старые: `explain.sent` по-прежнему только `EXPLAIN` и запросы каталога, `PREPARE` — в `explain.prepared`).

- [ ] **Step 6: Интеграционный тест: свёрнутая функция не выполняется до отказа**

В `_SETUP` `tests/integration/test_plan_check.py` (в конец строки, перед `"""`) добавить:

```sql
CREATE OR REPLACE FUNCTION secret.boom() RETURNS text
    LANGUAGE plpgsql IMMUTABLE AS $$BEGIN RAISE EXCEPTION 'secret.boom was executed'; END$$;
CREATE OR REPLACE VIEW public.app_boom_view AS SELECT secret.boom() AS b;
```

И тест в конец файла:

```python
@pytest.mark.asyncio
async def test_folded_function_of_a_view_is_not_executed_before_the_rejection(db_plan_check: DbAccess) -> None:
    """IMMUTABLE secret.boom() с константами планировщик выполнил бы при EXPLAIN (ошибка 'was executed');
    правила читаются после PREPARE, до планирования: приходит отказ по функции, а не её исключение."""
    with pytest.raises(PlanAccessError, match=r"function 'secret\.boom'"):
        await db_plan_check.sql_driver.execute("SELECT * FROM app_boom_view", readonly=True)
```

Сверить статически: `CREATE VIEW` не вычисляет `secret.boom()`; без Task 1 проверка упала бы `psycopg.errors.RaiseException` на `EXPLAIN`.

`test_information_schema_is_rejected_with_plan_check` — правила `information_schema.tables` теперь проверяются до узлов плана, и отказ приходит по типу колонки представления:

```python
@pytest.mark.asyncio
async def test_information_schema_is_rejected_with_plan_check(db_plan_check: DbAccess) -> None:
    """Строгий режим: определения представлений information_schema проверяются до плана — их типы вне public."""
    with pytest.raises(PlanAccessError, match=r"type 'information_schema\.sql_identifier'"):
        await db_plan_check.sql_driver.execute("SELECT table_name FROM information_schema.tables", readonly=True)
```

- [ ] **Step 7: Документация в коде и README**

`src/postgres_fastmcp/postgres/security/driver.py`, docstring `_checked_run` — первый абзац заменить на:

```python
        """Выполнение с SET LOCAL через делегата; с plan_check — проверка по плану в той же транзакции.

        С plan_check делегат получает precheck: на том же соединении, после BEGIN, он один раз ставит
        SET LOCAL statement_timeout/search_path, готовит и сразу снимает каждый оператор (PREPARE; DEALLOCATE —
        блокировки представлений без планирования), читает их правила, затем строит план каждого оператора
        (и, если выражения плана этого требуют, спрашивает каталог) и ещё раз читает правила; оператор идёт
        без префикса и наследует настройки транзакции.
        ...
```

(второй и третий абзацы без изменений). Комментарий в `precheck.run`:

```python
                # PREPARE и EXPLAIN (deparse pglast; standard_conforming_strings = on закрепляет SqlExecutor в BEGIN)
                # и запросы каталога PlanGuard — с тегом, в той же транзакции.
```

`src/postgres_fastmcp/postgres/driver.py`, docstring `_execute_with_connection` — предложение «Пометка hypopg смотрит только на оператор: …» заменить на:

```python
        Пометка hypopg смотрит только на оператор: тексты PREPARE и EXPLAIN проверки — deparse того же оператора,
        поэтому разметка по его тексту покрывает и их; PREPARE не планирует и ничего не выполняет, а функции hypopg
        VOLATILE, и планировщик их не вызывает (EXPLAIN без ANALYZE ничего не выполняет).
```

`README.md`, раздел «Проверка по плану»:

1. Абзац «Проверка и выполнение идут в одной транзакции…» — предложение «сначала `SET LOCAL` таймаута и `search_path`, затем EXPLAIN каждого оператора, затем сам оператор.» заменить на: «сначала `SET LOCAL` таймаута и `search_path`, затем `PREPARE` (и сразу `DEALLOCATE`) каждого оператора, чтение правил представлений, `EXPLAIN` каждого оператора, ещё одно чтение правил, затем сам оператор.»
2. В пункте «**Проверка зависимостей представлений и правил.**» фразу «Поэтому после всех EXPLAIN строки, но до выполнения, один запрос к каталогу читает правила (`pg_rewrite`) всех отношений, которые эта транзакция заблокировала:» заменить на: «Поэтому до планирования — после `PREPARE` каждого оператора строки (разбор и переписывание берут блокировки представлений, но план не строится и ничего не выполняется) — запрос к каталогу читает правила (`pg_rewrite`) всех отношений, которые эта транзакция заблокировала, и после всех `EXPLAIN` — ещё раз (представления, до которых дошёл только планировщик: встраиваемые SQL-функции):». В конец пункта добавить: «Отказ по правилу приходит до `EXPLAIN`: свёрнутая в константу `IMMUTABLE`-функция представления не выполняется. Оператор с `GENERIC_PLAN`, тип параметра которого `PREPARE` вывести не может (`$1 IS NULL`, `pg_typeof($1)`), готовится в точке сохранения; если не вышло, его представления проверяются только после `EXPLAIN`.»
3. Пункт «Цена — …»: «по одному лишнему запросу к БД на каждый оператор строки (EXPLAIN), один на `SET LOCAL` и один на правила представлений» → «по два лишних запроса к БД на каждый оператор строки (`PREPARE`/`DEALLOCATE` одной командой и `EXPLAIN`), один на `SET LOCAL` и два на правила представлений».
4. В «Что `plan_check` не закрывает» пункт «даже там, где закрыто: `IMMUTABLE`-функция представления, свёрнутая в константу, выполняется планировщиком …возможное развитие);» заменить на: «`IMMUTABLE`-функция с константными аргументами внутри встраиваемой SQL-функции `public` выполняется планировщиком во время `EXPLAIN` проверки — до отказа по правилам представления, которое читает эта функция (представление блокирует только планировщик);».

- [ ] **Step 8: Проверки и коммит**

Run: `uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check . && uv run pytest tests/unit -q && uv run pytest tests/integration -q`
Expected: ruff/mypy чисто; юнит-тесты PASS; интеграция собирается (без Docker — skipped).

```bash
git add src/postgres_fastmcp/postgres/security/plan_guard.py src/postgres_fastmcp/postgres/security/driver.py \
    src/postgres_fastmcp/postgres/driver.py README.md tests/unit/postgres/test_plan_guard.py \
    tests/unit/postgres/test_safe_sql_executor.py tests/integration/test_plan_check.py
git commit -m "feat(security): check view definitions before planning the statement"
```

---

### Task 2: Операторы и агрегаты `allowed_schema` — по их функциям

**Files:**
- Modify: `src/postgres_fastmcp/postgres/security/plan_catalog.py` (`_AGGREGATE_SUPPORT_FUNCTIONS`, `ALLOWED_IMPLEMENTATIONS_SQL`, `allowed_implementations`; `RULE_DEPENDENCIES_SQL` использует `_AGGREGATE_SUPPORT_FUNCTIONS`)
- Modify: `src/postgres_fastmcp/postgres/security/plan_guard.py` (`_StatementNames` вместо `_TypeNames`, `_CatalogNames.implementations`, `_OPERATOR_KIND`, `_looked_up`, `_note_implementation`, `_check_statement_names` вместо `_check_statement_types`, `_check_names`, `_check_catalog_names`)
- Modify: `README.md`
- Test: `tests/unit/postgres/test_plan_catalog.py`, `tests/unit/postgres/test_plan_guard.py`, `tests/integration/test_plan_check.py`

**Interfaces:**
- Consumes: `PlanGuard._check_rule_row(cells, pending)` (виды `operator_function`, `aggregate_function`, `operator` — без изменений), `_name_array`, `StatementRunner`.
- Produces:
  - `plan_catalog.ALLOWED_IMPLEMENTATIONS_SQL: str` (плейсхолдеры `{schema}`, `{operators}`, `{functions}`), колонки `kind, schema, name, parent_schema`;
  - `async def allowed_implementations(run: StatementRunner, schema: str, *, operators: Collection[str], functions: Collection[str]) -> list[RowResult] | None`;
  - `plan_guard._OPERATOR_KIND = "operator"`; `_CatalogNames.implementations: dict[tuple[str, str], None]` (ключ — `(вид, имя)`, вид `FUNCTION_KIND` или `_OPERATOR_KIND`);
  - `PlanGuard._note_implementation(self, kind: str, schema: str | None, name: str, pending: _CatalogNames) -> None`;
  - `PlanGuard._looked_up: set[tuple[str, str]]`;
  - `PlanGuard._check_statement_names(self, statements: list[Node], targets: list[Node]) -> None`;
  - в тестах: `_Explain(..., implementations: list[dict[str, Any]] | None = None)`, `_Explain.implementation_queries: list[str]` (не в `sent`).

- [ ] **Step 1: Тест SQL каталога — падающий**

В `tests/unit/postgres/test_plan_catalog.py` импорт дополнить `ALLOWED_IMPLEMENTATIONS_SQL, allowed_implementations`; в `_catalog_sql` перед `return` добавить вызов:

```python
    await allowed_implementations(recorder, "public", operators=["===", "="], functions=["app_agg"])
```

и тесты:

```python
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
        "aggtransfn", "aggfinalfn", "aggcombinefn", "aggserialfn",
        "aggdeserialfn", "aggmtransfn", "aggminvtransfn", "aggmfinalfn", "aggsortop", "oprcode",
    ):
        assert column in ALLOWED_IMPLEMENTATIONS_SQL
```

Run: `uv run pytest tests/unit/postgres/test_plan_catalog.py -q`
Expected: FAIL — `ImportError: cannot import name 'ALLOWED_IMPLEMENTATIONS_SQL'`.

- [ ] **Step 2: SQL каталога**

В `plan_catalog.py` перед комментарием к `RULE_DEPENDENCIES_SQL` (перед `# Правила (pg_rewrite) …`) добавить:

```python
# Опорные функции агрегата (pg_aggregate, PG 15–17): переход, финал, комбинирование, (де)сериализация и их
# варианты для движущегося окна; regproc, 0 — функции нет (соединение с pg_proc такую строку отбрасывает).
_AGGREGATE_SUPPORT_FUNCTIONS = (
    "(a.aggtransfn), (a.aggfinalfn), (a.aggcombinefn), (a.aggserialfn), "
    "(a.aggdeserialfn), (a.aggmtransfn), (a.aggminvtransfn), (a.aggmfinalfn)"
)
```

В `RULE_DEPENDENCIES_SQL` две строки

```python
    "CROSS JOIN LATERAL (VALUES (a.aggtransfn), (a.aggfinalfn), (a.aggcombinefn), (a.aggserialfn), "
    "(a.aggdeserialfn), (a.aggmtransfn), (a.aggminvtransfn), (a.aggmfinalfn)) AS s(fn) "
```

заменить одной:

```python
    f"CROSS JOIN LATERAL (VALUES {_AGGREGATE_SUPPORT_FUNCTIONS}) AS s(fn) "
```

После `RULE_DEPENDENCIES_SQL` добавить (SQL проверен живьём на PG 17 и `test_catalog_sql_resolves_nothing_through_the_search_path`):

```python
# Реализации операторов и агрегатов allowed_schema по именам. План и SQL агента печатают оператор или агрегат
# allowed_schema без схемы и без типов аргументов, поэтому берутся все перегрузки с этим именем. Строки — того же
# вида, что у RULE_DEPENDENCIES_SQL (их проверяет тот же разбор): operator_function — функция оператора (oprcode);
# aggregate_function — опорная функция агрегата; operator и operator_function — оператор сортировки агрегата
# (aggsortop: min/max планировщик заменяет индексным сканом с этим оператором) и его функция. parent_schema —
# схема оператора или агрегата.
ALLOWED_IMPLEMENTATIONS_SQL = (
    "WITH operators AS ("  # noqa: S608
    "SELECT o.oprcode FROM pg_catalog.pg_operator o "
    "JOIN pg_catalog.pg_namespace n ON n.oid OPERATOR(pg_catalog.=) o.oprnamespace "
    "WHERE n.nspname OPERATOR(pg_catalog.=) {schema} AND o.oprname OPERATOR(pg_catalog.=) ANY ({operators})"
    "), aggregates AS ("
    "SELECT a.* FROM pg_catalog.pg_aggregate a "
    "JOIN pg_catalog.pg_proc p ON p.oid OPERATOR(pg_catalog.=) a.aggfnoid::pg_catalog.oid "
    "JOIN pg_catalog.pg_namespace n ON n.oid OPERATOR(pg_catalog.=) p.pronamespace "
    "WHERE n.nspname OPERATOR(pg_catalog.=) {schema} AND p.proname OPERATOR(pg_catalog.=) ANY ({functions})"
    "), sort_operators AS ("
    "SELECT o.oprname, o.oprnamespace, o.oprcode FROM aggregates a "
    "JOIN pg_catalog.pg_operator o ON o.oid OPERATOR(pg_catalog.=) a.aggsortop"
    ") "
    "SELECT 'operator_function' AS kind, fn.nspname AS schema, f.proname AS name, "
    "{schema}::pg_catalog.name AS parent_schema "
    "FROM operators o JOIN pg_catalog.pg_proc f ON f.oid OPERATOR(pg_catalog.=) o.oprcode::pg_catalog.oid "
    "JOIN pg_catalog.pg_namespace fn ON fn.oid OPERATOR(pg_catalog.=) f.pronamespace "
    "UNION ALL "
    "SELECT 'aggregate_function', fn.nspname, f.proname, {schema}::pg_catalog.name "
    f"FROM aggregates a CROSS JOIN LATERAL (VALUES {_AGGREGATE_SUPPORT_FUNCTIONS}) AS s(fn) "
    "JOIN pg_catalog.pg_proc f ON f.oid OPERATOR(pg_catalog.=) s.fn::pg_catalog.oid "
    "JOIN pg_catalog.pg_namespace fn ON fn.oid OPERATOR(pg_catalog.=) f.pronamespace "
    "UNION ALL "
    "SELECT 'operator', opn.nspname, o.oprname, NULL::pg_catalog.name FROM sort_operators o "
    "JOIN pg_catalog.pg_namespace opn ON opn.oid OPERATOR(pg_catalog.=) o.oprnamespace "
    "UNION ALL "
    "SELECT 'operator_function', fn.nspname, f.proname, opn.nspname FROM sort_operators o "
    "JOIN pg_catalog.pg_namespace opn ON opn.oid OPERATOR(pg_catalog.=) o.oprnamespace "
    "JOIN pg_catalog.pg_proc f ON f.oid OPERATOR(pg_catalog.=) o.oprcode::pg_catalog.oid "
    "JOIN pg_catalog.pg_namespace fn ON fn.oid OPERATOR(pg_catalog.=) f.pronamespace"
)
```

В конец модуля:

```python
async def allowed_implementations(
    run: StatementRunner, schema: str, *, operators: Collection[str], functions: Collection[str]
) -> list[RowResult] | None:
    """Функции, которыми реализованы операторы и агрегаты схемы schema с этими именами (одним запросом)."""
    sql = (
        SQL(ALLOWED_IMPLEMENTATIONS_SQL)
        .format(schema=Literal(schema), operators=_name_array(operators), functions=_name_array(functions))
        .as_string()
    )
    return await run(sql)
```

Run: `uv run pytest tests/unit/postgres/test_plan_catalog.py -q`
Expected: PASS.

- [ ] **Step 3: Фальшивый каталог реализаций и падающие тесты guard**

В `_Explain.__init__` добавить параметр `implementations: list[dict[str, Any]] | None = None,` и поля:

```python
        self._implementations = implementations or []
        self.implementation_queries: list[str] = []
```

В `_Explain.__call__` сразу после ветки `pg_catalog.pg_rewrite` (запрос реализаций — единственный другой SQL с `pg_catalog.pg_aggregate`):

```python
        if "pg_catalog.pg_aggregate" in sql:
            self.implementation_queries.append(sql)
            return [RowResult(cells=_rule_row(**row)) for row in self._implementations]
```

Тесты (в конец `test_plan_guard.py`):

```python
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
    implementations = [{"kind": "operator_function", "schema": "public", "name": "app_close_to", "parent_schema": "public"}]
    explain = _Explain(implementations=implementations)

    await _guard(explain).check("SELECT id <~> 1 FROM app_t")

    [query] = explain.implementation_queries
    assert "'<~>'" in query


async def test_builtin_operator_rows_are_not_checked_by_their_functions() -> None:
    """Строка оператора pg_catalog (parent_schema) — встроенный int4eq вне списка basic не отклоняется."""
    implementations = [{"kind": "operator_function", "schema": "pg_catalog", "name": "int4eq", "parent_schema": "pg_catalog"}]
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
```

Run: `uv run pytest tests/unit/postgres/test_plan_guard.py -q`
Expected: FAIL — `implementation_queries == []` у новых тестов (guard ещё не спрашивает реализации).

- [ ] **Step 4: Реализация в `plan_guard.py`**

Импорты: к `pglast.ast` добавить `A_Expr`, `SortBy`, `SubLink`; из `plan_catalog` — `allowed_implementations`.

Константы, после `TYPE_KIND = "type"`:

```python
# Оператор — вид имени, чья реализация (oprcode) проверяется у операторов allowed_schema.
_OPERATOR_KIND = "operator"
```

`_TypeNames` заменить на:

```python
class _StatementNames(Visitor):
    """Имена SQL агента: (схема или None, имя); у имени с базой данных — две последние части.

    Типы проверяются до разбора на сервере (оракул ошибок разбора). Операторы и функции — кандидаты
    в операторы и агрегаты allowed_schema: их реализация проверяется до PREPARE и EXPLAIN.
    """

    def __init__(self) -> None:
        """Пустые списки имён в порядке обхода."""
        super().__init__()
        self.types: list[tuple[str | None, str]] = []
        self.operators: list[tuple[str | None, str]] = []
        self.functions: list[tuple[str | None, str]] = []

    @staticmethod
    def _add(found: list[tuple[str | None, str]], parts: tuple[Node, ...] | None) -> None:
        """Добавить имя; части не строки или пустые пропускаются."""
        names = [part.sval for part in parts or () if isinstance(part, String) and part.sval]
        if len(names) == 1:
            found.append((None, names[0]))
        elif len(names) > 1:
            found.append((names[-2], names[-1]))

    def visit_TypeName(self, _ancestors: object, node: TypeName) -> None:  # noqa: N802
        """Имя типа."""
        self._add(self.types, node.names)

    def visit_A_Expr(self, _ancestors: object, node: A_Expr) -> None:  # noqa: N802
        """Оператор выражения (в том числе IN, = ANY, NULLIF, IS DISTINCT FROM)."""
        self._add(self.operators, node.name)

    def visit_SubLink(self, _ancestors: object, node: SubLink) -> None:  # noqa: N802
        """Оператор сравнения с подзапросом (x = ANY (SELECT ...))."""
        self._add(self.operators, node.operName)

    def visit_SortBy(self, _ancestors: object, node: SortBy) -> None:  # noqa: N802
        """Оператор ORDER BY ... USING."""
        self._add(self.operators, node.useOp)

    def visit_FuncCall(self, _ancestors: object, node: FuncCall) -> None:  # noqa: N802
        """Вызов функции или агрегата."""
        self._add(self.functions, node.funcname)
```

`_CatalogNames` — новое поле:

```python
    # (вид, имя) операторов и функций без схемы или со схемой allowed_schema: чем они реализованы.
    implementations: dict[tuple[str, str], None] = field(default_factory=dict)
```

`PlanGuard.__init__` — в конец:

```python
        # Имена, реализации которых уже спрошены в этой проверке (у второго чтения — только новые).
        self._looked_up: set[tuple[str, str]] = set()
```

`check`: начало тела (до `targets = …`) заменить на — проверка имён переезжает после вычисления `plannable`, остальное без изменений:

```python
        statements = [raw.stmt for raw in pglast.parse_sql(query)]
        plannable = [target for target in (_plannable(statement) for statement in statements) if target is not None]
        await self._check_statement_names(statements, [statement for statement, _ in plannable])
        if not plannable:
            return
```

`_check_statement_types` заменить на:

```python
    async def _check_statement_names(self, statements: list[Node], targets: list[Node]) -> None:
        """Типы SQL агента и реализации его операторов и агрегатов allowed_schema — до PREPARE и EXPLAIN.

        Типы: ошибка разбора ((NULL::users).secret_note — нет колонки, '(1,2)'::users — число и типы полей)
        раскрывает структуру таблицы без префикса; PREPARE разбирает так же, как EXPLAIN. Каталог спрашивается,
        только если есть тип без схемы вне кэша pg_catalog или со схемой allowed_schema без префикса.

        Операторы и функции планируемых операторов (targets): оператор или агрегат allowed_schema называет себя,
        а не функции, которые вызывает, а IMMUTABLE-вызов с константами планировщик выполнил бы при EXPLAIN.
        """
        pending = _CatalogNames()
        collector = _StatementNames()
        for statement in statements:
            collector(statement)
        for schema, name in collector.types:
            self._check_type(schema, name, pending)
        reached = _StatementNames()
        for target in targets:
            reached(target)
        for schema, name in reached.operators:
            self._note_implementation(_OPERATOR_KIND, schema, name, pending)
        for schema, name in reached.functions:
            self._note_implementation(FUNCTION_KIND, schema, name, pending)
        await self._check_catalog_names(pending)
```

`_check_names` целиком:

```python
    def _check_names(self, names: ExpressionNames, pending: _CatalogNames, *, check_functions: bool) -> None:
        """Имена одного выражения; то, что решает только каталог, откладывается в pending."""
        for schema, name in names.functions:
            self._note_implementation(FUNCTION_KIND, schema, name, pending)
        if check_functions:
            for schema, name in names.functions:
                if schema is not None:
                    self._check_function(schema, name)
                elif name.lower() not in BASIC_ALLOWED_FUNCTIONS:
                    pending.functions[name] = None
        for schema, name in names.sequences:
            self._check_relation(schema or self._allowed_schema, name)
        for schema, name in names.operators:
            if schema is not None and schema not in (self._allowed_schema, _BUILTIN_FUNCTION_SCHEMA):
                qualified_name = f"{schema}.{name}"
                raise self._function_error(qualified_name)
            self._note_implementation(_OPERATOR_KIND, schema, name, pending)
        for schema, name in names.types:
            self._check_type(schema, name, pending)

    def _note_implementation(self, kind: str, schema: str | None, name: str, pending: _CatalogNames) -> None:
        """Оператор или функция, которые могут быть объектом allowed_schema: их реализацию спросит каталог.

        Имя без схемы может быть и встроенным (=, count) — каталог ищет только в allowed_schema. Имя, уже
        спрошенное в этой проверке, не спрашивается снова.
        """
        if (schema is None or schema == self._allowed_schema) and (kind, name) not in self._looked_up:
            pending.implementations[kind, name] = None
```

`_check_catalog_names` — в начало тела (до `if pending.functions:`):

```python
        if pending.implementations:
            keys = list(pending.implementations)
            self._looked_up.update(keys)
            rows = await allowed_implementations(
                self._run,
                self._allowed_schema,
                operators=[name for kind, name in keys if kind == _OPERATOR_KIND],
                functions=[name for kind, name in keys if kind == FUNCTION_KIND],
            )
            if rows is None:
                raise PlanUnverifiableError(rules=True)
            for row in rows:
                self._check_rule_row(row.cells, pending)
```

и docstring метода: «Реализации операторов и агрегатов allowed_schema — по правилам basic; функции без схемы вне списка basic — не из pg_catalog; строковые типы — не отношения без префикса.»

Docstring модуля — в абзац «Порядок.» после первого предложения вставить: «Туда же — реализации операторов и агрегатов allowed_schema, которые называет SQL агента (функция оператора и опорные функции агрегата; вызов с константами планировщик выполнил бы).» и в абзац про выражения узлов добавить предложение: «Оператор или агрегат без схемы (или со схемой allowed_schema) может быть объектом allowed_schema: каталог отдаёт функции, которыми они реализованы, и те проверяются по правилам функций.»

Run: `uv run pytest tests/unit/postgres -q`
Expected: PASS. Если падает тест, считающий `explain.catalog_queries()` — проверить, что ветка `pg_catalog.pg_aggregate` в `_Explain.__call__` стоит до `self.sent.append(sql)`.

- [ ] **Step 5: Интеграционные тесты**

В `_SETUP` добавить:

```sql
CREATE OR REPLACE FUNCTION secret.agg_step(state text, value text) RETURNS text
    LANGUAGE plpgsql IMMUTABLE AS $$BEGIN RETURN coalesce(state, '') || value; END$$;
CREATE OR REPLACE AGGREGATE public.sum(text) (SFUNC = secret.agg_step, STYPE = text);
CREATE OR REPLACE FUNCTION public.app_close_to(a int, b int) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE AS 'BEGIN RETURN abs(a - b) <= 1; END';
DROP OPERATOR IF EXISTS public.<~> (int, int);
CREATE OPERATOR public.<~> (LEFTARG = int, RIGHTARG = int, FUNCTION = public.app_close_to);
```

Тесты:

```python
@pytest.mark.asyncio
async def test_public_operator_over_a_builtin_outside_basic_is_rejected_in_the_agent_sql(
    db_plan_check: DbAccess,
) -> None:
    """public.!! (text, boolean) реализован pg_catalog.current_setting: SQL агента видит только имя оператора."""
    with pytest.raises(PlanAccessError, match=r"function 'pg_catalog\.current_setting'"):
        await db_plan_check.sql_driver.execute(
            "SELECT ('max_connections' !! true) AS s FROM app_plan_people", readonly=True
        )


@pytest.mark.asyncio
async def test_public_aggregate_over_a_foreign_function_is_rejected(db_plan_check: DbAccess) -> None:
    """Валидатор basic пускает только функции из списка basic; public.sum(text) — перегрузка разрешённого имени:
    sum(name) по тексту выглядит встроенным агрегатом, план печатает его без схемы."""
    with pytest.raises(PlanAccessError, match=r"function 'secret\.agg_step'"):
        await db_plan_check.sql_driver.execute("SELECT sum(name) AS c FROM app_plan_people", readonly=True)


@pytest.mark.asyncio
async def test_public_operator_over_a_public_function_passes(db_plan_check: DbAccess) -> None:
    rows = await db_plan_check.sql_driver.execute("SELECT (id <~> 2) AS near FROM app_plan_items", readonly=True)
    assert rows[0].cells["near"] is True
```

(`app_plan_items` всегда содержит строку `id = 1`; `abs(1 - 2) <= 1` — истина.)

- [ ] **Step 6: README**

В разделе «Проверка по плану»:

1. В пункт «Проверяет и выражения плана — …» после предложения про функцию без схемы вне списка basic вставить: «Оператор или агрегат без схемы (или со схемой `public`) — в выражениях плана и в самом SQL агента — может быть объектом `public`: каталог отдаёт функции, которыми он реализован (функцию оператора, опорные функции агрегата и оператор сортировки агрегата для `min`/`max`), и они проверяются по правилам функций: `public` или встроенные из списка basic. SQL агента проверяется так до `PREPARE` и `EXPLAIN`. Тип аргументов из текста не виден, поэтому проверяются все перегрузки с этим именем в `public`.»
2. Пункт «Цена — …» дополнить: «… и один запрос на реализации операторов и агрегатов до `PREPARE` и (только для имён, которых не было в SQL агента) — после всех `EXPLAIN`; он нужен почти любому запросу с `=`.»
3. В «Что `plan_check` не закрывает» удалить пункт «операторы и агрегаты `public`, реализованные функциями других схем или встроенными вне списка basic, когда их вызывает сам SQL агента …».
4. В «отклоняет, хотя это легитимно» пункт «агрегаты `public` с опорными функциями `pg_catalog` вне списка basic (например, `SFUNC = int4pl`) в представлениях и правилах: …» заменить на: «операторы и агрегаты `public`, реализованные встроенными функциями вне списка basic (`SFUNC = int4pl`, `FUNCTION = pg_catalog.textcat`), — в представлениях, правилах, выражениях плана и SQL агента; оператор `public`, одноимённый встроенному (`=` для своих типов), с такой реализацией отклоняет любое использование этого имени — перегрузки по тексту не различить.»

- [ ] **Step 7: Проверки и коммит**

Run: `uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check . && uv run pytest tests/unit -q && uv run pytest tests/integration -q`
Expected: чисто; PASS; интеграция собирается.

```bash
git add src/postgres_fastmcp/postgres/security/plan_catalog.py src/postgres_fastmcp/postgres/security/plan_guard.py \
    README.md tests/unit/postgres/test_plan_catalog.py tests/unit/postgres/test_plan_guard.py \
    tests/integration/test_plan_check.py
git commit -m "feat(security): check public operators and aggregates by their functions"
```

---

## Заметки к PR (изменения поведения)

- С `plan_check` правила и зависимости представлений проверяются после `PREPARE`, до `EXPLAIN`: свёрнутые IMMUTABLE-функции отклонённого представления больше не выполняются до отказа. На оператор строки — лишняя команда `PREPARE …; DEALLOCATE …`, правила читаются дважды.
- Оператор с `GENERIC_PLAN`, тип параметра которого `PREPARE` не выводит, проверяется по-старому (правила — после `EXPLAIN`).
- Представление, запрещённое и по определению, и по узлам плана, теперь отклоняется по определению: `information_schema.tables` — `type 'information_schema.sql_identifier'` вместо `relation 'pg_catalog.…'`.
- Операторы и агрегаты `public` на функциях чужих схем или встроенных вне basic отклоняются и в SQL агента, и в выражениях плана; почти каждый запрос получает один запрос каталога на реализации.
