# Дизайн: граница basic — канал агента и канал сервера

Дата: 2026-09-28. Статус: реализовано (ветки 1 и 2). Продолжение `2026-09-28-basic-prefix-catalog-design.md` (§6, «Вне объёма»).

## 1. Проблема

У режима `basic` нет явной модели того, что видит агент. Проверки опираются на неверную модель Postgres и смешивают служебный SQL сервера с SQL агента. `basic` — режим по умолчанию.

| # | Находка | Режимы | Последствие |
| --- | --- | --- | --- |
| 1 | Валидатор считает неквалифицированное имя отношения объектом `public`. Postgres неявно ищет `pg_catalog` раньше `search_path`, `SET LOCAL search_path = public` этого не меняет | basic без префикса | Доступны `pg_class`, `pg_roles`, `pg_stat_activity`, `pg_settings`; `pg_stats.most_common_vals` отдаёт **значения колонок** таблиц других схем |
| 2 | `ExtensionInspectorAdapter` (проверка hypopg, версии) идёт через `sql_driver` агента; `except Exception` превращает отказ валидатора в «не установлено» | basic + `table_prefix` | `explain_query` с `hypothetical_indexes` ложно отвечает `HypopgNotInstalledError` с советом про `.control`-файлы |
| 3 | Список функций один для basic и full | basic | `current_setting` читает пользовательские GUC (секреты приложений вида `app.jwt_secret`); `pg_get_functiondef`/`pg_get_viewdef` отдают исходники объектов любой схемы без проверки прав; `inet_server_addr`, `pg_current_logfile`, `pg_control_*` и др. раскрывают сервер |
| 4 | `SHOW` разрешён для любого параметра | basic | `SHOW app.jwt_secret` — тот же канал, что `current_setting` |
| 5 | `ts_stat(text)` и `ts_rewrite(tsquery, text)` выполняют SQL из строки; валидатор строку не разбирает | **все** | basic: `SELECT * FROM ts_stat('SELECT to_tsvector(c) FROM other.secret')` читает чужую схему; full: внутри строки доступны функции вне списка разрешённых |

Выбранная модель (вариант 1 из обсуждения): в basic агенту доступны данные `public` (с учётом `table_prefix`) и функции, не раскрывающие сервер и чужие объекты. Системные отношения и интроспекция сервера закрыты. `information_schema` остаётся: он фильтрует строки по правам роли и не отдаёт данные таблиц.

## 2. Принцип: два канала — два правила

| Канал | Кто пишет SQL | Правило |
| --- | --- | --- |
| Агента: `execute_sql`, запрос в `explain_query` | агент | `QueryValidator`; в basic — явная политика §4 на реальной модели разрешения имён |
| Сервера: каталог, проверка расширений, версия | сервер | только `CatalogSqlExecutor` (шаблоны из `CATALOG_QUERIES`, параметры `str`, read-only); ошибки не маскируются |

Второе исключение: `SqlParamReplacer` (`postgres/params/replacer.py`, подстановка примерных значений вместо `$1` в `explain_query`) читает `pg_stats` и `information_schema.columns` по именам таблиц из запроса агента, поэтому остаётся на канале агента. Ошибку он уже гасит и берёт общие значения; в basic после R1 (§4.1) чтение `pg_stats` отклоняется, и замена всегда идёт общими значениями.

Исключение, остающееся на канале агента: `hypopg_reset()`/`hypopg_create_index(...)` в `explain_query` склеиваются с `EXPLAIN` запроса агента в одну строку, потому что состояние hypopg живёт в сессии соединения. Строка проходит валидатор, но строковый аргумент `hypopg_create_index('CREATE INDEX … ON <table> …')` в общем случае валидатор не разбирает, а `table`/`columns` приходят от агента: так агент узнаёт, существуют ли таблица и колонка в чужой схеме, и получает оценку размера. Хуже того, hypopg сам прогоняет `CREATE INDEX` через `transformIndexStmt`: входные функции литеральных приведений в выражениях и `WHERE` вычисляются, а имена типов, функций, классов операторов и табличных пространств резолвятся против каталога — индекс-выражение раскрывает существование чужих объектов даже когда сама таблица индекса разрешена. Закрывается в ветке 2 правилом R5 (§4.5): гипотетический индекс — только простые столбцы.

