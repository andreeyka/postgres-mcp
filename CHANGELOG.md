# Журнал изменений

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версии — [SemVer](https://semver.org/lang/ru/). До 1.0 минорная версия может ломать совместимость.

## [0.2.0] — 2026-09-29

Главное: `access_mode=basic` теперь действительно ограничивает агента схемой `public`, серверные запросы к каталогу больше не проходят через валидатор агента, появился опциональный строгий режим `plan_check`, а соединения возвращаются в пул без состояния сессии.

### Ломающие изменения

**basic: SQL агента ограничен сильнее** (#15, #20). Запросы, которые раньше проходили, теперь отклоняются:

- системные отношения `pg_*`, `_pg_*`, `hypopg*` в любой схеме, в том числе без схемы (`pg_stats`, `pg_stat_activity`, `public.pg_stat_statements`) — `SystemRelationAccessError`; `pg_catalog.<x>` теперь даёт эту ошибку вместо `SchemaNotAllowedError`;
- функции интроспекции сервера: `current_setting`, `pg_get_*def`, размеры, `has_*_privilege`, `to_reg*`, `format_type`, `pg_typeof`, `currval`/`lastval` и др.;
- `SHOW` вне короткого списка (`SHOW ALL` в том числе) — `ShowParameterNotAllowedError`;
- типы `reg*` и `aclitem` в любой позиции, типы/collation/операторы/методы `TABLESAMPLE` из схем кроме `public` и `pg_catalog`, строковые типы системных отношений — `TypeNotAllowedError`;
- представления `information_schema` с секретами и исходниками: `user_mapping_options`, `user_mappings`, `*_options`, `routines`, `views`, `triggers`;
- опции `EXPLAIN` вне `FORMAT`, `VERBOSE`, `COSTS`, `SUMMARY`, `TIMING`, `BUFFERS`, `GENERIC_PLAN`, `MEMORY` — `ExplainOptionNotAllowedError`;
- гипотетические индексы — только простые столбцы таблиц `public` с префиксом (без выражений, `WHERE`, opclass, `TABLESPACE`); hypopg должен стоять в `public`;
- имена схем сравниваются точно: `"PUBLIC"` — другая схема.

Что делать: структуру объектов брать через `list_objects`/`get_object_details`; для диагностики сервера — `access_mode=full`.

**Все режимы.**

- `ts_stat` и `ts_rewrite` запрещены: они выполняют SQL из строки, которую валидатор не видит (#14).
- Состояние сессии больше не переживает вызов: `SET`, временные таблицы, `LISTEN`, `PREPARE`/`EXECUTE`, курсоры `WITH HOLD`, advisory-блокировки сбрасываются `DISCARD ALL` при возврате соединения в пул. Автоподготовка psycopg выключена (#19).
- `execute_sql` для операторов без результата (DML без `RETURNING`, DDL) вместо `0 rows.` возвращает статус команды: `UPDATE 3: 3 rows affected.`, в JSON — поля `status` и `affected_rows` (`null` для DDL). Клиентам, разбирающим ответ, — учесть новые поля (#12).
- `get_object_details`: существование объекта определяется по каталогу. Представление с `object_type="table"` (по умолчанию) теперь «не найдено» с подсказкой — передавайте `object_type="view"`. Таблицы без прав и foreign tables тоже «не найдены», как в `list_objects` (#12).

**Конфигурация и подключение** (#16, #17).

- URI с параметрами, которых не знает libpq (`prepared_statement_cache_size`, `pgbouncer` из URI SQLAlchemy/asyncpg/Prisma), — ошибка при загрузке конфигурации. Раньше они молча отбрасывались. Уберите их из URI.
- Поле `extra_kwargs` удалено: `config.json` с ним не проходит валидацию. Параметры libpq — в новое поле `connect_options`.
- basic по HTTP открывает пул на старте (там запускается проверка роли, см. «Добавлено»). Старт при недоступной БД не блокируется.
- Возврат соединения в пул идёт через воркер пула; при последовательной нагрузке может держаться одно лишнее простаивающее соединение.

**Библиотечный API** (#12, #14, #17, #21).

- `SafeSqlExecutor` и `CatalogSqlExecutor` требуют делегата `PrecheckSqlDriverPort`; `_plan_guard` и `_explain_for_plan_check` удалены.
- `SqlDriverPort` получил `execute_statement` (возвращает `StatementResult`) — собственные реализации порта должны его добавить.
- `ExtensionInspectorAdapter(executor, connection_id)` больше не принимает `template`; `EXT_INSTALLED_QUERY` и `EXT_AVAILABLE_QUERY` удалены — используйте `QUERY_EXTENSION_*` из `postgres/catalog.py`.
- `catalog_driver` — обязательный keyword-only аргумент `ExplainPlanBuilder`, `CostEvaluator`, `IndexTuningBase`/`DatabaseTuningAdvisor`, `TopQueriesCalc`.
- `DatabaseConfigPort` получил `max_inactive_connection_lifetime`.

**При включении `plan_check`** (настройка новая, по умолчанию выключена; README, «Проверка по плану») (#18, #25, #26).

- Функции `public` не на SQL (PL/pgSQL, PL/Python, C, `internal`), до которых доходит запрос, по умолчанию отклоняются. Свой триггер в `public` отклоняет `INSERT`/`UPDATE`/`DELETE` его таблицы (`SELECT` — нет). Что делать: переписать функцию на `LANGUAGE sql`, для `updated_at` взять расширение `moddatetime` либо включить `plan_check_allow_non_sql_functions`.
- Базовый тип в `public` с C/internal вводом-выводом не из расширения отклоняет запросы к таблицам с такой колонкой.
- `CREATE EXTENSION` отклоняется, пока включён событийный триггер не из расширения с функцией не на SQL или вне `public` (так на Supabase).
- Неявное binary-coercible приведение от встроенного типа к типу с отклонённым семейством операторов отклоняет все запросы в базе; ошибка называет приведение — удалите его или пересоздайте `AS ASSIGNMENT`.
- Отклоняются запросы к `information_schema`, секциям в других схемах, `ROWS FROM` из нескольких функций `public`; узлы, которые нельзя проверить (fdw pushdown, `Custom Scan`), — `PlanUnverifiableError`. Полный список ложных отказов — в README.

### Добавлено

- `plan_check` (env `MCP_DATABASE_PLAN_CHECK`, по умолчанию `false`) — строгий режим basic: каждый оператор агента проверяется по плану `EXPLAIN (VERBOSE, FORMAT JSON)` до выполнения. Закрывает представления, правила и функции в `public`, читающие другие схемы (#18).
- `plan_check_allow_non_sql_functions` (env `MCP_DATABASE_PLAN_CHECK_ALLOW_NON_SQL_FUNCTIONS`) — пропускать функции `public` не на SQL без проверки, как до #26.
- `connect_options` — параметры libpq (`target_session_attrs`, `options`, `sslrootcert`, …) из URI и конфига доходят до соединения; ключи других полей и секреты отклоняются (#17).
- Проверка роли БД на старте: одно WARNING, если роль может больше, чем нужно basic (суперпользователь, `BYPASSRLS`, `CREATEROLE`, `pg_read_all_data` и т. п., чужие схемы, таблицы без префикса). В README — раздел «Роль для basic» с готовым SQL. В библиотеке отключается `PostgresProvider(check_basic_role=False)`; в stdio не запускается (#16).
- `CatalogSqlExecutor` и `DbAccess.catalog_driver` — отдельный канал для серверных запросов к каталогу: только шаблоны `CATALOG_QUERIES`, всегда read-only и с `statement_timeout` (#13).
- `StatementResult` и `execute_statement` у `SqlExecutor`/`SafeSqlExecutor`/`SqlDriverPort` (#12).
- Новые ошибки: `SystemRelationAccessError`, `ShowParameterNotAllowedError`, `TypeNotAllowedError`, `ExplainOptionNotAllowedError`, `PlanAccessError`, `PlanUnverifiableError`.

### Изменено

- basic + `table_prefix`: `get_object_details` для объекта без префикса отвечает той же `TablePrefixAccessError`, что и `execute_sql`, до запроса и независимо от существования объекта; раньше детали последовательностей отдавались и без префикса (#13).
- Запросы к каталогу в basic + `write_mode=true` выполняются в read-only транзакции; в full + `write_mode=true` — с проверкой AST и `statement_timeout` (#13).
- `max_inactive_connection_lifetime` задаёт `max_idle` пула (#17).
- Каждая транзакция начинается с `SET LOCAL standard_conforming_strings = on` в одной команде с `BEGIN` (#19).
- С `plan_check` проверка плана и оператор идут в одной транзакции на одном соединении: один checkout пула вместо N+1, префикс `SET LOCAL` отправляется отдельно и в тексте оператора больше не виден (#21).
- `explain_query` возвращает настоящую ошибку валидатора или `plan_check` вместо общей (#18).
- Цена `plan_check`: около 5 запросов на обычный оператор, около 6 на `GENERIC_PLAN` (#23).

### Безопасность

- **Изоляция basic.** Агент не видит системные каталоги, интроспекцию сервера, `reg*`-разрешение имён, объекты других схем через типы, операторы и collation, секреты в `information_schema` (#15, #20).
- **Серверный канал каталога.** Запросы сервера (детали объектов, проверки расширений и версии) идут не через валидатор агента, а через `CatalogSqlExecutor` с полностью квалифицированными `pg_catalog`-отношениями (#13, #14).
- **Гигиена сессии.** `DISCARD ALL`, `hypopg_reset()` и `hypopg_unhide_all_indexes()` при возврате соединения; валидатор разбирает строки так же, как сервер, даже если у роли выключен `standard_conforming_strings` (#17, #19, #20).
- **`plan_check`, волна 1** — отношения, табличные функции и вложенные вызовы в дереве плана, непроверяемые узлы — отказ (#18); проверка идёт в той же транзакции, что и оператор, без окна между проверкой и выполнением (#21).
- **Волна 2** — выражения плана (`Output`, `Filter`, ключи и т. д.); определения представлений и правил по `pg_depend` и тексту правила; типы SQL агента до первого `EXPLAIN` (#22).
- **Волна 3** — `PREPARE` перед `EXPLAIN`, чтобы определения проверялись до свёртки `IMMUTABLE`-вызовов; операторы и агрегаты `public` по реализующим функциям, включая подставленные разбором (`BETWEEN`, `USING`, `IN`) (#23); путь записи DML (триггеры, `CHECK`, умолчания, домены, RLS), определения таблиц, которые планировщик сворачивает и для `SELECT`, тела SQL-функций `public` рекурсивно до 5 уровней (#24).
- **Волна 4** — машинерия типов: функции ввода-вывода, приведения, классы операторов, `CHECK` доменов, функции диапазонов (#25).
- **Волна 5** — тела функций `public` не на SQL, опорные функции `SUPPORT`, коммутаторы и отрицания операторов, событийные триггеры при `CREATE EXTENSION` (#26).

Оставшиеся пробелы `plan_check` перечислены в README, «Проверка по плану». Основная граница доступа — права роли в БД.

### Исправлено

- basic + `table_prefix`: `get_object_details` падал для всех объектов на `pg_indexes`, а `list_objects(object_type="extension")` — на `pg_extension` (#13).
- basic + `table_prefix`: `explain_query` с `hypothetical_indexes` падал ложной `HypopgNotInstalledError` (#14).
- Гипотетические индексы на `schema.table` не работали ни в одном режиме (#15).
- Колонки ограничений в `get_object_details` подмешивались из одноимённых ограничений других таблиц схемы (#13).
- Таймаут, отмена или обрыв соединения при проверке расширений выдавались за ошибку каталога расширений или за PostgreSQL версии 0 (#14).
- Зависание закрытия пула после ошибки одного из параллельных запросов каталога (#12).
- Размеры гипотетических индексов в подборе индексов читались неверно (#17).
- Пул не закрывался, если его открытие отменили (#17).
- Скрытые `hypopg_hide_index` индексы переживали возврат соединения и искажали чужие планы (#20).
- Отказ `plan_check` больше не считается ошибкой соединения и не инвалидирует пул (#21).

## [0.1.0]

Первый релиз форка: переход на FastMCP 4; библиотечный API — `PostgresProvider`, нативный `Provider` FastMCP для подключения тулов к своему серверу; аутентификация, выбираемая конфигом без кода, — `none`, `static`, `jwt`, `oidc`; сужение прав по claim токена с настраиваемым источником; консольный скрипт переименован в `postgres-fastmcp`.

[0.2.0]: https://github.com/andreeyka/postgres-mcp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/andreeyka/postgres-mcp/releases/tag/v0.1.0
