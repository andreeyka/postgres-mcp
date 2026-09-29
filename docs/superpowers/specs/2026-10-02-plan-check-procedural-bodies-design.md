# Дизайн: пятая волна `plan_check` — функции `public` не на SQL

Дата: 2026-10-02. Статус: спроектировано, план — `docs/superpowers/plans/2026-10-02-21-plan-check-procedural-bodies.md` (код плана опробован на копии репозитория и живом PostgreSQL 17.10). Продолжение `2026-10-01-plan-check-implicit-calls-design.md` §6 («Тела PL/pgSQL и других процедурных языков») и README «Что `plan_check` не закрывает» (тела триггерных функций, функции PL/pgSQL).

## 1. Объём

| # | Задача | PR / ветка |
| --- | --- | --- |
| 1 | Отказ по функции `public` не на языке `sql` (PL/pgSQL, PL/Python, C, `internal`, любой другой), до которой доходит запрос, — кроме агрегатов и членов расширений; настройка `plan_check_allow_non_sql_functions` (по умолчанию `false`) возвращает прежнее поведение | один PR — `claude/plan-check-procedural-bodies` |
| 2 | Опорная функция планировщика (`pg_proc.prosupport`) функций `public` — как функция машинерии типа | тот же |

Всё — только при `plan_check` (basic). Full и `plan_check=false` не меняются. Одна задача плана: обе строки — один новый фрагмент того же запроса реализаций (§4), решение — в одном месте `PlanGuard`.

## 2. Дыра (живьём, код до волны)

PG 17.10, basic + `table_prefix=app_` + `plan_check=true`, объекты создал суперпользователь:

| Объект в `public` | Запрос агента | До волны |
| --- | --- | --- |
| `app_leak()` на PL/pgSQL: `RETURN (SELECT token FROM secret.accounts LIMIT 1)`, представление `app_leak_view` | `SELECT t FROM app_leak_view` | `top-secret` |
| триггер `BEFORE INSERT` на `app_notes`, функция `app_fill_note()` на PL/pgSQL пишет в `NEW.note` токен из `secret.accounts` | `INSERT INTO app_notes (id) VALUES (1) RETURNING note` | `top-secret` |
| `app_setting(text) LANGUAGE internal AS 'show_config_by_name'` (это `current_setting`), представление | `SELECT s FROM app_setting_view` | путь `data_directory` — встроенная вне списка basic под чужим именем |
| `app_ns_boom()` на PL/pgSQL, `IMMUTABLE`, бросает; представление | `SELECT b FROM app_ns_boom_view` | исключение функции при `EXPLAIN` (свёртка) |
| функция оператора, функция приведения на PL/pgSQL, бросают | `SELECT 1 #!# 2`, `SELECT 1::app_ns_e` | исключение при `EXPLAIN` |
| триггер на PL/pgSQL, бросает | `INSERT INTO app_ns_trg …` | исключение при выполнении |

Сам SQL агента функцию `public` не на sql назвать не может: валидатор basic пускает только функции списка basic (`SELECT app_leak()` и `SELECT public.app_leak()` — `FunctionNotAllowedError`). Доходит до неё только то, что Postgres вычисляет за агента: представления, правила, триггеры, операторы и агрегаты, приведения, машинерия типов, умолчания, другие тела. Ровно эти пути `plan_check` уже собирает.

## 3. Модель

### Какие функции

**Правило: функция схемы `public` (allowed_schema), `pg_language.lanname <> 'sql'`, не агрегат (`prokind <> 'a'`), не член расширения (`pg_depend`, `deptype = 'e'`) — отказ.** Строка каталога — `non_sql_function`, в колонке `definition` — имя языка (для сообщения).