## 3. Канал сервера (ветка `claude/server-catalog-channel`)

### 3.1. Запросы

В `postgres/catalog.py` (удаляются из `postgres/extensions.py`):

```python
QUERY_EXTENSION_INSTALLED = """
SELECT extversion
FROM pg_catalog.pg_extension
WHERE extname = {}
"""

QUERY_EXTENSION_AVAILABLE = """
SELECT default_version
FROM pg_catalog.pg_available_extensions
WHERE name = {}
"""

QUERY_SERVER_VERSION = "SHOW server_version"
```

Все три входят в `CATALOG_QUERIES`. Тест «все отношения квалифицированы» (`test_catalog_queries_qualify_every_relation`) должен принять `SHOW` (в нём нет `RangeVar`): требование «хотя бы одно отношение» заменяется на «для запросов с отношениями — все квалифицированы».

### 3.2. `ExtensionInspectorAdapter`

- Конструктор: `ExtensionInspectorAdapter(executor: QueryExecutorPort, connection_id: str)`. Параметр `template` удаляется.
- `_run_param` удаляется: вызовы — `await self._executor.execute(QUERY_…, params=[extension_name], readonly=True)`. Предварительный рендер ломал бы проверку шаблона в `CatalogSqlExecutor`.
- `get_postgres_version` выполняет `QUERY_SERVER_VERSION`.
- `except Exception` → `except psycopg.Error` в обоих запросах `check_extension` и в `get_postgres_version`. `CATALOG_ERROR_MESSAGE` остаётся для настоящего сбоя БД (битый `.control` ломает `pg_available_extensions`). `UserFacingError`, `ValueError`, `TypeError` пробрасываются.

### 3.3. Места использования

Все получают `catalog_driver`:

| Место | Сейчас | Станет |
| --- | --- | --- |
| `domains/explain/service.py` `_explain_hypothetical` | `ExtensionInspectorAdapter(sql_driver, sql_driver, id)` | `ExtensionInspectorAdapter(self.db.catalog_driver, id)` |
| `domains/explain/explain_plan.py` `ExplainPlanBuilder` | строит инспектор из `sql_driver` | принимает `catalog_driver: QueryExecutorPort` в конструкторе |
| `domains/index_tuning/base.py` | то же | принимает `catalog_driver` |
| `domains/top_queries.py` `TopQueriesCalc` | то же | принимает `catalog_driver` |

Конструкторы расширяются обязательным именованным параметром `catalog_driver`; вызывающие места передают `db.catalog_driver`.

Вне объёма: отчёты `pg_stat_statements` в `top_queries` и расчёты `health` — full-тулы с полными правами и нестроковыми параметрами (`limit`); остаются на `sql_driver`.

### 3.4. R0: функции, выполняющие SQL из строки

Из `ALLOWED_FUNCTIONS` удаляются `ts_stat` и `ts_rewrite` (во всех режимах). Двухаргументную форму `ts_rewrite` по AST надёжно не отличить, а трёхаргументная (`ts_rewrite(query, target, substitute)`) без неё не нужна агенту настолько, чтобы держать отдельное правило.

Юнит-тест-запрет: ни один режим не разрешает `ts_stat`, `ts_rewrite`, `query_to_xml`, `query_to_xmlschema`, `query_to_xml_and_xmlschema`, `cursor_to_xml`, `cursor_to_xmlschema`, `table_to_xml`, `table_to_xmlschema`, `table_to_xml_and_xmlschema`, `schema_to_xml`, `schema_to_xmlschema`, `schema_to_xml_and_xmlschema`, `database_to_xml`, `database_to_xmlschema`, `database_to_xml_and_xmlschema`, `dblink`, `dblink_exec`, `dblink_open`, `dblink_fetch`, `dblink_send_query`.

### 3.5. Тесты канала сервера

