# Проверка по плану в транзакции оператора (PR 2) — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** С `plan_check=true` EXPLAIN проверки и сам оператор агента выполняются в одной транзакции на одном соединении пула — между проверкой и выполнением ничего не меняется.

**Architecture:** Сейчас `SafeSqlExecutor._checked_run` вызывает делегата на каждый EXPLAIN (`_explain_for_plan_check`, отдельная read-only транзакция) и ещё раз на оператор: разные транзакции и, возможно, разные соединения. Стало: у `SqlExecutor.execute`/`execute_statement` появляется необязательный именованный аргумент `precheck: Precheck | None`. `SqlExecutor` после `BEGIN …; SET LOCAL standard_conforming_strings = on` отдаёт `precheck` исполнитель строк `StatementRunner` поверх того же курсора, затем выполняет оператор; ошибка `precheck` — `ROLLBACK`, оператор не выполняется. Контракт описан новым протоколом `PrecheckSqlDriverPort(SqlDriverPort)` в `postgres/ports.py`: его реализует только `SqlExecutor`, и только его требует `SafeSqlExecutor` (и прокидывающий делегата `CatalogSqlExecutor`). `SafeSqlExecutor` с `plan_check` передаёт `precheck`, который один раз ставит `SET LOCAL statement_timeout …; SET LOCAL search_path = …;` и прогоняет `PlanGuard` через `StatementRunner`; оператор уходит без префикса и наследует настройки транзакции. `PlanGuard` не меняется: `SafeSqlExecutor` создаёт его на каждый вызов вокруг исполнителя транзакции. Без `plan_check` путь прежний, байт в байт. Спека: `docs/superpowers/specs/2026-09-29-basic-hardening-2-design.md`, §5, §6 (PR 2), §7.

**Tech Stack:** Python 3.12, uv, psycopg 3.3.4 (`AsyncCursor`, `DictRow`), psycopg_pool 3.3.1, pglast v8.2, pytest (asyncio auto).

## Spec corrections

- **Отказ проверки инвалидировал бы пул.** `SqlExecutor.execute_statement` при любом исключении зовёт `_is_connection_error`, а та консервативно считает соединением любое не-psycopg исключение. Раньше `PlanAccessError`/`PlanUnverifiableError` поднимались в `SafeSqlExecutor` вне делегата; теперь они летят из `precheck` внутри `SqlExecutor` и без правки помечали бы пул невалидным на каждый отказ. `_is_connection_error` возвращает `False` для `UserFacingError` (отказ проверки ничего не говорит о соединении). Ошибки соединения (`ConnectionFailedError`, `ConnectionNotEstablishedError`) — не `UserFacingError`, их классификация не меняется.
- **Типизация `precheck`.** Не параметр `SqlDriverPort`: его реализуют и `SafeSqlExecutor` (принимать чужой `precheck` охраннику нельзя), и двойники доменов в тестах. Отдельный протокол `PrecheckSqlDriverPort(SqlDriverPort, Protocol)` с тем же `execute`/`execute_statement` плюс `precheck: Precheck | None = None` — совместимое сужение (проверено `mypy --strict`: реализация с лишним необязательным kwarg удовлетворяет обоим протоколам). Домены и `DbAccess.sql_driver` остаются на `SqlDriverPort`.
- **`PlanGuard(explain, …)` не меняется.** Исполнитель EXPLAIN привязан к транзакции, поэтому `SafeSqlExecutor` строит `PlanGuard` внутри `precheck` на каждый вызов (объект лёгкий); `_explain_for_plan_check` удаляется. `tests/unit/postgres/test_plan_guard.py` не трогается.
- **EXPLAIN в пишущей транзакции.** Для basic с записью транзакция одна и пишущая: EXPLAIN без ANALYZE не запускает исполнитель (`ExecutorStart` с `EXEC_FLAG_EXPLAIN_ONLY`, DML не выполняется ни в read-only, ни в read-write транзакции); ANALYZE в basic отклоняет валидатор, а текст проверки `PlanGuard` строит сам (`EXPLAIN (VERBOSE, FORMAT JSON[, GENERIC_PLAN])`). Интеграционный тест `test_write_statement_is_planned_in_a_read_only_transaction` после изменения называется неверно — переименовывается и усиливается (INSERT выполняется ровно один раз).
- **Таймауты.** Каждый `cursor.execute` — отдельное сообщение Query, у каждого свой отсчёт `statement_timeout` (как и раньше, когда EXPLAIN шли отдельными вызовами). Общий бюджет по-прежнему держит клиентская страховка `timeout + client_timeout_grace` вокруг всего вызова. `_is_statement_timeout` теперь меряет время от начала всей транзакции, а не одного EXPLAIN: запасной признак «прошло не меньше таймаута» чуть раньше считает отмену таймаутом — допустимо, основной признак (текст сервера) не меняется.
- **Блокировки.** Разбор запроса со ссылкой на представление берёт `AccessShareLock` на само представление и на отношения, держит его до конца транзакции; `CREATE OR REPLACE VIEW` (нужен `AccessExclusiveLock`) ждёт окончания транзакции агента — определение между проверкой и выполнением не меняется. `now()` внутри транзакции постоянен, так что и отсечение секций по `now()` одинаково в EXPLAIN и в выполнении.
- Интеграционный пункт спеки §6 «представление, заменённое конкурентно» не воспроизводится детерминированно; вместо него: `SHOW search_path` через basic+`plan_check` возвращает `public` (оператор без префикса наследует `SET LOCAL` из `precheck`, то есть идёт в той же транзакции) и отклонённый `UPDATE` представления над `secret` не меняет данные (откат).

