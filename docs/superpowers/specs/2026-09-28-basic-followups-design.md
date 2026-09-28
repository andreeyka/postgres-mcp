# Дизайн: доработки после границы basic

Дата: 2026-09-28. Статус: реализовано (PR 1–3). Продолжение `2026-09-28-basic-confinement-design.md` (§6).

## 1. Объём

| # | Задача | PR / ветка |
| --- | --- | --- |
| 1 | Права роли для basic: README + предупреждение при старте | 1 — `claude/basic-role-guidance` |
| 2 | Параметры libpq из URI и конфига доходят до подключения; `max_inactive_connection_lifetime` работает | 2 — `claude/connection-options` |
| 3 | Гипотетические индексы hypopg сбрасываются на том же соединении пула | 2 — `claude/connection-options` |
| 4 | Проверка по плану запроса в basic (опционально) | 3 — `claude/basic-plan-check` |

Ветки — цепочкой: каждая от предыдущей, сливать по порядку.

Решения по вопросам (рекомендуемые варианты):

| Вопрос | Решение |
| --- | --- |
| Проверка по плану по умолчанию | Выключена, `plan_check=false`. Представление в `public` поверх чужой схемы обычно создано DBA намеренно и выдано роли через `GRANT`; строгий режим — для тех, кому нужно «только `public`» |
| Роль с широкими правами при старте basic | Только `WARNING` в лог с конкретикой; сервер стартует |
| Упаковка | 3 PR подряд |

## 2. Права роли для basic (PR 1)

### 2.1. README

Раздел «Роль для basic» (после описания `access_mode`/`table_prefix`): basic — защита в глубину, граница — права роли. Готовый SQL:

```sql
CREATE ROLE mcp_basic LOGIN PASSWORD '...' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
GRANT CONNECT ON DATABASE mydb TO mcp_basic;
GRANT USAGE ON SCHEMA public TO mcp_basic;
-- все таблицы public (без table_prefix):
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_basic;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO mcp_basic;
-- с table_prefix = 'app_': только таблицы с префиксом
DO $$
DECLARE t record;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'app\_%' LOOP
    EXECUTE format('GRANT SELECT ON public.%I TO mcp_basic', t.tablename);
  END LOOP;
END $$;
-- write_mode = true: добавить INSERT, UPDATE, DELETE на те же таблицы и USAGE на их последовательности
```

Плюс: не выдавать роли членство в `pg_read_all_data`/`pg_read_all_settings`/`pg_read_all_stats`/`pg_monitor`; ставить `pg_stat_statements` в отдельную схему или не давать на неё прав; про представления в `public` поверх чужих схем — они отдают данные тем, кому выдан `SELECT` на представление.

### 2.2. Проверка при старте

- Запускается фоновой задачей в `PostgresProvider.lifespan` (не задерживает старт; отменяется при остановке), если basic достижим: потолок `access_mode=basic`, или задан `access_resolver`, или `access_policy.enforced`.
- Идёт через `catalog_driver`; запросы — новые шаблоны в `postgres/catalog.py` (в `CATALOG_QUERIES`):

```sql
-- QUERY_ROLE_ATTRIBUTES
SELECT current_user AS role_name, r.rolsuper, r.rolbypassrls, r.rolcreaterole
FROM pg_catalog.pg_roles AS r
WHERE r.rolname = current_user

-- QUERY_ROLE_PREDEFINED_MEMBERSHIPS
SELECT r.rolname
FROM pg_catalog.pg_roles AS r
WHERE r.rolname IN ('pg_read_all_data', 'pg_write_all_data', 'pg_read_all_settings', 'pg_read_all_stats',
                    'pg_read_server_files', 'pg_write_server_files', 'pg_execute_server_program', 'pg_monitor')
  AND pg_catalog.pg_has_role(current_user, r.oid, 'MEMBER')
ORDER BY r.rolname

-- QUERY_ROLE_FOREIGN_SCHEMAS
SELECT n.nspname
FROM pg_catalog.pg_namespace AS n
WHERE n.nspname NOT IN ('public', 'information_schema')
  AND n.nspname NOT LIKE 'pg\_%'
  AND pg_catalog.has_schema_privilege(n.oid, 'USAGE')
ORDER BY n.nspname

-- QUERY_ROLE_UNPREFIXED_TABLES (только при table_prefix)
SELECT count(*) AS unprefixed
FROM pg_catalog.pg_class AS c
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind IN ('r', 'v', 'm', 'p', 'f')
  AND NOT starts_with(lower(c.relname), lower({}))
  AND pg_catalog.has_table_privilege(c.oid, 'SELECT')
```