- Юнит: инспектор вызывает `executor.execute` с константой из `CATALOG_QUERIES` и параметром-строкой (не отрендеренной строкой); `psycopg.Error` из `pg_available_extensions` → `catalog_error`; `UserFacingError` пробрасывается.
- Юнит на настоящей цепочке (`DbAccessService` + фальшивый делегат, как в `TestBasicTablePrefix`): basic + `app_` + `hypothetical_indexes` → проверка hypopg доходит до делегата через `pg_catalog.pg_extension`, `HypopgNotInstalledError` не бросается, когда делегат отвечает строкой расширения.
- Интеграция: basic + `app_` → `explain_query` с гипотетическим индексом на `app_users` возвращает план (hypopg установлен в CI).

## 4. Канал агента в basic (ветка `claude/basic-agent-policy`)

Политика включается тем же признаком, что ограничение схемы: `QueryValidator(allowed_schema=...)` задан ⇔ basic. Отдельного флага нет. Full не меняется (кроме R0).

### 4.1. R1: системные отношения

`RangeVar` с `relname`, начинающимся (без учёта регистра) с `pg_`, `_pg_` или `hypopg`, отклоняется при любом `schemaname` (нет, `pg_catalog`, `public`, `information_schema`, другая).

- Основание: имена системных отношений начинаются с `pg_` (документация PostgreSQL, «The System Catalog Schema»); создать отношение в `pg_catalog` нельзя без `allow_system_table_mods`. `_pg_*` — внутренние представления `information_schema` (`_pg_user_mappings` и др.).
- Попутно закрываются представления расширений в `public`: `public.pg_stat_statements` (тексты запросов всех пользователей), `pg_buffercache` и т. п.
- `hypopg*` — представления hypopg (`hypopg_list_indexes`, `hypopg_hidden_indexes`): показывают гипотетические и скрытые индексы всей сессии пулового соединения, то есть чужих вызовов.
- Цена: пользовательские `public.pg_x` и `public.hypopg_x` недоступны в basic (Postgres советует не давать имена `pg_*`).
- Порядок в `validate_schema_access`: проверка R1 идёт первой, до схемы и префикса.
- Ошибка — новая `SystemRelationAccessError(relation)`: `Access to system relation '{relation}' is not allowed in basic mode. Use list_objects and get_object_details to inspect tables in 'public'.` Существующие случаи `pg_catalog.<x>` теперь дают её же, а не `SchemaNotAllowedError`.

### 4.2. R2: функции

`BASIC_ALLOWED_FUNCTIONS = ALLOWED_FUNCTIONS - INTROSPECTION_FUNCTIONS`, оба множества — явные списки в `postgres/security/_allowed_functions.py`.

`INTROSPECTION_FUNCTIONS`:

