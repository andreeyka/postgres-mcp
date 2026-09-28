# Дизайн: итог оператора в `execute_sql` и существование объекта в `get_object_details`

Дата: 2026-09-28. Статус: реализовано (ветка `claude/tool-results`); ниже в §4 и §7 отмечено, чем реализация отличается от первоначального текста.

## 1. Контекст

Две правки поведения тулов, отложенные планом `2026-09-27-06-cleanup.md` («Вопросы», п.1). Работа идёт в ветке `claude/tool-results` от `main` `769ad48`. Ломающие изменения разрешены, легаси не поддерживается.

- `execute_sql` после `UPDATE` без `RETURNING` пишет `Statement executed successfully; no rows were returned.` и `0 rows.`, в `json` — `{"rows": [], "row_count": 0}`. Агент читает это как «ничего не изменилось», хотя `UPDATE` затронул 500 строк.
- `get_object_details` называет пустую таблицу (`CREATE TABLE t()`) «не найдено»: тул (`tools/definitions.py:113-116`) выводит отсутствие объекта из пустых разделов.

## 2. Решения

| Вопрос | Решение |
| --- | --- |
| Откуда итог оператора | Тег команды Postgres из `cursor.statusmessage` (`INSERT 0 5`, `UPDATE 3`, `CREATE TABLE`), снятый до `COMMIT`/`ROLLBACK` |
| Число строк | `cursor.rowcount` (libpq `PQcmdTuples`), `-1` → `None` |
| Как тег доходит до тула | Новый метод драйвера `execute_statement` → `StatementResult`; только `querying.execute_sql` |
| Кто решает, что объекта нет | Каталог: `CatalogService.get_object_details` бросает `ObjectNotFoundError` |
| Существование таблицы/представления | Строка в `information_schema.tables` с `table_type` `BASE TABLE`/`VIEW` |

## 3. `execute_sql`

```python
@dataclass(frozen=True, slots=True)
class StatementResult:  # postgres/models.py
    rows: list[RowResult] | None   # None — у оператора нет результирующего набора
    status: str | None             # "UPDATE 3", "CREATE TABLE"
    affected_rows: int | None      # None — в теге нет числа (DDL, DO)
```

- `SqlExecutor.execute_statement(query, params=None, *, readonly=True) -> StatementResult`. Прежний `execute` становится обёрткой: `(await self.execute_statement(...)).rows`. Путь соединения, транзакция и инвалидация пула — одни и те же.
- `SafeSqlExecutor.execute_statement` — тот же путь, что `execute`: оба идут через общий `_guarded(query, params, run)`, где `run` — `delegate.execute` или `delegate.execute_statement`. Тег, валидация, префикс `SET LOCAL statement_timeout/search_path`, клиентская страховка и разбор `QueryCanceled` не копируются.
- `SqlDriverPort` получает `execute_statement`; `QueryExecutorPort` не меняется, потому что расчёты `health` зависят только от `execute`.
- Префикс `SET LOCAL ...; SET LOCAL ...; <sql>` уходит одной строкой по простому протоколу, и psycopg получает несколько результатов. После `while cursor.nextset()` текущим становится последний результат, то есть оператор пользователя (`psycopg/_cursor_base.py`, `_select_current_result`). `COMMIT` на том же курсоре заменяет тег на `COMMIT`, поэтому тег читается до него. На Postgres 16.4 проверено: `P+UPDATE` → `UPDATE 5`, `P+MERGE` → `MERGE 1`, `P+CREATE TABLE AS` → `SELECT 6`, `P+DO` → `DO`/`-1`. Хвост `;`, `;;` или `-- comment` лишнего результата не даёт.
- Почему `rowcount`, а не разбор строки: `PQcmdTuples` в libpq уже знает, у каких тегов есть число (`INSERT/UPDATE/DELETE/MERGE/SELECT/CREATE TABLE AS/COPY/FETCH/MOVE`), и возвращает пусто для остальных. Свой список тегов не нужен, а `MERGE` поддержан (psycopg-binary 3.3.4 несёт libpq 18).
- Вывод (`tools/rendering.py::statement_result`):
  - `table`: `UPDATE 3: 3 rows affected.`, `DELETE 0: 0 rows affected.`, `CREATE TABLE: done.`;
  - `json`: `{"rows": [], "row_count": 0, "status": "UPDATE 3", "affected_rows": 3}`, у DDL `"affected_rows": null`.
- `SELECT` и `RETURNING` не меняются: `rows_result`, `N rows.`. Параметр `title` у `rows_result` и константа `SUCCESS_NO_ROWS` удаляются.
- Описания тулов не меняются, размер `tools/list` остаётся 9710 символов.

## 4. `get_object_details`