- Логика — модуль `domains/role_check.py`: функция `async def basic_role_findings(catalog: QueryExecutorPort, table_prefix: str | None) -> RoleFindings` возвращает имя роли и список находок (английские фразы); у суперпользователя находка одна — `superuser` (`has_*_privilege` у него всегда true). Функция `async def warn_about_basic_role(catalog, table_prefix)` пишет одну строку `WARNING`:
  `Database role '<role>' has privileges beyond basic mode: BYPASSRLS; member of pg_read_all_data; USAGE on schemas: a, b; SELECT on 3 public tables without prefix 'app_'. In basic mode the SQL validator is then the only barrier; grant the role access to 'public' only (see README).`
  (суперпользователь дал бы единственную находку `superuser` — остальные проверки для него ничего не добавили бы, см. выше). Схем — не больше 10, дальше `…`.
- Находки также включают `CREATEROLE` (рядом с `BYPASSRLS`): на PG ≤15 эта привилегия позволяет роли выдать себе членство в предопределённых ролях самостоятельно.
- Недоступность БД на старте (`OperationalError`/`ConnectionFailedError`/`ConnectionNotEstablishedError`/таймаут/отмена) — одна строка `INFO` `Basic role check skipped: <маскированная ошибка>`, сервер работает; прочая ошибка Postgres при выполнении шаблонов (`psycopg.Error`) — одна строка `WARNING` `Basic role check failed: <маскированная ошибка>`, а не тихий пропуск (иначе реальная дыра в проверке выглядела бы как штатный пропуск); программная ошибка самой проверки (не БД) ловится в обёртке `PostgresProvider._run_basic_role_check` и пишет `ERROR` `Basic role check crashed`, фоновая задача не падает незаметно и сервер не останавливается. Проверка запускается, только если достижим basic **и** включена (`PostgresProvider(check_basic_role=True)` по умолчанию; CLI передаёт `False` для stdio — там лог всё равно не виден).
- Тесты: юнит на находки (фальшивый исполнитель по шаблонам), на текст предупреждения, на пропуск при ошибке; юнит на условие запуска в провайдере; интеграция: суперпользователь CI (`postgres`) даёт `superuser` в находках.

## 3. Слой подключения (PR 2)

### 3.1. Параметры libpq

- Поле `extra_kwargs` (не используется) удаляется. Новое поле `connect_options: dict[str, str]` — параметры libpq, которые попадают в query string `database_uri`.
- `uri_fields` кладёт в `connect_options` все параметры query string, кроме `sslmode` и `client_encoding` (у них свои поля). Query string разбирается как в libpq, а не `parse_qs`: деление по `&` и первому `=`, `unquote` (а не `unquote_plus` — `+` остаётся плюсом), пустое значение сохраняется, повторяющийся ключ — последнее значение, сегмент без `=` и %-последовательность, не декодируемая как UTF-8, — `ValueError` без значения в тексте. Мягче libpq: `=` внутри значения и некорректный %-токен проходят буквально (`database_uri` кодирует их заново).
- `database_uri` кодирует query string через `quote` (пробел — `%20`): `urlencode` по умолчанию даёт `+`, который libpq не декодирует. `connect_options` попадает в поля URI, только если в query string есть параметры кроме `sslmode`/`client_encoding`.
- Валидация `connect_options`: ключи `host`, `hostaddr`, `port`, `dbname`, `user`, `password`, `sslmode`, `ssl` и `requiressl` (синонимы `sslmode=require` в libpq), `client_encoding` запрещены (у них поля) — `ValueError` с подсказкой поля; `sslpassword`, `passfile`, `oauth_client_secret` и `sslkeylogfile` (пишет ключи TLS-сессий на диск) запрещены: URI может попасть в логи и сообщения об ошибках. Ключи сравниваются без учёта регистра ради ранней понятной ошибки (libpq регистр различает и `PASSWORD` отверг бы сам, но без подсказки поля).
- Неизвестный libpq параметр — ошибка конфигурации: `model_validator` разбирает собранный `database_uri` через `conninfo_to_dict` (PQconninfoParse, без сети). Иначе параметры из URI SQLAlchemy/asyncpg/Prisma (`prepared_statement_cache_size`, `schema`, `pgbouncer`) всплывали бы при подключении как таймаут пула через 30 секунд. В сообщении — только ключ (libpq называет ключ без значения; прочие ошибки разбора — общий текст без URI).
- `DatabaseSettings` читает `MCP_DATABASE_CONNECT_OPTIONS` как JSON-объект (стандарт pydantic-settings); `config.json` — `database.connect_options`. URI и конфиг не сливаются: словарь из URI заменяет словарь конфига целиком (как остальные поля URI).