- настройки и сервер: `current_setting`, `pg_conf_load_time`, `pg_current_logfile`, `pg_postmaster_start_time`, `pg_is_in_recovery`, `pg_jit_available`, `pg_settings_get_flags`, `pg_control_checkpoint`, `pg_control_system`, `pg_control_init`, `pg_control_recovery`, `pg_available_wal_summaries`, `pg_wal_summary_contents`, `pg_get_wal_summarizer_state`, `pg_tablespace_location`, `pg_tablespace_databases`, `pg_tablespace_size`, `pg_database_size`, `inet_client_addr`, `inet_client_port`, `inet_server_addr`, `inet_server_port`;
- сессии и транзакции: `pg_backend_pid`, `pg_blocking_pids`, `pg_safe_snapshot_blocking_pids`, `pg_listening_channels`, `pg_notification_queue_usage`, `pg_my_temp_schema`, `pg_is_other_temp_schema`, `pg_current_xact_id`, `pg_current_snapshot`, `pg_snapshot_xip`, `pg_xact_commit_timestamp`;
- объекты по имени/OID: `pg_get_expr`, `pg_get_functiondef`, `pg_get_function_arguments`, `pg_get_function_identity_arguments`, `pg_get_function_result`, `pg_get_catalog_foreign_keys`, `pg_get_constraintdef`, `pg_get_userbyid`, `pg_get_partkeydef`, `format_type`, `pg_get_serial_sequence`, `pg_get_viewdef`, `pg_get_ruledef`, `pg_get_triggerdef`, `pg_get_statisticsobjdef`, `pg_get_indexdef`, `pg_relation_size`, `pg_table_size`, `pg_indexes_size`, `pg_total_relation_size`, `pg_relation_filenode`, `pg_column_toast_chunk_id`, `has_any_column_privilege`, `has_column_privilege`, `has_database_privilege`, `has_foreign_data_wrapper_privilege`, `has_function_privilege`, `has_language_privilege`, `has_parameter_privilege`, `has_schema_privilege`, `has_sequence_privilege`, `has_server_privilege`, `has_table_privilege`, `has_tablespace_privilege`, `has_type_privilege`, `pg_has_role`, `row_security_active`, `to_regtype`, `to_regtypemod`, `to_regclass`, `to_regcollation`, `to_regnamespace`, `to_regoper`, `to_regoperator`, `to_regproc`, `to_regprocedure`, `to_regrole`, `regclass`, `pg_index_column_has_property`, `pg_index_has_property`, `pg_indexam_has_property`, `pg_collation_is_visible`, `pg_conversion_is_visible`, `pg_function_is_visible`, `pg_opclass_is_visible`, `pg_operator_is_visible`, `pg_opfamily_is_visible`, `pg_statistics_obj_is_visible`, `pg_table_is_visible`, `pg_ts_config_is_visible`, `pg_ts_dict_is_visible`, `pg_ts_parser_is_visible`, `pg_ts_template_is_visible`, `pg_type_is_visible`, `acldefault`, `aclexplode`, `makeaclitem`, `pg_options_to_table`, `currval`, `lastval` (аргумент `currval` приводится через `regclass` — оракул существования последовательности в чужой схеме; `lastval` закрыт симметрично: на пуловом соединении он отдаёт значение чужого `nextval`), `pg_input_is_valid`, `pg_input_error_info` (с типом `reg*` во втором аргументе разрешают имена объектов через функцию ввода типа — обход R4), `hypopg_hide_index`, `hypopg_unhide_index` (принимают произвольный существующий OID реального индекса — оракул существования по перебору — и меняют планирование на пуловом соединении), `hypopg_get_indexdef`, `hypopg_list_indexes`, `hypopg_relation_size` (показывают гипотетические индексы всей сессии пулового соединения; `explain_query` обходится `hypopg_reset` и `hypopg_create_index`, они в basic остаются), `pg_basetype`, `pg_typeof` (аргумент или результат типа `regtype`: сравнение, `IN` и `COALESCE` приводят строковый литерал через `regtypein` — `pg_typeof(1) = 'secret.accounts'`, `pg_basetype('secret.accounts')` — оракул существования типа).

Остаются в basic из `pg_*` только функции над значениями — явный список `BASIC_PG_FUNCTIONS`: `pg_column_size`, `pg_column_compression`, `pg_size_pretty`, `pg_size_bytes`, `pg_client_encoding`, `pg_encoding_to_char`, `pg_char_to_encoding`, `pg_get_keywords`, `pg_trigger_depth`. Идентификация (`current_user`, `session_user`, `current_database`, `current_schema`, `current_schemas`, `current_catalog`, `current_role`, `user`, `system_user`, `version`) остаётся.

Ошибка — существующая `FunctionNotAllowedError`.

### 4.3. R3: `SHOW`

В basic `VariableShowStmt` разрешён только для параметров (без учёта регистра): `server_version`, `server_version_num`, `timezone`, `datestyle`, `intervalstyle`, `search_path`, `client_encoding`, `server_encoding`, `standard_conforming_strings`, `statement_timeout`, `transaction_isolation`, `transaction_read_only`. `SHOW ALL` запрещён. Ошибка — новая `ShowParameterNotAllowedError(name)`: `SHOW {name} is not allowed in basic mode. Allowed parameters: {список через запятую}.`

### 4.4. R4: имена типов