- **Почему не только процедурные языки (`lanispl`).** Проверяется только тело на `sql` (волна 3); всё остальное непрозрачно одинаково:
  - PL/pgSQL, PL/Python, PL/Perl, PL/v8 — любой код, в том числе динамический SQL (`EXECUTE format(…)`);
  - C (`LANGUAGE c`) — любой машинный код;
  - `LANGUAGE internal` — любая встроенная функция под своим именем. `CREATE FUNCTION app_setting(text) … LANGUAGE internal AS 'show_config_by_name'` — это `current_setting` в обход списка basic (живьём отдаёт настройку сервера, §2). Правило «`pg_catalog` — только список basic» такая обёртка обходит, и отличить её можно только по `prosrc` — это уже анализ тела.
  
  `lanispl` у `plpgsql` — `true`, у `c` и `internal` — `false` (живьём), так что граница по `lanispl` оставила бы обёртки `internal`. Граница по `lanname <> 'sql'` — дополнение ровно к тому, что проверяется: каждая функция из запроса реализаций либо даёт тело (`sql_body`/`sql_atomic_body`), либо отказ.
- **Агрегаты** (`prokind = 'a'`) исключены: у агрегата `prolang` — `internal` (`prosrc = aggregate_dummy`), а его опорные функции уже приходят строками `aggregate_function` и проверяются сами (а значит, и по этому правилу). Без исключения любой агрегат `public` отклонялся бы (живьём: `app_cat2` над SQL-функцией — `internal`).
- **Оконные функции и процедуры** (`prokind` `w`, `p`) — по общему правилу: оконная функция `public` не из расширения — это C, процедуру агент не вызывает (`CALL` валидатор не пускает).
- **Члены расширений доверены** — как их машинерия в волне 4. Функция расширения — код расширения: PostGIS, `pgcrypto`, `moddatetime` (C, живьём: `UPDATE` таблицы с триггером `moddatetime(updated_at)` проходит), `pg_partman` (PL/pgSQL). Доверие — по самой функции: у функции нет объекта-владельца, как у машинерии (тип, семейство, приведение), а кто вызвал её — представление DBA или скрипт — не меняет её тела.
- **C-функции `public` без расширения** (создал суперпользователь из `.so` напрямую) — отказ. Честного способа отличить безопасную от опасной нет; расширение — штатная упаковка такого кода. Базовые типы `public` с вводом-выводом на C или `internal` без расширения (так устроены интеграционные тесты волны 4) отклоняют любой запрос к колонке такого типа — это задокументированная цена (§8).

### Где функции уже собраны

Каждая функция `public`, до которой доходит проверка, проходит через одну точку — `PlanGuard._note_implementation(FUNCTION_KIND, …)`, а затем через запрос реализаций `ALLOWED_IMPLEMENTATIONS_SQL` (CTE `functions` — все перегрузки с именем в `allowed_schema`). Источники (все уже есть):

| Откуда | Строка, которая отмечает функцию |
| --- | --- |
| выражения плана, `Function Scan`, текст `Function Call` | `_check_names`, `_check_plan` |
| правила представлений, `CHECK`, индексы, ключи секционирования, статистика, политики, умолчания колонок и доменов | тексты определений → `_check_names`; `pg_depend` → `function` |
| триггеры целей DML (их потомков и каскадных таблиц) | текст `pg_get_triggerdef` и `pg_depend` триггера → `function` (функция триггера — `tgfoid`) |
| функции операторов, опорные функции агрегатов, оператор сортировки агрегата | `operator_function`, `aggregate_function` |
| машинерия типов: ввод-вывод, опорные функции классов операторов, оценка селективности, `canonical`/`subdiff`; функции приведения | `type_function`, `function` (волна 4) |
| умолчания аргументов и тела SQL-функций `public` | `argument_defaults`, `sql_body`, `sql_atomic_body` → `_check_names` |

Поэтому новое правило — **одна строка в CTE `functions` запроса реализаций и одно решение в `PlanGuard._check_implementations`**, а не проверки в каждом источнике. Функция, найденная любым путём, в следующем круге получает свою строку `non_sql_function`.

### Опорная функция планировщика (`prosupport`)