### 3.2. `max_inactive_connection_lifetime`

Передаётся в пул как `max_idle` (секунды): `DbConnPool(..., max_idle=...)` → `AsyncConnectionPool(max_idle=...)`.

### 3.3. Сброс hypopg на том же соединении

- `DbConnPool` хранит `weakref.WeakSet` соединений, на которых создавались гипотетические индексы, и метод `mark_hypopg_used(connection)`.
- `SqlExecutor._execute_with_connection` при выполнении SQL, содержащего `hypopg_create_index` (без учёта регистра), помечает соединение. Покрывает `explain_query` и прямой вызов агента.
- Пул создаётся с `reset=` callback: для помеченного соединения — `SELECT hypopg_reset()` (соединение в autocommit) и снятие пометки; у остальных — ничего (без лишнего запроса). Ошибка сброса — `WARNING` в лог и повторный подъём исключения: psycopg_pool выбрасывает такое соединение, состояние hypopg на нём неизвестно. Помеченное соединение, чей сброс не удался, выбрасывается всегда — в том числе при `UndefinedFunction`, когда hypopg не установлен или не лежит в `search_path` роли по умолчанию: basic создаёт индексы под `SET LOCAL search_path = public`, а сброс идёт вне этой транзакции, так что индексы могут быть созданы, а `hypopg_reset()` не найден. Состояние hypopg не переживает возврат соединения в пул. При заданном `reset` psycopg_pool возвращает соединения в пул рабочей задачей.
- `CandidateGenerator._estimate_hypothetical_index_sizes` создаёт индексы и читает размеры одним оператором `unnest({}::text[]) WITH ORDINALITY … LATERAL hypopg_create_index(...)` и сопоставляет размер с кандидатом по позиции: имя от hypopg (`<oid>btree_…`) с `IndexRecommendation.name` (`dba_idx_…`) не совпадает, поэтому прежнее сопоставление по имени всегда давало `estimated_size_bytes = 0`.
- Спека `basic-confinement` §6: пункт про отложенную очистку помечается как выполненный.

### 3.4. Тесты

- Юнит: `uri_fields` переносит `target_session_attrs`, `options`, `connect_timeout` в `connect_options`; `database_uri` возвращает их; запрещённые ключи → `ValidationError`; `max_idle` доходит до `AsyncConnectionPool` (патч конструктора).
- Юнит: пометка и callback — помеченное соединение получает `hypopg_reset()`, непомеченное — нет, ошибка сброса поднимается.
- Интеграция: `connect_options={"application_name": "mcp-test"}` видно в `current_setting('application_name')` (full); после `explain_query` с гипотетическим индексом и возврата соединения `hypopg_list_indexes` на том же соединении пуст (пул `max_size=1`).

## 4. Проверка по плану (PR 3)

### 4.1. Где и когда

- Настройка `plan_check: bool = False` в `DatabaseConfig` (env `MCP_DATABASE_PLAN_CHECK`). Действует только для исполнителей basic (`allowed_schema` задан); в full игнорируется.
- `SafeSqlConfig.plan_check`; `SafeSqlExecutor._guarded` после валидации и до выполнения вызывает `PlanGuard.check(query)` (модуль `postgres/security/plan_guard.py`).

