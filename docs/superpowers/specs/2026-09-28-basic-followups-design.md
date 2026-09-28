# Дизайн: доработки после границы basic

Дата: 2026-09-28. Статус: согласовано (ответа на вопросы по дизайну не было — приняты рекомендуемые варианты); PR 1 реализован. Продолжение `2026-09-28-basic-confinement-design.md` (§6).

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
- `uri_fields` кладёт в `connect_options` все параметры query string, кроме `sslmode` и `client_encoding` (у них свои поля); повторяющийся ключ — последнее значение.
- Валидация `connect_options`: ключи `host`, `hostaddr`, `port`, `dbname`, `user`, `password`, `sslmode`, `client_encoding` запрещены (у них поля) — `ValueError` с подсказкой поля; `sslpassword` и `passfile` запрещены (секрет или путь к секретам в URI, который попадает в логи и ошибки). Остальное не проверяется: неизвестный параметр отклонит libpq при подключении.
- `DatabaseSettings` читает `MCP_DATABASE_CONNECT_OPTIONS` как JSON-объект (стандарт pydantic-settings); `config.json` — `database.connect_options`. URI и конфиг не сливаются: словарь из URI заменяет словарь конфига целиком (как остальные поля URI).

### 3.2. `max_inactive_connection_lifetime`

Передаётся в пул как `max_idle` (секунды): `DbConnPool(..., max_idle=...)` → `AsyncConnectionPool(max_idle=...)`.

### 3.3. Сброс hypopg на том же соединении

- `DbConnPool` хранит `weakref.WeakSet` соединений, на которых создавались гипотетические индексы, и метод `mark_hypopg_used(connection)`.
- `SqlExecutor._execute_with_connection` при выполнении SQL, содержащего `hypopg_create_index` (без учёта регистра), помечает соединение. Покрывает `explain_query` и прямой вызов агента.
- Пул создаётся с `reset=` callback: для помеченного соединения — `SELECT hypopg_reset()` (соединение в autocommit) и снятие пометки; у остальных — ничего (без лишнего запроса). Ошибка сброса — `WARNING` в лог и повторный подъём исключения: psycopg_pool выбрасывает такое соединение, состояние hypopg на нём неизвестно.
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
  - `ExplainStmt` — вложенный запрос; если у EXPLAIN есть опция `generic_plan`, она переносится;
  - `DeclareCursorStmt` — вложенный запрос;
  - остальные (`SHOW`, `PREPARE`, `DEALLOCATE`, `FETCH`, `CLOSE`, `CREATE EXTENSION`) — пропускаются: у них нет плана или подготовленный запрос нельзя выполнить (`EXECUTE` запрещён).
- Текст оператора — `pglast.stream.RawStream()(node)`; выполняется `EXPLAIN (VERBOSE, FORMAT JSON[, GENERIC_PLAN]) <текст>` через делегата с тем же префиксом `SET LOCAL statement_timeout …; SET LOCAL search_path = public;`, `readonly=True` (EXPLAIN без ANALYZE не исполняет DML и допустим в read-only транзакции).
- По всему дереву плана (вложенные `Plans`, `InitPlan`/`SubPlan`) собираются пары `Schema`/`Relation Name` и `Schema`/`Function Name`:
  - отношение: схема ровно `allowed_schema`; имя не `pg_*`/`_pg_*`/`hypopg*`; с `table_prefix` — имя начинается с префикса (без учёта регистра);
  - функция: схема `pg_catalog` или `allowed_schema`.
- Нарушение — новая `PlanAccessError(kind, qualified_name)`: `Access to {kind} '{schema}.{name}' is not allowed in basic mode: the query reaches it through a view, rule or function. Only tables in 'public' are permitted.` (`kind` — `relation`/`function`).
- Ошибка планирования (например, отношения нет) приходит агенту как ошибка Postgres — та же, что дало бы выполнение.

### 4.3. Что не покрывается

Функции `SECURITY DEFINER` и PL/pgSQL непрозрачны для плана (SQL-функции, которые Postgres встраивает, видны). Цена — лишний запрос к БД на оператор. Спека `basic-confinement` §6: пункт про представления дополняется ссылкой на `plan_check`.

### 4.4. Тесты

- Юнит: `PlanGuard` на фальшивом делегате с заготовленными JSON-планами — представление поверх `secret.t` → `PlanAccessError`; `public.app_t` проходит; функция `secret.f` → отказ; `pg_catalog.now` проходит; вложенные `Plans`/`InitPlan`; EXPLAIN с `GENERIC_PLAN`; `SHOW`/`PREPARE` не порождают EXPLAIN; префикс.
- Юнит: `plan_check=false` — EXPLAIN не выполняется; full — не выполняется никогда.
- Интеграция: схема `secret` с таблицей, представление `public.app_secret_view` поверх неё; basic + `plan_check=true` → `PlanAccessError`, basic без `plan_check` → данные (задокументированное поведение); `app_users` проходит в обоих.

## 5. Изменения поведения (для заметок к PR)

- PR 1: при старте basic с широкими правами роли — `WARNING` в лог; БД недоступна на старте — `INFO` `Basic role check skipped`; прочая ошибка Postgres при проверке — `WARNING` `Basic role check failed`; программная ошибка самой проверки — `ERROR` `Basic role check crashed` (сервер не падает); в basic пул открывается при старте (фоновая проверка), а не при первом запросе; README — раздел «Роль для basic».
- PR 2: параметры libpq из URI (`target_session_attrs`, `options`, `connect_timeout`, `sslrootcert`, …) доходят до подключения — раньше молча отбрасывались; поле `extra_kwargs` удалено, новое `connect_options`; `max_inactive_connection_lifetime` работает (`max_idle` пула); гипотетические индексы сбрасываются при возврате соединения в пул.
- PR 3: новая настройка `plan_check` (по умолчанию выключена); с ней basic отклоняет запросы, план которых читает отношения вне `public`/префикса или функции чужих схем (`PlanAccessError`).