Правило проверяет каждый узел `TypeName` (приведение `'t'::regclass`, `CAST(... AS regclass)`, список колонок табличной функции `json_to_record('...') AS x(a regclass)` и `ROWS FROM (...)`, `XMLTABLE ... COLUMNS a regclass`, аргументы `PREPARE p(regclass) AS ...`) и каждое имя в `COLLATE`. Точечная проверка только `TypeCast` пропускала бы остальные формы — тот же обход, что у `pg_input_is_valid`/`pg_input_error_info` в §4.2. Имя сравнивается без учёта регистра; ведущий `_` массива (`_regclass`, `pg_catalog._aclitem`) снимается до проверки, `[]` в AST имя не меняет.

| Что | Пример | Ошибка |
| --- | --- | --- |
| Типы, чья функция ввода резолвит имена объектов: `regclass`, `regproc`, `regprocedure`, `regoper`, `regoperator`, `regtype`, `regrole`, `regnamespace`, `regconfig`, `regdictionary`, `regcollation`, `aclitem` (множество `NAME_LOOKUP_TYPES`) | `'{secret.t}'::_regclass`, `'secretrole=r/postgres'::aclitem` | `TypeNotAllowedError(type_name)` |
| Имя `pg_*`/`_pg_*`/`hypopg*` — строковый тип системного отношения (R1), кроме скалярных `pg_lsn`, `pg_snapshot` (`BASIC_PG_SCALAR_TYPES`) | `NULL::pg_authid`, `json_populate_record(NULL::pg_class, '{}')` | `SystemRelationAccessError(type_name)` |
| Явная схема типа — не `allowed_schema` и не `pg_catalog` | `NULL::secret.accounts`, `enum_range(NULL::secret.status)`, `ROW(1)::secret.accounts` | `SchemaNotAllowedError` |
| Явная схема collation — не `allowed_schema` и не `pg_catalog` | `'a' COLLATE secret.coll` | `SchemaNotAllowedError` |
| Явная схема оператора (`A_Expr.name` любого вида, `SubLink.operName` в `ANY`/`ALL`/`SOME (SELECT …)`, `SortBy.useOp`) или метода `TABLESAMPLE` — не `allowed_schema` и не `pg_catalog` | `1 OPERATOR(secret.+) 2`, `a OPERATOR(secret.=) ANY (...)`, `ORDER BY a USING OPERATOR(secret.<)`, `TABLESAMPLE secret.m(1)` | `SchemaNotAllowedError` |

`TypeNotAllowedError`: `Type {type_name} is not allowed in basic mode. Rewrite the query without object identifier types.`

Имя схемы сравнивается точно — здесь, в R5 и в `validate_schema_access` (R1/схема отношения): pglast уже свернул имена без кавычек (`PUBLIC.app_t` → `public`), а `"PUBLIC"` в кавычках — другая схема. Префикс таблицы по-прежнему сравнивается без учёта регистра.

Остальные `pg_*`-имена типов, кроме строковых типов отношений, — внутренние скалярные типы (`pg_node_tree`, `pg_ndistinct`, `pg_mcv_list`, `pg_dependencies`, `pg_brin_*`); они тоже отклоняются, это безвредно.

Неквалифицированное имя типа без префикса (`NULL::users` при `table_prefix = app_`) не проверяется: без каталога встроенный тип не отличить от строкового типа таблицы (§6). Префикс для квалифицированного `public.<тип>` тоже не проверяется: в `public` живут типы расширений (`citext`, `hstore`, `vector`).

### 4.5. R5: цель гипотетического индекса