`CREATE FUNCTION f(…) … SUPPORT s`: планировщик вызывает `s` для каждого вызова `f` (`SupportRequestSimplify`, оценки строк и селективности) — функция, которую Postgres выполняет за агента, без имени в тексте и плане. До волны не проверялась. Сигнатура — `internal → internal`, то есть C или `internal`. Та же CTE `functions` отдаёт её строкой `type_function` (правило волны 4: `pg_catalog` — любая, `public` — отмечается и проверяется как функция `public`, то есть по этому правилу — отказ, если не член расширения; другая схема — отказ). У функции — члена расширения не проверяется: доверие по владельцу. Живьём: `SUPPORT secret.ns_support` (обёртка `internal` над `textlike_support`) у SQL-функции `public` в представлении проходил проверку; стал отказом по `secret.ns_support`.

### Статический анализ тел PL/pgSQL — не делается

Он был бы честен, только если закрыт: `EXECUTE` с текстом из переменных, `format()`, вызов других функций, курсоры по строке, `PERFORM`, `SET` внутри тела, `SECURITY DEFINER`. Разбор (`pglast.parse_plpgsql`) даёт дерево, но динамический SQL по нему не проверить, а «всё, кроме `EXECUTE`» — это отдельная большая проверка с собственным хвостом обходов (ср. проверку тел SQL: волна 3). Для C и `internal` тела нет вовсе. Вне объёма.

## 4. Порядок, запросы, настройка

- **Строки.** К запросу реализаций добавлен фрагмент `plan_catalog._NON_SQL_FUNCTION_ROWS` (две ветви `UNION ALL` над CTE `functions`, которой добавлены колонки `prokind` и `prosupport`): `non_sql_function` и `type_function` опорной функции планировщика. Новых обращений к БД нет.
- **Решение — в `PlanGuard._check_implementations`.** Строки `non_sql_function` откладываются и проверяются после остальных строк того же ответа: отказ по функции чужой схемы в умолчании аргумента или в машинерии той же функции точнее и не зависит от порядка строк (у SQL порядок `UNION ALL` не гарантирован). Остальные строки не меняются, `_check_row` новый вид не видит.
- **Когда отказ.** Как у тел: для функций SQL агента и выражений его операторов — до `PREPARE`; для функций определений (правила, триггеры, путь записи, зависимости) — после `PREPARE`, до `EXPLAIN`; для функций, до которых дошёл только планировщик (встраивание), — во втором чтении определений, до выполнения. `EXPLAIN` свернул бы `IMMUTABLE`-функцию, выполнение запустило бы триггер — отказ приходит раньше (интеграционные тесты с бросающими функциями, §7).
- **Настройка.** `plan_check_allow_non_sql_functions: bool = False` в `DatabaseConfig` (env `MCP_DATABASE_PLAN_CHECK_ALLOW_NON_SQL_FUNCTIONS`, config.json `database.plan_check_allow_non_sql_functions`), в `DatabaseConfigPort`, в `SafeSqlConfig`, в `PlanGuard(allow_non_sql_functions=…)`. `true` — строки `non_sql_function` игнорируются: поведение до волны. Без `plan_check` настройка ничего не делает. CLI отдельных флагов для полей БД не имеет (поля — из config.json/env, как `plan_check`), библиотечный API — тот же `DatabaseConfig`.
  - **Почему `bool`, а не `reject | allow` или список доверенных функций.** Два значения — это `bool`, как у `plan_check` и `write_mode`; имя говорит, что разрешает (`allow_non_sql_functions`), значение по умолчанию — безопасное. Список доверенных имён (allowlist) — YAGNI: у владельца базы уже есть два точных инструмента — переписать функцию на SQL или оформить код расширением; список имён ещё и обходился бы перегрузкой с тем же именем (проверка — по имени, все перегрузки).
  - **Почему `non_sql`, а не `procedural`.** Правило шире процедурных языков (C, `internal`, §3); имя совпадает с сообщением об ошибке.
