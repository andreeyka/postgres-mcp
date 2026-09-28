# Гигиена сессии пула — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Соединение пула не переносит состояние сессии между запросами, а каждая транзакция агента разбирается с `standard_conforming_strings = on`.

**Architecture:** Пул общий для full и basic и для всех пользователей HTTP-сервера. Сейчас состояние сессии (`SET ROLE`, `SET` параметров, временные таблицы, курсоры `WITH HOLD`, `LISTEN`, advisory-блокировки, `PREPARE`) переживает возврат соединения: запрос full+write с `SET ROLE` или `CREATE TEMP TABLE app_x` влияет на следующий basic-запрос на том же соединении (временная таблица перекрывает имя `public.app_x`; `FETCH ALL FROM c` читает чужой курсор). Отдельно: валидатор (pglast) и проверка по плану разбирают строки как при `standard_conforming_strings = on`; если у БД или роли `off`, текст исполняется иначе, чем проверен (`SELECT 'a\', $$' FROM secret.t --$$`). `SET LOCAL` в той же строке, что и запрос, не помогает: Postgres лексит всю строку простого протокола до выполнения. Решение: (1) `SET LOCAL standard_conforming_strings = on` отправляется в той же команде, что и `BEGIN`, — следующий `execute` с запросом уже лексится с `on`, без лишнего круга к БД; (2) reset-callback пула выполняет `DISCARD ALL` для каждого возвращённого открытого соединения (после `hypopg_reset()` для помеченных), а у psycopg отключается автоподготовка (`prepare_threshold=None`), чтобы `DISCARD ALL` (он включает `DEALLOCATE ALL`) не ломал клиентский кэш подготовленных запросов. Цена — один запрос к БД на каждый возврат соединения. Спека: `docs/superpowers/specs/2026-09-28-basic-followups-design.md` (§5 PR 4 дописывается в Task 2).

**Tech Stack:** Python 3.12, psycopg 3.3, psycopg_pool 3.3.1.

## Global Constraints

- Всё, что видит агент или внешняя система (ошибки, логи, коммиты), — на английском; docstring и комментарии — по-русски. README — по-русски.
- Нет `from __future__ import annotations`.
- Ломающие изменения разрешены; описываются только в заметках к PR (спека §5).
- Перед каждым коммитом: `uv run ruff check --fix --show-fixes`, `uv run mypy src/`, `uv run ruff format`; затем `uv run ruff check .`.
- Юнит-тесты: `uv run pytest tests/unit -q`. Интеграция: `uv run pytest tests/integration -q` — должна собираться; локально пропускается (нет Docker), в CI — Postgres 15/16 с hypopg. Сверять статически.
- Коммиты: `type(scope): message`, повелительное наклонение, английский.
- **Никогда не запускать никакие `git config` и `git stash`.** Не пушить.
- Ветка: `claude/session-hygiene` (создана от вершины `claude/basic-plan-check`).

---

### Task 1: закрепить `standard_conforming_strings` и сбрасывать сессию при возврате

**Files:**
- Modify: `src/postgres_fastmcp/postgres/driver.py` (`SqlExecutor._execute_with_connection`)
- Modify: `src/postgres_fastmcp/postgres/connection.py` (`DbConnPool._open_pool`, `_reset_connection`)
- Test: `tests/unit/postgres/test_sql_executor.py`, `tests/unit/postgres/test_db_conn_pool.py`

**Interfaces:**
- Produces: `SqlExecutor` открывает транзакцию командой `BEGIN TRANSACTION READ ONLY; SET LOCAL standard_conforming_strings = on` (или `BEGIN; SET LOCAL standard_conforming_strings = on`) — одним `cursor.execute`. `DbConnPool` создаёт `AsyncConnectionPool(..., kwargs={"prepare_threshold": None}, reset=self._reset_connection)`; `_reset_connection` для открытого соединения: `hypopg_reset()` при пометке (как сейчас, ошибка — WARNING и re-raise), затем всегда `DISCARD ALL` (ошибка — WARNING и re-raise: пул выбросит соединение).

- [ ] **Step 1: Write the failing tests**
  - `test_sql_executor.py`: первая команда курсора для `readonly=True` — `BEGIN TRANSACTION READ ONLY; SET LOCAL standard_conforming_strings = on`, для `readonly=False` — `BEGIN; SET LOCAL standard_conforming_strings = on`; запрос идёт отдельным `execute` после неё (посмотреть, как существующие тесты этого файла подменяют курсор, и следовать им; обновить тесты, которые проверяют старую строку `BEGIN…`).
  - `test_db_conn_pool.py` (следовать стилю `TestHypopgReset`):
    - непомеченное открытое соединение получает ровно `DISCARD ALL`;
    - помеченное — `SELECT hypopg_reset()`, затем `DISCARD ALL`, пометка снята;
    - закрытое соединение — ни одного запроса;
    - ошибка `DISCARD ALL` — WARNING и исключение поднимается;
    - ошибка `hypopg_reset()` — WARNING, исключение поднимается, `DISCARD ALL` не выполняется;
    - `AsyncConnectionPool` создаётся с `kwargs={"prepare_threshold": None}` (патч конструктора, как в существующем тесте `max_idle`).
