# Вторая волна закалки basic (PR 1) — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** В basic закрыты представления `information_schema` с секретами и исходниками и небезопасные опции `EXPLAIN`; в full скрытые `hypopg_hide_index` индексы снимаются при возврате соединения в пул.

**Architecture:** Два правила валидатора и одна доработка пула. (1) `schema_guard.validate_schema_access` в ветке `information_schema` рядом с `schemata` отклоняет имена из новой константы `BASIC_BLOCKED_INFORMATION_SCHEMA_VIEWS` (`policies.py`) ошибкой `SystemRelationAccessError`. (2) `_NodeValidationVisitor` для `ExplainStmt` сначала, как раньше, проверяет `analyze` по флагу `allow_explain_analyze`, затем в basic пропускает только опции из `BASIC_EXPLAIN_OPTIONS`; остальные — новая `ExplainOptionNotAllowedError`. (3) `SqlExecutor` помечает соединение пула, если SQL содержит `hypopg_hide_index`: `DbConnPool.mark_hypopg_hidden(connection)`; reset-callback для помеченного вызывает `SELECT hypopg_unhide_all_indexes()` между `hypopg_reset()` и `DISCARD ALL`. Full-валидатор, канал сервера (`CatalogSqlExecutor`) и `PlanGuard` не меняются. Спека: `docs/superpowers/specs/2026-09-29-basic-hardening-2-design.md`, §2–§4, §6 (PR 1), §7.

**Tech Stack:** Python 3.12, uv, pglast v8.2, psycopg 3.3.4, psycopg_pool 3.3.1, pytest (asyncio auto); hypopg ≥ 1.4.0 для скрытия индексов.

## Spec corrections

- hypopg: `hypopg_unhide_all_indexes()` появилась в **1.4.0** (2023-05-27) одним скриптом `hypopg--1.3.1--1.4.0.sql` вместе с `hypopg_hide_index`, `hypopg_unhide_index`, `hypopg_hidden_indexes()` и представлением `hypopg_hidden_indexes`. Значит, «hide есть, unhide_all нет» в одной установке не бывает; ошибка reset-шага реальна только при другом `search_path` роли (как у `hypopg_reset()`) или при удалённом расширении — в обоих случаях соединение выбрасывается, как и задумано. CI (`tests/Dockerfile.postgres-hypopg`) собирает hypopg из `git clone --depth 1` ветки по умолчанию `REL1_STABLE` — сейчас 1.4.3. README называет минимальную версию 1.4.0.
- hypopg: `hypopg_reset()` скрытые индексы **не** снимает (в `hypo_index_reset()` чистится только список гипотетических; `hypoHiddenIndexes` чистит лишь `hypopg_unhide_all_indexes()`), а `DISCARD ALL` не трогает память расширения. Поэтому отдельный шаг и отдельная пометка действительно нужны.
- pglast `ExplainStmt.options` — список `DefElem`, `defname` уже в нижнем регистре для имён без кавычек (проверено на pglast v8.2): `analyze`, `verbose`, `costs`, `settings`, `generic_plan`, `buffers`, `serialize`, `wal`, `timing`, `summary`, `memory`, `format`. Старый синтаксис `EXPLAIN ANALYZE VERBOSE` даёт те же `DefElem`. Имя в кавычках (`"SETTINGS"`) pglast сохраняет как есть, Postgres такую опцию не распознаёт — сравнение через `lower()` отказывает и ей, обхода нет. `generic_plan` — PostgreSQL 16+, `memory`/`serialize` — 17+: на CI Postgres 15 `EXPLAIN (GENERIC_PLAN)` отклоняет сам сервер, валидатор тут ни при чём.
- `ExplainOptionNotAllowedError(option, allowed)` — со списком разрешённых вторым аргументом, как у `ShowParameterNotAllowedError`: `shared/errors.py` — нижний слой и не импортирует `postgres/security/policies.py`.
- `analyze` в проверке списка basic не участвует: его судьбу решает только `allow_explain_analyze` (своя ошибка, проверяется первой). В basic этот флаг всегда `False` (`DbAccessService._executor`), так что поведение не меняется, а флаг остаётся ортогональным.
- Ошибка для закрытых представлений называет отношение с схемой: `SystemRelationAccessError("information_schema.routines")` — «system relation 'information_schema.routines'»; подсказка `list_objects`/`get_object_details` та же.
- С `plan_check=true` все запросы к `information_schema` и так отклоняются по плану (представления читают `pg_catalog`); новое правило закрывает перечисленные представления и без `plan_check`.

## Global Constraints

