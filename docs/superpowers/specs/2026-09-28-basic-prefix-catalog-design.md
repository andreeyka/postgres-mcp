# Дизайн: каталог в режиме basic с `table_prefix`

Дата: 2026-09-28. Статус: согласовано, к реализации. Ветка `claude/basic-prefix-catalog` от `main` `66056b1`.

## 1. Проблема

В `access_mode=basic` с непустым `table_prefix` (например `app_`) `get_object_details` не работает ни для одного объекта:

- таблица/представление: `Access to table 'pg_indexes' is not allowed. Only tables with names starting with 'app_' are permitted.`;
- расширение: то же про `pg_extension`.

Причина: служебные запросы каталога (`postgres/catalog.py`) идут через `SafeSqlExecutor` агента и проходят тот же `QueryValidator`. `schema_guard.validate_schema_access` отклоняет любое неквалифицированное имя без префикса, в том числе системные представления `pg_indexes` и `pg_extension`. Квалифицированное `pg_catalog.*` в basic запрещено всегда.

Тот же дефект у `list_objects(object_type="extension")`: `QUERY_LIST_EXTENSIONS` читает `pg_extension`.

Попутная несогласованность: `get_object_details(object_type="sequence")` отдаёт детали последовательности без префикса (запрос идёт в `information_schema.sequences`, валидатор его пропускает), хотя `list_objects` такую последовательность скрывает, а `execute_sql` по ней отказывает.

Баг закреплён тестом `test_basic_table_prefix_error_is_unchanged` (`tests/unit/domains/test_catalog_service.py`); он заменяется тестами правильного поведения.

## 2. Рассмотренные варианты

| Вариант | Что видит агент | Граница доверия | Вердикт |
| --- | --- | --- | --- |
| 1. Отдельный исполнитель каталога; схему и префикс проверяет домен до запроса | Детали только объектов, разрешённых схемой и префиксом | Агент управляет лишь `schema_name`, `object_type` (перечисление) и `object_name` (строковый параметр). SQL агента в этот исполнитель не попадает | Выбран |
| 2. Только `information_schema` | Нет обычных (не PK/UNIQUE) индексов, нет расширений | Не меняется | Неполный: в `information_schema` нет индексов (понятие вне стандарта SQL) и нет аналога `pg_extension` |
| 3. Контекст «серверный запрос» в `QueryValidator` со списком системных представлений | Как в варианте 1 | Обход живёт на том же исполнителе и валидаторе, через которые идёт SQL агента: один неверный вызов — лазейка; валидатор становится зависимым от контекста | Отклонён |

## 3. Решение

### 3.1. Исполнитель каталога `CatalogSqlExecutor`

Новый модуль `postgres/security/catalog_driver.py`. Реализует только `QueryExecutorPort.execute` — ни `execute_statement`, ни `render` у него нет, поэтому как `SqlDriverPort` его не передать.

Проверки в `execute(query, params, *, readonly=True)` — до обращения к делегату, в таком порядке:

1. **Шаблон из списка.** `query` должен входить в `CATALOG_QUERIES` (`frozenset` всех констант `postgres/catalog.py`). Сравнение — по тексту шаблона до рендера. Иначе `ValueError` (ошибка программиста, не агента).
2. **Только строковые параметры.** Каждый параметр — ровно `str` (`type(p) is str`). `Composable` (`SQL(...)`, `Identifier`), `bytes`, числа и `None` отклоняются `TypeError`. Причина: `SafeSqlExecutor.render()` вставляет `Composable` как SQL-фрагмент — `SQL("'x' OR TRUE")` в параметре меняет условие запроса. Все параметры текущих запросов каталога — строки (схема, имя, `table_type`).
3. **Делегирование** во внутренний `SafeSqlExecutor`:
   - валидатор `QueryValidator(allowed_schema=None, table_prefix=None, read_only=True, allow_explain_analyze=False)` — AST-проверка остаётся (только чтение, разрешённые функции), снимаются лишь схема и префикс;
   - `SafeSqlConfig(read_only=True, allowed_schema=None, timeout=safe_sql_timeout, query_tag=...)` — всегда read-only транзакция и `statement_timeout`; `search_path` не выставляется.