- [ ] **Step 2: Run tests to verify they fail** — `uv run pytest tests/unit/postgres/test_sql_executor.py tests/unit/postgres/test_db_conn_pool.py -q`.
- [ ] **Step 3: Implement**
  - `driver.py`: константы модуля `_BEGIN_READ_ONLY = "BEGIN TRANSACTION READ ONLY; SET LOCAL standard_conforming_strings = on"` и `_BEGIN_READ_WRITE = "BEGIN; SET LOCAL standard_conforming_strings = on"` с русским комментарием: валидатор и проверка по плану разбирают SQL как при `on`; `SET LOCAL` в строке запроса не действует на её же лексинг, поэтому параметр ставится командой до запроса, в той же транзакции. Docstring `_execute_with_connection` дополнить.
  - `connection.py`: `kwargs={"prepare_threshold": None}` в конструкторе пула с комментарием (DISCARD ALL включает DEALLOCATE ALL; клиентский кэш подготовленных запросов psycopg иначе ссылался бы на удалённые на сервере). `_reset_connection`: обновить docstring (соединение возвращается в пул без состояния сессии: роль, параметры, временные таблицы, курсоры, LISTEN, блокировки, подготовленные запросы); после ветки hypopg — `await connection.execute("DISCARD ALL")` в том же `try` с тем же WARNING-текстом, но отдельным сообщением `Failed to discard session state on a returned connection: %s`.
- [ ] **Step 4: Run tests to verify they pass** — `uv run pytest tests/unit -q`.
- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
git add src/postgres_fastmcp/postgres tests/unit/postgres
git commit -m "fix(postgres): return pooled connections without session state"
```

---

### Task 2: интеграция, README, спека

**Files:**
- Create: `tests/integration/test_session_hygiene.py`
- Modify: `README.md`, `docs/superpowers/specs/2026-09-28-basic-followups-design.md`, `docs/superpowers/specs/2026-09-28-basic-confinement-design.md` (§6 — ссылка), plan `docs/superpowers/plans/2026-09-28-13-basic-plan-check.md` не трогать.

- [ ] **Step 1: Integration tests** (фикстуры и стиль — как в `tests/integration/test_connection_options.py`: свой `DbAccessService` с `pool_min_size=1, pool_max_size=1`, чтобы следующий запрос получил то же соединение; full+write и basic views из одного сервиса):
  - full+write `CREATE TEMP TABLE app_tmp_leak AS SELECT 1 AS x`, затем basic `SELECT * FROM app_tmp_leak` → ошибка Postgres «relation does not exist» (`psycopg.errors.UndefinedTable`);
  - full+write `DECLARE leak_cur CURSOR WITH HOLD FOR SELECT 1`, затем basic `FETCH ALL FROM leak_cur` → `psycopg.errors.InvalidCursorName`;
  - full+write `SET application_name = 'leak'`, затем full read-only `SELECT current_setting('application_name') AS v` → не `'leak'`;
  - роль с `standard_conforming_strings = off`: создать `CREATE ROLE scs_probe LOGIN` (идемпотентно, `DO`-блок), `ALTER ROLE scs_probe SET standard_conforming_strings = off`, подключиться этой ролью (trust в CI — см. `tests/integration/test_role_check.py`, как там собирается URI другой роли), выполнить через basic `SELECT 'a\' AS v` → `v == "a\\"` (строка из двух символов `a\`), то есть лексинг шёл с `on`.
- [ ] **Step 2: Verify statically**: заголовки ошибок psycopg, что `DISCARD ALL` выполняется в autocommit (пул возвращает IDLE-соединение; `SqlExecutor` ставит `autocommit=True` при выдаче), что trust позволяет войти `scs_probe`.
- [ ] **Step 3: README** — в разделе про безопасность выполнения SQL: пункт «Соединение пула возвращается без состояния сессии (`DISCARD ALL`); каждая транзакция идёт с `standard_conforming_strings = on`; автоподготовка psycopg отключена»; в разделе про пул — цена: один запрос к БД на возврат соединения.
- [ ] **Step 4: Spec** — `2026-09-28-basic-followups-design.md`: статус (PR 4 реализован), в §1 таблицу — строка PR 4 `claude/session-hygiene`, §5 — PR 4: состояние сессии не переживает возврат соединения (`SET ROLE`, параметры, временные таблицы, курсоры `WITH HOLD`, `LISTEN`, advisory-блокировки, `PREPARE`); `standard_conforming_strings = on` в каждой транзакции (validator/plan_check больше не зависят от настройки сервера); `prepare_threshold=None`; цена — запрос на возврат. В §4 (plan_check) заменить отсылку «до PR 4 предполагается on» на факт. `2026-09-28-basic-confinement-design.md` §6: пункт про общий пул и состояние сессии — закрыт PR 4.
- [ ] **Step 5: Full verification, lint, commit**

```bash
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
git add tests/integration/test_session_hygiene.py README.md docs/superpowers/specs
git commit -m "test(postgres): pin session hygiene of pooled connections against Postgres"
```