- Всё, что видит агент или внешняя система (ошибки, логи, коммиты), — на английском; docstring и комментарии — по-русски. README — по-русски.
- Нет `from __future__ import annotations`.
- Ломающие изменения разрешены; описываются только в заметках к PR (спека §7). В коде — никаких шимов и упоминаний старого поведения.
- `shared/` — нижний слой: `shared/errors.py` не импортирует `postgres/`.
- Безопасность: ни одно существующее правило валидатора не ослабляется; full не меняется, кроме снятия скрытых индексов hypopg (спека §4); канал сервера (`CatalogSqlExecutor`) не затрагивается.
- Каждый новый подкласс `UserFacingError` получает запись в `_SAMPLES` (`tests/unit/shared/test_errors.py`), исправимый агентом — ещё и строку в `test_correctable_error_ends_with_hint`.
- Перед каждым коммитом: `uv run ruff check --fix --show-fixes`, `uv run mypy src/`, `uv run ruff format`; затем `uv run ruff check .`.
- Юнит-тесты: `uv run pytest tests/unit -q`. Интеграция: `uv run pytest tests/integration -q` — должна собираться; локально пропускается (нет Docker), в CI — Postgres 15/16 с hypopg. Сверять интеграционные тесты статически с кодом.
- Коммиты: `type(scope): message`, повелительное наклонение, английский.
- **Никогда не запускать никакие `git config` и `git stash`.** Не пушить.
- Ветка: `claude/basic-hardening-2` (уже создана, на ней коммит спеки `238cb5c`).

---

### Task 1: валидатор basic — `information_schema` с секретами и опции `EXPLAIN`

**Files:**
- Modify: `src/postgres_fastmcp/postgres/security/policies.py` (две константы, `__all__`)
- Modify: `src/postgres_fastmcp/postgres/security/schema_guard.py` (`validate_schema_access`, ветка `information_schema`)
- Modify: `src/postgres_fastmcp/postgres/security/query_validator.py` (`_NodeValidationVisitor.visit`, новый `_validate_explain_options`, docstring `QueryValidator.validate`)
- Modify: `src/postgres_fastmcp/shared/errors.py` (`ExplainOptionNotAllowedError`)
- Test: `tests/unit/shared/test_errors.py`, `tests/unit/postgres/test_query_validator.py`, `tests/unit/postgres/test_query_validator_corpus.py`

**Interfaces:**
- Produces: `BASIC_BLOCKED_INFORMATION_SCHEMA_VIEWS: frozenset[str]`, `BASIC_EXPLAIN_OPTIONS: frozenset[str]` в `postgres_fastmcp.postgres.security.policies`; `ExplainOptionNotAllowedError(option: str, allowed: Sequence[str])` в `postgres_fastmcp.shared.errors` с атрибутом `option` и текстом `EXPLAIN option SETTINGS is not allowed in basic mode. Allowed options: BUFFERS, COSTS, FORMAT, GENERIC_PLAN, MEMORY, SUMMARY, TIMING, VERBOSE.`

- [ ] **Step 1: Write the failing tests**

`tests/unit/shared/test_errors.py` — в `_SAMPLES` (после `ShowParameterNotAllowedError`):

```python
    "ExplainOptionNotAllowedError": lambda: errors.ExplainOptionNotAllowedError("settings", ["format", "costs"]),
```

в параметры `test_correctable_error_ends_with_hint`:

```python
        ("ExplainOptionNotAllowedError", "Allowed options: COSTS, FORMAT."),
```

и отдельный тест в конец файла:

```python
def test_explain_option_message_names_the_option_in_upper_case() -> None:
    """Опция и список — заглавными, как их пишут в EXPLAIN; список отсортирован."""
    error = errors.ExplainOptionNotAllowedError("wal", ["verbose", "costs"])
    assert str(error) == "EXPLAIN option WAL is not allowed in basic mode. Allowed options: COSTS, VERBOSE."
    assert error.option == "wal"
```

`tests/unit/postgres/test_query_validator.py` — добавить в импорт `ExplainOptionNotAllowedError` и новый класс после `TestQueryValidatorExplainAnalyze`:

```python
BASIC = QueryValidator(allowed_schema="public", read_only=True)
FULL = QueryValidator(read_only=True, allow_explain_analyze=True)


class TestBasicExplainOptions:
    """basic: только опции BASIC_EXPLAIN_OPTIONS; ANALYZE — своей ошибкой и первым; full не меняется."""

    @pytest.mark.parametrize(
        "sql",
        [
            "EXPLAIN (SETTINGS) SELECT 1",
            "EXPLAIN (settings on, format json) SELECT 1",
            "EXPLAIN (SETTINGS false) SELECT 1",
            "EXPLAIN (WAL) SELECT 1",
            "EXPLAIN (SERIALIZE TEXT) SELECT 1",
            'EXPLAIN ("SETTINGS") SELECT 1',
            "EXPLAIN (FUTURE_OPTION) SELECT 1",
        ],
    )
    def test_option_outside_the_list_is_rejected(self, sql: str) -> None:
        with pytest.raises(ExplainOptionNotAllowedError, match="Allowed options: BUFFERS, COSTS, FORMAT"):
            BASIC.validate(sql)

    def test_error_names_the_rejected_option(self) -> None:
        with pytest.raises(ExplainOptionNotAllowedError) as exc_info:
            BASIC.validate("EXPLAIN (FORMAT JSON, SETTINGS) SELECT 1")
        assert exc_info.value.option == "settings"
        assert str(exc_info.value).startswith("EXPLAIN option SETTINGS is not allowed in basic mode.")

    @pytest.mark.parametrize(
        "sql",
        [
            "EXPLAIN SELECT 1",
            "EXPLAIN VERBOSE SELECT 1",
            "EXPLAIN (FORMAT JSON, COSTS false, VERBOSE) SELECT 1",
            "EXPLAIN (SUMMARY, TIMING false, BUFFERS, MEMORY) SELECT 1",
            "EXPLAIN (GENERIC_PLAN) SELECT $1",
            "EXPLAIN (FORMAT JSON, GENERIC_PLAN, COSTS TRUE) SELECT $1",
        ],
    )
    def test_listed_options_pass(self, sql: str) -> None:
        BASIC.validate(sql)

    @pytest.mark.parametrize(
        "sql", ["EXPLAIN (ANALYZE) SELECT 1", "EXPLAIN (SETTINGS, ANALYZE) SELECT 1", "EXPLAIN ANALYZE VERBOSE SELECT 1"]
    )
    def test_analyze_keeps_its_own_error_and_is_checked_first(self, sql: str) -> None:
        with pytest.raises(ExplainAnalyzeNotSupportedError):
            BASIC.validate(sql)

    @pytest.mark.parametrize(
        "sql", ["EXPLAIN (SETTINGS) SELECT 1", "EXPLAIN (WAL, ANALYZE) SELECT 1", "EXPLAIN (SERIALIZE, ANALYZE) SELECT 1"]
    )
    def test_full_is_unchanged(self, sql: str) -> None:
        FULL.validate(sql)


class TestBasicInformationSchemaSecrets:
    """basic: представления information_schema с секретами и исходниками — SystemRelationAccessError."""

    @pytest.mark.parametrize(
        "view",
        [
            "user_mapping_options",
            "user_mappings",
            "foreign_server_options",
            "foreign_data_wrapper_options",
            "routines",
            "views",
            "triggers",
        ],
    )
    def test_view_is_rejected(self, view: str) -> None:
        with pytest.raises(SystemRelationAccessError, match=rf"'information_schema\.{view}'"):
            BASIC.validate(f"SELECT * FROM information_schema.{view}")

    @pytest.mark.parametrize(
        "sql",
        [
            'SELECT * FROM "information_schema".routines',
            'SELECT * FROM information_schema."ROUTINES"',
            "SELECT * FROM INFORMATION_SCHEMA.Routines",
            "SELECT 1 WHERE EXISTS (SELECT 1 FROM information_schema.views)",
            "WITH x AS (SELECT * FROM information_schema.triggers) SELECT * FROM x",
        ],
    )
    def test_any_spelling_and_position_is_rejected(self, sql: str) -> None:
        with pytest.raises(SystemRelationAccessError):
            BASIC.validate(sql)

    @pytest.mark.parametrize(
        "sql", ["SELECT * FROM information_schema.tables", "SELECT * FROM information_schema.columns"]
    )
    def test_structure_views_still_pass(self, sql: str) -> None:
        BASIC.validate(sql)
        QueryValidator(allowed_schema="public", table_prefix="app_", read_only=True).validate(sql)

    def test_full_is_unchanged(self) -> None:
        FULL.validate("SELECT * FROM information_schema.user_mapping_options")
```

`tests/unit/postgres/test_query_validator_corpus.py`:
- в `BASIC_BLOCKED_FULL_ALLOWED` (после блока `# схема в кавычках …`) новый блок:

```python
    # information_schema: секреты user mapping, опции серверов и обёрток, исходники функций, представлений, триггеров
    "SELECT * FROM information_schema.user_mapping_options",
    "SELECT * FROM information_schema.user_mappings",
    "SELECT * FROM information_schema.foreign_server_options",
    "SELECT * FROM information_schema.foreign_data_wrapper_options",
    "SELECT * FROM information_schema.routines",
    "SELECT * FROM information_schema.views",
    "SELECT * FROM information_schema.triggers",
    'SELECT routine_definition FROM "information_schema".routines',
    # опции EXPLAIN вне списка basic
    "EXPLAIN (SETTINGS) SELECT 1",
    "EXPLAIN (WAL) SELECT 1",
    "EXPLAIN (SERIALIZE) SELECT 1",
```