### 4.2. Что проверяется

- Запрос разбирается pglast; для каждого оператора:
  - `SelectStmt`, `InsertStmt`, `UpdateStmt`, `DeleteStmt` — сам оператор;
  - `ExplainStmt` — вложенный запрос; если у EXPLAIN есть опция `generic_plan`, она переносится (только включённая: `generic_plan false`/`0`/`off` не переносится);
  - `DeclareCursorStmt` — вложенный запрос;
  - остальные (`SHOW`, `PREPARE`, `DEALLOCATE`, `FETCH`, `CLOSE`, `CREATE EXTENSION`) — пропускаются: у них нет плана или подготовленный запрос нельзя выполнить (`EXECUTE` запрещён).
- Текст оператора — `pglast.stream.RawStream()(node)`; выполняется `EXPLAIN (VERBOSE, FORMAT JSON[, GENERIC_PLAN]) <текст>` через делегата с тем же префиксом `SET LOCAL statement_timeout …; SET LOCAL search_path = public;`, `readonly=True` (EXPLAIN без ANALYZE не исполняет DML и допустим в read-only транзакции). Строка с несколькими операторами (`CREATE EXTENSION hypopg; SELECT hypopg_create_index(...)`) — документированное ограничение: `PlanGuard` строит план каждого оператора до выполнения первого, поэтому оператор, зависящий от результата предыдущего в той же строке, может быть отклонён ошибкой планирования, хотя без `plan_check` выполнился бы.
- По всему документу плана (все вложенные словари и списки: `Plans` с `InitPlan`/`SubPlan` в `Parent Relationship`, `Target Tables` у `ModifyTable`) собираются пары `Schema`/`Relation Name` и `Schema`/`Function Name`:
  - отношение: схема ровно `allowed_schema`; имя не `pg_*`/`_pg_*`/`hypopg*`; с `table_prefix` — имя начинается с префикса (без учёта регистра);
  - функция: схема `pg_catalog` или `allowed_schema`.
- Нарушение — `PlanAccessError(kind, qualified_name, *, allowed_schema, table_prefix)`: `Access to {kind} '{schema}.{name}' is not allowed in basic mode: the query reaches it through a view, rule or function. {hint}` (`kind` — `relation`/`function`; `hint` называет `allowed_schema`, а для отношения с заданным `table_prefix` — ещё и префикс).
- Закрыто по умолчанию (fail closed): узел сканирования, который не называет, что читает, — `PlanUnverifiableError(node_type)`, а не пропуск. `Foreign Scan`/`Custom Scan` без `Relation Name` (join или агрегат, которые `postgres_fdw` целиком пересчитал на удалённом сервере; `scanrelid = 0` при pushdown) и `Function Scan` без `Function Name` (`ROWS FROM` из нескольких функций — `Function Name` бывает только у одиночной функции во `FROM`) отклоняются, даже если результат сам по себе безобиден. То же для документа плана, который EXPLAIN не вернул или вернул не в ожидаемой форме.
- Ошибка планирования (например, отношения нет) приходит агенту как ошибка Postgres — та же, что дало бы выполнение.
- Клиентский таймаут (`safe_sql_timeout` + клиентская страховка `asyncio.timeout`) покрывает EXPLAIN-проверку по плану и само выполнение одним бюджетом — оба раунд-трипа к БД идут внутри одного `_guarded`.

### 4.3. Что не покрывается

Функции `SECURITY DEFINER` и PL/pgSQL непрозрачны для плана (SQL-функции, которые Postgres встраивает, видны). `Function Name` бывает только у `Function Scan` (функции во `FROM`): функция в выражении представления (`SELECT secret.f(x)`) в плане не видна. С `plan_check` запросы к `information_schema` отклоняются — её представления читают `pg_catalog`. Цена — лишний запрос к БД на оператор. Спека `basic-confinement` §6: пункт про представления дополняется ссылкой на `plan_check`.

### 4.4. Тесты