## Global Constraints

- Всё, что видит агент или внешняя система (ошибки, логи, коммиты), — на английском; docstring и комментарии — по-русски. README — по-русски.
- Нет `from __future__ import annotations`.
- Ломающие изменения разрешены; описываются только в заметках к PR (спека §7). В коде — никаких шимов и упоминаний старого поведения.
- Безопасность: ни одно правило валидатора не ослабляется (`QueryValidator` вызывается первым, как раньше); full не меняется; канал сервера (`CatalogSqlExecutor`) не получает `precheck` и шлёт делегату прежние строки.
- Перед каждым коммитом: `uv run ruff check --fix --show-fixes`, `uv run mypy src/`, `uv run ruff format`; затем `uv run ruff check .`.
- Юнит-тесты: `uv run pytest tests/unit -q`. Интеграция: `uv run pytest tests/integration -q` — должна собираться; локально пропускается (нет Docker), в CI — Postgres 15/16 с hypopg. Сверять интеграционные тесты статически с кодом.
- Коммиты: `type(scope): message`, повелительное наклонение, английский.
- **Никогда не запускать никакие `git config` и `git stash`.** Не пушить.
- Ветка: `claude/plan-check-one-transaction`, создать от вершины `claude/basic-hardening-2` перед Task 1: `git switch claude/basic-hardening-2 && git switch -c claude/plan-check-one-transaction`.

---

### Task 1: `SqlExecutor` выполняет `precheck` в транзакции оператора

**Files:**
- Modify: `src/postgres_fastmcp/postgres/ports.py` (`StatementRunner`, `Precheck`, `PrecheckSqlDriverPort`)
- Modify: `src/postgres_fastmcp/postgres/driver.py` (`_is_connection_error`, `SqlExecutor.execute`, `execute_statement`, `_execute_with_connection`, новый `_cursor_runner`)
- Test: `tests/unit/postgres/test_sql_executor.py`

**Interfaces:**
- Produces (в `postgres_fastmcp.postgres.ports`):
  - `StatementRunner = Callable[[str], Awaitable[list[RowResult] | None]]` — выполняет строку на курсоре транзакции, возвращает строки последнего результата или `None`;
  - `Precheck = Callable[[StatementRunner], Awaitable[None]]`;
  - `class PrecheckSqlDriverPort(SqlDriverPort, Protocol)` с `execute(query, params=None, *, readonly=True, precheck: Precheck | None = None) -> list[RowResult] | None` и `execute_statement(…, precheck: Precheck | None = None) -> StatementResult`.
- Produces: `SqlExecutor.execute`/`execute_statement` принимают `precheck`; порядок команд курсора: `_BEGIN_READ_ONLY`/`_BEGIN_READ_WRITE` → строки `precheck` → оператор → `ROLLBACK` (readonly) / `COMMIT`; ошибка `precheck` → `ROLLBACK`, re-raise, оператор не выполняется. `_is_connection_error(UserFacingError) is False`.

- [ ] **Step 1: Write the failing tests** — в `tests/unit/postgres/test_sql_executor.py` добавить импорт `from postgres_fastmcp.shared.errors import ConnectionNotEstablishedError, PlanAccessError` и после `_pooled_executor`:

```python
class _BatchCursor(_FakeCursor):
    """Курсор, у которого каждая строка запроса — своя пачка результатов: nextset() не уходит в следующую строку."""

    def __init__(self, *batches: list[tuple[str, int, list[dict] | None]]) -> None:
        super().__init__()
        self._batches = [list(batch) for batch in batches]

    async def execute(self, query: str, params: object = None) -> None:
        self.executed.append(query)
        if query.startswith(("BEGIN", "COMMIT", "ROLLBACK")):
            self._current = (query.split(maxsplit=1)[0], -1, None)
            return
        batch = self._batches.pop(0)
        self._current = batch[0]
        self._pending = batch[1:]


_PLAN = [{"Plan": {"Node Type": "Result"}}]


class TestSqlExecutorPrecheck:
    """precheck: предварительные запросы на том же курсоре, в той же транзакции, до оператора."""

    async def test_precheck_runs_between_begin_and_the_statement(self) -> None:
        cursor = _BatchCursor(
            [("SET", -1, None), ("SET", -1, None)],
            [("EXPLAIN", -1, [{"QUERY PLAN": _PLAN}])],
            [("SELECT 1", 1, [{"a": 1}])],
        )
        seen: list[list[RowResult] | None] = []

        async def precheck(run):
            seen.append(await run("SET LOCAL statement_timeout = 5000; SET LOCAL search_path = public;"))
            seen.append(await run("/* t */ EXPLAIN (VERBOSE, FORMAT JSON) SELECT 1 AS a"))

        result = await _executor_on(cursor).execute_statement("/* t */ SELECT 1 AS a", precheck=precheck)

        assert cursor.executed == [
            "BEGIN TRANSACTION READ ONLY; SET LOCAL standard_conforming_strings = on",
            "SET LOCAL statement_timeout = 5000; SET LOCAL search_path = public;",
            "/* t */ EXPLAIN (VERBOSE, FORMAT JSON) SELECT 1 AS a",
            "/* t */ SELECT 1 AS a",
            "ROLLBACK",
        ]
        assert seen == [None, [RowResult(cells={"QUERY PLAN": _PLAN})]]
        assert result == StatementResult(rows=[RowResult(cells={"a": 1})], status="SELECT 1", affected_rows=1)

    async def test_write_transaction_commits_after_precheck(self) -> None:
        cursor = _BatchCursor([("EXPLAIN", -1, [{"QUERY PLAN": _PLAN}])], [("UPDATE 2", 2, None)])

        async def precheck(run):
            await run("EXPLAIN (VERBOSE, FORMAT JSON) UPDATE t SET v = 1")

        result = await _executor_on(cursor).execute_statement("UPDATE t SET v = 1", readonly=False, precheck=precheck)

        assert cursor.executed[0] == "BEGIN; SET LOCAL standard_conforming_strings = on"
        assert cursor.executed[-2:] == ["UPDATE t SET v = 1", "COMMIT"]
        assert result.status == "UPDATE 2"

    async def test_execute_passes_precheck_through(self) -> None:
        cursor = _BatchCursor([("EXPLAIN", -1, [{"QUERY PLAN": _PLAN}])], [("SELECT 1", 1, [{"a": 1}])])
        calls: list[str] = []

        async def precheck(run):
            calls.append("precheck")
            await run("EXPLAIN SELECT 1")

        rows = await _executor_on(cursor).execute("SELECT 1 AS a", precheck=precheck)

        assert calls == ["precheck"]
        assert rows == [RowResult(cells={"a": 1})]
        assert cursor.executed[1] == "EXPLAIN SELECT 1"

    async def test_precheck_rejection_rolls_back_and_skips_the_statement(self) -> None:
        """Отказ проверки: ROLLBACK, оператор не отправлен, пул не помечен невалидным."""
        cursor = _BatchCursor([("EXPLAIN", -1, [{"QUERY PLAN": _PLAN}])])
        executor, pool, _ = _pooled_executor(cursor)

        async def precheck(run):
            await run("EXPLAIN (VERBOSE, FORMAT JSON) SELECT * FROM app_v")
            raise PlanAccessError("relation", "secret.accounts", allowed_schema="public", table_prefix=None)

        with pytest.raises(PlanAccessError):
            await executor.execute("SELECT * FROM app_v", readonly=False, precheck=precheck)

        assert cursor.executed[-1] == "ROLLBACK"
        assert "SELECT * FROM app_v" not in cursor.executed
        pool.mark_invalid.assert_not_called()

    async def test_postgres_error_in_precheck_rolls_back_without_invalidating(self) -> None:
        """Ошибка планирования (отношения нет) — ошибка SQL, не соединения."""
        cursor = _BatchCursor()
        executor, pool, _ = _pooled_executor(cursor)

        async def precheck(run):
            raise UndefinedTable('relation "app_missing" does not exist')

        with pytest.raises(UndefinedTable):
            await executor.execute("SELECT * FROM app_missing", precheck=precheck)

        assert cursor.executed[-1] == "ROLLBACK"
        pool.mark_invalid.assert_not_called()

    async def test_connection_error_in_precheck_still_invalidates(self) -> None:
        cursor = _BatchCursor()
        executor, pool, _ = _pooled_executor(cursor)

        async def precheck(run):
            raise OperationalError("server closed the connection unexpectedly")

        with pytest.raises(OperationalError):
            await executor.execute("SELECT 1", precheck=precheck)

        pool.mark_invalid.assert_called_once()
```

Проверить, что `_pooled_executor` выставляет `connection.cursor.return_value = cursor` (так и есть — строка перед `checkout`), и что `UndefinedTable`, `OperationalError`, `AsyncMock` уже импортированы в файле (да).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/postgres/test_sql_executor.py -q -k Precheck`
Expected: FAIL — `TypeError: SqlExecutor.execute_statement() got an unexpected keyword argument 'precheck'`.

- [ ] **Step 3: Implement**

`src/postgres_fastmcp/postgres/ports.py` — импорт `from collections.abc import Awaitable, Callable`; после `SqlDriverPort`:

```python
# Выполняет одну строку SQL на курсоре текущей транзакции и возвращает строки её последнего результата
# (None — у результата нет строк, например SET).
StatementRunner = Callable[[str], Awaitable[list[RowResult] | None]]