- **Сообщение** (агенту, английский): `Access to function 'public.app_touch' is not allowed in basic mode: the query reaches it, and its body in LANGUAGE plpgsql cannot be verified (plan_check verifies only LANGUAGE sql bodies). A database owner can rewrite it in LANGUAGE sql; the server operator can set plan_check_allow_non_sql_functions=true, which lets such functions run unchecked.` — `PlanAccessError(kind="function", qualified_name="public.…", language=…)`. Имя функции `public` агенту не секрет (её видят `list_objects`/`get_object_details`).

## 5. SQL и стоимость

- Весь новый SQL — с `pg_catalog.` у отношений, функций и типов и `OPERATOR(pg_catalog.…)` у операторов; его покрывает существующий тест квалификации (`_unqualified_names`). Членство в расширении — существующий `_not_extension_member(_PG_PROC, 'p.oid')`.
- Живьём (оценка планировщика запроса реализаций, семь имён функций, два оператора, тип и два отношения, база с расширением `moddatetime`): до — 7749, после — 7804 (+55), 5 мс. Порог JIT (`jit_above_cost` = 100000) далеко. Новая ветвь читает только CTE `functions` (строки по названным именам) и `pg_language`.

## 6. Не входит

- (x) Статический анализ тел PL/pgSQL (§3): отказ целиком, без попытки проверить безопасное подмножество.
- (x) Функции расширений доверены, в том числе те, что выполняют переданный им текст SQL: `crosstab('SELECT … FROM secret.t')` из `tablefunc`, `dblink`. Представление DBA с таким вызовом проверка не видит — как и до волны; описано в README.
- (x) Функция, добавленная в расширение явно (`ALTER EXTENSION … ADD FUNCTION`), доверена — доверие по членству (как у машинерии в волне 4).
- (x) Конкурентная замена: блокировка на функцию до конца транзакции не держится, SQL-функцию можно пересоздать на PL/pgSQL (`CREATE OR REPLACE FUNCTION … LANGUAGE plpgsql`) между проверкой и выполнением. Как и до волны (README).
- (x) Обработчики и валидаторы языков (`lanplcallfoid`, `laninline`, `lanvalidator`) и трансформы (`pg_transform`): вызываются только ради функций этого языка, а те уже отклонены.
- (x) Функции `pg_catalog` на не-SQL языках — встроенные, правило списка basic не меняется. Функции других схем отклоняются по схеме, как и раньше.
- (x) Триггеры событий (`EVENT TRIGGER`): DDL агенту в basic недоступен.
- (i) Проверка по имени, все перегрузки сразу (как у тел и умолчаний): PL/pgSQL-перегрузка отклоняет и вызов одноимённой SQL-перегрузки; функция `public`, одноимённая встроенной (`public.lower(app_t)`), отклоняет любой вызов имени — план печатает оба без схемы.

## 7. Тесты

- **Юнит, `test_plan_catalog.py`.** Ветвь `non_sql_function`: `l.lanname::pg_catalog.text`, `lanname <> 'sql'`, `prokind <> 'a'`, членство по `pg_proc` и `p.oid`. `prosupport` — строка `type_function` с членством функции. Тест доверия машинерии по владельцу сужен до CTE машинерии (членство `pg_proc` теперь есть в запросе реализаций, но не в машинерии). Тест «умолчания — любого языка» смотрит только на свою ветвь. Квалификация — существующий тест.
- **Юнит, `test_plan_guard.py`.**
  - Функция не на sql, найденная представлением (`function`), машинерией (`type_function`) и триггером (текст определения), — отказ до `EXPLAIN`; сообщение называет `LANGUAGE plpgsql`, `rewrite it in LANGUAGE sql` и `plan_check_allow_non_sql_functions=true`.
  - Функция оператора в SQL агента на `internal` — отказ до `PREPARE`.
  - Умолчание аргумента той же функции с `secret.api_key()` — отказ называет `secret.api_key` при любом порядке строк.
  - `allow_non_sql_functions=True` — проходит, `EXPLAIN` уходит.