- Решение о существовании принимает домен. `TablesService`, `SequencesService` и `ExtensionsService` возвращают `None`, если объекта нет, а `CatalogService.get_object_details` бросает `ObjectNotFoundError(schema, name, type)`. Тул только рисует ответ; `_TOOL_HEADER_KEYS` и вывод по пустым разделам удаляются.
- Такое разделение проще, чем отдельный раздел `basic` из `pg_class`: ошибка создаётся в одном месте, а сообщение без схемы для расширения даёт уже существующий `ObjectNotFoundError`. Последовательности и расширения ищутся тем же запросом, что сейчас; меняется только то, кто делает вывод.
- Таблицы и представления проверяет `QUERY_TABLE_EXISTS`, это `information_schema.tables` с `table_type` (`BASE TABLE` = relkind `r`/`p`, `VIEW` = `v`). Запрос идёт четвёртым параллельно со столбцами, ограничениями и индексами, поэтому лишней задержки нет. **Как сделано:** вместо `asyncio.gather` используется `_run_concurrently` (`domains/catalog/tables.py`): при ошибке одного запроса остальные отменяются и дожидаются, наружу выходит исходное исключение, отмена запроса сохраняется. `asyncio.TaskGroup` не подошёл: на Python 3.12 после ошибки дочерней задачи он оставляет у родителя `cancelling() == 1`. Для таблицы и представления ошибка «не найдено» подсказывает повторить с другим `object_type`.
- Пустая таблица отдаёт заголовок и пустые разделы: `schema: public\nname: t\ntype: table` в `table`, пустые списки в `json`.
- Таблица, запрошенная как `view` (и наоборот), теперь даёт «не найдено». Сейчас `get_object_details(t, object_type="view")` отдаёт столбцы таблицы с `type: view`.

### 4.1 BASIC и `table_prefix`: поведение прежнее

На `769ad48` (Postgres 16.4) в BASIC с `table_prefix="app_"` `get_object_details(table)` падает для **любой** таблицы, с префиксом и без, существующей и нет. Текст ошибки: `Access to table 'pg_indexes' is not allowed ...`, потому что валидатор видит неквалифицированный `pg_indexes`. Для расширения так же падает `pg_extension`. Без префикса BASIC работает. Новый запрос проходит валидатор во всех режимах, а «не найдено» бросается только после успешного `gather`. Поэтому ошибка префикса остаётся той же, и это закреплено юнит-тестом на настоящем `SafeSqlExecutor` с валидатором.

Исправлено отдельной задачей: `docs/superpowers/specs/2026-09-28-basic-prefix-catalog-design.md`.

## 5. Отступление от согласованного дизайна

| Согласовано | Сделано | Почему |
| --- | --- | --- |
| Поиск в `pg_class`/`pg_namespace` по `relkind` | `information_schema.tables` по `table_type` | Валидатор BASIC отвергает `pg_class`: без схемы — `TablePrefixAccessError` при `table_prefix`, с `pg_catalog.` — `SchemaNotAllowedError` в любом BASIC. `information_schema.tables` проходит всегда. Это тот же источник, что у `list_objects`, и тот же фильтр прав, что у раздела столбцов (`information_schema.columns`). `table_type` однозначно отражает `relkind` |

Следствие: таблица, на которую у роли нет никаких прав, теперь «не найдена», как и в `list_objects`. Сейчас для неё отдаётся заголовок с индексами из `pg_indexes`.

## 6. Тесты

- Юнит: `execute_statement` на фейковом курсоре (тег последнего результата после префикса `SET LOCAL`, тег снят до `COMMIT`, DDL → `None`); `SafeSqlExecutor.execute_statement` (валидация, префикс, `read_only`, таймауты); `statement_result`; тул `execute_sql`; каталог (пустая таблица, нет таблицы/представления/последовательности/расширения, `table_type`, BASIC+префикс).
- Интеграция (CI, Postgres 15/16): `test_write_mode.py` — точные строки `CREATE TABLE: done.`, `INSERT 0 2: 2 rows affected.`, JSON `UPDATE 2` и `DELETE 2` через `SafeSqlExecutor`; `test_tools_integration.py` — пустая таблица, таблица как `view`, отсутствующие объекты трёх типов.

## 7. Вне объёма

- (x) Сломанный `get_object_details` в BASIC с `table_prefix` (§4.1) — отдельная задача.
- (/) Исправлено в этой же ветке (см. §4): раньше `asyncio.gather` в `TablesService.get_details` не отменял соседние запросы при ошибке. На `769ad48` интеграционный тест с BASIC+префиксом зависает на teardown фикстуры (30 с, pytest-timeout). Поэтому закрепление §4.1 сделано юнит-тестом.
- (x) Материализованные представления и внешние таблицы в `get_object_details` (их нет и в `list_objects`).
