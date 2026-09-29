# Дизайн: проверка выражений плана в `plan_check`

Дата: 2026-09-29. Статус: к реализации (пользователь: «доделывай»; решения приняты по рекомендации, без отдельного согласования). Продолжение `2026-09-28-basic-followups-design.md` §4.3 («функции в выражениях представлений») и `2026-09-28-basic-confinement-design.md` §6 (строковые типы таблиц без префикса).

## 1. Проблема

`PlanGuard` проверяет узлы сканирования (`Relation Name`, `Function Scan`), но не выражения плана. Через представление в `public` агент получает:

- функции чужих схем в выражениях: `CREATE VIEW public.app_v AS SELECT secret.decrypt(c) FROM public.app_t`;
- встроенные функции вне списка basic: `SELECT current_setting('app.jwt_secret') AS s FROM …` внутри представления;
- операторы и приведения к типам чужих схем в выражениях;
- строковые типы таблиц `public` без префикса (`json_populate_record(NULL::users, '{}')`) — сейчас оракул структуры таблицы вне префикса; валидатор не отличает `users` от встроенного типа без каталога.

SQL самого агента уже прошёл валидатор (его функции — из списка basic). Новое в выражениях плана — то, что пришло из представлений, правил и встроенных SQL-функций.

## 2. Решение

Только при `plan_check` (basic). В `PlanGuard` добавляется проверка выражений.

### 2.1. Какие ключи плана — выражения

Строковые значения (или списки строк) ключей: `Output`, `Filter`, `Join Filter`, `Hash Cond`, `Merge Cond`, `Index Cond`, `Recheck Cond`, `TID Cond`, `One-Time Filter`, `Run Condition`, `Sort Key`, `Presorted Key`, `Group Key`, `Hash Key`, `Cache Key`, `Order By`, `Function Call` (уже разбирается для сканов функций — единый путь), `Table Function Call`. Точный список сверить по `explain.c` PG 15/16/17 (VERBOSE) и записать в «Spec corrections» плана.

### 2.2. Разбор

- Выражение разбирается pglast как `SELECT <expr>`; ключи сортировки (`x DESC NULLS LAST`, `USING <`) — как `SELECT 1 ORDER BY <key>`.
- Перед разбором ссылки планировщика заменяются на `NULL`: `(SubPlan N)`, `(hashed SubPlan N)`, `(InitPlan N)`, `(InitPlan N).colM`, `$N`, `(returns $N)` и подобные — полный перечень сверить по ruleutils/explain.c и тестами на реальных планах CI.
- Неразборчивое после замены выражение → `PlanUnverifiableError` (fail closed, как остальная проверка).

### 2.3. Правила для разобранного выражения

- `FuncCall` с явной схемой: схема `allowed_schema` — пропуск; `pg_catalog` — имя в `BASIC_ALLOWED_FUNCTIONS`; иначе `PlanAccessError(function, …)`.
- `FuncCall` без схемы (под `search_path = public` так печатаются функции `public` и `pg_catalog`): имя в `BASIC_ALLOWED_FUNCTIONS` — пропуск; иначе, если имя есть среди функций `pg_catalog` (список из каталога, см. 2.4) — `PlanAccessError`; иначе (функция `public`) — пропуск.
- Оператор (`A_Expr.name`, `SubLink.operName` и пр.) с явной схемой вне `allowed_schema`/`pg_catalog` → `PlanAccessError(function, …)` (оператор реализован функцией).
- `TypeName` с явной схемой вне `allowed_schema`/`pg_catalog` → `PlanAccessError(relation|type, …)`; `TypeName` без схемы, не встроенный, совпадающий со строковым типом отношения `public` без префикса (при `table_prefix`) → `PlanAccessError(relation, 'public.<name>')`; reg*-типы в выражениях плана не отклоняются (поиск по имени уже произошёл при создании объекта или планировании).
- Агрегаты, оконные функции — те же `FuncCall`.

### 2.4. Каталог