- **Юнит, настройка.** `test_settings.py` — по умолчанию `false`, env `MCP_DATABASE_PLAN_CHECK_ALLOW_NON_SQL_FUNCTIONS`; `test_db_access.py` — значение доходит до `SafeSqlConfig`; `test_safe_sql_executor.py` — до `PlanGuard(allow_non_sql_functions=…)`.
- **Интеграция, `test_plan_check.py`, бросающие функции и чтение секрета** (на коде до волны все падают: 4 — исключением бросающей функции `public.app_ns_*_was executed`, 4 — `DID NOT RAISE`, 2 — нет поля настройки):
  - представление над `IMMUTABLE`-функцией на PL/pgSQL (свёртка при `EXPLAIN`);
  - триггер цели `INSERT` на PL/pgSQL (выполнение);
  - функция оператора и функция приведения на PL/pgSQL (свёртка);
  - представление и триггер, читающие `secret.accounts`;
  - обёртка `internal` над `show_config_by_name` в представлении;
  - опорная функция планировщика `secret.ns_support` у SQL-функции `public`;
  - тип с вводом-выводом на `internal` в `public` (цепочка волны 4) — отказ по умолчанию.

  Проходят: представление над агрегатом `public` (над SQL-функцией), `UPDATE` таблицы с триггером `moddatetime` (расширение). С `plan_check_allow_non_sql_functions=true` — `SELECT t FROM app_ns_leak_view` отдаёт `top-secret` (цена настройки). Существующие тесты с PL/pgSQL-функциями `public` в роли безобидных (`app_double`, `app_close_to`) и цепочки с типом `app_ic_okt` (ввод-вывод `internal`) идут через фикстуру `db_plan_check_non_sql` (настройка включена). Тесты волны 4, где у типа есть и функция `internal` в `public`, и функция `secret`, отказ по `secret` сохраняют: функция `public` получает свою строку `non_sql_function` только следующим кругом.

## 8. Изменения поведения (для заметок к PR)

- С `plan_check` отклоняется запрос, который доходит до функции `public` не на языке `sql` (PL/pgSQL, PL/Python, C, `internal` и любого другого языка), кроме агрегатов и функций расширений: через представление, правило, выражение плана, триггер цели DML (и её секций и каскадных таблиц), функцию оператора, опорную функцию агрегата, приведение, машинерию типа, умолчание аргумента, тело SQL-функции. Ошибка — `PlanAccessError` с языком функции и тем, как это исправить. Отказ приходит до `PREPARE` или `EXPLAIN`, то есть до выполнения.
- Новая настройка `plan_check_allow_non_sql_functions` (env `MCP_DATABASE_PLAN_CHECK_ALLOW_NON_SQL_FUNCTIONS`), по умолчанию `false`. `true` возвращает поведение до волны (тела таких функций выполняются непроверенными).
- Частые ложные отказы при `false`: `INSERT`/`UPDATE`/`DELETE` таблиц с любым своим триггером в `public` — триггерная функция не может быть на `sql` (Postgres не принимает `LANGUAGE sql … RETURNS trigger`), так что триггер `updated_at` или аудита — это всегда отказ; замена — триггерная функция расширения (`moddatetime` из contrib для `updated_at`) или настройка; представления над PL/pgSQL-функциями; любой запрос к таблице с колонкой базового типа `public` без расширения (ввод-вывод на C или `internal`); все перегрузки имени проверяются вместе.
- Опорная функция планировщика (`SUPPORT`) функции `public` проверяется как функция машинерии типа: чужая схема — отказ, `public` не из расширения — отказ (она всегда C или `internal`).
- `PlanAccessError.__init__` получил необязательный `language`; `PlanGuard.__init__` — `allow_non_sql_functions`; `SafeSqlConfig` и `DatabaseConfigPort` — `plan_check_allow_non_sql_functions`.
- Запрос реализаций читает немного больше: оценка +55 (порог JIT — 100000); новых запросов нет.