- в `BASIC_ALLOWED_EXTRA`:

```python
    # опции EXPLAIN из списка basic
    "EXPLAIN (FORMAT JSON, COSTS false, VERBOSE) SELECT 1",
    "EXPLAIN (SUMMARY, TIMING false, BUFFERS, MEMORY) SELECT 1",
    "EXPLAIN (GENERIC_PLAN) SELECT $1",
```

- в `TABLE_QUERIES_BASIC_ALLOWED`: `"SELECT * FROM information_schema.columns",`
- в `TABLE_QUERIES_BASIC_BLOCKED`: `"EXPLAIN (SETTINGS) SELECT * FROM app_users",`

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/shared/test_errors.py tests/unit/postgres/test_query_validator.py tests/unit/postgres/test_query_validator_corpus.py -q`
Expected: FAIL — `AttributeError: module 'postgres_fastmcp.shared.errors' has no attribute 'ExplainOptionNotAllowedError'` (импорт в `test_query_validator.py` падает на сборке), в корпусе — `DID NOT RAISE` для новых строк.

- [ ] **Step 3: Implement**

`src/postgres_fastmcp/shared/errors.py` — после `ExplainAnalyzeNotSupportedError`:

```python
class ExplainOptionNotAllowedError(UserFacingError):
    """Опция EXPLAIN вне разрешённого списка basic (валидация SQL)."""

    def __init__(self, option: str, allowed: Sequence[str]) -> None:
        """Инициализация с именем опции и разрешённым списком.

        Args:
            option: Имя опции из EXPLAIN (как его отдал pglast).
            allowed: Опции, которые basic разрешает.
        """
        allowed_names = ", ".join(sorted(name.upper() for name in allowed))
        message = f"EXPLAIN option {option.upper()} is not allowed in basic mode. Allowed options: {allowed_names}."
        super().__init__(message)
        self.option = option
```

`src/postgres_fastmcp/postgres/security/policies.py` — после `BASIC_SHOW_PARAMETERS`:

```python
# Представления information_schema, которые раскрывают секреты и исходники: опции user mapping (в том числе
# пароли), user mapping, опции серверов и обёрток (хосты, пути), тексты функций, представлений и триггеров.
# Фильтр по правам роли их не прячет — владелец объекта видит своё в любой схеме. Структуру объектов агент
# получает через list_objects/get_object_details.
BASIC_BLOCKED_INFORMATION_SCHEMA_VIEWS: frozenset[str] = frozenset(
    {
        "user_mapping_options",
        "user_mappings",
        "foreign_server_options",
        "foreign_data_wrapper_options",
        "routines",
        "views",
        "triggers",
    }
)

# Опции EXPLAIN, открытые в basic: форма и объём плана. SETTINGS показывает параметры сервера с
# нестандартными значениями; WAL и SERIALIZE имеют смысл только с ANALYZE; незнакомые (будущие) опции
# закрыты по умолчанию. ANALYZE сюда не входит: его разрешает отдельный флаг allow_explain_analyze.
BASIC_EXPLAIN_OPTIONS: frozenset[str] = frozenset(
    {"format", "verbose", "costs", "summary", "timing", "buffers", "generic_plan", "memory"}
)
```

и в `__all__`: `"BASIC_BLOCKED_INFORMATION_SCHEMA_VIEWS"`, `"BASIC_EXPLAIN_OPTIONS"` (в алфавитном порядке списка).

`src/postgres_fastmcp/postgres/security/schema_guard.py` — импорт `from postgres_fastmcp.postgres.security.policies import BASIC_BLOCKED_INFORMATION_SCHEMA_VIEWS` (цикла нет: `policies` не импортирует `schema_guard`); ветку `information_schema` заменить на:

```python
    if schemaname == "information_schema":
        view = relname.lower()
        if view == "schemata":
            raise SchemataTableAccessError(schemaname, relname)
        if view in BASIC_BLOCKED_INFORMATION_SCHEMA_VIEWS:
            raise SystemRelationAccessError(f"{schemaname}.{relname}")
        return
```

В docstring `Raises:` строку `SystemRelationAccessError` дополнить: «…или представление information_schema с секретами и исходниками (BASIC_BLOCKED_INFORMATION_SCHEMA_VIEWS)». Комментарий над веткой: без схемы имя в information_schema не резолвится (её нет в search_path basic), поэтому проверяются только квалифицированные.

`src/postgres_fastmcp/postgres/security/query_validator.py`:
- импорт `BASIC_EXPLAIN_OPTIONS` из `policies` и `ExplainOptionNotAllowedError` из `shared.errors`;
- в `visit` заменить блок `if isinstance(node, ExplainStmt) and not self._allow_explain_analyze: …` на

```python
        if isinstance(node, ExplainStmt):
            self._validate_explain_options(node)