- Функции `pg_catalog`: `SELECT DISTINCT proname FROM pg_catalog.pg_proc WHERE pronamespace = 'pg_catalog'::regnamespace` — запрашивается через раннер той же транзакции только при встрече неразрешённого безсхемного имени; кэш в процессе по `connection_id` (набор встроенных функций меняется только при установке расширений в `pg_catalog`).
- Строковые типы `public`: по одному запросу на имя, только когда встретился безсхемный невстроенный `TypeName` и задан `table_prefix`: `SELECT 1 FROM pg_catalog.pg_type t JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace WHERE n.nspname = 'public' AND t.typname = {} AND t.typrelid <> 0`; без кэша.
- Запросы сервера идут через тот же раннер (одна транзакция, `search_path = public`), без валидатора агента — это SQL сервера; имя встраивается как `Literal`.

## 3. Не входит

- Тела функций `public` и `SECURITY DEFINER` (непрозрачны для плана), триггеры, RLS — как раньше.
- Неявное приведение к reg*-колонке — поиск по имени выполняется при планировании; не закрывается.
- На PG 15/16 `EXPLAIN` печатает подплан `IN`/`ANY`/сравнения строк только как `(SubPlan N)`/`(hashed SubPlan N)`: само левое выражение проверки (например, `current_setting('x') = ANY (SELECT …)` внутри представления) не видно. На PG 17 оно печатается (`(ANY (expr = (hashed SubPlan 1).col1))`) и проверяется.
- Выражения `LIMIT`/`OFFSET` и смещения оконного фрейма `EXPLAIN` не печатает ни на одной версии (15–17), поэтому функции в них не проверяются.
- Выражения времени записи, которых нет в плане: генерируемые столбцы, `CHECK` таблицы и домена, `WITH CHECK OPTION` представлений, выражения индексов (тот же класс, что и триггеры).
- Известный ложноположительный отказ в безопасную сторону: функция `public`, перегружающая имя `pg_catalog` вне списка basic (например, `public.current_setting(int)`), печатается без схемы и отклоняется.
- Выражение плана с подзапросом, ссылкой на таблицу, комментарием или доллар-квотированной строкой считается неразборчивым (fail closed) — в выводе ruleutils такого не бывает.

## 4. Тесты

- Юнит (`test_plan_guard.py`): выражения с `secret.f(x)`, `current_setting('x')`, `OPERATOR(secret.+)`, `::secret.t`, `NULL::users` (с префиксом, каталог отвечает «таблица есть») → отказ; `lower(name)`, `count(*)`, `my_public_fn(x)` (каталог: не в `pg_catalog`), `x::text`, `'1'::regclass` (reg*), подзапросы `(SubPlan 1)`, `$0`, ключи сортировки → проходят; неразборчивое → `PlanUnverifiableError`; кэш функций `pg_catalog` — один запрос на много проверок.
- Интеграция (CI PG 15/16): представление `public.app_expr_v` с `secret.f(c)` и с `current_setting('application_name')` → `PlanAccessError`; представление с `lower(c)` и с функцией `public` → данные; `json_populate_record(NULL::other_users, '{}')` при `app_` → `PlanAccessError`; `app_users` с подзапросом в `WHERE` → данные.

## 5. Изменения поведения (для заметок к PR)

- С `plan_check`: отклоняются запросы, план которых вызывает в выражениях функции/операторы чужих схем или встроенные функции вне списка basic, приводит к типам чужих схем или использует строковый тип таблицы `public` без префикса. Неразборчивое выражение плана → `PlanUnverifiableError`. Возможен лишний запрос к каталогу (список функций `pg_catalog` — один раз на процесс; строковые типы — по одному на имя).
- Известные пробелы проверки выражений: на PG 15/16 левое выражение проверки `IN`/`ANY`/сравнения строк за подпланом (`(SubPlan N)`/`(hashed SubPlan N)`) не видно и не проверяется — на PG 17 видно и проверяется; `LIMIT`/`OFFSET` и смещения оконного фрейма `EXPLAIN` не печатает ни на одной версии; выражения времени записи (генерируемые столбцы, `CHECK` таблицы и домена, `WITH CHECK OPTION`, выражения индексов) в план не попадают — тот же класс, что и триггеры.
- Известный ложноположительный отказ в безопасную сторону: функция `public`, перегружающая имя `pg_catalog` вне списка basic (например, `public.current_setting(int)`), печатается планом без схемы и отклоняется как встроенная вне basic.
- Выражение плана с подзапросом, ссылкой на таблицу, комментарием или доллар-квотированной строкой — неразборчиво и отклоняется (`PlanUnverifiableError`, fail closed); в реальном выводе ruleutils такие формы не встречаются.
