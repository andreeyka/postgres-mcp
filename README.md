
# Postgres MCP Pro (форк FastMCP)

## Обзор

**Postgres MCP Pro** — MCP-сервер (Model Context Protocol) с открытым исходным кодом на базе [FastMCP](https://gofastmcp.com/), который помогает вам и ИИ-агентам на всех этапах: от написания кода до тестирования, развёртывания и эксплуатации в production.

Этот форк оригинального проекта [postgres-fastmcp](https://github.com/crystaldba/postgres-fastmcp) переписан на FastMCP и даёт:

- **🚀 Высокая производительность** — FastMCP оптимизирован для быстрой работы
- **🔧 Гибкая настройка** — `config.json`, переменные окружения (`MCP_SERVER_*`, `MCP_DATABASE_*`, `MCP_AUTH_*`, `MCP_FASTMCP_*`) или CLI
- **🌐 HTTP и STDIO** — запуск как HTTP-сервер или через stdio для настольных MCP-клиентов
- **🔐 Детальный контроль доступа** — access_mode (basic/full) и write_mode (true/false)
- **📌 Одна БД на сервер** — один экземпляр MCP-сервера обслуживает одну базу PostgreSQL

### Основные возможности

- **🔍 Здоровье БД** — анализ индексов, загрузки соединений, буферного кэша, vacuum, лимитов последовательностей, лага репликации и др.
- **⚡ Подбор индексов** — перебор тысяч вариантов индексов для вашей нагрузки с использованием промышленных алгоритмов
- **📈 Планы запросов** — проверка и оптимизация по планам EXPLAIN и симуляция гипотетических индексов
- **🧠 Понимание схемы** — контекстная генерация SQL на основе детального знания схемы БД
- **🛡️ Безопасное выполнение SQL** — настраиваемый контроль доступа, режим только чтение и разбор SQL, пригодно для разработки и production

## Быстрый старт

### Требования

Перед началом нужны:

1. Учётные данные для подключения к PostgreSQL
2. Python 3.12 или выше
3. Менеджер зависимостей `uv` (рекомендуется)

### Запуск сервера

Один экземпляр MCP-сервера обслуживает **одну базу данных**. Запуск возможен через CLI, конфигурационный файл или переменные окружения.

#### 1. CLI (одна база)

**Режим HTTP:**

```bash
uv run postgres-fastmcp \
  --database-uri "postgresql://user:password@localhost:5432/dbname" \
  --transport http \
  --port 8000 \
  --access-mode full
```

**Режим STDIO (для MCP-клиентов вроде Claude Desktop):**

```bash
uv run postgres-fastmcp \
  --database-uri "postgresql://user:password@localhost:5432/dbname" \
  --transport stdio \
  --access-mode basic
```

Все опции CLI необязательны и действуют независимо от `--database-uri`: `--transport` (http|stdio), `--host`, `--port`, `--access-mode` (basic|full), `--write-mode` / `--no-write-mode`. Незаданная опция ничего не переопределяет: значение берётся из `config.json`, затем из переменных окружения, затем по умолчанию.

#### 2. Конфигурационный файл (`config.json`)

Создайте `config.json` в текущей директории с секциями `server`, `fastmcp` и `database` (одна база на сервер):

```json
{
    "server": {
        "host": "0.0.0.0",
        "port": 8000,
        "transport": "http",
        "endpoint": "/mcp"
    },
    "fastmcp": {
        "server_name": "postgres-fastmcp"
    },
    "database": {
        "host": "localhost",
        "port": 5432,
        "user": "user",
        "password": "password",
        "name": "dbname",
        "access_mode": "full",
        "write_mode": false
    }
}
```

Затем выполните:

```bash
uv run postgres-fastmcp
```

Подключение к БД задаётся полями (`host`, `port`, `user`, `password`, `name`). Опционально: `access_mode`, `write_mode`, `table_prefix`, `plan_check`, `sslmode`, `client_encoding`, `pool_min_size`, `pool_max_size`, `safe_sql_timeout`, `query_tag`, `connect_options`, `max_inactive_connection_lifetime`.

`connect_options` — параметры libpq, которые попадают в query string URI подключения: `{"target_session_attrs": "read-write", "options": "-c statement_timeout=5000", "sslrootcert": "/etc/ssl/ca.pem", "application_name": "mcp"}`. Значения — строки. Ключи, у которых есть свои поля (`host`, `hostaddr`, `port`, `dbname`, `user`, `password`, `sslmode`, `ssl`, `requiressl`, `client_encoding`), и секреты (`sslpassword`, `passfile`, `oauth_client_secret`, `sslkeylogfile` — последний пишет на диск ключи TLS-сессий) отклоняются: URI подключения может попасть в логи и сообщения об ошибках. Параметр, которого libpq не знает (например `prepared_statement_cache_size` или `pgbouncer` из URI SQLAlchemy, asyncpg или Prisma), — ошибка при загрузке конфигурации; в сообщении назван только ключ. В env — JSON-объект: `MCP_DATABASE_CONNECT_OPTIONS='{"application_name": "mcp"}'`. `max_inactive_connection_lifetime` (секунды, по умолчанию 300) — через сколько простоя пул закрывает соединения сверх `pool_min_size`.

Опционально в `server`: `response_max_tokens` — предел ответа инструмента в токенах (по умолчанию `20000`, минимум `1000`). Ответ больше предела заменяется ошибкой с просьбой уточнить запрос.

#### 3. Переменные окружения

Имя переменной — `MCP_<СЕКЦИЯ>_<ПОЛЕ>`, где секция совпадает с секцией `config.json`: `MCP_SERVER_*`, `MCP_DATABASE_*`, `MCP_AUTH_*`, `MCP_FASTMCP_*` (см. [env.example](env.example)). Переменные без префикса секции (`MCP_PORT`, `DATABASE` и т.п.) не читаются.

```bash
export MCP_SERVER_HOST=0.0.0.0
export MCP_SERVER_PORT=8000
export MCP_SERVER_TRANSPORT=http
export MCP_DATABASE_HOST=localhost
export MCP_DATABASE_PORT=5432
export MCP_DATABASE_USER=user
export MCP_DATABASE_PASSWORD=password
export MCP_DATABASE_NAME=dbname
export MCP_DATABASE_ACCESS_MODE=full
export MCP_DATABASE_WRITE_MODE=false
export MCP_SERVER_RESPONSE_MAX_TOKENS=20000

uv run postgres-fastmcp
```

#### 4. Приоритет конфигурации

Порядок (от высшего к низшему):

1. Явно заданные параметры CLI
2. Файл `config.json` в текущей директории; CLI переопределяет в нём отдельные поля, а не секцию целиком
3. Переменные окружения и `.env`
4. Значения по умолчанию

Особенности `--database-uri`:

- Задаёт только то, что есть в URI: `host`, `port`, `user`, `password`, `name`, `sslmode` и `client_encoding` из query string, а остальные параметры query string (`target_session_attrs`, `options`, `connect_timeout`, `sslrootcert`, …) — как `connect_options`; словарь из URI заменяет `connect_options` из `config.json` целиком. Query string разбирается как в libpq: `+` остаётся плюсом, пустое значение (`application_name=`) сохраняется, при повторе ключа берётся последнее значение, параметр без `=` — ошибка. Остальные поля секции `database` (например `table_prefix`) берутся из `config.json`/env как обычно.
- Сам по себе **не** переключает сервер в режим только чтения: `write_mode` по-прежнему берётся из `config.json`/env (по умолчанию `false`). Чтобы принудительно оставить только чтение вместе с `--database-uri`, добавьте `--no-write-mode` — именно он отвечает за режим SQL. `--access-mode basic` — независимый параметр: он сужает видимость до схемы `public` и базовых инструментов, но не отключает запись сам по себе (`basic` + `write_mode=true` по-прежнему разрешает DML, см. таблицу ниже).
- Если в URI нет пароля, он берётся из `config.json` или переменных окружения — даже если host или user в URI отличаются от заданных там же.
- Если в URI не указан порт, подставляется `5432`; это значение перекрывает порт, заданный в `config.json` или env.

## Конфигурация

### Контроль доступа

Безопасность задаётся двумя независимыми параметрами:

#### Уровень доступа (`access_mode`)

Определяет доступ к схемам и набор доступных инструментов:

| access_mode | Схемы          | Инструменты | Описание |
| ----------- | -------------- | ----------- | -------- |
| `basic`     | Только `public` | Базовые (4) | Только схема public; опционально `table_prefix` — ограничение по префиксу имён таблиц |
| `full`      | Все схемы      | Все (9)     | Все схемы и расширенные инструменты (схемы, здоровье, топ запросов, анализ индексов) |

#### Режим записи (`write_mode`)

Определяет уровень выполнения SQL:

| write_mode | SQL-доступ                        | Описание |
| ---------- | --------------------------------- | -------- |
| `false`    | Только чтение (только SELECT)     | Разрешён только SELECT |
| `true`     | Чтение-запись (DML); DDL при access_mode=full | Разрешены INSERT/UPDATE/DELETE; DDL только при access_mode=full |

#### Матрица комбинаций

| access_mode | write_mode | Инструменты | SQL-доступ        | Схемы |
| -------- | ---------- | ----------- | ----------------- | ----- |
| `basic`  | `false`    | Базовые (4) | Только чтение     | `public` (опционально `table_prefix`) |
| `basic`  | `true`     | Базовые (4) | Чтение-запись    | `public` |
| `full`   | `false`    | Все (9)     | Только чтение     | Все |
| `full`   | `true`     | Все (9)     | Полный доступ (DDL) | Все |

**По умолчанию:** `access_mode=basic`, `write_mode=false` (максимально ограниченный режим).

**Для access_mode=basic опционально:** `table_prefix` ограничивает таблицы, представления и последовательности по префиксу имени: `list_objects` скрывает объекты без префикса, а `get_object_details` и `execute_sql` отказывают по ним. Расширения префиксом не ограничиваются. Для full игнорируется.

**Что закрыто в access_mode=basic для SQL агента:** системные отношения `pg_*` (в том числе `pg_catalog.*`, `pg_stats`, `pg_stat_activity` и представления расширений в `public`, например `pg_stat_statements`), функции интроспекции сервера и объектов (`current_setting`, `pg_get_functiondef`, `pg_relation_size`, `has_*_privilege`, `to_regclass` и др.), `SHOW` параметров вне короткого списка (`search_path`, `TimeZone`, `server_version` и т. п.); представления `information_schema` с секретами и исходниками — `user_mapping_options`, `user_mappings`, `foreign_server_options`, `foreign_data_wrapper_options`, `foreign_table_options`, `column_options`, `routines`, `views`, `triggers` (структуру объектов дают `list_objects`/`get_object_details`); опции `EXPLAIN` вне списка `FORMAT`, `VERBOSE`, `COSTS`, `SUMMARY`, `TIMING`, `BUFFERS`, `GENERIC_PLAN`, `MEMORY` (в том числе `SETTINGS`); типы, резолвящие имена объектов (`reg*` и `aclitem`, в том числе их массивы), в любой позиции — не только касты, но и списки колонок табличных функций (`json_to_record(...) AS x(a regclass)`) и аргументы `PREPARE`; имена типов и collation из других схем (`NULL::secret.accounts`, `COLLATE secret.coll`) и строковые типы системных отношений (`NULL::pg_authid`); операторы и методы `TABLESAMPLE` из других схем (`OPERATOR(secret.=)`, `TABLESAMPLE secret.m(1)`); `pg_typeof`/`pg_basetype`, `currval`/`lastval`; `hypopg_create_index` по таблицам вне `public` или без префикса. Гипотетические индексы в basic (`hypothetical_indexes` в `explain_query` и сам `hypopg_create_index`) допускают только простые столбцы таблиц `public` с нужным префиксом — без выражений, `WHERE`, opclass и `TABLESPACE`. Функции `ts_stat`/`ts_rewrite` закрыты во всех режимах. Основная граница доступа — права роли в БД; basic — защита в глубину поверх них. Статически basic закрыть не может всё: представление в `public` поверх таблиц другой схемы отдаёт данные этой схемы (закрывается настройкой `plan_check`, см. ниже), а `information_schema` показывает метаданные других схем в пределах прав роли (кроме закрытых представлений выше). Полный список принятых ограничений — `docs/superpowers/specs/2026-09-28-basic-confinement-design.md`, §6. Представления расширений в `public` с именами `pg_*` и `hypopg*` (`pg_stat_statements`, `hypopg_list_indexes` и т. п.) в basic закрыты. Для `hypothetical_indexes` в basic расширение hypopg должно быть установлено в `public`: basic выставляет `search_path = public` и не принимает функции с явной схемой.

#### Роль для basic

`access_mode=basic` — защита в глубину: валидатор SQL отклоняет обращения вне `public`, но настоящая граница — права роли, под которой сервер подключается к БД. Роли для basic достаточно `SELECT` на таблицы `public` (и DML на них при `write_mode=true`):

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

Про сам скрипт:

- цикл `DO $$ ... $$` выше проходит по `pg_tables` и грантует только таблицы; представления и материализованные представления с тем же префиксом (`pg_views`/`pg_matviews`) им не покрыты — на них `SELECT` нужно выдавать отдельно;
- `ALTER DEFAULT PRIVILEGES` действует только на объекты, которые создаёт **та роль, что его выполнила**; если таблицы с префиксом создаёт другая роль (например, `app_owner`), нужен `ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA public GRANT SELECT ON TABLES TO mcp_basic`;
- на PostgreSQL ≤14 у `PUBLIC` по умолчанию есть `CREATE` на схему `public` (с PG15 это уже не так по умолчанию); если это лишнее — дополнительно `REVOKE CREATE ON SCHEMA public FROM PUBLIC`.

Чего роли для basic не давать:

- членства в `pg_read_all_data`, `pg_read_all_settings`, `pg_read_all_stats`, `pg_monitor` (и других предопределённых ролях с доступом к данным или серверу) — проверка при старте (ниже) находит и косвенное членство: например, `pg_monitor` подразумевает `pg_read_all_settings` и `pg_read_all_stats`;
- `CREATEROLE`: на PostgreSQL ≤15 эта привилегия позволяет роли выдать себе членство в предопределённых ролях самостоятельно;
- прав на `pg_stat_statements`: расширение лучше ставить в отдельную схему, на которую у роли нет `USAGE`;
- `SELECT` на представления в `public` поверх таблиц других схем, если эти данные агенту не нужны: представление отдаёт данные схемы, над которой построено, каждому, кому выдан `SELECT` на само представление.

При старте с достижимым basic (потолок `basic`, свой `access_resolver` или `access_policy.enforced=true`) сервер в фоне проверяет права роли и пишет одну строку WARNING, если роль может больше, например:

```text
Database role 'app' has privileges beyond basic mode: BYPASSRLS; member of pg_read_all_data; USAGE on schemas: billing, secret; SELECT on 3 public tables without prefix 'app_'. In basic mode the SQL validator is then the only barrier; grant the role access to 'public' only (see README).
```

Сервер при этом стартует. Если БД на старте недоступна, проверка пропускается с одной строкой INFO `Basic role check skipped: ...`; сам пул при этом ещё отдельно логирует свои предупреждения о повторных попытках подключения (`psycopg_pool`, около 30 секунд) — это его штатное поведение, а не часть проверки. Прочая ошибка Postgres при самой проверке — WARNING `Basic role check failed: ...`; программная ошибка проверки (а не БД) — ERROR `Basic role check crashed` в лог, сервер тоже не останавливается.

Проверка и её строка в логе видны только при включённом логировании, то есть на транспорте HTTP: в `stdio` логи отключены (см. «Транспорты»), и фоновая проверка там не запускается. В `stdio` права роли можно проверить вручную теми же запросами (`src/postgres_fastmcp/postgres/catalog.py`, шаблоны `QUERY_ROLE_*`) либо один раз временно поднять сервер с `--transport http`.

#### Проверка по плану (`plan_check`)

`plan_check=true` (env `MCP_DATABASE_PLAN_CHECK=true`, по умолчанию `false`) — строгий режим basic: перед выполнением каждого оператора SQL агента сервер строит его план (`EXPLAIN (VERBOSE, FORMAT JSON)`, без выполнения) и отклоняет запрос, если план читает отношение вне `public` (или без `table_prefix`), системное отношение `pg_*`, табличную функцию схемы, отличной от `pg_catalog` и `public`, или функцию `pg_catalog` вне списка функций basic (так закрыты представления в `public` поверх `pg_settings`, `pg_file_settings`, `pg_hba_file_rules`, `pg_ls_dir(...)`, `pg_stat_get_activity(...)` и подобных). Ответ — ошибка `Access to relation 'secret.accounts' is not allowed in basic mode: the query plan reads it. ...`.

Проверка и выполнение идут в одной транзакции на одном соединении пула: сначала `SET LOCAL` таймаута и `search_path`, затем EXPLAIN каждого оператора, затем сам оператор. Разбор берёт `AccessShareLock` на представления и таблицы до конца транзакции, так что `CREATE OR REPLACE VIEW` между проверкой и выполнением невозможен. Отказ проверки откатывает транзакцию, оператор не выполняется.

- Закрывает представления, правила и встраиваемые SQL-функции поверх чужих схем, которые валидатор по тексту запроса не видит.
- Проверяет и выражения плана — `Output`, `Filter`, условия соединений и индексов, ключи сортировки и группировки, аргументы табличных функций и прочие выражения `EXPLAIN VERBOSE`. Отклоняются функции и операторы других схем (`secret.decrypt(c)`, `OPERATOR(secret.+)`), встроенные функции вне списка basic (`current_setting('app.jwt_secret')` в представлении), приведения к типам других схем (`::secret.t`) и, при `table_prefix`, строковый тип таблицы `public` без префикса (`json_populate_record(NULL::users, '{}')`). Функция без схемы вне списка basic проверяется по каталогу: функция `public` проходит, функция `pg_catalog` — нет. `nextval('app_t_id_seq'::regclass)` из `DEFAULT` serial/identity проверяется как отношение — последовательность `public` с префиксом. Ссылки на подпланы (`(SubPlan 1)`, `(InitPlan 1).col1`, `$0`) пропускаются: подпланы проверяются как узлы плана. Выражение, которое не удаётся разобрать, — `PlanUnverifiableError`.
- Отклоняет и запросы к `information_schema`: её представления читают `pg_catalog`.
- **Закрыт по умолчанию (fail closed):** узел плана, который не называет, что читает, — тоже отказ, с ошибкой `PlanUnverifiableError`: join или агрегат, которые `postgres_fdw` целиком пересчитал на удалённом сервере (`Foreign Scan` без `Relation Name`), любой `Custom Scan` без отношения. Вызовы внутри табличных функций во `FROM` проверяются по тексту вызова (`Function Call` в плане): вложенные в аргументы (`unnest(secret.get_secrets())`, `unnest(pg_ls_dir('.'))` отклоняются) и все функции `ROWS FROM` из нескольких функций (так Postgres переписывает и `unnest(a, b)`). Проходят только разрешённые встроенные функции basic и функции, записанные со схемой `public`; имя без схемы допустимо, только если оно в списке функций basic. Перепишите запрос на прямое обращение к разрешённым отношениям — такое проверяется штатно.
- Многооператорная строка: `PlanGuard` строит план каждого оператора до выполнения первого (документированное ограничение), поэтому поздний оператор, зависящий от результата раннего (`CREATE EXTENSION hypopg; SELECT hypopg_create_index(...)`), может быть отклонён ошибкой планирования Postgres, хотя без `plan_check` строка выполнилась бы оператор за оператором.
- Цена — по одному лишнему запросу к БД на каждый оператор строки (EXPLAIN) и один на `SET LOCAL`, в том же соединении; отдельного обращения к пулу нет. Клиентский таймаут (`safe_sql_timeout` + клиентская страховка) покрывает проверку и выполнение одним бюджетом; `statement_timeout` действует на каждый запрос транзакции отдельно. Для `access_mode=full` настройка игнорируется. Проверка выражений спрашивает каталог в той же транзакции только при необходимости: имена типов `pg_catalog` — один раз на процесс, функции без схемы вне списка basic и строковые типы без префикса — не больше чем по одному запросу на оператор.

**Что `plan_check` не закрывает.** План показывает, какие отношения и табличные функции читаются, но не что происходит внутри них:

- материализованные представления в `public` поверх других схем (данные в них уже скопированы);
- сторонние таблицы (foreign tables) в `public`, в том числе `postgres_fdw`, смотрящий в ту же БД;
- триггеры на таблицах, которые меняет DML агента;
- невстраиваемые функции (`VOLATILE`, `SECURITY DEFINER`, PL/pgSQL) и тела функций `public`, вызванных во `FROM`, — их тело плану непрозрачно; блокировка на функцию не держится до конца транзакции (в отличие от `AccessShareLock` на отношения), поэтому встраиваемую SQL-функцию можно конкурентно пересоздать (`CREATE OR REPLACE FUNCTION`) между EXPLAIN и оператором;
- функции, которых план не называет: неявные приведения и приведения `CREATE CAST … WITH FUNCTION`, операторы за `IS DISTINCT FROM`, `NULLIF` и `IN`; схема оператора `USING` в ключе сортировки, collation и метода `TABLESAMPLE` (план печатает их без схемы);
- на PG 15/16 `EXPLAIN` печатает подплан `IN`/`ANY`/сравнения строк только как `(SubPlan N)`/`(hashed SubPlan N)` — само левое выражение проверки (например, `current_setting('x') = ANY (SELECT …)` внутри представления) не видно; на PG 17 оно печатается (`(ANY (expr = (hashed SubPlan 1).col1))`) и проверяется;
- выражения `LIMIT`/`OFFSET` и смещения оконного фрейма — `EXPLAIN` их не печатает ни на одной версии (15–17), поэтому функции в них не проверяются;
- выражения времени записи, которых нет в плане: генерируемые столбцы, `CHECK` таблицы и домена, `WITH CHECK OPTION` представлений, выражения индексов (тот же класс, что и триггеры);
- известный ложноположительный отказ в безопасную сторону: функция `public`, перегружающая имя `pg_catalog` вне списка basic (например, `public.current_setting(int)`), печатается без схемы и отклоняется;
- выражение плана с подзапросом, ссылкой на таблицу, комментарием или доллар-квотированной строкой считается неразборчивым (fail closed) — в выводе ruleutils такого не бывает.

**Что `plan_check` отклоняет, хотя это легитимно:**

- секции и дочерние таблицы наследования в другой схеме или без префикса (например, чанки TimescaleDB в `_timescaledb_internal`);
- политики RLS с подзапросами к другим схемам или с функциями вне списка basic (`current_setting('app.tenant')`): выражения политик попадают в `Filter` плана;
- `DEFAULT` столбцов с функциями вне списка basic в `INSERT` (кроме `nextval` последовательности `public` с префиксом);
- функции `public`, одноимённые встроенным функциям `pg_catalog` (перегрузки): без схемы их не отличить от встроенных;
- временные таблицы, заслоняющие имя из `public` (схема `pg_temp_N`);
- `Custom Scan` без отношения (например, распределённые запросы Citus);
- правила `DO INSTEAD NOTHING`/`NOTIFY` — у оператора нет плана (`PlanUnverifiableError`);
- функции пользователя из `public`, вложенные в аргументы функции во `FROM` (`my_srf(my_helper(1))`) или перечисленные в `ROWS FROM` из нескольких функций / `unnest(a, b)`: при `search_path = public` план печатает их без схемы, и их не отличить от встроенных функций вне списка basic;
- подзапросы в аргументах функций во `FROM` на PG 17+ (в плане — `(InitPlan 1).col1`, `(SubPlan 1)`): такой текст вызова не разбирается (`PlanUnverifiableError`).

Ошибка планирования Postgres (например, отношения нет) цитирует разобранный заново текст оператора со служебным префиксом EXPLAIN, а не исходный SQL агента.

По умолчанию выключено: представление в `public` поверх другой схемы обычно создаёт DBA намеренно и выдаёт роли через `GRANT`.

### Транспорты

Поддерживаются транспорты **http** и **stdio**.

#### HTTP

Запуск как HTTP-приложение для Cursor, веб-клиентов или production:

```bash
uv run postgres-fastmcp \
  --database-uri "postgresql://user:password@localhost:5432/dbname" \
  --transport http \
  --port 8000
```

Сервер доступен по адресу `http://localhost:8000/mcp`; путь задаёт `server.endpoint` (`MCP_SERVER_ENDPOINT`, ведущий `/` добавляется сам).

#### Проверка здоровья: `GET /health`

`http://localhost:8000/health` — для балансировщика и проб Kubernetes. Маршрут всегда в корне, независимо от `server.endpoint`, и не требует токена даже при включённой аутентификации. Сервер открывает отдельное соединение с базой (пул инструментов не трогается) и выполняет `SELECT 1`:

| Ответ | Когда |
| --- | --- |
| `200 {"status": "ok"}` | база ответила не дольше чем за 2 секунды |
| `503 {"status": "degraded", "error": "database unavailable"}` | ошибка подключения (адрес, пользователь, пароль в ответе не раскрываются) |
| `503 {"status": "degraded", "error": "database did not answer within 2 s"}` | база не ответила за 2 секунды |

Подробности неудачи (замаскированный пароль) уходят только в лог сервера на уровне WARNING, не чаще раза в секунду: результат пробы кэшируется на ~1 секунду и общий для всех одновременных запросов, чтобы поток анонимных `GET /health` не открывал по соединению на запрос. Если нужно увидеть в логе, к какому хосту/порту/базе реально подключается драйвер, поднимите уровень логгера `psycopg` до `DEBUG` — тогда в лог попадают сообщения libpq с хостом, портом, пользователем и именем базы (без пароля).

Отключается `server.health_endpoint_enabled=false` (`MCP_SERVER_HEALTH_ENDPOINT_ENABLED=false`), тогда `/health` отвечает `404`. В `stdio` маршрута нет.

#### STDIO

Для настольных MCP-клиентов (например Claude Desktop) или интеграции через процесс:

```bash
uv run postgres-fastmcp \
  --database-uri "postgresql://user:password@localhost:5432/dbname" \
  --transport stdio
```

### Справочник конфигурации

- **CLI:** `--database-uri`, `--transport`, `--host`, `--port`, `--access-mode`, `--write-mode` / `--no-write-mode`. Вывод версии: `--version`. Каждая заданная опция переопределяет `config.json` и переменные окружения на этот запуск.
- **config.json:** Должен содержать `server`, `fastmcp` и `database` (см. Быстрый старт). Загружается из текущей директории.
- **Переменные окружения / .env:** `MCP_<СЕКЦИЯ>_<ПОЛЕ>`: `MCP_SERVER_*`, `MCP_DATABASE_*`, `MCP_AUTH_*`, `MCP_FASTMCP_*` (см. [env.example](env.example)).
- **Итоговая конфигурация в логе:** на старте сервер пишет одну строку INFO `Database ceiling: access_mode=..., write_mode=..., table_prefix=...; auth=...` (класс auth-провайдера или `none`), а для HTTP ещё `Serving MCP over HTTP on host:port/endpoint`; секретов в них нет.

### Подключение MCP-клиентов

#### Cursor (HTTP)

В файле `~/.cursor/mcp.json`:

```json
{
    "mcpServers": {
        "postgres": {
            "type": "sse",
            "url": "http://localhost:8000/mcp"
        }
    }
}
```

#### Claude Desktop (STDIO)

В файле `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) или `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
    "mcpServers": {
        "postgres": {
            "command": "uv",
            "args": ["run", "postgres-fastmcp", "--transport", "stdio", "--database-uri", "postgresql://user:pass@localhost:5432/dbname"]
        }
    }
}
```

Либо задать подключение переменными окружения вместо `--database-uri`:

```json
{
    "mcpServers": {
        "postgres": {
            "command": "uv",
            "args": ["run", "postgres-fastmcp", "--transport", "stdio"],
            "env": {
                "MCP_DATABASE_HOST": "localhost",
                "MCP_DATABASE_PORT": "5432",
                "MCP_DATABASE_USER": "user",
                "MCP_DATABASE_PASSWORD": "pass",
                "MCP_DATABASE_NAME": "dbname",
                "MCP_DATABASE_ACCESS_MODE": "basic",
                "MCP_DATABASE_WRITE_MODE": "false"
            }
        }
    }
}
```

## Аутентификация

Аутентификация защищает HTTP-транспорт: сервер принимает запрос, только если клиент прислал действующий токен. В `stdio` клиент сам запускает сервер как процесс, токенов там нет и аутентификация не применяется. Библиотечная функция `create_server(settings)` всегда строит провайдер из блока `auth`, независимо от `settings.server.transport` — это только конфигурационное значение, а не гарантия того, каким транспортом вызывающий код реально поднимет сервер. Пропускает сборку провайдера (и обращения к IdP, и файлы на диске для `oidc`) только сам CLI этого пакета, и только когда он в самом деле запускает `stdio`.

Коротко о терминах:

- **Токен** — строка, которую клиент отправляет в каждом запросе в заголовке `Authorization: Bearer <токен>`.
- **Claim** — именованное поле внутри токена: `sub` (кто), `iss` (кто выдал), `groups` (группы пользователя) и т.д.
- **Scope** — claim `scope`: список разрешений через пробел, например `pg:read pg:write`.
- **Группы/роли** — claim со списком групп или ролей пользователя в IdP (`groups`, `realm_access.roles` в Keycloak).
- **IdP** — сервис входа, который выдаёт токены: Keycloak, Auth0, Okta, корпоративный SSO.

### Режимы

Режим задаётся `auth.mode` в `config.json` или переменной `MCP_AUTH_MODE`:

| Режим | Когда использовать | Что проверяет токен |
| --- | --- | --- |
| `none` (по умолчанию) | `stdio`, доверенный localhost | ничего, токен не нужен |
| `static` | сервисные клиенты, тестовые стенды, небольшая команда | строка токена из списка в конфиге |
| `jwt` | токены выдаёт внешний IdP (machine-to-machine, CI) | подпись, `iss`, `aud`, срок действия |
| `oidc` | люди входят через корпоративный SSO прямо из MCP-клиента | вход в браузере через IdP, затем токен сервера |

Все настройки лежат в блоке `auth` (`config.json`) или в переменных `MCP_AUTH_*`; вложенные поля в переменных разделяются `__` (`MCP_AUTH_ACCESS_POLICY__ENFORCED`). Поля чужого режима игнорируются. Некорректный блок `auth` останавливает запуск с `ValidationError`, в которой названо поле; строки токенов и секреты в текст ошибки не попадают.

`required_scopes` (`MCP_AUTH_REQUIRED_SCOPES='["mcp"]'`) действует во всех режимах, кроме `none`: токен без любого из этих скоупов отклоняется целиком. Поля-списки и поля-словари в переменных окружения задаются JSON-строкой, как в этом примере; то же самое для `MCP_AUTH_TOKENS` (см. ниже) — и если JSON в `MCP_AUTH_TOKENS` некорректен, сервер не запустится ни в одном режиме, даже не в `static`.

#### `static`

```json
"auth": {
  "mode": "static",
  "tokens": {
    "<длинная случайная строка>": {"client_id": "ci-bot", "scopes": ["pg:write"]},
    "<другая строка>": {"client_id": "alice", "claims": {"groups": ["dba"]}}
  }
}
```

- `client_id` — имя клиента в логах; `scopes` — его скоупы; `claims` — любые дополнительные поля токена (например `groups` для политики прав).
- Строка токена — секрет: не храните её в git. Для Kubernetes положите словарь той же формы в secret и укажите файл: `MCP_AUTH_TOKENS_FILE=/run/secrets/mcp-tokens.json`. Токены из `tokens` и из `tokens_file` объединяются; при совпадении строки побеждает `tokens`.
- Через окружение: `MCP_AUTH_TOKENS='{"<строка>": {"client_id": "ci-bot", "scopes": ["pg:write"]}}'`.
- `MCP_AUTH_TOKENS` задаётся только JSON-строкой, как выше. Вложенная форма `MCP_AUTH_TOKENS__<строка_токена>__client_id=...` тоже работает, но приводит строку токена к нижнему регистру — для случайной строки со смешанным регистром сервер её не узнает.

#### `jwt`

| Поле | Обязательно | Назначение |
| --- | --- | --- |
| `jwt_jwks_uri` или `jwt_public_key` | ровно одно из двух | где взять ключ проверки подписи: URL набора ключей IdP (JWKS) или PEM публичного ключа |
| `jwt_issuer` | да | ожидаемый `iss` токена |
| `jwt_audience` | да | ожидаемый `aud`: строка или список |
| `jwt_algorithm` | нет | алгоритм подписи, по умолчанию `RS256` |

Чтобы положить в `jwt_public_key` общий HS-секрет (а не PEM), нужно явно задать `MCP_AUTH_JWT_ALGORITHM=HS256` — по умолчанию сервер проверяет подпись как RS256, и любой токен, подписанный HS-секретом, будет отклонён с `401`.

```bash
MCP_AUTH_MODE=jwt
MCP_AUTH_JWT_JWKS_URI=https://sso.example.com/realms/main/protocol/openid-connect/certs
MCP_AUTH_JWT_ISSUER=https://sso.example.com/realms/main
MCP_AUTH_JWT_AUDIENCE=postgres-mcp
```

#### `oidc`

| Поле | Обязательно | Назначение |
| --- | --- | --- |
| `oidc_config_url` | да | `https://<IdP>/.well-known/openid-configuration` |
| `oidc_client_id`, `oidc_client_secret` | да | клиент, заведённый для этого сервера в IdP |
| `base_url` | да | публичный адрес сервера, например `https://mcp.example.com` |
| `oidc_audience` | нет | ожидаемый `aud` токена IdP |

Как проходит вход:

1. MCP-клиент подключается без токена и получает `401` со ссылкой на метаданные сервера.
2. Клиент регистрируется на сервере и открывает браузер на `<base_url>/authorize`.
3. Сервер показывает страницу подтверждения доступа и перенаправляет пользователя в IdP, пользователь входит.
4. IdP возвращает пользователя на `<base_url>/auth/callback` — этот адрес нужно добавить в разрешённые redirect URI клиента в IdP.
5. Сервер выдаёт MCP-клиенту свой токен и на каждом запросе проверяет токен IdP за ним.

Сервер читает `oidc_config_url` при запуске: если IdP недоступен, сервер не стартует. Исключение — CLI этого пакета при реальном запуске с `--transport stdio` (или `MCP_SERVER_TRANSPORT=stdio`): он знает, каким транспортом запускается, и в этом случае сознательно не строит провайдер и не обращается к IdP, поскольку в `stdio` аутентификация всё равно не действует. Библиотечный `create_server()` от этого не зависит и всегда пытается построить провайдер (см. начало раздела). Регистрации MCP-клиентов хранятся зашифрованными в каталоге данных FastMCP (`FASTMCP_HOME`, по умолчанию `~/.local/share/fastmcp`); в контейнере вынесите его в volume, иначе после перезапуска клиентам придётся войти заново. `FASTMCP_HOME` должен указывать на доступный для записи volume — с read-only корневой файловой системой контейнера `oidc` не заработает.

### Подключение клиентов с токеном

Cursor (`~/.cursor/mcp.json`):

```json
{
    "mcpServers": {
        "postgres": {
            "url": "https://mcp.example.com/mcp",
            "headers": {"Authorization": "Bearer <токен>"}
        }
    }
}
```

Claude Code:

```bash
claude mcp add --transport http postgres https://mcp.example.com/mcp --header "Authorization: Bearer <токен>"
```

В режиме `oidc` заголовок не нужен: добавьте сервер без `--header`, клиент сам откроет браузер для входа (в Claude Code — команда `/mcp`).

### Ответы при отказе

| Ситуация | Ответ |
| --- | --- |
| Нет токена или токен неизвестен, подделан, просрочен | HTTP `401` с заголовком `WWW-Authenticate: Bearer` |
| У токена нет скоупа из `required_scopes` | HTTP `401`: верификаторы FastMCP отклоняют такой токен целиком |
| full-инструмент при эффективном режиме `basic` | инструмента нет в списке, вызов даёт `Unknown tool` |
| Запись при эффективном режиме только чтения | ошибка `... allowed in read-only mode` |

### Предупреждения на старте

Сервер пишет WARNING, если:

- транспорт HTTP, аутентификации нет, а `host` не `127.0.0.1`/`localhost`/`::1` — любой, кто достучится до порта, получит все права из `database`;
- транспорт `stdio`, а аутентификация включена — в `stdio` она не действует;
- `access_policy.enforced=true`, а аутентификации нет — без токена политика ничего не сужает;
- basic достижим (и проверка роли включена, см. «Роль для basic»), а роль БД может больше, чем таблицы `public` (суперпользователь, `BYPASSRLS`, `CREATEROLE`, предопределённые роли вроде `pg_read_all_data`, `USAGE` на другие схемы, таблицы `public` без `table_prefix`) — см. «Роль для basic».

## Права по claim

Серверный **потолок** прав задают `access_mode` и `write_mode` в `database`. Политика `auth.access_policy` сужает его для каждого запроса по значениям одного claim токена. Токен никогда не даёт больше потолка.

| Поле | По умолчанию | Назначение |
| --- | --- | --- |
| `enforced` | `false` | включить сужение; при `false` каждый запрос получает потолок |
| `claim` | `scope` | откуда брать значения: `scope`, имя claim (`groups`) или путь через точку (`realm_access.roles`) |
| `write_values` | `["pg:write"]` | любое из этих значений разрешает запись (если её разрешает потолок) |
| `full_values` | `["pg:full"]` | любое из этих значений даёт режим `full` (если потолок `full`) |

Если путь через точку и в токене одновременно есть буквальный ключ верхнего уровня с тем же именем целиком (например ключ `"realm_access.roles"`, а не вложенный объект `realm_access: {roles: [...]}`), побеждает этот буквальный ключ — так claim'ы Auth0 с URL-именами (`https://example.com/roles`) не разбираются по точкам ошибочно.

Правила:

- Сравнение значений — точное и с учётом регистра: `pg:full` не совпадёт с `PG:FULL`. Строковый claim делится по пробелам (`"Domain Admins"` даёт два значения — `Domain` и `Admins`); из claim-списка берутся только строковые элементы, остальные игнорируются; нет claim — значений нет.
- Без значения из `full_values` запрос работает в `basic`: только схема `public`, четыре инструмента; full-инструменты для этого токена скрыты.
- Без значения из `write_values` запрос только читает.
- Без токена (`stdio`, `auth.mode=none`) запрос получает потолок.

Пример со скоупами (потолок `full` + `write_mode=true`):

```bash
MCP_AUTH_ACCESS_POLICY__ENFORCED=true
```

| Скоупы токена | Права запроса |
| --- | --- |
| нет | `basic`, только чтение |
| `pg:write` | `basic`, запись в `public` (DML) |
| `pg:full` | `full`, только чтение |
| `pg:full pg:write` | `full`, запись и DDL |

Пример с группами из IdP:

```bash
MCP_AUTH_MODE=jwt
MCP_AUTH_JWT_JWKS_URI=https://sso.example.com/realms/main/protocol/openid-connect/certs
MCP_AUTH_JWT_ISSUER=https://sso.example.com/realms/main
MCP_AUTH_JWT_AUDIENCE=postgres-mcp
MCP_AUTH_ACCESS_POLICY__ENFORCED=true
MCP_AUTH_ACCESS_POLICY__CLAIM=groups
MCP_AUTH_ACCESS_POLICY__WRITE_VALUES='["dba","backend-writers"]'
MCP_AUTH_ACCESS_POLICY__FULL_VALUES='["dba"]'
```

То же со статическими токенами:

```json
"auth": {
  "mode": "static",
  "access_policy": {"enforced": true, "claim": "groups", "full_values": ["dba"], "write_values": ["dba"]},
  "tokens": {"s3cr3t-alice": {"client_id": "alice", "claims": {"groups": ["dba"]}}}
}
```

## Технические детали

### FastMCP

Форк построен на [FastMCP](https://gofastmcp.com/), который обеспечивает:

- Асинхронное выполнение и высокую производительность
- Транспорты HTTP и stdio
- Управление жизненным циклом и внедрение зависимостей для инструментов

### Управление жизненным циклом

Пулом соединений владеет `PostgresProvider`, его жизненный цикл привязан к lifespan сервера FastMCP:

- Пул открывается при первом запросе к БД; если достижим basic — сразу при старте, фоновой проверкой прав роли (она не задерживает старт и отменяется при остановке)
- Гипотетические индексы hypopg (`explain_query` с `hypothetical_indexes`, `hypopg_create_index` в `execute_sql`) сбрасываются, когда соединение возвращается в пул: следующий запрос на том же соединении их не видит
- Индексы, скрытые `hypopg_hide_index` (full), снова видны планировщику, когда соединение возвращается в пул: reset-хук вызывает `hypopg_unhide_all_indexes()` (hypopg 1.4.0+; при ошибке соединение выбрасывается из пула)
- Каждое открытое соединение при возврате в пул сбрасывается (`DISCARD ALL`), чтобы состояние сессии не переживало запрос — заодно сбрасываются и закэшированные планы запросов; цена: один лишний запрос к БД на каждый возврат соединения
- Соединения закрываются при остановке сервера
- Обработка сигналов (SIGINT, SIGTERM)

### Безопасное выполнение SQL

В проекте используется многоуровневая защита при выполнении SQL:

1. **Разбор SQL** — библиотека `pglast` анализирует SQL перед выполнением; разрешён только allowlist типов операторов, узлов AST и функций
2. **Транзакции только для чтения** — в режимах только чтение используются read-only транзакции PostgreSQL
3. **Проверки COMMIT/ROLLBACK** — блокируются попытки обойти режим только чтение
4. **Таймауты** — `safe_sql_timeout` выставляется как `statement_timeout` внутри транзакции, запрос отменяет сам PostgreSQL. Таймаут самого тула выводится так, чтобы быть длиннее `statement_timeout` с клиентской страховкой, поэтому первым срабатывает именно PostgreSQL. При включённом `plan_check` (см. «Проверка по плану» выше) в этот же бюджет входит и EXPLAIN-проверка плана, выполняемая в той же транзакции перед оператором
5. **Расширения** — `CREATE EXTENSION` допускается только для `hypopg` и `pg_stat_statements` и только при `write_mode=true`
6. **Проверка по плану** — опциональная (`plan_check`, только для `access_mode=basic`, по умолчанию выключена): строит план каждого оператора и отклоняет то, что видит только в плане, но не в тексте запроса. Подробности и её ограничения — «Проверка по плану» выше
7. **Гигиена сессии пула** — соединение пула возвращается без состояния сессии (`DISCARD ALL`); каждая транзакция агента открывается с `standard_conforming_strings = on`, независимо от настройки сервера или роли; автоподготовка запросов psycopg отключена (`prepare_threshold=None`), чтобы её клиентский кэш не ссылался на запросы, снятые `DISCARD ALL`

## MCP API

Функциональность сервера доступна через [инструменты MCP](https://modelcontextprotocol.io/docs/concepts/tools).

### Доступные инструменты

| Инструмент              | Описание |
| ----------------------- | -------- |
| `list_schemas`          | Список всех схем БД в экземпляре PostgreSQL |
| `list_objects`          | Список объектов БД (таблицы, представления, последовательности, расширения) в указанной схеме |
| `get_object_details`   | Информация об объекте БД: столбцы, ограничения, индексы таблицы и т.п. |
| `execute_sql`           | Выполнение SQL с ограничениями только чтение при write_mode=false |
| `explain_query`         | План выполнения запроса; поддерживаются гипотетические индексы для симуляции |
| `get_top_queries`      | Самые медленные или ресурсоёмкие запросы из `pg_stat_statements`, не больше `limit` (до 100) |
| `analyze_workload_indexes` | Анализ нагрузки и рекомендации оптимальных индексов |
| `analyze_query_indexes`    | Анализ списка запросов (до 10) и рекомендации индексов |
| `analyze_db_health`    | Проверка здоровья БД: буферный кэш, соединения, ограничения, индексы (дубликаты/неиспользуемые/невалидные), последовательности, vacuum |

### Формат ответа и бюджет

- `execute_sql`, `list_schemas`, `list_objects`, `get_object_details` и `get_top_queries` принимают `output`: `table` (по умолчанию) — Markdown-таблица, в которой колонки перечислены один раз, и строка `N rows.`; `json` — `{"rows": [...], "row_count": N}` в `structuredContent` и тот же JSON текстом. `get_object_details` в `json` отдаёт поля объекта и разделы (`columns`, `constraints`, `indexes`) одним объектом.
- Оператор без результирующего набора (`INSERT`/`UPDATE`/`DELETE`/`MERGE` без `RETURNING`, DDL) `execute_sql` описывает тегом команды PostgreSQL: в `table` — одна строка `UPDATE 3: 3 rows affected.` (для DDL — `CREATE TABLE: done.`), в `json` — `{"rows": [], "row_count": 0, "status": "UPDATE 3", "affected_rows": 3}`; у DDL `affected_rows` равно `null`. Тег — это то, что вернул PostgreSQL: для `CREATE TABLE ... AS` это `SELECT n`; если в запросе несколько операторов, показывается тег последнего.
- `get_object_details` проверяет существование объекта по каталогу: отсутствующий объект — ошибка `Object not found ...`, пустая таблица возвращает заголовок без разделов. Тип важен: представление, запрошенное с `object_type="table"` (по умолчанию), даёт «не найдено» с подсказкой повторить с `object_type="view"` (и наоборот). Таблица, на которую у роли нет прав, тоже считается ненайденной — как и в `list_objects`.
- Ответ инструмента больше `response_max_tokens` (переменная `MCP_SERVER_RESPONSE_MAX_TOKENS`, по умолчанию 20000) заменяется ошибкой `Response is too large ... Refine the request`: агенту нужно добавить `WHERE`/`LIMIT`, выбрать меньше колонок или агрегировать. Размер оценивается как байты текста / 3. Если инструмент мог записать данные (аннотация `readOnlyHint=false`, то есть `execute_sql` при `write_mode=true`), текст другой и не утверждает, что запись точно произошла — это мог быть и обычный `SELECT`: `... If the statement modified data, its changes are already applied — do not re-run it; query the affected rows with a narrower SELECT instead. Otherwise refine the request: ...`.
- Ввод нормализуется: `object_type` понимает `Tables`, `VIEW`, `sequences`; `health_type` — список или строку через запятую в любом регистре; `sort_by` — синонимы `total`, `mean`, `avg`, `resource`; `limit` больше 100 урезается до 100 и действует для всех `sort_by`, включая `resources`. Неверное значение даёт ошибку, в которой всегда перечислены допустимые значения; если есть похожее, добавляется подсказка `Did you mean ...?`.

### Ограничения по доступу

- **access_mode=basic**: только базовые инструменты (`list_objects`, `get_object_details`, `explain_query`, `execute_sql`); опционально `table_prefix` для ограничения набора таблиц
- **access_mode=full**: все инструменты (базовые + `list_schemas`, `analyze_workload_indexes`, `analyze_query_indexes`, `analyze_db_health`, `get_top_queries`)
- **write_mode=false**: разрешён только SELECT
- **write_mode=true** при access_mode=basic: в схеме `public` разрешены INSERT/UPDATE/DELETE (изменения коммитятся), DDL отклоняется
- **write_mode=true** при access_mode=full: без ограничений, включая DDL

## Установка расширений PostgreSQL (опционально)

Для подбора индексов и полного анализа производительности в БД нужно установить расширения `pg_stat_statements` и `hypopg`.

### Установка расширений

```sql
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS hypopg;
```

**Важно:** для `pg_stat_statements` необходимо добавить его в `shared_preload_libraries` в конфигурации PostgreSQL и перезапустить сервер.

Скрытие индексов (`hypopg_hide_index`, только full) требует hypopg 1.4.0 или новее.

### Облачные провайдеры

Если PostgreSQL работает в управляемом сервисе (AWS RDS, Azure SQL, Google Cloud SQL), расширения `pg_stat_statements` и `hypopg` обычно уже доступны. Достаточно выполнить `CREATE EXTENSION` от имени роли с достаточными правами.

### Собственный экземпляр Postgres

При самостоятельном управлении Postgres может потребоваться:

- Перед загрузкой `pg_stat_statements` — указать его в `shared_preload_libraries` в конфиге Postgres
- Расширение `hypopg` может потребовать установки на уровне системы (например, через пакетный менеджер), так как не всегда поставляется с Postgres

## Примеры конфигурации

### Пример 1: Production (только чтение, access_mode=full)

`config.json` для одной production-БД с полным набором инструментов и SQL только на чтение:

```json
{
    "server": { "host": "0.0.0.0", "port": 8000, "transport": "http" },
    "fastmcp": { "server_name": "postgres-fastmcp" },
    "database": {
        "host": "prod-server",
        "port": 5432,
        "user": "user",
        "password": "password",
        "name": "production",
        "access_mode": "full",
        "write_mode": false
    }
}
```

### Пример 2: access_mode=basic с префиксом таблиц

Ограничение схемой `public` и объектами, имена которых начинаются с `app_`:

```json
{
    "server": { "transport": "http", "port": 8000 },
    "database": {
        "host": "localhost",
        "port": 5432,
        "user": "appuser",
        "password": "secret",
        "name": "mydb",
        "access_mode": "basic",
        "write_mode": false,
        "table_prefix": "app_"
    }
}
```

### Пример 3: STDIO для Claude Desktop

Запуск с одной БД через CLI:

```bash
uv run postgres-fastmcp --transport stdio --database-uri "postgresql://user:pass@localhost:5432/dbname" --access-mode basic
```

### Пример 4: Разработка (чтение-запись, access_mode=full)

Полный набор инструментов и разрешён DML/DDL:

```json
{
    "server": { "host": "127.0.0.1", "port": 8000, "transport": "http" },
    "database": {
        "host": "localhost",
        "port": 5432,
        "user": "dev",
        "password": "dev",
        "name": "development",
        "access_mode": "full",
        "write_mode": true
    }
}
```

## Примеры запросов к агенту

### Проверка здоровья БД

Спросите у ИИ-агента:
> Проверь здоровье моей базы данных и укажи возможные проблемы.

### Анализ медленных запросов

> Какие запросы в моей БД самые медленные и как их ускорить?

### Рекомендации по производительности

> Приложение тормозит. Как его ускорить?

### Рекомендации по индексам

> Проанализируй нагрузку на БД и предложи индексы для улучшения производительности.

### Оптимизация конкретного запроса

> Помоги оптимизировать запрос: SELECT * FROM orders JOIN customers ON orders.customer_id = customers.id WHERE orders.created_at > '2023-01-01';

## Использование как библиотека

Публичное API пакета:

```python
from postgres_fastmcp import (
    PostgresProvider,  # источник девяти инструментов одной базы
    DatabaseConfig,  # подключение и серверный потолок прав (access_mode, write_mode)
    AccessPolicy,  # политика сужения прав по claim токена
    EffectiveAccess,  # права одного запроса
    AccessResolver,  # тип резолвера: токен -> EffectiveAccess
    full_access_check,  # проверка «эффективный режим full» для своих компонентов
    ResponseBudgetMiddleware,  # бюджет ответа тула в токенах
    Settings,
    create_server,
)
```

`Middleware`, `LocalProvider` и прочие классы FastMCP импортируйте из `fastmcp`.

### Свой сервер: `PostgresProvider`

`DatabaseConfig` берёт только то, что передано в конструктор: переменные `MCP_DATABASE_*` и файл `.env` он не читает, поэтому окружение хоста не может поднять потолок прав. Не переданные `access_mode` и `write_mode` означают `basic` без записи; неизвестное поле (например устаревшее `role`) — ошибка `ValidationError`. Если конфигурация базы должна приходить из окружения, как у CLI, возьмите `Settings().database` или `DatabaseSettings()` из `postgres_fastmcp.app.config.database`.

```python
from fastmcp import FastMCP
from postgres_fastmcp import DatabaseConfig, PostgresProvider

mcp = FastMCP("my-app")
mcp.add_provider(
    PostgresProvider(
        DatabaseConfig(
            host="db", port=5432, user="u", password="p", name="orders", access_mode="basic", write_mode=False
        )
    )
)
mcp.add_provider(
    PostgresProvider(
        DatabaseConfig(host="db", user="u", password="p", name="analytics", access_mode="full", write_mode=False)
    ),
    namespace="analytics",  # инструменты analytics_execute_sql, analytics_list_schemas, ...
)
mcp.run(transport="http", host="0.0.0.0", port=8000)
```

- Провайдер владеет пулом соединений: пул открывается при первом запросе и закрывается при остановке сервера.
- Видимость инструментов задаёт `access_mode` из `DatabaseConfig`: в `basic` доступны четыре инструмента, в `full` — девять.
- `access_policy=AccessPolicy(enforced=True, ...)` сужает права запроса по claim токена, как описано в разделе «Права по claim»; токен даёт `AuthProvider` вашего сервера. `access_resolver` — функция `AccessToken | None -> EffectiveAccess` вместо политики, если нужна своя логика; она приоритетнее `access_policy`. Результат обоих никогда не превышает потолок из `DatabaseConfig`. Без политики и резолвера права запроса равны потолку.
- `/health` на своём сервере не появляется сам: `create_server` добавляет его через `custom_route`. Для своего маршрута вызовите `await provider.ping()` — `SELECT 1` на отдельном соединении, при недоступной базе бросает ошибку psycopg (её текст может содержать строку подключения — не отдавайте его наружу без маскировки); таймаут задайте сами, например `asyncio.timeout(2)`.
- `create_server` подключает бюджет ответа автоматически. На своём сервере добавьте его сами первым middleware: `mcp.add_middleware(ResponseBudgetMiddleware(20000))`, импорт — `from postgres_fastmcp import ResponseBudgetMiddleware`.

### Готовый сервер: `create_server`

```python
from postgres_fastmcp import Settings, create_server

server = create_server(
    Settings(),
    auth=None,  # None: провайдер из settings.auth; или любой AuthProvider из fastmcp.server.auth
    access_resolver=None,  # свой резолвер прав для PostgresProvider
    extra_providers=[],  # свои источники tools/resources/prompts
    extra_middleware=[],  # добавляются после встроенных middleware
)
server.run(transport="http", host="0.0.0.0", port=8000)
```

Замечания:
- `auth=None` означает «взять из `settings.auth`» (раздел «Аутентификация»); при `mode=none` аутентификации нет, что безопасно только для `stdio` или доверенного localhost. Переданный `AuthProvider` из `fastmcp.server.auth` (или собственный) заменяет провайдер из настроек; политика прав `settings.auth.access_policy` действует и с ним.
- Имена инструментов из `extra_providers` не должны совпадать со встроенными (`execute_sql`, `list_objects`, `get_object_details`, `explain_query`, `list_schemas`, `analyze_db_health`, `get_top_queries`, `analyze_query_indexes`, `analyze_workload_indexes`): при совпадении FastMCP пишет предупреждение и оставляет встроенный инструмент. Чтобы развести имена, подключите свой провайдер через `server.add_provider(provider, namespace="...")`.
- В `access_mode=basic` скрываются только full-инструменты этого пакета; инструменты из `extra_providers` тегом `full` не фильтруются и остаются видимыми независимо от `access_mode`.
- Middleware выполняются в порядке: бюджет ответа, timing, logging, затем `extra_middleware`. Бюджет стоит первым и проверяет в том числе результат ваших middleware.

## Разработка

### Локальная настройка

1. **Установка uv**:

   ```bash
   curl -sSL https://astral.sh/uv/install.sh | sh
   ```

2. **Клонирование репозитория**:

   ```bash
   git clone https://github.com/your-username/postgres-fastmcp.git
   cd postgres-fastmcp
   ```

3. **Установка зависимостей**:

   ```bash
   uv sync
   ```

4. **Установка пакета в режиме разработки**:

   ```bash
   uv pip install -e .
   ```

### Запуск тестов

**Требования:**

- Установлен и запущен Docker
- Образы Docker собираются при первом запуске тестов или их можно подготовить вручную:

```bash
# Подготовка образов для тестов (опционально, ускоряет последующие запуски)
uv run python tests/prepare_docker_images.py
```

**Запуск всех тестов:**

```bash
uv run python -m pytest
```

**Запуск конкретного файла тестов:**

```bash
uv run python -m pytest tests/unit/domains/index_tuning -v
```

**Интеграционные тесты с реальной БД:**

```bash
uv run python -m pytest tests/integration/ -v
```

### Форматирование кода

В проекте используется `ruff` для форматирования и линтинга:

```bash
uv run ruff format .
uv run ruff check .
```

### Проверка типов

Проверка типов с помощью `mypy`:

```bash
uv run mypy src/
```

## Отличия от оригинального проекта

Этот форк отличается от [postgres-fastmcp](https://github.com/crystaldba/postgres-fastmcp) следующими изменениями:

| Оригинальный проект     | Этот форк |
| ----------------------- | --------- |
| Стандартная реализация MCP | Фреймворк FastMCP |
| Режимы                        | access_mode (`basic` / `full`) + write_mode (true/false)                      |
| Только транспорт SSE   | HTTP и stdio |
| Настройка через CLI/env | config.json, env (`MCP_SERVER_*`, `MCP_DATABASE_*`, `MCP_AUTH_*`, `MCP_FASTMCP_*`) и CLI |
| —                      | Опциональный `table_prefix` для `access_mode=basic`; endpoint здоровья `/health` |

## Технические заметки

### Подбор индексов

Реализация подбора индексов следует подходу оригинального проекта и использует [Anytime Algorithm of Database Tuning Advisor for Microsoft SQL Server](https://www.microsoft.com/en-us/research/wp-content/uploads/2020/06/Anytime-Algorithm-of-Database-Tuning-Advisor-for-Microsoft-SQL-Server.pdf).

### Здоровье БД

Проверки здоровья БД адаптированы из [PgHero](https://github.com/ankane/pghero) и включают:

- Здоровье индексов (неиспользуемые, дубликаты, раздутые)
- Доля попаданий буферного кэша
- Здоровье соединений
- Vacuum (предотвращение wraparound transaction ID)
- Репликация
- Ограничения
- Последовательности

### Клиентская библиотека Postgres

В проекте используется [psycopg3](https://www.psycopg.org/) для асинхронного I/O при подключении к Postgres с доступом к полному набору возможностей Postgres.

### Защищённое выполнение SQL

Реализована многоуровневая защита при выполнении SQL:

- Разбор SQL через `pglast` для выявления и отклонения небезопасных операторов
- Транзакции только для чтения в ограниченных режимах
- `statement_timeout` на стороне PostgreSQL в ограниченных режимах
- Ограничения по схемам для режима `basic`
- `CREATE EXTENSION` только для `hypopg` и `pg_stat_statements` в режиме записи

## Лицензия

MIT License

## Благодарности

Проект является форком [postgres-fastmcp](https://github.com/crystaldba/postgres-fastmcp) от [Crystal DBA](https://www.crystaldba.ai), переписанным на [FastMCP](https://gofastmcp.com/).