# Предварительные запросы в транзакции оператора (проверка по плану): получают StatementRunner;
# исключение отменяет оператор и откатывает транзакцию.
Precheck = Callable[[StatementRunner], Awaitable[None]]


class PrecheckSqlDriverPort(SqlDriverPort, Protocol):
    """SQL-драйвер, который выполняет предварительные запросы в той же транзакции и на том же соединении, что и оператор.

    Отдельный протокол, а не параметр SqlDriverPort: его реализует только исполнитель без проверок
    (SqlExecutor). SafeSqlExecutor сам принимать чужой precheck не должен, а домены и их тестовые
    двойники остаются на SqlDriverPort.
    """

    async def execute(
        self,
        query: str,
        params: list[Any] | None = None,
        *,
        readonly: bool = True,
        precheck: Precheck | None = None,
    ) -> list[RowResult] | None:
        """Выполнение запроса (после precheck, если он задан) и возвращение строк или None."""
        ...

    async def execute_statement(
        self,
        query: str,
        params: list[Any] | None = None,
        *,
        readonly: bool = True,
        precheck: Precheck | None = None,
    ) -> StatementResult:
        """Выполнение запроса (после precheck, если он задан): строки и тег команды Postgres."""
        ...
```

`src/postgres_fastmcp/postgres/driver.py`:
- импорты: `from psycopg import AsyncCursor` (в существующий `from psycopg import (...)`), `from psycopg.rows import DictRow, dict_row`, `from postgres_fastmcp.postgres.ports import Precheck, StatementRunner`, `from postgres_fastmcp.shared.errors import ConnectionNotEstablishedError, UserFacingError`. Цикла нет: `ports` импортирует только `models`.
- `_is_connection_error`: первой проверкой

```python
    if isinstance(error, UserFacingError):
        return False
```

и в docstring: «Отказ проверки (UserFacingError из precheck: PlanAccessError, PlanUnverifiableError) — не ошибка соединения: пул исправен.»
- модульная функция перед `class SqlExecutor`:

```python
def _cursor_runner(cursor: AsyncCursor[DictRow]) -> StatementRunner:
    """StatementRunner поверх курсора транзакции: строка выполняется там же, где затем выполнится оператор."""

    async def run(sql: str) -> list[RowResult] | None:
        await cursor.execute(sql)
        while cursor.nextset():
            pass
        if cursor.description is None:
            return None
        return [RowResult(cells=dict(row)) for row in await cursor.fetchall()]

    return run
```

- `execute(…, *, readonly: bool = True, precheck: Precheck | None = None)` → `return (await self.execute_statement(query, params, readonly=readonly, precheck=precheck)).rows`; docstring `Args:` + `precheck: Предварительные запросы в той же транзакции до оператора (проверка по плану); исключение отменяет оператор.`
- `execute_statement(…, precheck: Precheck | None = None)`: оба вызова `_execute_with_connection(..., readonly=readonly, precheck=precheck)`; тот же `Args:`.
- `_execute_with_connection(self, connection, query, params, *, readonly: bool, precheck: Precheck | None = None)`: внутри `try:` первой строкой

```python
                if precheck is not None:
                    await precheck(_cursor_runner(cursor))
```

Docstring: «precheck выполняется после BEGIN на том же курсоре: его запросы и оператор — одна транзакция одного соединения; исключение precheck уходит в общий ROLLBACK, оператор не выполняется. Пометка hypopg смотрит только на оператор: EXPLAIN функций не вызывает.»

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit -q`
Expected: PASS (прежние тесты `SqlExecutor` не передают `precheck` и идут прежним путём; `test_execute_exception_marks_pool_invalid` с `RuntimeError` по-прежнему инвалидирует пул).

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
git add src/postgres_fastmcp/postgres/ports.py src/postgres_fastmcp/postgres/driver.py tests/unit/postgres/test_sql_executor.py
git commit -m "feat(postgres): run prechecks in the statement's transaction on one connection"
```

---

### Task 2: `SafeSqlExecutor` проверяет план через `precheck`; интеграция, README, спека

**Files:**
- Modify: `src/postgres_fastmcp/postgres/security/driver.py` (`SafeSqlExecutor.__init__`, `_checked_run`, `_run`, удалить `_explain_for_plan_check`, `_with_session_settings` → + `_session_settings`)
- Modify: `src/postgres_fastmcp/postgres/security/catalog_driver.py` (тип `delegate`)
- Modify: `src/postgres_fastmcp/postgres/security/plan_guard.py` (только docstring аргумента `explain`)
- Test: `tests/unit/postgres/test_safe_sql_executor.py` (класс `TestSafeSqlExecutorPlanCheck`), `tests/unit/domains/test_explain_service.py` (`_plan_check_delegate` и тесты на нём)
- Modify: `tests/integration/test_plan_check.py`
- Modify: `README.md` (раздел «Проверка по плану (`plan_check`)» ~стр. 220–228, «Безопасное выполнение SQL» п. 4 ~стр. 561), `docs/superpowers/specs/2026-09-29-basic-hardening-2-design.md` (статус)

**Interfaces:**
- Consumes: `PrecheckSqlDriverPort`, `Precheck`, `StatementRunner` из Task 1; `PlanGuard(explain: ExplainRunner, *, allowed_schema: str, table_prefix: str | None).check(query)` без изменений.
- Produces: `SafeSqlExecutor(delegate: PrecheckSqlDriverPort, validator, config)`; с `plan_check` и `allowed_schema` — ровно один вызов делегата на оператор: `run(<тег> <запрос>, params=None, readonly=config.read_only, precheck=<fn>)`, где `<fn>` сначала выполняет `SET LOCAL statement_timeout = N; SET LOCAL search_path = public;`, затем `/* <tag> */ EXPLAIN (VERBOSE, FORMAT JSON[, GENERIC_PLAN]) …` по каждому планируемому оператору. Без `plan_check` — прежний вызов `run(<префикс> <тег> <запрос>, params=None, readonly=…)` без ключа `precheck`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/postgres/test_safe_sql_executor.py` — заменить тела тестов `TestSafeSqlExecutorPlanCheck` (хелперы `_plan_rows`, `_basic_executor` остаются) и добавить фабрику делегата:

```python
_SETTINGS = "SET LOCAL statement_timeout = 5000; SET LOCAL search_path = public;"


def _precheck_delegate(plan: list[RowResult] | None = None, *, fail: Exception | None = None) -> MagicMock:
    """Делегат как SqlExecutor: precheck получает исполнитель строк; delegate.sent — строки транзакции по порядку."""
    delegate = MagicMock()
    delegate.sent = []

    async def runner(sql: str) -> list[RowResult] | None:
        delegate.sent.append(sql)
        if fail is not None and "EXPLAIN" in sql:
            raise fail
        return plan if "EXPLAIN" in sql else None

    async def call(query, params=None, *, readonly=True, precheck=None):
        if precheck is not None:
            await precheck(runner)
        delegate.sent.append(query)
        return [RowResult(cells={"x": 1})]

    delegate.execute = AsyncMock(side_effect=call)
    delegate.execute_statement = AsyncMock(side_effect=call)
    return delegate


class TestSafeSqlExecutorPlanCheck:
    """plan_check: EXPLAIN (VERBOSE) и оператор — один вызов делегата, одна транзакция; только с allowed_schema."""

    async def test_plan_is_checked_in_the_statement_transaction(self) -> None:
        delegate = _precheck_delegate(_plan_rows("public", "app_t"))

        result = await _basic_executor(delegate).execute("SELECT * FROM app_t")

        assert result == [RowResult(cells={"x": 1})]
        delegate.execute.assert_awaited_once()
        assert delegate.execute.await_args.kwargs["readonly"] is False
        assert delegate.sent == [
            _SETTINGS,
            "/* t */ EXPLAIN (VERBOSE, FORMAT JSON) SELECT * FROM app_t",
            "/* t */ SELECT * FROM app_t",
        ]

    async def test_settings_are_sent_once_for_every_statement_of_the_string(self) -> None:
        delegate = _precheck_delegate(_plan_rows("public", "app_t"))

        await _basic_executor(delegate).execute("SELECT * FROM app_t; SELECT * FROM app_u")

        assert delegate.sent.count(_SETTINGS) == 1
        assert [q for q in delegate.sent if "EXPLAIN" in q] == [
            "/* t */ EXPLAIN (VERBOSE, FORMAT JSON) SELECT * FROM app_t",
            "/* t */ EXPLAIN (VERBOSE, FORMAT JSON) SELECT * FROM app_u",
        ]

    async def test_plan_violation_stops_execution(self) -> None:
        delegate = _precheck_delegate(_plan_rows("secret", "accounts"))

        with pytest.raises(PlanAccessError, match=r"secret\.accounts"):
            await _basic_executor(delegate).execute("SELECT * FROM app_secret_view")

        assert "/* t */ SELECT * FROM app_secret_view" not in delegate.sent

    async def test_execute_statement_is_checked_too(self) -> None:
        delegate = _precheck_delegate(_plan_rows("secret", "accounts"))

        with pytest.raises(PlanAccessError):
            await _basic_executor(delegate).execute_statement("UPDATE app_secret_view SET token = 'x'")

        delegate.execute_statement.assert_awaited_once()
        assert "precheck" in delegate.execute_statement.await_args.kwargs
        delegate.execute.assert_not_awaited()
        assert delegate.sent[-1].startswith("/* t */ EXPLAIN (VERBOSE, FORMAT JSON) UPDATE")

    async def test_validator_runs_before_the_plan_check(self) -> None:
        """Проверка по плану не ослабляет валидатор: отклонённый им запрос не доходит до делегата."""
        delegate = _precheck_delegate()

        with pytest.raises(SchemaNotAllowedError):
            await _basic_executor(delegate).execute("SELECT * FROM secret.accounts")

        delegate.execute.assert_not_awaited()

    async def test_plan_check_off_keeps_the_prefixed_single_call(self) -> None:
        delegate = MagicMock()
        delegate.execute = AsyncMock(return_value=[])

        await _basic_executor(delegate, plan_check=False).execute("SELECT * FROM app_t")

        delegate.execute.assert_awaited_once_with(f"{_SETTINGS} /* t */ SELECT * FROM app_t", params=None, readonly=False)

    async def test_plan_check_is_ignored_without_allowed_schema(self) -> None:
        """Full (allowed_schema=None): plan_check не действует никогда, precheck не передаётся."""
        delegate = MagicMock()
        delegate.execute = AsyncMock(return_value=[])
        config = SafeSqlConfig(query_tag="t", plan_check=True)

        await _make_executor(delegate, config=config).execute("SELECT 1")

        delegate.execute.assert_awaited_once()
        assert "precheck" not in delegate.execute.await_args.kwargs

    async def test_explain_cancel_maps_to_query_timeout_error(self) -> None:
        delegate = _precheck_delegate(fail=_query_canceled("canceling statement due to statement timeout"))

        with pytest.raises(QueryTimeoutError):
            await _basic_executor(delegate).execute("SELECT * FROM app_t")
```