Аргумент `readonly` игнорируется: исполнитель всегда read-only.

### 3.2. Квалификация системных источников

В `postgres/catalog.py`: `FROM pg_catalog.pg_indexes`, `FROM pg_catalog.pg_extension`. Без `allowed_schema` исполнитель не выставляет `search_path`, и неквалифицированное имя разрешалось бы по настройкам подключения/роли (если `pg_catalog` явно стоит в `search_path` не первым, объект из пользовательской схемы может его затенить). Явное имя закрепляет источник. `information_schema.*` уже квалифицированы.

Комментарий у `QUERY_TABLE_EXISTS` про `pg_class` и валидатор BASIC устаревает — переписать.

### 3.3. Доступ из доменов

- `DbAccessPort` и `DbAccess` получают `catalog_driver: QueryExecutorPort`.
- `DbAccessService` создаёт один `CatalogSqlExecutor` на сервис (конфигурация не зависит от прав запроса) и отдаёт его в каждом `view()`.
- Все запросы каталога (`CatalogService.list_schemas`, `TablesService`, `SequencesService`, `ExtensionsService`) идут через `db.catalog_driver`. `sql_driver` (путь `execute_sql`, `explain_query`) не меняется.

### 3.4. Проверки в домене (до любого запроса)

1. `_resolve_schema(schema_name)` в `list_objects` и `get_object_details` — как сейчас, для всех типов, включая расширения: в basic `schema_name="other"` → `SchemaAccessError`. Контракт сохраняется.
2. Только в `get_object_details`: префикс в basic с `table_prefix` для `table`, `view`, `sequence`: имя не начинается с префикса (без учёта регистра) → `TablePrefixAccessError(object_name, table_prefix)` — тот же класс и текст, что у `execute_sql`. Проверка идёт до запросов, поэтому ответ одинаков для существующего и несуществующего объекта.
3. Сопоставление имени с префиксом — одна функция в `domains/catalog/`, её используют и фильтр `list_objects` (сейчас продублирован в `tables.py` и `sequences.py`), и проверка деталей. Правило то же, что в `schema_guard`: `name.lower().startswith(prefix.lower())`.

`list_schemas` в basic по-прежнему отвечает без обращения к БД.

### 3.5. Поведение в basic с `table_prefix="app_"`

| Объект | С префиксом (`app_users`) | Без префикса (`users`) |
| --- | --- | --- |
| table / view | колонки, ограничения, индексы (включая неуникальные); нет объекта → `ObjectNotFoundError` | `TablePrefixAccessError('users', 'app_')` |
| sequence | детали; нет → `ObjectNotFoundError` | `TablePrefixAccessError` (как фильтр `list_objects` и `execute_sql`) |
| extension | префикс не применяется: имя расширения не принадлежит схеме (его объекты имеют схему размещения, но само расширение — нет). Работают `list_objects` и детали | — |

### 3.6. Что не ослабляется

- `execute_sql` в basic по-прежнему отклоняет `pg_indexes`, `pg_catalog.*`, чужие схемы и таблицы без префикса: `sql_driver` и его валидатор не меняются.
- `object_name` доходит до БД только как `Literal`-строка: `app_x' OR 1=1 --` — это имя, которого нет → `ObjectNotFoundError`.
- Произвольный SQL через `catalog_driver` не выполнить: только константы из `CATALOG_QUERIES` и только строковые параметры.

## 4. Изменения поведения (для заметок к PR)

