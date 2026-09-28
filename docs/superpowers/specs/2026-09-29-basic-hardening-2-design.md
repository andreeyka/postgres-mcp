# Дизайн: вторая волна закалки basic

Дата: 2026-09-29. Статус: PR 1 реализован (`claude/basic-hardening-2`). Продолжение `2026-09-28-basic-confinement-design.md` §6 и `2026-09-28-basic-followups-design.md` §4.3.

## 1. Объём

| # | Задача | PR / ветка |
| --- | --- | --- |
| 1 | В basic закрыть представления `information_schema` с секретами и исходниками | 1 — `claude/basic-hardening-2` |
| 2 | В basic — только безопасные опции `EXPLAIN` (без `SETTINGS`) | 1 |
| 3 | Скрытые индексы hypopg (`hypopg_hide_index`) снимаются при возврате соединения в пул | 1 |
| 4 | Проверка по плану и оператор — в одной транзакции на одном соединении | 2 — `claude/plan-check-one-transaction` (от вершины PR 1) |

Не входит (остаётся в §6 прежних спек): функции чужих схем в выражениях плана (`Output`/`Filter`), неявное приведение к reg*-колонке, строковые типы таблиц без префикса, оракул полнотекстового поиска.

## 2. `information_schema` с секретами (PR 1)

В basic отношения `information_schema` с именами ниже отклоняются ошибкой `SystemRelationAccessError` (как R1), при любом написании схемы, которое проходит проверку схемы (`information_schema.x`, `"information_schema".x`); без схемы имя в `information_schema` не резолвится (её нет в `search_path`) — проверять только квалифицированные:

| Представление | Что раскрывает |
| --- | --- |
| `user_mapping_options` | значения опций user mapping, в том числе пароли своих mapping |
| `user_mappings` | user mapping (серверы, роли) |
| `foreign_server_options`, `foreign_data_wrapper_options` | опции серверов и обёрток (хосты, пути) |
| `foreign_table_options`, `column_options` | опции внешних таблиц и их колонок: у `file_fdw` — `filename`/`program`, `program` — командная строка шелла, может нести учётные данные |
| `routines` | `routine_definition` — исходники своих функций в любой схеме |
| `views` | `view_definition` — SQL своих представлений в любой схеме |
| `triggers` | `action_statement` — код триггеров |

Список — константа `BASIC_BLOCKED_INFORMATION_SCHEMA_VIEWS` в `postgres/security/policies.py`; проверка — в `schema_guard.validate_schema_access` в ветке `information_schema` (рядом с существующим `schemata`). Агент получает структуру объектов через `list_objects`/`get_object_details`.

## 3. Опции `EXPLAIN` в basic (PR 1)

В basic `ExplainStmt` допускает только опции из `BASIC_EXPLAIN_OPTIONS = {format, verbose, costs, summary, timing, buffers, generic_plan, memory}` (по `DefElem.defname`, без учёта регистра); остальные (`settings`, `wal`, `serialize`, будущие) → новая `ExplainOptionNotAllowedError(option)`: `EXPLAIN option {option} is not allowed in basic mode. Allowed options: …`. `analyze` по-прежнему отклоняется своей ошибкой (`ExplainAnalyzeNotSupportedError`) — проверка `analyze` идёт первой. Full не меняется.

`explain_query` сервер сам строит `EXPLAIN (FORMAT JSON[, GENERIC_PLAN][, COSTS TRUE])` — всё в списке.

## 4. Скрытые индексы hypopg (PR 1)

- `SqlExecutor` помечает соединение отдельной меткой, если SQL содержит `hypopg_hide_index` (без учёта регистра): `DbConnPool.mark_hypopg_hidden(connection)`.
- reset-callback: для помеченного — `SELECT hypopg_unhide_all_indexes()` (порядок: `hypopg_reset()` если помечено созданием, затем `hypopg_unhide_all_indexes()` если помечено скрытием, затем `DISCARD ALL`). Ошибка — WARNING и re-raise: пул выбрасывает соединение (в том числе если в установленной версии hypopg нет `hypopg_unhide_all_indexes` — появилась в hypopg 1.4.0 вместе с `hypopg_hide_index`).
- В basic `hypopg_hide_index`/`hypopg_unhide_index` уже закрыты (`INTROSPECTION_FUNCTIONS`); изменение касается full.