`tests/unit/domains/test_explain_service.py` — `_plan_check_delegate` переписать так, чтобы EXPLAIN проверки шли через `precheck` (ответы — те же ветки, что сейчас):

```python
def _plan_check_delegate(scanned_schema: str, scanned_relation: str) -> MagicMock:
    """Делегат как SqlExecutor: precheck получает исполнитель строк; delegate.sent — все строки по порядку."""
    scan = {"Node Type": "Seq Scan", "Relation Name": scanned_relation, "Schema": scanned_schema}
    delegate = MagicMock()
    delegate.sent = []

    async def answer(query):
        delegate.sent.append(query)
        if "pg_catalog.pg_extension" in query:
            return [RowResult(cells={"extversion": "1.4.1"})]
        if "EXPLAIN (VERBOSE, FORMAT JSON) SELECT * FROM" in query:
            return [RowResult(cells={"QUERY PLAN": [{"Plan": scan}]})]
        if "EXPLAIN (VERBOSE" in query:
            return [RowResult(cells={"QUERY PLAN": [{"Plan": {"Node Type": "Result"}}]})]
        if "EXPLAIN" in query:
            return [RowResult(cells={"QUERY PLAN": _SEQ_SCAN_PLAN})]
        return []

    async def execute(query, params=None, *, readonly=True, precheck=None):
        if precheck is not None:
            await precheck(answer)
        return await answer(query)

    delegate.execute = AsyncMock(side_effect=execute)
    return delegate
```

В `test_hypothetical_explain_passes_the_plan_check` и `test_hypothetical_explain_over_a_foreign_view_is_rejected` заменить `sent = [c.args[0] for c in delegate.execute.await_args_list]` на `sent = delegate.sent`; остальные утверждения остаются (EXPLAIN проверки по-прежнему с тегом `/* … */`, `standard_conforming_strings` в строках нет, `sent[-1]` — сам оператор `hypopg_reset(); hypopg_create_index…; EXPLAIN (FORMAT JSON, COSTS TRUE) …`). В `test_hypothetical_explain_passes_the_plan_check` добавить в конец:

```python
    # Канал сервера (версия hypopg) и один вызов на оператор агента; до PR 2 было 5: ещё три отдельных EXPLAIN.
    assert delegate.execute.await_count == 2
    assert sent.count("SET LOCAL statement_timeout = 30000; SET LOCAL search_path = public;") == 1
```