- `explain_query` в basic: определение гипотетического индекса собирает сервер (`IndexDefinition.definition`) и отправляет в `hypopg_create_index` через `sql_driver`, поэтому проверка валидатора ниже покрывает и `hypothetical_indexes`; ошибки — те же, что у `execute_sql` (`SchemaNotAllowedError`, `TablePrefixAccessError`, `SystemRelationAccessError`), до обращения к hypopg.
- `execute_sql` в basic: `hypopg_create_index` разрешён только с одним строковым константным аргументом; строка разбирается pglast и должна быть ровно одним `IndexStmt`, чей `relation` проходит `validate_schema_access` (схема, префикс, R1) и не имеет явной схемы, кроме `allowed_schema` (`information_schema`, открытая для чтения, здесь даёт `SchemaNotAllowedError`), а сам индекс состоит только из простых столбцов — без выражений, `WHERE`, opclass, collation, `INCLUDE` не-колонок, `TABLESPACE` и опций (`USING <метод>` и сортировка `ASC/DESC`, `NULLS FIRST/LAST` разрешены). Причина — §2: hypopg прогоняет `CREATE INDEX` через `transformIndexStmt`, которая резолвит имена объектов и вычисляет входные функции литеральных приведений. Иначе `FunctionNotAllowedError('hypopg_create_index')`.
- Остаток гипотетических индексов: `explain_query` вызывает `hypopg_reset()` только до `EXPLAIN`, в той же строке. Индексы, оставшиеся на пуловом соединении после вызова, из basic не прочитать: функции и представления, которые их показывают (`hypopg_list_indexes`, `hypopg_hidden_indexes`, `hypopg_get_indexdef`, `hypopg_relation_size`), закрыты (§4.1, §4.2). Надёжная очистка на том же соединении — §6.

### 4.6. Страховочные тесты

1. Юнит: `{f for f in BASIC_ALLOWED_FUNCTIONS if f.startswith("pg_")} == BASIC_PG_FUNCTIONS`. Новая `pg_*`-функция в общем списке без решения по basic роняет тест.
2. Юнит: `INTROSPECTION_FUNCTIONS <= ALLOWED_FUNCTIONS` (нет опечаток в списке исключений).
3. Юнит, корпус `test_query_validator_corpus.py` (блокируются в basic и проходят в full): `pg_class`, `pg_stats`, `public.pg_stat_statements`, `information_schema._pg_user_mappings`, `current_setting('app.jwt_secret')`, `pg_get_functiondef(1)`, `format_type(25, NULL)`, `SHOW app.jwt_secret`, `SHOW ALL`, `'t'::regclass`, `pg_input_is_valid('secret.t', 'regclass')`, `hypopg_create_index('CREATE INDEX ON secret.t (c)')`, `hypopg_create_index('CREATE INDEX ON app_t ((''secret.t''::regclass))')`, `hypopg_hide_index(12345)`; reg*-типы вне приведения — `json_to_record(...) AS x(a regclass)`, `jsonb_to_recordset(...) AS x(a regclass)`, `ROWS FROM (json_to_record(...) AS (a regclass))`, `XMLTABLE (... COLUMNS a regclass ...)`, `PREPARE p(regclass) AS ...`; массивы и `aclitem` — `'{secret.t}'::_regclass`, `PREPARE p(_regrole)`, `'…'::aclitem`; типы и collation чужих схем, строковые типы системных отношений — `NULL::secret.accounts`, `'a' COLLATE secret.coll`, `NULL::pg_authid`; `currval('secret.accounts')`, `lastval()`; состояние hypopg — `hypopg_list_indexes`, `hypopg_get_indexdef(1)`, `hypopg_create_index('CREATE INDEX ON information_schema.sql_features (feature_id)')`. Корпус проверяется и без `table_prefix`, чтобы ошибку правила §4 не скрыла ошибка префикса. Проходят в basic: `SHOW search_path`, `current_user`, `version()`, `NULL::public.app_t`, `'a' COLLATE "C"`, `'0/0'::pg_lsn`, `'{1,2}'::int[]`. Юнит, `TestBasicPolicy` в `test_query_validator.py`: типы ошибок и тексты, цели `hypopg_create_index` (`secret.t` → `SchemaNotAllowedError`, `users` → `TablePrefixAccessError`, `pg_class` → `SystemRelationAccessError`, не-`CREATE INDEX` и выражения/`WHERE`/opclass/`TABLESPACE` → `FunctionNotAllowedError`; `app_t (c)` проходит).
4. Интеграция (CI, Postgres 15/16): на живом сервере перечислить `pg_class.relname` для `relnamespace` ∈ {`pg_catalog`, `information_schema`} с `relname LIKE 'pg\_%' OR relname LIKE '\_pg\_%'`; для каждого basic-исполнитель отклоняет `SELECT * FROM <имя>`, `SELECT * FROM pg_catalog.<имя>`/`information_schema.<имя>`, `SELECT NULL::<имя>` и `SELECT NULL::<схема>.<имя>` ошибкой `SystemRelationAccessError`. Новые системные представления следующих версий Postgres не откроют дыру молча.
5. Интеграция: `get_object_details` и `list_objects` в basic по-прежнему работают (канал сервера не зависит от политики агента).
6. Юнит: `explain_query` в basic + `app_` с `hypothetical_indexes` на `secret.accounts`, `users`, `pg_stats` отклоняется, `hypopg_create_index` до делегата не доходит.