- Юнит: `PlanGuard` на фальшивом делегате с заготовленными JSON-планами — представление поверх `secret.t` → `PlanAccessError`; `public.app_t` проходит; функция `secret.f` → отказ; `pg_catalog.now` проходит; вложенные `Plans`/`InitPlan`/`SubPlan`, `Target Tables` у `ModifyTable`; EXPLAIN с `GENERIC_PLAN` (и с явно выключенной опцией); `SHOW`/`PREPARE` не порождают EXPLAIN; префикс; текст подсказки в сообщении.
- Юнит: fail closed — `Foreign Scan`/`Custom Scan` без `Relation Name`, `Function Scan` без `Function Name`, план без результата или не в ожидаемой форме → `PlanUnverifiableError`.
- Юнит: `plan_check=false` — EXPLAIN не выполняется; full — не выполняется никогда.
- Юнит: `explain_query` (`ExplainService`) не оборачивает `PlanAccessError`/`SystemRelationAccessError`/`ExplainAnalyzeNotSupportedError` из `_run_explain_query` в `ExplainPlanExecutionError` — агент видит настоящую причину; EXPLAIN ANALYZE в basic по-прежнему подменяется обычным EXPLAIN с пометкой.
- Интеграция: схема `secret` с таблицей, представление `public.app_secret_view` поверх неё; basic + `plan_check=true` → `PlanAccessError`, basic без `plan_check` → данные (задокументированное поведение); `app_users` проходит в обоих; запрос к `information_schema.tables` с `plan_check=true` → `PlanAccessError`; INSERT планируется в read-only транзакции, сам INSERT проходит.

## 5. Изменения поведения (для заметок к PR)

- PR 1: при старте basic с широкими правами роли — `WARNING` в лог; БД недоступна на старте — `INFO` `Basic role check skipped`; прочая ошибка Postgres при проверке — `WARNING` `Basic role check failed`; программная ошибка самой проверки — `ERROR` `Basic role check crashed` (сервер не падает); в basic пул открывается при старте (фоновая проверка), а не при первом запросе; README — раздел «Роль для basic».
- PR 2: параметры libpq из URI (`target_session_attrs`, `options`, `connect_timeout`, `sslrootcert`, …) доходят до подключения — раньше молча отбрасывались; поле `extra_kwargs` удалено, новое `connect_options`; `max_inactive_connection_lifetime` работает (`max_idle` пула); гипотетические индексы сбрасываются при возврате соединения в пул. Ломающие изменения и смена поведения:
  - URI с параметром, которого libpq не знает (`prepared_statement_cache_size`, `schema`, `pgbouncer` из URI SQLAlchemy/asyncpg/Prisma), не проходит валидацию конфигурации — раньше такие параметры молча отбрасывались;
  - запрещённые ключи в query string URI (`password`, `host`, `ssl`, `requiressl`, `sslpassword`, `sslkeylogfile`, …) — ошибка конфигурации;
  - `config.json` с `database.extra_kwargs` не загружается (`extra="forbid"`);
  - протокол `DatabaseConfigPort` получил обязательный атрибут `max_inactive_connection_lifetime` — затрагивает библиотечные реализации протокола;
  - query string URI разбирается как в libpq: `+` больше не пробел, пустое значение сохраняется, параметр без `=` — ошибка;
  - с `reset`-callback каждый возврат соединения идёт через рабочую задачу пула: при последовательной нагрузке пул может держать одно лишнее простаивающее соединение (в пределах `max_idle`);
  - `estimated_size_bytes` у кандидатов DTA теперь настоящий размер гипотетического индекса (раньше всегда 0).
- PR 3: новая настройка `plan_check` (по умолчанию выключена); с ней basic отклоняет запросы, план которых читает отношения вне `public`/префикса или функции чужих схем (`PlanAccessError`), а узел плана без имени читаемого — всегда (`PlanUnverifiableError`, fail closed). Заодно `_run_explain_query` (`explain_query`) перестал прятать ошибки валидатора и `plan_check` за `ExplainPlanExecutionError` — агент видит настоящую причину отказа, а не generic "Error calling tool".