(Проверено на текущем коде: `_plan_check_db` даёт `safe_sql_timeout` по умолчанию 30 с, канал сервера идёт через тот же подменённый `SqlExecutor` одним запросом `pg_catalog.pg_extension`; сейчас вызовов 5 — `1 + 3 EXPLAIN + 1`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/postgres/test_safe_sql_executor.py tests/unit/domains/test_explain_service.py -q`
Expected: FAIL — `delegate.sent` пуст / `assert_awaited_once` видит несколько вызовов (EXPLAIN ещё идут отдельными вызовами без `precheck`).

- [ ] **Step 3: Implement** — `src/postgres_fastmcp/postgres/security/driver.py`:
  - импорты: `from postgres_fastmcp.postgres.ports import Precheck, PrecheckSqlDriverPort, StatementRunner` (вместо `SqlDriverPort`);
  - `__init__(self, delegate: PrecheckSqlDriverPort, …)`, docstring `delegate`: «Исполнитель без проверок (SqlExecutor): execute и execute_statement с precheck.»; вместо `self._plan_guard = PlanGuard(...)`:

```python
        # Проверка по плану — только для basic (allowed_schema задан); full и канал сервера её не получают.
        self._plan_check_schema = config.allowed_schema if config.plan_check else None
```

  - `_checked_run`:

```python
    async def _checked_run[T](self, query: str, run: Callable[..., Awaitable[T]]) -> T:
        """Выполнение с SET LOCAL через делегата; с plan_check — проверка по плану в той же транзакции.

        С plan_check делегат получает precheck: на том же соединении, после BEGIN, он один раз ставит
        SET LOCAL statement_timeout/search_path и строит план каждого оператора; оператор идёт без префикса
        и наследует настройки транзакции. AccessShareLock, взятый разбором, держится до конца транзакции:
        определение представления между проверкой и выполнением не меняется. Отказ проверки откатывает
        транзакцию, оператор не выполняется.

        Ограничение: PlanGuard строит планы всех операторов строки до выполнения первого, поэтому строка,
        где поздний оператор зависит от раннего (CREATE EXTENSION …; SELECT функция расширения), отклоняется
        ошибкой планирования.
        """
        allowed_schema = self._plan_check_schema
        if allowed_schema is None:
            return await self._run(self._with_session_settings(query), run, readonly=self._config.read_only)
        settings = self._session_settings()
        tag = self._config.query_tag
        table_prefix = self._config.table_prefix

        async def precheck(runner: StatementRunner) -> None:
            if settings:
                await runner(settings)

            async def explain(explain_sql: str) -> list[RowResult] | None:
                # Текст EXPLAIN — deparse pglast (standard_conforming_strings = on закрепляет SqlExecutor в BEGIN).
                return await runner(f"/* {tag} */ {explain_sql}")

            await PlanGuard(explain, allowed_schema=allowed_schema, table_prefix=table_prefix).check(query)

        return await self._run(query, run, readonly=self._config.read_only, precheck=precheck)
```

  - удалить `_explain_for_plan_check`;
  - `_run(self, query, run, *, readonly: bool, precheck: Precheck | None = None)`: вызов делегата

```python
            if precheck is None:
                return await run(query, params=None, readonly=readonly)
            return await run(query, params=None, readonly=readonly, precheck=precheck)
```

  (без `precheck` ключ не передаётся — канал сервера и путь без `plan_check` шлют делегату ровно то же, что раньше); docstring: «Отмена во время EXPLAIN проверки разбирается так же: они идут внутри того же вызова.»
  - `_session_settings()` и `_with_session_settings()`:

```python
    def _session_settings(self) -> str:
        """SET LOCAL statement_timeout и search_path одной строкой; порядок важен для читаемости логов."""
        prefix: list[str] = []
        if self._config.timeout is not None:
            prefix.append(f"SET LOCAL statement_timeout = {int(self._config.timeout * MS_PER_SECOND)};")
        if self._config.allowed_schema:
            prefix.append(f"SET LOCAL search_path = {self._config.allowed_schema};")
        return " ".join(prefix)

    def _with_session_settings(self, query: str) -> str:
        """Запрос с префиксом _session_settings (без префикса, если настраивать нечего)."""
        settings = self._session_settings()
        return f"{settings} {query}" if settings else query
```

  - docstring `execute`/`execute_statement` не меняются (`PlanAccessError`/`PlanUnverifiableError` поднимаются как раньше).
  - `catalog_driver.py`: `delegate: PrecheckSqlDriverPort` (импорт из `ports`), docstring «Исполнитель без проверок (SqlExecutor) на пуле сервиса.» — без изменений по смыслу; `plan_check` каталогу не включается, `precheck` не передаётся.
  - `plan_guard.py`: в `PlanGuard.__init__` docstring `explain: Выполняет оператор EXPLAIN в транзакции проверяемого оператора (SET LOCAL уже выставлен) и возвращает строки.`; комментарий у `ExplainRunner` — «на курсоре транзакции оператора».
  - `mypy`: `DbAccessService._executor` передаёт `SqlExecutor(conn=self._pool)` — удовлетворяет `PrecheckSqlDriverPort` структурно; `self._executors: dict[EffectiveAccess, SqlDriverPort]` не меняется.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit -q`
Expected: PASS (включая `tests/unit/postgres/test_catalog_driver.py` и `TestSafeSqlExecutorExecuteStatement::test_validates_prefixes_and_delegates` — без `plan_check` вызов `assert_awaited_once_with(..., params=None, readonly=False)` не меняется).

- [ ] **Step 5: Integration** — `tests/integration/test_plan_check.py` (фикстуры `db_plan_check`, `db_full` уже есть; `_SETUP` создаёт `secret.accounts` с `token = 'top-secret'`, `public.app_secret_view`, `public.app_plan_items`):
  - переименовать `test_write_statement_is_planned_in_a_read_only_transaction` → `test_write_statement_is_planned_and_run_once_in_one_transaction`:

```python
@pytest.mark.asyncio
async def test_write_statement_is_planned_and_run_once_in_one_transaction(db_plan_check: DbAccess) -> None:
    """EXPLAIN без ANALYZE в пишущей транзакции не исполняет DML: строка вставляется ровно один раз."""
    await db_plan_check.sql_driver.execute("DELETE FROM app_plan_items WHERE id = 2", readonly=False)
    await db_plan_check.sql_driver.execute("INSERT INTO app_plan_items (id) VALUES (2)", readonly=False)
    rows = await db_plan_check.sql_driver.execute("SELECT count(*) AS n FROM app_plan_items WHERE id = 2", readonly=True)
    await db_plan_check.sql_driver.execute("DELETE FROM app_plan_items WHERE id = 2", readonly=False)
    assert rows[0].cells["n"] == 1
```

  - новые:

```python
@pytest.mark.asyncio
async def test_statement_inherits_the_settings_of_the_check(db_plan_check: DbAccess) -> None:
    """Оператор идёт без префикса SET LOCAL: search_path = public он видит, только если выполнен в транзакции проверки."""
    rows = await db_plan_check.sql_driver.execute("SHOW search_path", readonly=True)
    assert rows[0].cells["search_path"] == "public"


@pytest.mark.asyncio
async def test_rejected_write_changes_nothing(db_plan_check: DbAccess, db_full: DbAccess) -> None:
    with pytest.raises(PlanAccessError, match=r"secret\.accounts"):
        await db_plan_check.sql_driver.execute("UPDATE app_secret_view SET token = 'leaked'", readonly=False)
    rows = await db_full.sql_driver.execute("SELECT token FROM secret.accounts WHERE id = 1", readonly=True)
    assert rows[0].cells["token"] == "top-secret"
```

  Сверить статически: `SHOW search_path` проходит валидатор basic (`search_path` в `BASIC_SHOW_PARAMETERS`) и не планируется (`PlanGuard` пропускает `VariableShowStmt`), так что `precheck` отправляет только строку настроек; `UPDATE` представления проходит валидатор basic+запись (`app_` префикс), отказ даёт план (`Seq Scan` на `secret.accounts` в `ModifyTable`), обновление не выполняется.
- [ ] **Step 6: README и спека** (по-русски):
  - «Проверка по плану (`plan_check`)»: после первого абзаца — «Проверка и выполнение идут в одной транзакции на одном соединении пула: сначала `SET LOCAL` таймаута и `search_path`, затем EXPLAIN каждого оператора, затем сам оператор. Разбор берёт `AccessShareLock` на представления и таблицы до конца транзакции, так что `CREATE OR REPLACE VIEW` между проверкой и выполнением невозможен. Отказ проверки откатывает транзакцию, оператор не выполняется»; пункт «Цена — лишний запрос(ы) к БД…» заменить на «Цена — по одному лишнему запросу к БД на каждый оператор строки (EXPLAIN) и один на `SET LOCAL`, в том же соединении; отдельного обращения к пулу нет. Клиентский таймаут (`safe_sql_timeout` + клиентская страховка) покрывает проверку и выполнение одним бюджетом; `statement_timeout` действует на каждый запрос транзакции отдельно».
  - «Безопасное выполнение SQL», п. 4: «…в этот же бюджет входит и EXPLAIN-проверка плана, выполняемая в той же транзакции перед оператором».
  - `2026-09-29-basic-hardening-2-design.md`: статус — «PR 1 и PR 2 реализованы (`claude/basic-hardening-2`, `claude/plan-check-one-transaction`)»; в §5 форму `precheck` дополнить: «типизирован протоколом `PrecheckSqlDriverPort` (`postgres/ports.py`); `_is_connection_error` не считает `UserFacingError` ошибкой соединения».
- [ ] **Step 7: Full verification, lint, commit**

```bash
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
git add src/postgres_fastmcp/postgres/security tests/unit/postgres/test_safe_sql_executor.py tests/unit/domains/test_explain_service.py tests/integration/test_plan_check.py README.md docs/superpowers/specs
git commit -m "feat(security): check the plan in the same transaction as the statement"
```

---

## Заметки к PR (спека §7)

- С `plan_check` проверка по плану и выполнение — одна транзакция на одном соединении; на каждый запрос агента на одно обращение к пулу меньше (было: по вызову на каждый EXPLAIN плюс вызов на оператор).
- EXPLAIN проверки для basic с записью идёт в пишущей транзакции оператора (без ANALYZE он DML не исполняет).
- `SafeSqlExecutor`/`CatalogSqlExecutor` принимают делегата типа `PrecheckSqlDriverPort`.

## Self-review

- Спека §5: один вызов, одна транзакция и соединение → Task 1 (`precheck` в `_execute_with_connection`) + Task 2 (`_checked_run`); префикс один раз → Task 2 (`_session_settings` в `precheck`, тест `…settings_are_sent_once…`); ошибка → откат → Task 1 (`…rolls_back_and_skips…`) и интеграция `test_rejected_write_changes_nothing`; режим транзакции = `read_only` конфигурации → Task 2 (`readonly is False` в тесте) и интеграция записи; клиентский таймаут общий — `_guarded` не меняется. §6 PR 2 — юнит (порядок курсора, `ROLLBACK`, один вызов делегата) и интеграция (замена пункта про конкурентную замену — см. Spec corrections).
- Имена одинаковы в обеих задачах: `StatementRunner`, `Precheck`, `PrecheckSqlDriverPort`, `_cursor_runner`, `_session_settings`, `_plan_check_schema`.