## 5. Проверка по плану в той же транзакции (PR 2)

- Сейчас `SafeSqlExecutor._checked_run` делает отдельный вызов делегата на каждый EXPLAIN проверки и ещё один на оператор: разные транзакции и, возможно, разные соединения пула. Между ними возможны `CREATE OR REPLACE VIEW`, смена разбиения по `now()`, другое соединение.
- Стало: `SqlExecutor` получает способ выполнить на одном соединении, в одной транзакции (`BEGIN …; SET LOCAL standard_conforming_strings = on`), сначала предварительные запросы, затем оператор. Предлагаемая форма (уточнить в плане по коду): необязательный именованный аргумент `precheck: Callable[[Runner], Awaitable[None]] | None` у `execute`/`execute_statement` (`Runner = Callable[[str], Awaitable[list[RowResult] | None]]` выполняет строку на текущем курсоре и возвращает строки). `SafeSqlExecutor` передаёт `precheck`, который гонит `PlanGuard` через `Runner`.
- Префикс `SET LOCAL statement_timeout …; SET LOCAL search_path = …;` ставится один раз в начале транзакции; EXPLAIN и оператор его наследуют.
- EXPLAIN не берёт блокировок, которые бы помешали; `AccessShareLock` на отношения, взятый планированием, держится до конца транзакции — определение представления между проверкой и выполнением не меняется.
- Ошибка проверки или EXPLAIN — откат транзакции, оператор не выполняется. Read-only транзакция basic для EXPLAIN и для оператора: для basic с записью транзакция пишущая — EXPLAIN DML в ней не исполняет DML (без ANALYZE).
- Клиентский таймаут по-прежнему общий на всё.

## 6. Тесты

- PR 1 юнит: корпус — каждое представление из §2 (`SELECT * FROM information_schema.routines` и т. п.) блокируется в basic и проходит в full; `information_schema.tables`/`columns` проходят в basic; `EXPLAIN (SETTINGS) SELECT 1` → `ExplainOptionNotAllowedError` в basic, проходит в full; `EXPLAIN (FORMAT JSON, COSTS false, VERBOSE) SELECT 1` проходит в basic; `EXPLAIN (ANALYZE) …` → прежняя ошибка; пул — помеченное скрытием соединение получает `hypopg_unhide_all_indexes()` до `DISCARD ALL`.
- PR 1 интеграция: basic `SELECT * FROM information_schema.user_mapping_options` → `SystemRelationAccessError`; full + hypopg: `hypopg_hide_index(<oid реального индекса>)`, возврат соединения, на том же соединении (пул 1) `hypopg_hidden_indexes` пуст.
- PR 2 юнит: `SqlExecutor` с `precheck` — все запросы на одном курсоре, порядок `BEGIN…`, precheck-запросы, оператор, `COMMIT`/`ROLLBACK`; ошибка precheck → `ROLLBACK`, оператор не выполнен; `SafeSqlExecutor` с `plan_check` делает один вызов делегата на оператор.
- PR 2 интеграция: basic + `plan_check` — представление поверх `secret`, заменённое конкурентно, не проходит (достаточно проверить, что проверка и выполнение идут на одном соединении: `pg_backend_pid()` в EXPLAIN-контексте недоступен basic — вместо этого юнит-уровень + существующие интеграционные тесты `plan_check` проходят).

## 7. Изменения поведения (для заметок к PR)

- PR 1, basic: `information_schema.user_mapping_options`, `user_mappings`, `foreign_server_options`, `foreign_data_wrapper_options`, `foreign_table_options`, `column_options`, `routines`, `views`, `triggers` недоступны (`SystemRelationAccessError`); `EXPLAIN` — только опции `FORMAT`, `VERBOSE`, `COSTS`, `SUMMARY`, `TIMING`, `BUFFERS`, `GENERIC_PLAN`, `MEMORY` (`ExplainOptionNotAllowedError`).
- PR 1, full: скрытые `hypopg_hide_index` индексы снимаются при возврате соединения в пул.
- PR 2: с `plan_check` проверка и выполнение — одна транзакция на одном соединении; на один запрос к пулу меньше.