## 5. Изменения поведения (для заметок к PR)

Ветка 1 (`claude/server-catalog-channel`):

- basic + `table_prefix`: `explain_query` с `hypothetical_indexes` работает (раньше — ложный `HypopgNotInstalledError`).
- Все режимы: проверка расширений и версии идёт read-only с `statement_timeout`; ошибка валидатора или программиста больше не выдаётся за «расширение не установлено».
- Все режимы: `ts_stat` и `ts_rewrite` недоступны в `execute_sql` и `explain_query`.
- Все режимы: таймаут, отмена или сбой соединения во время проверки расширений/версии приходят агенту как есть (`QueryTimeoutError`, `QueryCancelledError`, ошибка соединения); раньше они превращались в «ошибку каталога расширений» с советом про `.control`-файлы или в версию 0.
- API библиотеки: `ExtensionInspectorAdapter(executor, connection_id)` — без параметра `template`; `EXT_INSTALLED_QUERY`/`EXT_AVAILABLE_QUERY` удалены (замена — `QUERY_EXTENSION_INSTALLED`/`QUERY_EXTENSION_AVAILABLE` в `postgres/catalog.py`); `catalog_driver` — обязательный именованный аргумент `ExplainPlanBuilder`, `CostEvaluator`, `IndexTuningBase`/`DatabaseTuningAdvisor`, `TopQueriesCalc`.

Ветка 2 (`claude/basic-agent-policy`):

- basic: отношения `pg_*`/`_pg_*` недоступны (в том числе `public.pg_stat_statements`); `pg_catalog.<x>` отвечает `SystemRelationAccessError` вместо `SchemaNotAllowedError`.
- basic: `hypothetical_indexes` и `hypopg_create_index` — только таблицы `public` с префиксом (§4.5).
- basic: гипотетический индекс — только простые столбцы, без выражений, `WHERE`, opclass, collation, `INCLUDE` не-колонок, `TABLESPACE` и опций (§4.5); `USING <метод>` и сортировка разрешены.
- basic: функции интроспекции (§4.2) недоступны; `SHOW` — только параметры §4.3; приведения к `reg*` недоступны (ломается идиома `tableoid::regclass`).
- basic: `format_type`, `hypopg_hide_index`, `hypopg_unhide_index` недоступны (§4.2).
- basic: reg*-типы отклоняются не только в приведениях, но и в списках колонок табличных функций, `XMLTABLE` и `PREPARE` (§4.4).
- basic: массивы reg*-типов (`_regclass`) и `aclitem`/`_aclitem` отклоняются так же (§4.4).
- basic: имена типов и collation с явной схемой, кроме `public` и `pg_catalog`, отклоняются `SchemaNotAllowedError`; строковые типы системных отношений (`NULL::pg_authid`) — `SystemRelationAccessError`; `pg_lsn` и `pg_snapshot` разрешены (§4.4).
- API/клиенты: `TypeCastNotAllowedError` переименована в `TypeNotAllowedError`, текст — `Type {type_name} is not allowed in basic mode. …` (срабатывает не только на касты); `REG_TYPES` → `NAME_LOOKUP_TYPES` в `postgres/security/policies.py`. Псевдонимов нет.
- basic: `currval` и `lastval` недоступны (§4.2).
- basic: `pg_typeof` и `pg_basetype` недоступны (оракул типов через `regtypein`, §4.2); ломается идиома `pg_typeof(x)` для отладки типов.
- basic: операторы и методы `TABLESAMPLE` с явной схемой, кроме `public` и `pg_catalog`, отклоняются `SchemaNotAllowedError` (§4.4), в том числе оператор сравнения с подзапросом `a OPERATOR(secret.=) ANY/ALL/SOME (SELECT …)` (`SubLink.operName`); `IN (SELECT …)` и `EXISTS` оператора не несут.
- basic: имя схемы сравнивается точно: `"PUBLIC".t` (в кавычках) отклоняется, `PUBLIC.t` без кавычек и `"public".t` проходят (§4.4).
- basic: представления `hypopg*` закрыты как системные (`SystemRelationAccessError`); `hypopg_get_indexdef`, `hypopg_list_indexes`, `hypopg_relation_size` недоступны (§4.1, §4.2).
- basic: `hypopg_create_index` не принимает явную схему, кроме `public` (раньше проходила `information_schema.<таблица>`) (§4.5).
- Все режимы: гипотетические индексы на таблицах со схемой (`schema.table`) работают — имя индекса раньше включало точку и не разбиралось.