- basic + `table_prefix`: `get_object_details` работает для объектов с префиксом; для таблиц/представлений/последовательностей без префикса — ошибка префикса по имени объекта (раньше — ошибка про `pg_indexes`/`pg_extension` для всех, а для последовательностей без префикса — детали).
- basic + `table_prefix`: `list_objects(object_type="extension")` работает (раньше — ошибка про `pg_extension`).
- basic + `write_mode=true`: запросы каталога идут в read-only транзакции (раньше `SafeSqlExecutor` игнорировал `readonly=True` и открывал пишущую).
- full + `write_mode=true`: запросы каталога проходят AST-валидацию read-only и получают `statement_timeout` (раньше шли через `SqlExecutor` без проверок и таймаута; транзакция и тогда была read-only).

## 5. Тесты

Юнит (фальшивый делегат, настоящие `SafeSqlExecutor` и `QueryValidator`):

- `CatalogSqlExecutor`: шаблон не из `CATALOG_QUERIES` → `ValueError`, делегат не вызван; параметр `SQL(...)`/`Identifier`/`bytes`/`int`/`None` → `TypeError`, делегат не вызван; запрос с `pg_catalog.pg_indexes` доходит до делегата в read-only с `statement_timeout`; `readonly=False` не делает транзакцию пишущей.
- Замена `test_basic_table_prefix_error_is_unchanged`, basic + `app_`, `write_mode` ∈ {false, true}:
  - table и view с префиксом → запросы (включая индексы) дошли до делегата, результат собран;
  - table, view, sequence без префикса → `TablePrefixAccessError` с именем объекта, делегат не вызван;
  - с префиксом, но нет в каталоге → `ObjectNotFoundError`;
  - префикс без учёта регистра (`APP_Users`);
  - `object_name="app_x' OR 1=1 --"` → до делегата дошёл экранированный литерал, результат `ObjectNotFoundError`;
  - extension без префикса → детали; `list_objects(extension)` работает;
  - `schema_name="other"` для каждого типа, включая extension → `SchemaAccessError`, делегат не вызван.
- `list_schemas` в basic: делегат не вызван.
- `DbAccessService.view`: в basic `sql_driver` по-прежнему с префиксом и `allowed_schema="public"`; `catalog_driver` — `CatalogSqlExecutor`, один на сервис.
- Регрессия `execute_sql`-пути в basic + `app_`: `pg_indexes` → `TablePrefixAccessError`, `pg_catalog.pg_class` → `SchemaNotAllowedError`, `other_users` → `TablePrefixAccessError`.

Интеграция (`tests/integration/test_table_prefix.py`, фикстура `db_user_prefix`; к `setup_test_tables` добавить неуникальный индекс на `app_orders(user_id)`, представление `app_active_users`):

- `app_users`: колонки, PK, уникальный индекс по `email`; `app_orders`: PK и неуникальный индекс;
- `app_active_users` (view) → колонки;
- `other_users` → `TablePrefixAccessError`; `app_ghost` → `ObjectNotFoundError`;
- `app_users_id_seq` → детали; `other_users_id_seq` → `TablePrefixAccessError`;
- `plpgsql` (установлен всегда) → детали; `list_objects(extension)` содержит `plpgsql`;
- `execute_sql` через `sql_driver`: `pg_indexes` и `pg_catalog.pg_indexes` по-прежнему отклоняются.

Локально интеграция пропускается (нет Docker) — проверяется статически по коду; в CI идёт на Postgres 15/16.

## 6. Вне объёма (отдельные задачи)

- (!) В basic **без** префикса валидатор пропускает неквалифицированные системные отношения: `SELECT * FROM pg_class`, `pg_roles`, `pg_stat_activity`. Запрещено только явное `pg_catalog.`.
- (!) `explain_query` с гипотетическими индексами в basic + `table_prefix`: проверка hypopg читает `pg_extension` через `sql_driver`, отказ валидатора перехватывается (`postgres/extensions.py:155`) и наружу выходит `HypopgNotInstalledError`.
- (i) Метаданные объектов без префикса и из других схем видны агенту через `information_schema.*` в `execute_sql`. Отказ `get_object_details` по префиксу — согласованность с `list_objects`, а не сокрытие.