```

- новый метод рядом с `_validate_type_name`:

```python
    def _validate_explain_options(self, node: ExplainStmt) -> None:
        """ANALYZE — по флагу allow_explain_analyze и первым; в basic прочие опции — только из BASIC_EXPLAIN_OPTIONS.

        Имя сравнивается без учёта регистра: pglast уже свернул имена без кавычек, а имя в кавычках
        ("SETTINGS") Postgres не распознаёт — отказ такому имени ничего не ломает и обхода не даёт.

        Raises:
            ExplainAnalyzeNotSupportedError: ANALYZE при allow_explain_analyze=False.
            ExplainOptionNotAllowedError: В basic опция вне BASIC_EXPLAIN_OPTIONS.
        """
        names = [
            option.defname.lower() for option in node.options or () if isinstance(option, DefElem) and option.defname
        ]
        if not self._allow_explain_analyze and "analyze" in names:
            raise ExplainAnalyzeNotSupportedError
        if not self._basic:
            return
        for name in names:
            if name != "analyze" and name not in BASIC_EXPLAIN_OPTIONS:
                raise ExplainOptionNotAllowedError(name, sorted(BASIC_EXPLAIN_OPTIONS))
```

- в docstring `visit` и `QueryValidator.validate` после `ExplainAnalyzeNotSupportedError` добавить `ExplainOptionNotAllowedError: Опция EXPLAIN вне списка basic (SETTINGS, WAL, SERIALIZE, незнакомые).`; строку `SystemRelationAccessError` дополнить «или закрытое представление information_schema».

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit -q`
Expected: PASS (все, включая `test_every_user_facing_error_has_a_sample` и `tests/unit/domains/test_explain_service.py::…test_explain_analyze_falls_back_to_plain_in_basic_mode` — `EXPLAIN (FORMAT JSON, ANALYZE)` по-прежнему даёт `ExplainAnalyzeNotSupportedError`).

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
git add src/postgres_fastmcp/postgres/security src/postgres_fastmcp/shared/errors.py tests/unit/shared/test_errors.py tests/unit/postgres
git commit -m "feat(security): close secret-bearing information_schema views and unsafe EXPLAIN options in basic"
```

---

### Task 2: снимать скрытые индексы hypopg при возврате соединения

**Files:**
- Modify: `src/postgres_fastmcp/postgres/connection.py` (`DbConnPool.__init__`, новый `mark_hypopg_hidden`, `_reset_connection`)
- Modify: `src/postgres_fastmcp/postgres/driver.py` (`SqlExecutor._execute_with_connection`)
- Test: `tests/unit/postgres/test_db_conn_pool.py`, `tests/unit/postgres/test_sql_executor.py`

**Interfaces:**
- Produces: `DbConnPool.mark_hypopg_hidden(connection: AsyncConnection[Any]) -> None`. Порядок запросов reset-callback для открытого соединения: `SELECT hypopg_reset()` (если помечено `mark_hypopg_used`) → `SELECT hypopg_unhide_all_indexes()` (если помечено `mark_hypopg_hidden`) → `DISCARD ALL`; у каждого — `RESET_TIMEOUT_SECONDS`; ошибка любого — WARNING и re-raise, следующие шаги не выполняются. Обе пометки снимаются в начале reset.

- [ ] **Step 1: Write the failing tests**

`tests/unit/postgres/test_db_conn_pool.py` — новый класс после `TestHypopgReset` (хелперы `_reset_callback` и `_returned_connection` уже есть в файле):

```python
class TestHypopgHiddenIndexes:
    """Скрытые hypopg_hide_index индексы — тоже состояние сессии: hypopg_reset() их не снимает."""

    async def test_hidden_mark_gets_unhide_all_before_discard_all(self) -> None:
        pool_mgr = DbConnPool(connection_url="postgresql://localhost/test")
        reset = await _reset_callback(pool_mgr)
        connection = _returned_connection()
        pool_mgr.mark_hypopg_hidden(connection)

        await reset(connection)

        assert [call.args[0] for call in connection.execute.await_args_list] == [
            "SELECT hypopg_unhide_all_indexes()",
            "DISCARD ALL",
        ]

        # Пометка снята: второй возврат того же соединения — только DISCARD ALL.
        connection.execute.reset_mock()
        await reset(connection)
        connection.execute.assert_awaited_once_with("DISCARD ALL")

    async def test_both_marks_reset_then_unhide_then_discard_all(self) -> None:
        pool_mgr = DbConnPool(connection_url="postgresql://localhost/test")
        reset = await _reset_callback(pool_mgr)
        connection = _returned_connection()
        pool_mgr.mark_hypopg_used(connection)
        pool_mgr.mark_hypopg_hidden(connection)

        await reset(connection)

        assert [call.args[0] for call in connection.execute.await_args_list] == [
            "SELECT hypopg_reset()",
            "SELECT hypopg_unhide_all_indexes()",
            "DISCARD ALL",
        ]

    async def test_unhide_failure_is_logged_and_raised_without_discard_all(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Нет функции (другой search_path роли, расширение удалено) — соединение выбрасывается пулом."""
        pool_mgr = DbConnPool(connection_url="postgresql://localhost/test")
        reset = await _reset_callback(pool_mgr)
        connection = _returned_connection()
        connection.execute = AsyncMock(
            side_effect=UndefinedFunction("function hypopg_unhide_all_indexes() does not exist")
        )
        pool_mgr.mark_hypopg_hidden(connection)

        with caplog.at_level(logging.WARNING, logger="postgres_fastmcp.postgres.connection"):
            with pytest.raises(UndefinedFunction):
                await reset(connection)

        assert any("Failed to unhide hidden indexes" in r.getMessage() for r in caplog.records)
        connection.execute.assert_awaited_once_with("SELECT hypopg_unhide_all_indexes()")

    async def test_closed_hidden_connection_is_left_alone(self) -> None:
        pool_mgr = DbConnPool(connection_url="postgresql://localhost/test")
        reset = await _reset_callback(pool_mgr)
        connection = _returned_connection(closed=True)
        pool_mgr.mark_hypopg_hidden(connection)

        await reset(connection)

        connection.execute.assert_not_awaited()
```

`tests/unit/postgres/test_sql_executor.py` — в `TestSqlExecutorMarksHypopgConnections` (хелперы `_pooled_executor`, `_FakeCursor` уже есть):

```python
    async def test_hide_index_marks_the_connection_as_hidden(self) -> None:
        executor, pool, connection = _pooled_executor(_FakeCursor(("SELECT 1", 1, [{"hypopg_hide_index": True}])))

        await executor.execute("SELECT HYPOPG_HIDE_INDEX(16384)")

        pool.mark_hypopg_hidden.assert_called_once_with(connection)
        pool.mark_hypopg_used.assert_not_called()

    async def test_hidden_mark_is_set_even_if_the_statement_fails(self) -> None:
        executor, pool, connection = _pooled_executor(_FakeCursor())

        with pytest.raises(IndexError):
            await executor.execute("SELECT hypopg_hide_index(16384)")

        pool.mark_hypopg_hidden.assert_called_once_with(connection)
```

и в `test_plain_query_does_not_mark` добавить `pool.mark_hypopg_hidden.assert_not_called()`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/postgres/test_db_conn_pool.py tests/unit/postgres/test_sql_executor.py -q`
Expected: FAIL — `AttributeError: 'DbConnPool' object has no attribute 'mark_hypopg_hidden'` (у `MagicMock(spec=DbConnPool)` — то же `AttributeError`).

- [ ] **Step 3: Implement**

`src/postgres_fastmcp/postgres/connection.py`:
- в `__init__` рядом с `_hypopg_connections`:

```python
        # Соединения, на которых скрывались индексы (hypopg_hide_index): список скрытых живёт в памяти сессии,
        # hypopg_reset() и DISCARD ALL его не чистят — только hypopg_unhide_all_indexes() (hypopg 1.4.0+).
        self._hypopg_hidden_connections: weakref.WeakSet[AsyncConnection[Any]] = weakref.WeakSet()
```

- после `mark_hypopg_used`:

```python
    def mark_hypopg_hidden(self, connection: AsyncConnection[Any]) -> None:
        """Пометить соединение: при возврате в пул на нём выполнится hypopg_unhide_all_indexes().

        Args:
            connection: Соединение пула, на котором выполнялся hypopg_hide_index.
        """
        self._hypopg_hidden_connections.add(connection)
```

- в `_reset_connection`: после `self._hypopg_connections.discard(connection)` —

```python
        is_hidden = connection in self._hypopg_hidden_connections
        self._hypopg_hidden_connections.discard(connection)
```

между блоком `hypopg_reset()` и блоком `DISCARD ALL` —

```python
        try:
            if is_hidden:
                async with asyncio.timeout(RESET_TIMEOUT_SECONDS):
                    await connection.execute("SELECT hypopg_unhide_all_indexes()")
        except Exception as e:
            logger.warning("Failed to unhide hidden indexes on a returned connection: %s", e)
            raise
```

Docstring `_reset_connection`: после фразы про гипотетические индексы — «для соединения, где скрывались индексы (hypopg_hide_index), затем снимается скрытие (hypopg_unhide_all_indexes(): hypopg_reset() скрытые индексы не трогает)»; фраза про UndefinedFunction относится к обоим вызовам hypopg. Комментарий у `RESET_TIMEOUT_SECONDS`: перечислить `hypopg_reset(), hypopg_unhide_all_indexes(), DISCARD ALL`.

`src/postgres_fastmcp/postgres/driver.py` — в `_execute_with_connection` заменить проверку пометки на:

```python
        if isinstance(self.conn, DbConnPool):
            lowered = str(query).lower()
            if "hypopg_create_index" in lowered:
                self.conn.mark_hypopg_used(connection)
            if "hypopg_hide_index" in lowered:
                self.conn.mark_hypopg_hidden(connection)
```

Docstring: «SQL с hypopg_hide_index помечает соединение так же: пул снимет скрытие индексов при возврате».

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit -q`
Expected: PASS.

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
git add src/postgres_fastmcp/postgres/connection.py src/postgres_fastmcp/postgres/driver.py tests/unit/postgres
git commit -m "fix(postgres): unhide hypopg-hidden indexes when a connection returns to the pool"
```

---

### Task 3: интеграция, README, спеки

**Files:**
- Create: `tests/integration/test_basic_hardening.py`
- Modify: `README.md` (абзац «Что закрыто в access_mode=basic…» ~стр. 173, «Управление жизненным циклом» ~стр. 549, «Установка расширений» ~стр. 600)
- Modify: `docs/superpowers/specs/2026-09-29-basic-hardening-2-design.md` (статус), `docs/superpowers/specs/2026-09-28-basic-confinement-design.md` (§6)

- [ ] **Step 1: Integration tests** — `tests/integration/test_basic_hardening.py` (фикстуры: `db_user_prefix` из `tests/integration/conftest.py` — basic, `app_`, только чтение; пул из одного соединения — как в `tests/integration/test_connection_options.py::test_hypothetical_indexes_are_reset_when_the_connection_returns`):

```python
# mypy: ignore-errors
"""Вторая волна закалки basic на живом Postgres: information_schema с секретами, опции EXPLAIN, скрытые индексы."""

import logging

import pytest

from postgres_fastmcp.access import EffectiveAccess
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.domains.db_access import DbAccess, DbAccessService
from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.shared.errors import ExplainOptionNotAllowedError, SystemRelationAccessError


logger = logging.getLogger(__name__)

_FULL_WRITE = EffectiveAccess(AccessMode.FULL, write_mode=True)


@pytest.mark.asyncio
async def test_user_mapping_options_are_rejected_in_basic(db_user_prefix: DbAccess) -> None:
    with pytest.raises(SystemRelationAccessError, match=r"information_schema\.user_mapping_options"):
        await db_user_prefix.sql_driver.execute("SELECT * FROM information_schema.user_mapping_options", readonly=True)


@pytest.mark.asyncio
async def test_information_schema_columns_still_work_in_basic(db_user_prefix: DbAccess) -> None:
    rows = await db_user_prefix.sql_driver.execute(
        "SELECT count(*) AS n FROM information_schema.columns WHERE table_schema = 'public'", readonly=True
    )
    assert rows[0].cells["n"] >= 0


@pytest.mark.asyncio
async def test_explain_settings_is_rejected_and_safe_options_run_in_basic(db_user_prefix: DbAccess) -> None:
    with pytest.raises(ExplainOptionNotAllowedError, match="SETTINGS"):
        await db_user_prefix.sql_driver.execute("EXPLAIN (SETTINGS) SELECT 1", readonly=True)

    rows = await db_user_prefix.sql_driver.execute("EXPLAIN (FORMAT JSON, COSTS false, VERBOSE) SELECT 1", readonly=True)
    assert rows[0].cells["QUERY PLAN"][0]["Plan"]["Node Type"] == "Result"


@pytest.mark.asyncio
async def test_hidden_indexes_are_unhidden_when_the_connection_returns(
    test_postgres_connection_string: tuple[str, str],
) -> None:
    """Пул из одного соединения: следующий запрос получает то же соединение, и скрытых индексов на нём нет."""
    connection_string, _ = test_postgres_connection_string
    config = DatabaseConfig.from_uri(
        connection_string, access_mode=AccessMode.FULL, write_mode=True, pool_min_size=1, pool_max_size=1
    )
    service = DbAccessService(config)
    db = service.view(_FULL_WRITE)
    try:
        await db.sql_driver.execute("CREATE TABLE IF NOT EXISTS hypo_hide_t (id int PRIMARY KEY)", readonly=False)
        try:
            await db.sql_driver.execute("CREATE EXTENSION IF NOT EXISTS hypopg", readonly=False)
        except Exception as e:
            logger.warning("hypopg not available: %s", e)
            pytest.skip("hypopg extension is not available")

        hidden = await db.sql_driver.execute(
            "SELECT hypopg_hide_index('hypo_hide_t_pkey'::regclass) AS hidden", readonly=True
        )
        after_return = await db.sql_driver.execute("SELECT count(*) AS n FROM hypopg_hidden_indexes", readonly=True)
    finally:
        await service.close()

    assert hidden[0].cells["hidden"] is True
    assert after_return[0].cells["n"] == 0
```

- [ ] **Step 2: Verify statically** — `uv run pytest tests/integration -q` собирается (локально — skip). Сверить: full+write идёт через `SqlExecutor` без обёртки (`DbAccessService._executor`), так что пометка ставится в `_execute_with_connection`; `hypopg_hide_index(oid)` принимает `regclass` через неявное приведение к `oid`; представление `hypopg_hidden_indexes` есть с hypopg 1.4.0 (CI собирает `REL1_STABLE`, 1.4.3); `db_user_prefix` — basic без `plan_check`, иначе `information_schema.columns` отклонила бы проверка по плану.

- [ ] **Step 3: README** (по-русски):
  - абзац «**Что закрыто в access_mode=basic для SQL агента:**» — после `SHOW` добавить: «представления `information_schema` с секретами и исходниками — `user_mapping_options`, `user_mappings`, `foreign_server_options`, `foreign_data_wrapper_options`, `routines`, `views`, `triggers` (структуру объектов дают `list_objects`/`get_object_details`); опции `EXPLAIN` вне списка `FORMAT`, `VERBOSE`, `COSTS`, `SUMMARY`, `TIMING`, `BUFFERS`, `GENERIC_PLAN`, `MEMORY` (в том числе `SETTINGS`)»; фразу «`information_schema` показывает метаданные других схем в пределах прав роли» дополнить «(кроме закрытых представлений выше)».
  - «Управление жизненным циклом»: после пункта про гипотетические индексы — «Индексы, скрытые `hypopg_hide_index` (full), снова видны планировщику, когда соединение возвращается в пул: reset-хук вызывает `hypopg_unhide_all_indexes()` (hypopg 1.4.0+; при ошибке соединение выбрасывается из пула)».
  - «Установка расширений»: «Скрытие индексов (`hypopg_hide_index`, только full) требует hypopg 1.4.0 или новее».
- [ ] **Step 4: Specs**
  - `2026-09-29-basic-hardening-2-design.md`: строка статуса — «PR 1 реализован (`claude/basic-hardening-2`)»; в §4 заменить «проверить версию, где она появилась» на «появилась в hypopg 1.4.0 вместе с `hypopg_hide_index`».
  - `2026-09-28-basic-confinement-design.md` §6: пункт про `hypopg_hide_index` — `(/)` со ссылкой на спеку 2026-09-29 §4; пункт про `routines`/`views`/`user_mapping_options` — `(/)` в basic закрыты (§2 спеки 2026-09-29); пункт `EXPLAIN (SETTINGS)` — `(/)` (§3 спеки 2026-09-29).
- [ ] **Step 5: Full verification, lint, commit**

```bash
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
git add tests/integration/test_basic_hardening.py README.md docs/superpowers/specs
git commit -m "test(security): pin the second basic hardening wave against Postgres"
```

---

## Заметки к PR (спека §7)

- basic: `information_schema.user_mapping_options`, `user_mappings`, `foreign_server_options`, `foreign_data_wrapper_options`, `routines`, `views`, `triggers` недоступны (`SystemRelationAccessError`).
- basic: `EXPLAIN` — только опции `FORMAT`, `VERBOSE`, `COSTS`, `SUMMARY`, `TIMING`, `BUFFERS`, `GENERIC_PLAN`, `MEMORY` (`ExplainOptionNotAllowedError`); `ANALYZE` — прежняя `ExplainAnalyzeNotSupportedError`.
- full: скрытые `hypopg_hide_index` индексы снимаются при возврате соединения в пул (hypopg 1.4.0+).

## Self-review

- Спека §2 → Task 1 (константа, `schema_guard`, корпус) и Task 3 (интеграция `user_mapping_options`); §3 → Task 1 (опции, ошибка, `analyze` первым, full без изменений) и Task 3; §4 → Task 2 (пометка, порядок reset, WARNING и re-raise) и Task 3 (интеграция, README с версией); §6 PR 1 — все пункты покрыты; §7 → «Заметки к PR».
- `explain_query` строит `EXPLAIN (FORMAT JSON[, ANALYZE][, GENERIC_PLAN][, COSTS TRUE])` — в basic без `ANALYZE` всё в списке; `PlanGuard` строит свой EXPLAIN мимо валидатора — не затронут.
- Имена: `BASIC_BLOCKED_INFORMATION_SCHEMA_VIEWS`, `BASIC_EXPLAIN_OPTIONS`, `ExplainOptionNotAllowedError(option, allowed)`, `DbConnPool.mark_hypopg_hidden`, лог `Failed to unhide hidden indexes on a returned connection: %s` — одинаковы во всех задачах.