README (ветка 2): основная граница — права роли БД; basic — защита в глубину; представления расширений `pg_*`/`hypopg*` в `public` закрыты в basic; для `hypothetical_indexes` в basic hypopg должен стоять в `public` (basic выставляет `search_path = public` и не принимает функции с явной схемой).

## 6. Вне объёма

- (i) Представление в `public` поверх таблиц другой схемы остаётся доступным: проверка идёт по тексту запроса, не по плану. Проверка по плану (`EXPLAIN (VERBOSE)`) рассмотрена и отложена: круг к БД на каждый запрос и смена семантики представлений.
- (i) `information_schema` в basic остаётся (кроме `schemata` и `_pg_*`): метаданные чужих схем с учётом прав роли.
- (/) Гипотетические индексы сбрасываются на том же соединении: `SqlExecutor` помечает соединение пула, на котором выполнялся `hypopg_create_index`, и `reset`-хук пула вызывает на нём `hypopg_reset()` при возврате (спека `2026-09-28-basic-followups-design.md`, §3.3).
- (!) Неквалифицированный строковый тип таблицы без префикса (`NULL::users`, `json_populate_record(NULL::users, '{}')` при `table_prefix = app_`) проходит: без каталога его не отличить от встроенного типа. Раскрывает существование и колонки таблицы `public` вне префикса, не данные. Квалифицированный `NULL::public.users` проходит по той же причине (типы расширений в `public`).
- (!) Владелец объекта видит `information_schema.routines.routine_definition` и `information_schema.views.view_definition` своих объектов в любой схеме, а также свои `information_schema.user_mapping_options` (в том числе пароли user mapping): фильтр по правам роли их не прячет.
- (!) Конфигурации, словари и парсеры полнотекстового поиска через `to_tsvector`/`to_tsquery`/`plainto_tsquery`/`phraseto_tsquery`/`websearch_to_tsquery`/`ts_headline`/`ts_debug` (включая сравнение его колонок `regdictionary`)/`ts_lexize`/`ts_parse`/`ts_token_type` и `get_current_ts_config()` (`get_current_ts_config() = 'cfg'` приводит литерал через `regconfigin`) — только существование объектов: имя резолвится функцией или неявным приведением, а не явным типом.
- (!) Неявное приведение литерала к пользовательской колонке типа `reg*` или домена над ним (`WHERE regclass_col = 'secret.t'`) статически не обнаружить: тип колонки известен только каталогу. Настоящая граница — права роли.
- (!) `EXPLAIN (SETTINGS)` показывает планировочные параметры с нестандартными значениями.
- (x) `SET LOCAL search_path = public, pg_catalog` отклонён: объекты `public` перекрыли бы встроенные функции и операторы (класс CVE-2018-1058).
