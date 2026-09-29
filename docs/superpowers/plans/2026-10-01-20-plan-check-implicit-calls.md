# `plan_check`: неявные вызовы типов (машинерия типов) — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** С `plan_check=true` (basic) отклонять запрос до того, как Postgres выполнит неявную машинерию типа, до которого доходит запрос. Машинерия — это функции ввода-вывода, функции приведения (явные, неявные, присваивания), опорные функции и операторы классов операторов, функции оценки селективности, `CHECK` доменов при приведении и `canonical`/`subdiff` диапазонов — если она вызывает функцию другой схемы (не члена расширения), функцию `public`, которая не проходит проверки, или (для приведения) встроенную функцию вне списка basic.

**Architecture:** Новый фрагмент SQL `plan_catalog._type_machinery(seeds)` — CTE замыкания типов от семян (домен → база, массив → элемент, составной → атрибуты, диапазон → подтип, мультидиапазон → диапазон; `pg_catalog` не раскрывается) и CTE `machinery` со строками того же вида, что у существующих запросов (`type_function`, `function`, `operator`, `operator_function`, `domain`). Он встраивается в оба существующих запроса каталога, новых обращений к БД нет:

- в `ALLOWED_IMPLEMENTATIONS_SQL` (до `PREPARE`) — семена: имена типов и отношений SQL агента (`to_regtype`/`to_regclass`), типы найденных функций, операторов и агрегатов `public`; плюс `oprrest`/`oprjoin` названных операторов `public`;
- в `DEFINITION_DEPENDENCIES_SQL` (после `PREPARE` и после всех `EXPLAIN`) — колонки `relations` и типы `types` из `pg_depend`.

`PlanGuard` собирает семена в `_CatalogNames.footprint_*`, шлёт запрос реализаций и тогда, когда в SQL агента есть только отношения или типы, и проверяет новый вид строки `type_function` (`pg_catalog` — любая функция, `public` — с телом, другая схема — отказ). Спека: `docs/superpowers/specs/2026-10-01-plan-check-implicit-calls-design.md`.

**Tech Stack:** Python 3.12, uv, pglast v8 (`Visitor`, `RangeVar`), psycopg 3.3.4 (`psycopg.sql.SQL`/`Identifier`/`Literal`), pytest (asyncio auto).

## Spec corrections

Проверено на живом PostgreSQL 17.10 (временный сервер из `.deb`, `localhost:55432`, суперпользователь); PG 15/16 — по документации: все используемые колонки каталога (`typsubscript`, `rngmultitypid`, `opfnamespace`) есть с PG 14. План опробован: код и тесты обеих задач применены к копии репозитория (`git worktree` в scratchpad, затем удалён):

- `uv run pytest tests/unit -q` — 2147 passed;
- `mypy src/`, `ruff check .`, `ruff format --check .` — чисто;
- `tests/integration/test_plan_check.py` на живом PG 17 (плагин подмены контейнера, `-k "postgres:16"`) — 88 passed, дважды на одной базе (идемпотентно);
- новые интеграционные тесты на коде до волны (`git archive HEAD src`): 13 из 14 падают исключением бросающей функции (`RaiseException secret.ic_*`, `invalid input syntax … "abc"`, `invalid internal value for enum`), оценка селективности — `DID NOT RAISE`.

Оба запроса каталога выполнены живьём, стоимость — в спеке §5 (до ~8000 при `jit_above_cost` = 100000).

- **`CHECK` домена при `PREPARE` литерала не выполняется.** `coerce_type` вводит литерал функцией базового типа и оборачивает `CoerceToDomain` (`PREPARE … INSERT INTO app_dt (d) VALUES ('5')` проходит). `IMMUTABLE`-вызов `CHECK` сворачивает typcache уже при `EXPLAIN`. При `PREPARE` `CHECK` выполняет только `domain_in` — ввод подтипа диапазона (`'[1,2]'::app_rr` бросает при `PREPARE`), элемента массива, атрибута составного типа. Поэтому `CHECK` доменов замыкания проверяется до `PREPARE` для типов SQL агента и до `EXPLAIN` для остальных.
- **Функция вывода выполняется при `EXPLAIN VERBOSE`.** Она печатает константы: `EXPLAIN SELECT '5'::app_t` проходит, `EXPLAIN VERBOSE` бросает. Проверка до `PREPARE` и до `EXPLAIN` покрывает и вывод.
- **Представления.** Функцию приведения представления `pg_depend` уже записывает (закрыто в волне 3). Опорные функции класса операторов и `CHECK` домена (`SELECT 1::app_rd` в представлении) — нет: в `pg_depend` только тип или оператор `<`. Поэтому машинерия читается и от типов `pg_depend` (`types`), и от колонок заблокированных отношений (`relations`).
- **Функции машинерии на PL/pgSQL невозможны** (`cstring`, `internal`). В интеграционных тестах ввод и вывод — обёртки `LANGUAGE internal`: ввод `int4in` бросает на `'abc'`, вывод `enum_out` — на любом значении, которое не метка перечисления. Оценка селективности — обёртка над `eqsel`, она не бросает: тест доказывает отказ, а не то, что вызов был бы.
- **Типы результата и параметров подготовленного оператора не нужны.** Они выводятся из семян (колонки, названные типы, функции `public`).
- **Расширения.** 14 расширений contrib в `public` — ни одного ложного отказа, приведений с функцией `pg_catalog` вне списка basic нет. В схеме `extensions` без исключения членов расширений `SELECT id FROM app_z` (колонка `extensions.citext`) отклонялся по `extensions.citextsend`. Поэтому функции и операторы расширений (`pg_depend.deptype = 'e'`) в строки `machinery` не попадают.
- **Семейства `pg_catalog` отбрасываются.** Диапазон над встроенным подтипом (класс подтипа `int4_ops`) иначе тянул бы всё семейство `integer_ops` — около сотни строк на каждый запрос.
- **Порядок строк не определён.** Если у типа ввод и вывод чужой схемы, отказ называет любую из двух функций; тесты используют `ic_t_(in|out)`.
- **Имена `=`/`<` в интеграционных объектах не используются.** Оператор `public` с именем `=` над типом с чужой машинерией отклонял бы любое `=` в базе тестов: семена — типы всех перегрузок с именем. Операторы класса в тестах — `#<`, `#<=`, `#=`, `#>=`, `#>`; класс операторов — пять разных операторов (повтор даёт `pg_amop_opr_fam_index`).

## Global Constraints

- Всё, что видит агент или внешняя система (ошибки, логи, коммиты), — на английском; docstring и комментарии — по-русски. README — по-русски.
- Нет `from __future__ import annotations`.
- Ломающие изменения разрешены; описываются только в заметках к PR. В коде — никаких шимов и упоминаний старого поведения.
- Безопасность: ни одно правило валидатора и существующей проверки по плану не ослабляется. Правила представлений, узлы, выражения, реализации операторов и агрегатов, путь записи, тела и умолчания — как в волне 3. `full` и `plan_check=false` не затрагиваются. Весь SQL каталога — с `pg_catalog.` у каждого отношения, функции и типа и `OPERATOR(pg_catalog.…)` у каждого оператора (в том числе на смешанных типах); имена — `Literal`/`Identifier`.
- Стоимость запросов каталога — много ниже `jit_above_cost` (100000): множества — массивами через `unnest` (`_oid_set`).
- Перед каждым коммитом: `uv run ruff check --fix --show-fixes`, `uv run mypy src/`, `uv run ruff format`; затем `uv run ruff check .`.
- Юнит-тесты: `uv run pytest tests/unit -q`. Интеграция: `uv run pytest tests/integration -q` — должна собираться; без Docker пропускается, в CI — Postgres 15/16 (суперпользователь `postgres`: базовые типы с `LANGUAGE internal` создаёт только он). Интеграционные тесты сверять статически с кодом.
- Коммиты: `type(scope): message`, повелительное наклонение, английский.
- **Никогда не запускать никакие `git config` и `git stash`.** Не пушить.
- Ветка: `claude/plan-check-implicit-calls` от `main` (уже создана, на ней — спека и этот план).

---

### Task 1: Машинерия типов в запросах каталога и `PlanGuard`

**Files:**
- Modify: `src/postgres_fastmcp/postgres/security/plan_catalog.py` (`_type_machinery`, `_function_rows`, `_not_extension_member`, `_PG_CATALOG_NAMESPACE`, `_MACHINERY_TYPES`; `DEFINITION_DEPENDENCIES_SQL`; `ALLOWED_IMPLEMENTATIONS_SQL`; `_text_array`; `allowed_implementations(..., types, relations)`)
- Modify: `src/postgres_fastmcp/postgres/security/plan_guard.py` (docstring модуля; `_StatementNames.relations`/`visit_RangeVar`; `_quoted_name`; `_CatalogNames.footprint_types/footprint_relations/_all`; `_check_rule_row` — `type_function`; `_check_statement_names`; `_check_catalog_names`; `_check_implementations(current, pending)`)
- Test: `tests/unit/postgres/test_plan_catalog.py`, `tests/unit/postgres/test_plan_guard.py`, `tests/unit/postgres/test_safe_sql_executor.py`

**Interfaces:**
- Consumes (волна 3): `_oid_set`, `_LIVE_COLUMN`, `_NO_PARENT`, `_NO_DEFINITION`, `_NO_NAME`, `_NO_RELATION`, `_PG_PROC`, `_PG_OPERATOR`, `_RELATIONS`, CTE `types`/`relations`/`relation_set` запроса определений; `PlanGuard._check_function`, `_note_implementation`, `_check_rule_row`, `_seen_rows`, `_looked_up`, `_CatalogNames`, `_MAX_DEFINITION_DEPTH`.
- Produces:
  - `plan_catalog._type_machinery(seeds: str) -> str` — CTE `type_seed_set`, `type_closure`, `machinery_set`, `families`, `family_operators`, `machinery(kind, schema, name, parent_schema, definition)`;
  - `plan_catalog._function_rows(kind, source, function, parent=_NO_PARENT) -> str`, `_not_extension_member(catalog, alias) -> str`, `_text_array(values) -> Composable`;
  - `allowed_implementations(run, schema, *, operators, functions, types=(), relations=())`;
  - вид строки `type_function` (в обоих запросах);
  - `plan_guard._quoted_name(schema: str | None, name: str) -> str`;
  - `_CatalogNames.footprint_types: dict[str, None]`, `_CatalogNames.footprint_relations: dict[str, None]`;
  - `PlanGuard._check_implementations(current: _CatalogNames, pending: _CatalogNames)`.

- [ ] **Step 1: Падающие тесты SQL каталога**

В `tests/unit/postgres/test_plan_catalog.py` в `_catalog_sql` вызов `allowed_implementations` заменить на:

```python
    await allowed_implementations(
        recorder, "public", operators=["===", "="], functions=["app_agg"], types=['"app_t"'], relations=['"app_x"']
    )
```

(так новый SQL проходит `test_catalog_sql_resolves_nothing_through_the_search_path`) и добавить в конец файла:

```python
_BOTH_QUERIES = pytest.mark.parametrize(
    "sql", [ALLOWED_IMPLEMENTATIONS_SQL, DEFINITION_DEPENDENCIES_SQL], ids=["implementations", "definitions"]
)


@_BOTH_QUERIES
def test_type_machinery_covers_io_casts_operator_classes_domains_and_ranges(sql: str) -> None:
    """Всё, что Postgres вызывает для значения типа без имени функции в тексте (plan_catalog._type_machinery)."""
    for fragment in (
        "(t.typinput), (t.typoutput), (t.typreceive), (t.typsend), (t.typmodin), (t.typmodout), (t.typanalyze), "
        "(t.typsubscript)",
        "(r.rngcanonical), (r.rngsubdiff)",
        "r.rngsubopc",
        "pg_catalog.pg_amproc",
        "pg_catalog.pg_amop",
        "(o.oprrest), (o.oprjoin)",
        "k.castsource",
        "k.casttarget",
        "k.castfunc",
        "k.contypid",
    ):
        assert fragment in sql


@_BOTH_QUERIES
def test_type_closure_follows_what_calls_nested_machinery(sql: str) -> None:
    """Домен -> база, массив -> элемент, составной -> атрибуты, диапазон -> подтип, мультидиапазон -> диапазон.

    Типы и семейства операторов pg_catalog не раскрываются: встроенная машинерия.
    """
    for fragment in (
        "SELECT t.typbasetype UNION ALL SELECT t.typelem",
        "a.attrelid OPERATOR(pg_catalog.=) t.typrelid",
        "r.rngsubtype",
        "r.rngmultitypid OPERATOR(pg_catalog.=) t.oid",
        "t.typnamespace OPERATOR(pg_catalog.<>) 'pg_catalog'::pg_catalog.regnamespace::pg_catalog.oid",
        "f.opfnamespace OPERATOR(pg_catalog.<>) 'pg_catalog'::pg_catalog.regnamespace::pg_catalog.oid",
    ):
        assert fragment in sql


@_BOTH_QUERIES
def test_type_machinery_skips_extension_members(sql: str) -> None:
    """Функции и операторы расширений (deptype 'e') — машинерия, которую поставил скрипт расширения."""
    assert "e.deptype OPERATOR(pg_catalog.=) 'e'" in sql
    assert "e.classid OPERATOR(pg_catalog.=) 'pg_catalog.pg_proc'::pg_catalog.regclass::pg_catalog.oid" in sql
    assert "e.classid OPERATOR(pg_catalog.=) 'pg_catalog.pg_operator'::pg_catalog.regclass::pg_catalog.oid" in sql


async def test_allowed_implementations_seed_types_and_relation_columns() -> None:
    """Имена SQL агента разрешаются по search_path, как их разрешит PREPARE; к ним — типы найденных функций."""
    recorder = _Recorder()

    await allowed_implementations(
        recorder, "public", operators=[], functions=[], types=['"app_t"'], relations=['"public"."app_x"']
    )

    [sql] = recorder.sent
    assert "pg_catalog.to_regtype(n.name)" in sql
    assert "pg_catalog.to_regclass(n.name)" in sql
    assert """ARRAY['"app_t"']::pg_catalog.text[]""" in sql
    assert """ARRAY['"public"."app_x"']::pg_catalog.text[]""" in sql
    for column in ("p.prorettype", "p.proargtypes", "p.proallargtypes", "o.oprleft", "o.oprright", "o.oprresult"):
        assert column in sql
    assert "a.aggtranstype" in sql


async def test_allowed_implementations_default_to_no_seeds() -> None:
    recorder = _Recorder()

    await allowed_implementations(recorder, "public", operators=["="], functions=[])

    [sql] = recorder.sent
    assert sql.count("ARRAY[]::pg_catalog.text[]") == 2


def test_definition_machinery_is_seeded_by_locked_relations_and_dependency_types() -> None:
    """Колонки заблокированных отношений, целей DML и их потомков, типы из pg_depend определений."""
    assert {"relation_set", "types", "pg_catalog.pg_attribute"} <= _cte_sources("type_seed_set")
```

Run: `uv run pytest tests/unit/postgres/test_plan_catalog.py -q`
Expected: FAIL (`allowed_implementations() got an unexpected keyword argument 'types'`, фрагментов нет).

- [ ] **Step 2: `_type_machinery` и оба запроса в `plan_catalog.py`**

1. Сразу после функции `_definition_rows` вставить:

```python
# Машинерия типов: то, что Postgres вызывает для значения типа сам, без имени функции или оператора в тексте.
# Функции ввода и вывода (typinput при разборе константы 'x'::тип и неявного приведения литерала к типу колонки —
# при PREPARE; typoutput при печати константы в EXPLAIN VERBOSE и при выдаче результата), двоичного обмена,
# модификатора типа (typmodin — при разборе тип(10), typmodout — при печати), сбора статистики и индексирования
# (typsubscript — при разборе x[1]); функции приведения (pg_cast.castfunc: явные, неявные и присваивания —
# INSERT в колонку типа без имени типа в тексте), в обе стороны; классы операторов типа (любого метода доступа):
# все опорные функции (pg_amproc) и операторы (pg_amop) их семейств — сравнение btree и хеш-функцию вызывают
# ORDER BY, DISTINCT, GROUP BY, UNION, GREATEST/LEAST, сравнение массивов, соединения слиянием и хешем, оценка
# селективности; у операторов — их функция и функции оценки (oprrest, oprjoin); у диапазона — canonical, subdiff
# и класс операторов подтипа (rngsubopc); CHECK доменов (при приведении к домену: typcache сворачивает
# IMMUTABLE-вызовы CHECK уже при планировании).
#
# Типы берутся замыканием от семян (seeds) по тому, что вызывает машинерию вложенных типов: домен -> базовый тип,
# массив -> элемент, составной тип -> типы атрибутов, диапазон -> подтип, мультидиапазон -> диапазон. Типы
# pg_catalog не раскрываются и не проверяются (встроенная машинерия); их элементы и атрибуты — тоже pg_catalog.
# Множества — массивами через unnest (_oid_set): оценка рекурсии через соединения иначе растёт выше jit_above_cost.
#
# Строки machinery (kind, schema, name, parent_schema, definition): type_function — функция машинерии типа
# (ввод-вывод, опорная функция класса операторов, оценка селективности, canonical/subdiff); function — функция
# приведения; operator и operator_function — оператор семейства и его функция (parent_schema — схема оператора);
# domain — текст CHECK домена.
#
# Функции и операторы расширений (pg_depend, deptype 'e') в строки machinery не попадают:
# машинерию типа расширения (citext, hstore, PostGIS) ставит скрипт расширения, в том числе в схему вне public
# (extensions у Supabase), и её функции ввода-вывода вызывает любое чтение колонки такого типа. Обёртка DBA над
# функцией чужой схемы членом расширения не бывает, пока её не добавят в расширение явно (ALTER EXTENSION ... ADD).
_PG_CATALOG_NAMESPACE = "'pg_catalog'::pg_catalog.regnamespace::pg_catalog.oid"
_MACHINERY_TYPES = _oid_set("machinery_set", "m")


def _not_extension_member(catalog: str, alias: str) -> str:
    """Условие: объект alias каталога catalog не входит в расширение (pg_depend, deptype 'e')."""
    return (
        "NOT EXISTS (SELECT FROM pg_catalog.pg_depend e "
        f"WHERE e.classid OPERATOR(pg_catalog.=) {catalog} AND e.objid OPERATOR(pg_catalog.=) {alias}.oid "
        "AND e.deptype OPERATOR(pg_catalog.=) 'e')"
    )


def _function_rows(kind: str, source: str, function: str, parent: str = _NO_PARENT) -> str:
    """Строки функций машинерии: function — выражение oid функции над source (0 — функции нет, строка пропадает).

    Функции расширений пропускаются.
    """
    return (
        f"SELECT '{kind}', fn.nspname, f.proname, {parent}, {_NO_DEFINITION} FROM {source} "  # noqa: S608
        f"JOIN pg_catalog.pg_proc f ON f.oid OPERATOR(pg_catalog.=) ({function})::pg_catalog.oid "
        "JOIN pg_catalog.pg_namespace fn ON fn.oid OPERATOR(pg_catalog.=) f.pronamespace "
        f"WHERE {_not_extension_member(_PG_PROC, 'f')}"
    )


def _type_machinery(seeds: str) -> str:
    """CTE машинерии типов от семян seeds (подзапрос oid типов; NULL допустим): последняя — machinery."""
    return (
        f"type_seed_set(oids) AS (SELECT ARRAY({seeds})), "  # noqa: S608
        "type_closure(oid) AS ("
        f"SELECT s.oid FROM {_oid_set('type_seed_set', 's')} "
        "UNION "
        "SELECT n.oid FROM type_closure c JOIN pg_catalog.pg_type t ON t.oid OPERATOR(pg_catalog.=) c.oid "
        "CROSS JOIN LATERAL ("
        "SELECT t.typbasetype UNION ALL SELECT t.typelem "
        "UNION ALL SELECT a.atttypid FROM pg_catalog.pg_attribute a "
        f"WHERE a.attrelid OPERATOR(pg_catalog.=) t.typrelid AND {_LIVE_COLUMN} "
        "UNION ALL SELECT r.rngsubtype FROM pg_catalog.pg_range r WHERE r.rngtypid OPERATOR(pg_catalog.=) t.oid "
        "UNION ALL SELECT r.rngtypid FROM pg_catalog.pg_range r WHERE r.rngmultitypid OPERATOR(pg_catalog.=) t.oid"
        ") AS n(oid) "
        f"WHERE t.typnamespace OPERATOR(pg_catalog.<>) {_PG_CATALOG_NAMESPACE} "
        "AND n.oid OPERATOR(pg_catalog.<>) 0::pg_catalog.oid"
        "), machinery_set(oids) AS ("
        "SELECT ARRAY(SELECT DISTINCT t.oid FROM type_closure c "
        "JOIN pg_catalog.pg_type t ON t.oid OPERATOR(pg_catalog.=) c.oid "
        f"WHERE t.typnamespace OPERATOR(pg_catalog.<>) {_PG_CATALOG_NAMESPACE})"
        "), families(oid) AS ("
        "SELECT DISTINCT f.oid FROM ("
        f"SELECT oc.opcfamily FROM {_MACHINERY_TYPES} "
        "JOIN pg_catalog.pg_opclass oc ON oc.opcintype OPERATOR(pg_catalog.=) m.oid "
        "UNION ALL "
        f"SELECT oc.opcfamily FROM {_MACHINERY_TYPES} "
        "JOIN pg_catalog.pg_range r ON r.rngtypid OPERATOR(pg_catalog.=) m.oid "
        "JOIN pg_catalog.pg_opclass oc ON oc.oid OPERATOR(pg_catalog.=) r.rngsubopc"
        ") AS c(family) JOIN pg_catalog.pg_opfamily f ON f.oid OPERATOR(pg_catalog.=) c.family "
        f"WHERE f.opfnamespace OPERATOR(pg_catalog.<>) {_PG_CATALOG_NAMESPACE}"
        "), family_operators AS ("
        "SELECT DISTINCT o.oid, o.oprname, o.oprnamespace, o.oprcode, o.oprrest, o.oprjoin FROM families y "
        "JOIN pg_catalog.pg_amop a ON a.amopfamily OPERATOR(pg_catalog.=) y.oid "
        "JOIN pg_catalog.pg_operator o ON o.oid OPERATOR(pg_catalog.=) a.amopopr "
        f"WHERE {_not_extension_member(_PG_OPERATOR, 'o')}"
        "), machinery(kind, schema, name, parent_schema, definition) AS ("
        + _function_rows(
            "type_function",
            f"{_MACHINERY_TYPES} JOIN pg_catalog.pg_type t ON t.oid OPERATOR(pg_catalog.=) m.oid "
            "CROSS JOIN LATERAL (VALUES (t.typinput), (t.typoutput), (t.typreceive), (t.typsend), (t.typmodin), "
            "(t.typmodout), (t.typanalyze), (t.typsubscript)) AS s(fn)",
            "s.fn",
        )
        + " UNION ALL "
        + _function_rows(
            "type_function",
            f"{_MACHINERY_TYPES} JOIN pg_catalog.pg_range r ON r.rngtypid OPERATOR(pg_catalog.=) m.oid "
            "CROSS JOIN LATERAL (VALUES (r.rngcanonical), (r.rngsubdiff)) AS s(fn)",
            "s.fn",
        )
        + " UNION ALL "
        + _function_rows(
            "type_function",
            "families y JOIN pg_catalog.pg_amproc p ON p.amprocfamily OPERATOR(pg_catalog.=) y.oid",
            "p.amproc",
        )
        + " UNION ALL "
        + _function_rows(
            "type_function",
            "family_operators o CROSS JOIN LATERAL (VALUES (o.oprrest), (o.oprjoin)) AS s(fn)",
            "s.fn",
        )
        + " UNION ALL "  # noqa: S608
        f"SELECT 'operator', opn.nspname, o.oprname, {_NO_PARENT}, {_NO_DEFINITION} FROM family_operators o "
        "JOIN pg_catalog.pg_namespace opn ON opn.oid OPERATOR(pg_catalog.=) o.oprnamespace"
        " UNION ALL "
        + _function_rows(
            "operator_function",
            "family_operators o JOIN pg_catalog.pg_namespace opn ON opn.oid OPERATOR(pg_catalog.=) o.oprnamespace",
            "o.oprcode",
            "opn.nspname",
        )
        + " UNION ALL "
        + _function_rows(
            "function",
            f"{_MACHINERY_TYPES} JOIN pg_catalog.pg_cast k ON k.castsource OPERATOR(pg_catalog.=) m.oid",
            "k.castfunc",
        )
        + " UNION ALL "
        + _function_rows(
            "function",
            f"{_MACHINERY_TYPES} JOIN pg_catalog.pg_cast k ON k.casttarget OPERATOR(pg_catalog.=) m.oid",
            "k.castfunc",
        )
        + " UNION ALL "  # noqa: S608
        f"SELECT 'domain', {_NO_NAME}, {_NO_NAME}, {_NO_PARENT}, "
        "pg_catalog.pg_get_expr(k.conbin, 0::pg_catalog.oid) "
        f"FROM {_MACHINERY_TYPES} JOIN pg_catalog.pg_constraint k ON k.contypid OPERATOR(pg_catalog.=) m.oid "
        "WHERE k.conbin IS NOT NULL"
        ")"
    )
```

2. В комментарии перед `DEFINITION_DEPENDENCIES_SQL` после абзаца, который кончается «…они видны только в тексте.», добавить:

```python
#
# Машинерия типов (_type_machinery) — от колонок relations (заблокированные отношения, цели DML, их потомки) и типов
# из pg_depend (types): строки machinery того же вида (type_function, function, operator, operator_function, domain).
```

3. В `DEFINITION_DEPENDENCIES_SQL` конец CTE `types` (строки `"WHERE v.next OPERATOR(pg_catalog.<>) 0::pg_catalog.oid"` и `") "`) заменить на:

```python
    "WHERE v.next OPERATOR(pg_catalog.<>) 0::pg_catalog.oid"
    "), "
    + _type_machinery(
        f"SELECT a.atttypid FROM {_RELATIONS} "  # noqa: S608
        f"JOIN pg_catalog.pg_attribute a ON a.attrelid OPERATOR(pg_catalog.=) t.oid WHERE {_LIVE_COLUMN} "
        "UNION ALL SELECT y.oid FROM types y"
    )
    + " "  # noqa: S608
    f"SELECT 'rule' AS kind, {_NO_NAME} AS schema, {_NO_NAME} AS name, {_NO_PARENT} AS parent_schema, "
```

а последнюю строку запроса (`"LEFT JOIN pg_catalog.pg_namespace cn ON cn.oid OPERATOR(pg_catalog.=) c.relnamespace"` перед `)`) — на:

```python
    "LEFT JOIN pg_catalog.pg_namespace cn ON cn.oid OPERATOR(pg_catalog.=) c.relnamespace "
    "UNION ALL "
    f"SELECT DISTINCT m.kind, m.schema, m.name, m.parent_schema, {_NO_RELATION}, m.definition FROM machinery m"
)
```

(`domain_checks`, `column_types` и `domain_defaults` пути записи не меняются: `CHECK` доменов целей DML дают и они — одинаковые строки убирает `_seen_rows`.)

4. Комментарий и `ALLOWED_IMPLEMENTATIONS_SQL` целиком заменить на (`WITH RECURSIVE`; операторы с типами и функциями оценки; функции с типами; CTE `seeds`; строки `type_function` оценки и `machinery`):

```python
# Реализации операторов и функций allowed_schema по именам. План и SQL агента печатают их без схемы и без типов
# аргументов, поэтому берутся все перегрузки с этим именем. Строки — того же вида, что у DEFINITION_DEPENDENCIES_SQL
# (их проверяет тот же разбор): operator_function — функция оператора (oprcode); aggregate_function — опорная
# функция агрегата; operator и operator_function — оператор сортировки агрегата (aggsortop: min/max планировщик
# заменяет индексным сканом с этим оператором) и его функция; sql_body — тело SQL-функции как написано (prosrc;
# config — proconfig: SET search_path меняет разрешение имён тела); sql_atomic_body — тело BEGIN ATOMIC / RETURN
# (pg_get_function_sqlbody: имена вне search_path — со схемой); argument_defaults — умолчания аргументов функции
# любого языка через запятую (pg_get_expr(proargdefaults)): планировщик подставляет их в вызов без этих аргументов
# и сворачивает IMMUTABLE. parent_schema — схема оператора или агрегата. type_function — функции оценки
# селективности (oprrest, oprjoin) этих операторов: их вызывает планировщик. Плюс строки машинерии типов
# (_type_machinery) от семян seeds: типы ({types}) и колонки отношений ({relations}) SQL агента, типы аргументов и
# результатов найденных функций и операторов, типы состояния агрегатов.
ALLOWED_IMPLEMENTATIONS_SQL = (
    "WITH RECURSIVE operators AS ("  # noqa: S608
    "SELECT o.oprcode, o.oprrest, o.oprjoin, o.oprleft, o.oprright, o.oprresult FROM pg_catalog.pg_operator o "
    "JOIN pg_catalog.pg_namespace n ON n.oid OPERATOR(pg_catalog.=) o.oprnamespace "
    "WHERE n.nspname OPERATOR(pg_catalog.=) {schema} AND o.oprname OPERATOR(pg_catalog.=) ANY ({operators})"
    "), functions AS ("
    "SELECT p.oid, p.proname, p.prolang, p.prosrc, p.prosqlbody IS NOT NULL AS atomic, p.proconfig, "
    "p.proargdefaults, p.prorettype, p.proargtypes, p.proallargtypes "
    "FROM pg_catalog.pg_proc p "
    "JOIN pg_catalog.pg_namespace n ON n.oid OPERATOR(pg_catalog.=) p.pronamespace "
    "WHERE n.nspname OPERATOR(pg_catalog.=) {schema} AND p.proname OPERATOR(pg_catalog.=) ANY ({functions})"
    "), aggregates AS ("
    "SELECT a.* FROM functions p "
    "JOIN pg_catalog.pg_aggregate a ON a.aggfnoid::pg_catalog.oid OPERATOR(pg_catalog.=) p.oid"
    "), sort_operators AS ("
    "SELECT o.oprname, o.oprnamespace, o.oprcode FROM aggregates a "
    "JOIN pg_catalog.pg_operator o ON o.oid OPERATOR(pg_catalog.=) a.aggsortop"
    "), seeds(oid) AS ("
    "SELECT pg_catalog.to_regtype(n.name)::pg_catalog.oid FROM pg_catalog.unnest({types}) AS n(name) "
    "UNION ALL SELECT a.atttypid FROM pg_catalog.unnest({relations}) AS n(name) "
    "JOIN pg_catalog.pg_attribute a "
    "ON a.attrelid OPERATOR(pg_catalog.=) pg_catalog.to_regclass(n.name)::pg_catalog.oid "
    f"WHERE {_LIVE_COLUMN} "
    "UNION ALL SELECT p.prorettype FROM functions p "
    "UNION ALL SELECT v.oid FROM functions p CROSS JOIN LATERAL "
    "pg_catalog.unnest(pg_catalog.array_cat(p.proargtypes::pg_catalog.oid[], p.proallargtypes)) AS v(oid) "
    "UNION ALL SELECT v.oid FROM operators o "
    "CROSS JOIN LATERAL (VALUES (o.oprleft), (o.oprright), (o.oprresult)) AS v(oid) "
    "UNION ALL SELECT a.aggtranstype FROM aggregates a"
    "), " + _type_machinery("SELECT s.oid FROM seeds s") + " "  # noqa: S608
    "SELECT 'operator_function' AS kind, fn.nspname AS schema, f.proname AS name, "
    "{schema}::pg_catalog.name AS parent_schema, NULL::pg_catalog.text AS definition, "
    "NULL::pg_catalog.text[] AS config "
    "FROM operators o JOIN pg_catalog.pg_proc f ON f.oid OPERATOR(pg_catalog.=) o.oprcode::pg_catalog.oid "
    "JOIN pg_catalog.pg_namespace fn ON fn.oid OPERATOR(pg_catalog.=) f.pronamespace "
    "UNION ALL "
    "SELECT 'aggregate_function', fn.nspname, f.proname, {schema}::pg_catalog.name, NULL, NULL "
    f"FROM aggregates a CROSS JOIN LATERAL (VALUES {_AGGREGATE_SUPPORT_FUNCTIONS}) AS s(fn) "
    "JOIN pg_catalog.pg_proc f ON f.oid OPERATOR(pg_catalog.=) s.fn::pg_catalog.oid "
    "JOIN pg_catalog.pg_namespace fn ON fn.oid OPERATOR(pg_catalog.=) f.pronamespace "
    "UNION ALL "
    "SELECT 'operator', opn.nspname, o.oprname, NULL, NULL, NULL FROM sort_operators o "
    "JOIN pg_catalog.pg_namespace opn ON opn.oid OPERATOR(pg_catalog.=) o.oprnamespace "
    "UNION ALL "
    "SELECT 'operator_function', fn.nspname, f.proname, opn.nspname, NULL, NULL FROM sort_operators o "
    "JOIN pg_catalog.pg_namespace opn ON opn.oid OPERATOR(pg_catalog.=) o.oprnamespace "
    "JOIN pg_catalog.pg_proc f ON f.oid OPERATOR(pg_catalog.=) o.oprcode::pg_catalog.oid "
    "JOIN pg_catalog.pg_namespace fn ON fn.oid OPERATOR(pg_catalog.=) f.pronamespace "
    "UNION ALL "
    "SELECT CASE WHEN p.atomic THEN 'sql_atomic_body' ELSE 'sql_body' END, {schema}::pg_catalog.name, p.proname, "
    "NULL, CASE WHEN p.atomic THEN pg_catalog.pg_get_function_sqlbody(p.oid) ELSE p.prosrc END, p.proconfig "
    "FROM functions p JOIN pg_catalog.pg_language l ON l.oid OPERATOR(pg_catalog.=) p.prolang "
    "WHERE l.lanname OPERATOR(pg_catalog.=) 'sql' "
    "UNION ALL "
    "SELECT 'argument_defaults', {schema}::pg_catalog.name, p.proname, NULL, "
    "pg_catalog.pg_get_expr(p.proargdefaults, 0::pg_catalog.oid), NULL FROM functions p "
    "WHERE p.proargdefaults IS NOT NULL "
    "UNION ALL "
    "SELECT 'type_function', fn.nspname, f.proname, NULL, NULL, NULL FROM operators o "
    "CROSS JOIN LATERAL (VALUES (o.oprrest), (o.oprjoin)) AS s(fn) "
    "JOIN pg_catalog.pg_proc f ON f.oid OPERATOR(pg_catalog.=) s.fn::pg_catalog.oid "
    "JOIN pg_catalog.pg_namespace fn ON fn.oid OPERATOR(pg_catalog.=) f.pronamespace "
    "UNION ALL "
    "SELECT DISTINCT m.kind, m.schema, m.name, m.parent_schema, m.definition, NULL::pg_catalog.text[] FROM machinery m"
)
```

5. После `_name_array` добавить:

```python
def _text_array(values: Collection[str]) -> Composable:
    """ARRAY[...]::pg_catalog.text[] из Literal."""
    return SQL("ARRAY[{}]::pg_catalog.text[]").format(SQL(", ").join(Literal(value) for value in values))
```

6. `allowed_implementations` заменить на:

```python
async def allowed_implementations(  # noqa: PLR0913
    run: StatementRunner,
    schema: str,
    *,
    operators: Collection[str],
    functions: Collection[str],
    types: Collection[str] = (),
    relations: Collection[str] = (),
) -> list[RowResult] | None:
    """Реализации операторов и агрегатов схемы schema с этими именами, тела и умолчания аргументов её функций.

    И машинерия типов (_type_machinery) от семян: типы types (текст для to_regtype) и колонки отношений relations
    (текст для to_regclass) — имена SQL агента, разрешаемые по search_path, как их разрешит PREPARE, — плюс типы
    аргументов и результатов найденных функций и операторов и типы состояния агрегатов.
    """
    sql = (
        SQL(ALLOWED_IMPLEMENTATIONS_SQL)
        .format(
            schema=Literal(schema),
            operators=_name_array(operators),
            functions=_name_array(functions),
            types=_text_array(types),
            relations=_text_array(relations),
        )
        .as_string()
    )
    return await run(sql)
```

- [ ] **Step 3: Падающие тесты `PlanGuard` и правка существующих**

Первый запрос реализаций теперь уходит до `PREPARE` у любого оператора с отношением или именем типа (семена). Правки существующих тестов `tests/unit/postgres/test_plan_guard.py`:

1. `_Rows.__call__`: `if "pg_catalog.pg_rewrite" in sql:` → `if "pg_catalog.pg_rewrite" in sql or "pg_catalog.pg_aggregate" in sql:`.
2. `_step`: после ветки `rules` добавить

```python
    if "pg_catalog.pg_aggregate" in sql:
        return "implementations"
```

   В `test_definitions_are_checked_between_prepare_and_explain` ожидаемый журнал начинается с `"implementations",`.
3. `test_public_operator_in_the_plan_is_checked_by_its_function`: `implementations=_BANG_SETTING` → `implementations={"!!": _BANG_SETTING}` (список отвечал бы и на запрос семян), а проверка запросов — на

```python
    [footprint, query] = explain.implementation_queries
    assert "'!!'" not in footprint
    assert "'!!'" in query
```

4. `test_plan_only_names_get_a_second_lookup`: `[query] = …` → `[footprint, query] = explain.implementation_queries` и `assert "'app_agg'" not in footprint` перед `assert "'app_agg'" in query`.
5. `test_no_lookup_without_names_of_the_allowed_schema` заменить на:

```python
async def test_no_name_lookup_without_names_of_the_allowed_schema() -> None:
    """Выражения плана без имён allowed_schema не дают второго запроса; первый — семена машинерии типов SQL агента."""
    node = _with(Output=["pg_catalog.lower(app_t.name)", "(app_t.a OPERATOR(pg_catalog.=) 1)"])
    explain = _Explain({_EXPLAIN + _SELECT: node})

    await _guard(explain).check(_SELECT)

    [footprint] = explain.implementation_queries
    assert footprint.count("ARRAY[]::pg_catalog.name[]") == 2
```

6. `test_types_of_non_planned_statements_are_checked_but_their_names_are_not_looked_up` — docstring и проверка:

```python
    """PREPARE агента разбирает текст при выполнении (ввод констант): его отношения — семена машинерии типов."""
    explain = _Explain()

    await _guard(explain).check("PREPARE p AS SELECT app_agg(id) FROM app_t WHERE id <~> 1")

    [footprint] = explain.implementation_queries
    assert "'app_agg'" not in footprint
    assert "'<~>'" not in footprint
    assert "'\"app_t\"'" in footprint
```

7. `test_constructs_without_an_operator_do_not_look_up_implementations` переименовать в `test_constructs_without_an_operator_look_up_no_operators`, тело:

```python
    explain = _Explain()

    await _guard(explain).check(sql)

    [footprint] = explain.implementation_queries
    assert footprint.count("ARRAY[]::pg_catalog.name[]") == 2
```

8. `test_chain_of_bodies_within_the_depth_passes`: `== 4` → `== 5` с комментарием `# Семена машинерии типов SQL агента до PREPARE и четыре круга тел.`; `test_recursive_body_is_looked_up_once`: `== 1` → `== 2`.

Новые тесты — в конец файла:

```python
_SECRET_TYPE_INPUT = {"kind": "type_function", "schema": "secret", "name": "t_in"}


@pytest.mark.parametrize(
    ("sql", "seed"),
    [
        ("SELECT '5'::app_t", '"app_t"'),
        ("SELECT '5'::public.app_t", '"public"."app_t"'),
        ("SELECT '(abc)'::app_pair", '"app_pair"'),
        ("INSERT INTO app_items (t) VALUES ('abc')", '"app_items"'),
        ("SELECT * FROM public.app_items", '"public"."app_items"'),
    ],
)
async def test_type_machinery_of_the_statement_is_checked_before_prepare(sql: str, seed: str) -> None:
    """Ввод константы ('5'::тип) и неявное приведение литерала к типу колонки выполняет уже PREPARE: имена типов и
    отношений SQL агента — семена машинерии в запросе реализаций до него."""
    explain = _Explain(implementations={seed: [_SECRET_TYPE_INPUT]})

    with pytest.raises(PlanAccessError, match=r"function 'secret\.t_in'"):
        await _guard(explain).check(sql)

    assert explain.prepared == []


async def test_builtin_type_names_are_not_seeds() -> None:
    explain = _Explain()

    await _guard(explain).check("SELECT 1::integer, 'x'::pg_catalog.text")

    assert explain.implementation_queries == []


@pytest.mark.parametrize(
    ("row", "error"),
    [
        ({"kind": "type_function", "schema": "secret", "name": "ct_cmp"}, r"function 'secret\.ct_cmp'"),
        ({"kind": "function", "schema": "secret", "name": "int_to_e"}, r"function 'secret\.int_to_e'"),
        ({"kind": "function", "schema": "pg_catalog", "name": "current_setting"}, r"'pg_catalog\.current_setting'"),
        (
            {"kind": "operator_function", "schema": "pg_catalog", "name": "current_setting", "parent_schema": "public"},
            r"'pg_catalog\.current_setting'",
        ),
        ({"kind": "operator", "schema": "secret", "name": "#<"}, r"function 'secret\.#<'"),
        ({"kind": "domain", "definition": "(VALUE > secret.boom('dom'::text))"}, r"function 'secret\.boom'"),
    ],
)
async def test_type_machinery_rows_follow_the_basic_rules(row: dict[str, Any], error: str) -> None:
    """Машинерия типов колонок заблокированных отношений и зависимостей правил: опорная функция класса операторов,
    функция приведения (как функции: pg_catalog — только из списка basic), оператор семейства, CHECK домена.
    Отказ — до EXPLAIN."""
    explain = _Explain(rules=[row])

    with pytest.raises(PlanAccessError, match=error):
        await _guard(explain).check(_SELECT)

    assert [sql for sql in explain.log if sql.startswith("EXPLAIN")] == []


@pytest.mark.parametrize("name", ["enum_out", "btint4cmp", "eqsel", "current_setting"])
async def test_builtin_type_functions_pass(name: str) -> None:
    """Функция машинерии из pg_catalog — любая: её сигнатуру (cstring, internal, сравнение двух значений типа) задаёт
    Postgres, и вызывается она только со значением типа."""
    explain = _Explain(rules=[{"kind": "type_function", "schema": "pg_catalog", "name": name}])

    await _guard(explain).check(_SELECT)


async def test_public_type_function_is_checked_by_its_body() -> None:
    explain = _Explain(
        rules=[{"kind": "type_function", "schema": "public", "name": "app_ct_cmp"}],
        implementations={"app_ct_cmp": [_body("app_ct_cmp", "SELECT count(*)::int FROM secret.accounts")]},
    )

    with pytest.raises(PlanAccessError, match=r"relation 'secret\.accounts'"):
        await _guard(explain).check(_SELECT)
```

В `tests/unit/postgres/test_safe_sql_executor.py`:

- `test_plan_is_checked_in_the_statement_transaction` — после `assert delegate.sent[0] == _SETTINGS`:

```python
        # Машинерия типов колонок app_t — до PREPARE (ввод констант выполняет разбор).
        assert delegate.sent[1].startswith("/* t */ WITH RECURSIVE operators AS")
```

  и индексы дальше сдвинуть на один (`PREPARE` — `sent[2]`, определения — `sent[3]`, `EXPLAIN` — `sent[4]`, определения — `sent[5]`, оператор — `sent[6:]`);
- `test_builtin_type_names_are_loaded_once_per_executor`: `catalog = [q for q in delegate.sent if "pg_catalog.pg_rewrite" not in q and "pg_catalog.pg_aggregate" not in q]` (запрос реализаций теперь тоже содержит `pg_catalog.pg_type` и `typrelid`).

Run: `uv run pytest tests/unit/postgres -q`
Expected: FAIL (новые тесты: `type_function` — незнакомый вид, семян нет).

- [ ] **Step 4: `PlanGuard`**

В `src/postgres_fastmcp/postgres/security/plan_guard.py`:

1. Docstring модуля: после предложения «Умолчания аргументов функции allowed_schema любого языка (…) проверяются как текст определения.» добавить

```text
Машинерия типов (plan_catalog._type_machinery) — функции ввода-вывода, приведения, классы операторов, CHECK доменов,
которые Postgres вызывает сам, без имени в тексте, — проверяется для типов, до которых доходит оператор (и вложенных
в них): те же строки, что у определений, плюс вид type_function (встроенная — любая функция pg_catalog).
```

   В абзаце «Порядок.» после «(функция оператора и опорные функции агрегата; вызов с константами планировщик выполнил бы)» вставить «, и машинерия его типов и типов колонок его отношений (ввод константы и приведение литерала к типу колонки выполняет уже PREPARE)»; после «(свёртка констант, LIMIT, функции операторов и агрегатов public, всё время записи)» — «; машинерия типов колонок заблокированного и типов из зависимостей».
2. Импорт `pglast.ast`: добавить `RangeVar` (после `ParamRef`).
3. `_StatementNames`: в docstring после «…их реализация проверяется до PREPARE и EXPLAIN.» добавить «Типы и отношения (их колонки) — семена машинерии типов: ввод константы и неявное приведение литерала к типу колонки выполняются уже при PREPARE.»; в `__init__` — `self.relations: list[tuple[str | None, str]] = []`; после `visit_TypeName`:

```python
    def visit_RangeVar(self, _ancestors: object, node: RangeVar) -> None:  # noqa: N802
        """Отношение (или имя CTE: каталог его не найдёт или найдёт одноимённую таблицу — лишнее семя)."""
        if node.relname:
            self.relations.append((node.schemaname, node.relname))
```

4. Перед `_function_call_names`:

```python
def _quoted_name(schema: str | None, name: str) -> str:
    """Имя в кавычках ("схема"."имя" или "имя") для to_regtype/to_regclass: разбор по search_path, как у PREPARE."""
    return Identifier(name).as_string() if schema is None else Identifier(schema, name).as_string()
```

5. `_CatalogNames`: после поля `relations`:

```python
    # Семена машинерии типов из SQL агента: имена типов (для to_regtype) и отношений, чьи колонки — семена
    # (для to_regclass); текст — идентификаторы в кавычках, разрешаются по search_path, как их разрешит PREPARE.
    footprint_types: dict[str, None] = field(default_factory=dict)
    footprint_relations: dict[str, None] = field(default_factory=dict)

    def _all(self) -> tuple[dict[Any, None], ...]:
        """Все множества в порядке полей."""
        return (
            self.functions,
            self.unqualified_types,
            self.schema_types,
            self.implementations,
            self.relations,
            self.footprint_types,
            self.footprint_relations,
        )

    def take(self) -> Self:
        """Забрать накопленное: self пустеет, имена следующего круга копятся в нём заново."""
        taken = type(self)(*(dict(names) for names in self._all()))
        for names in self._all():
            names.clear()
        return taken

    def empty(self) -> bool:
        """Спрашивать каталог больше нечего."""
        return not any(self._all())
```

   (прежние `take` и `empty` удалить).
6. `_check_rule_row`: после ветки `kind == "function"`:

```python
        elif kind == "type_function":
            # Машинерия типа (ввод-вывод, опорная функция класса операторов, оценка селективности, canonical/subdiff
            # диапазона): встроенная — любая функция pg_catalog, её сигнатуру (cstring, internal) задаёт Postgres;
            # иначе — функция allowed_schema (с проверкой тела и умолчаний), чужая схема — отказ.
            if schema != _BUILTIN_FUNCTION_SCHEMA:
                self._check_function(schema, name)
                self._note_implementation(FUNCTION_KIND, schema, name, pending)
```

7. `_check_statement_names`: в docstring добавить абзац

```text
        Машинерия типов (plan_catalog._type_machinery): имена типов и колонки отношений SQL агента — семена
        того же запроса реализаций. Функцию ввода типа ('x'::тип, INSERT литерала в колонку типа) и подтипа
        диапазона вместе с CHECK домена выполняет уже PREPARE.
```

   а цикл по типам заменить на:

```python
        for schema, name in collector.types:
            self._check_type(schema, name, pending)
            if schema != _BUILTIN_FUNCTION_SCHEMA:
                pending.footprint_types[_quoted_name(schema, name)] = None
        for schema, name in collector.relations:
            pending.footprint_relations[_quoted_name(schema, name)] = None
```

8. `_check_catalog_names`: `await self._check_implementations(current.implementations, pending)` → `await self._check_implementations(current, pending)`.
9. `_check_implementations` целиком:

```python
    async def _check_implementations(self, current: _CatalogNames, pending: _CatalogNames) -> None:
        """Функции операторов и агрегатов allowed_schema, тела её SQL-функций и машинерия типов — по правилам basic.

        Семена машинерии — типы и отношения SQL агента (current.footprint_*) и типы найденных функций и операторов.
        """
        if not (current.implementations or current.footprint_types or current.footprint_relations):
            return
        keys = list(current.implementations)
        self._looked_up.update(keys)
        rows = await allowed_implementations(
            self._run,
            self._allowed_schema,
            operators=[name for kind, name in keys if kind == _OPERATOR_KIND],
            functions=[name for kind, name in keys if kind == FUNCTION_KIND],
            types=list(current.footprint_types),
            relations=list(current.footprint_relations),
        )
        if rows is None:
            raise PlanUnverifiableError(rules=True)
        for row in rows:
            self._check_rule_row(row.cells, pending)
```

Run: `uv run pytest tests/unit -q`
Expected: PASS.

- [ ] **Step 5: Проверки и коммит**

Run: `uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check . && uv run pytest tests/unit -q`
Expected: чисто; PASS. Если ruff отметит `S608` на строке, где начинается новая конкатенация SQL (`+ " UNION ALL "` перед f-строкой, `+ " "` перед `SELECT`), поставить там `# noqa: S608`, как у соседних; лишний `noqa` (`RUF100`) — убрать.

```bash
git add src/postgres_fastmcp/postgres/security/plan_catalog.py src/postgres_fastmcp/postgres/security/plan_guard.py \
    tests/unit/postgres/test_plan_catalog.py tests/unit/postgres/test_plan_guard.py \
    tests/unit/postgres/test_safe_sql_executor.py
git commit -m "feat(security): check the type machinery a query reaches in plan_check"
```

---

### Task 2: Интеграция на живом Postgres и README

**Files:**
- Test: `tests/integration/test_plan_check.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 1 (строки машинерии в обоих запросах, семена SQL агента); фикстуры `db_plan_check`, `db_full`; `public.app_close_to(int, int)` из `_SETUP`.
- Produces: фикстура `db_implicit_calls`; тесты `test_implicit_call_of_a_type_is_rejected_before_it_runs` (14 случаев), `test_type_with_builtin_machinery_passes`.

- [ ] **Step 1: Интеграционные тесты**

В конец `tests/integration/test_plan_check.py`:

```python
# Неявные вызовы типов (машинерия): функции ввода-вывода, приведения, классы операторов, CHECK доменов. Бросающие
# функции доказывают «отклонено до выполнения»: без проверки PREPARE, EXPLAIN или выполнение получили бы их
# исключение. Функции ввода-вывода — обёртки LANGUAGE internal (нужен суперпользователь): ввод int4in бросает на
# 'abc', вывод enum_out бросает на любом значении, которое не метка перечисления. Операторы класса названы #<, #=
# и т. д.: операторы public с именами =, < проверяются по имени, все перегрузки сразу, и затронули бы любые тесты.
_DROP_IMPLICIT_CALLS = """
DROP VIEW IF EXISTS public.app_ic_dom_view, public.app_ic_hidden_dom_view, public.app_ic_sorted_view;
DROP TABLE IF EXISTS public.app_ic_cast_t, public.app_ic_io_t, public.app_ic_ct_t, public.app_ic_ok_t;
DROP TYPE IF EXISTS public.app_ic_pair, public.app_ic_rr, public.app_ic_e, public.app_ic_ok CASCADE;
DROP DOMAIN IF EXISTS public.app_ic_rd CASCADE;
DROP TYPE IF EXISTS public.app_ic_t, public.app_ic_ct CASCADE;
DROP OPERATOR IF EXISTS public.#~ (int, int);
DROP FUNCTION IF EXISTS secret.ic_sel(internal, oid, internal, integer);
"""
_IC_BOOM_RETURNS = """
CREATE OR REPLACE FUNCTION secret.ic_boom() RETURNS int LANGUAGE plpgsql IMMUTABLE AS $$BEGIN RETURN 0; END$$;
"""
_IC_BOOM_RAISES = """
CREATE OR REPLACE FUNCTION secret.ic_boom() RETURNS int
    LANGUAGE plpgsql IMMUTABLE AS $$BEGIN RAISE EXCEPTION 'secret.ic_boom was executed'; END$$;
"""
_IMPLICIT_CALLS = """
CREATE TYPE public.app_ic_e AS ENUM ('a');
CREATE OR REPLACE FUNCTION secret.ic_to_e(n int) RETURNS public.app_ic_e
    LANGUAGE plpgsql IMMUTABLE AS $$BEGIN RAISE EXCEPTION 'secret.ic_to_e was executed'; END$$;
CREATE CAST (int AS public.app_ic_e) WITH FUNCTION secret.ic_to_e(int) AS ASSIGNMENT;
CREATE TABLE public.app_ic_cast_t (e public.app_ic_e);
CREATE TYPE public.app_ic_t;
CREATE FUNCTION secret.ic_t_in(cstring) RETURNS public.app_ic_t AS 'int4in' LANGUAGE internal IMMUTABLE STRICT;
CREATE FUNCTION secret.ic_t_out(public.app_ic_t) RETURNS cstring AS 'enum_out' LANGUAGE internal IMMUTABLE STRICT;
CREATE TYPE public.app_ic_t (INPUT = secret.ic_t_in, OUTPUT = secret.ic_t_out, LIKE = int4);
CREATE TABLE public.app_ic_io_t (t public.app_ic_t);
CREATE TYPE public.app_ic_pair AS (t public.app_ic_t);
CREATE TYPE public.app_ic_ct;
CREATE FUNCTION public.app_ic_ct_in(cstring) RETURNS public.app_ic_ct AS 'int4in' LANGUAGE internal IMMUTABLE STRICT;
CREATE FUNCTION public.app_ic_ct_out(public.app_ic_ct) RETURNS cstring
    AS 'int4out' LANGUAGE internal IMMUTABLE STRICT;
CREATE TYPE public.app_ic_ct (INPUT = public.app_ic_ct_in, OUTPUT = public.app_ic_ct_out, LIKE = int4);
CREATE CAST (public.app_ic_ct AS int4) WITHOUT FUNCTION;
CREATE FUNCTION public.app_ic_lt(a public.app_ic_ct, b public.app_ic_ct) RETURNS boolean
    LANGUAGE sql IMMUTABLE AS 'SELECT a::int4 < b::int4';
CREATE FUNCTION public.app_ic_le(a public.app_ic_ct, b public.app_ic_ct) RETURNS boolean
    LANGUAGE sql IMMUTABLE AS 'SELECT a::int4 <= b::int4';
CREATE FUNCTION public.app_ic_eq(a public.app_ic_ct, b public.app_ic_ct) RETURNS boolean
    LANGUAGE sql IMMUTABLE AS 'SELECT a::int4 = b::int4';
CREATE FUNCTION public.app_ic_ge(a public.app_ic_ct, b public.app_ic_ct) RETURNS boolean
    LANGUAGE sql IMMUTABLE AS 'SELECT a::int4 >= b::int4';
CREATE FUNCTION public.app_ic_gt(a public.app_ic_ct, b public.app_ic_ct) RETURNS boolean
    LANGUAGE sql IMMUTABLE AS 'SELECT a::int4 > b::int4';
CREATE OPERATOR public.#< (LEFTARG = public.app_ic_ct, RIGHTARG = public.app_ic_ct, FUNCTION = public.app_ic_lt);
CREATE OPERATOR public.#<= (LEFTARG = public.app_ic_ct, RIGHTARG = public.app_ic_ct, FUNCTION = public.app_ic_le);
CREATE OPERATOR public.#= (LEFTARG = public.app_ic_ct, RIGHTARG = public.app_ic_ct, FUNCTION = public.app_ic_eq);
CREATE OPERATOR public.#>= (LEFTARG = public.app_ic_ct, RIGHTARG = public.app_ic_ct, FUNCTION = public.app_ic_ge);
CREATE OPERATOR public.#> (LEFTARG = public.app_ic_ct, RIGHTARG = public.app_ic_ct, FUNCTION = public.app_ic_gt);
CREATE OR REPLACE FUNCTION secret.ic_cmp(a public.app_ic_ct, b public.app_ic_ct) RETURNS int
    LANGUAGE plpgsql IMMUTABLE AS $$BEGIN RAISE EXCEPTION 'secret.ic_cmp was executed'; END$$;
CREATE OPERATOR CLASS public.app_ic_ct_ops DEFAULT FOR TYPE public.app_ic_ct USING btree AS
    OPERATOR 1 public.#<, OPERATOR 2 public.#<=, OPERATOR 3 public.#=, OPERATOR 4 public.#>=, OPERATOR 5 public.#>,
    FUNCTION 1 secret.ic_cmp(public.app_ic_ct, public.app_ic_ct);
CREATE TABLE public.app_ic_ct_t (c public.app_ic_ct);
INSERT INTO public.app_ic_ct_t VALUES ('1'), ('2');
CREATE DOMAIN public.app_ic_rd AS int CHECK (VALUE > secret.ic_boom());
CREATE TYPE public.app_ic_rr AS RANGE (subtype = public.app_ic_rd);
CREATE VIEW public.app_ic_dom_view AS SELECT 1::public.app_ic_rd AS d;
CREATE VIEW public.app_ic_hidden_dom_view AS SELECT 1 AS n WHERE 1::public.app_ic_rd IS NOT NULL;
CREATE VIEW public.app_ic_sorted_view AS SELECT 1 AS n FROM public.app_ic_ct_t ORDER BY c;
CREATE FUNCTION secret.ic_sel(internal, oid, internal, integer) RETURNS float8 AS 'eqsel' LANGUAGE internal STABLE;
CREATE OPERATOR public.#~ (LEFTARG = int, RIGHTARG = int, FUNCTION = public.app_close_to, RESTRICT = secret.ic_sel);
CREATE TYPE public.app_ic_ok AS ENUM ('x', 'y');
CREATE TABLE public.app_ic_ok_t (e public.app_ic_ok);
INSERT INTO public.app_ic_ok_t VALUES ('y'), ('x');
"""


@pytest.fixture
async def db_implicit_calls(db_plan_check: DbAccess, db_full: DbAccess) -> AsyncGenerator[DbAccess, None]:
    """db_plan_check, пока в public есть типы, чья машинерия вызывает функции secret (большинство — бросающие)."""
    await db_full.sql_driver.execute(
        _IC_BOOM_RETURNS + _DROP_IMPLICIT_CALLS + _IMPLICIT_CALLS + _IC_BOOM_RAISES, readonly=False
    )
    try:
        yield db_plan_check
    finally:
        await db_full.sql_driver.execute(_DROP_IMPLICIT_CALLS, readonly=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sql", "function"),
    [
        pytest.param("SELECT 1::app_ic_e AS e", "ic_to_e", id="explicit-cast"),
        pytest.param("INSERT INTO app_ic_cast_t (e) VALUES (1)", "ic_to_e", id="assignment-cast"),
        pytest.param("SELECT '5'::app_ic_t AS t", "ic_t_(in|out)", id="type-output-at-explain"),
        pytest.param("INSERT INTO app_ic_io_t (t) VALUES ('abc')", "ic_t_(in|out)", id="type-input-at-prepare"),
        pytest.param("SELECT '(abc)'::app_ic_pair AS p", "ic_t_(in|out)", id="composite-attribute-input"),
        pytest.param("SELECT c FROM app_ic_ct_t ORDER BY c", "ic_cmp", id="opclass-order-by"),
        pytest.param("SELECT GREATEST(c, c) AS g FROM app_ic_ct_t", "ic_cmp", id="opclass-greatest"),
        pytest.param("SELECT GREATEST('1'::app_ic_ct, '2'::app_ic_ct) AS g", "ic_cmp", id="opclass-folded"),
        pytest.param("SELECT 1::app_ic_rd AS d", "ic_boom", id="domain-cast"),
        pytest.param("SELECT d FROM app_ic_dom_view", "ic_boom", id="domain-cast-in-view"),
        pytest.param("SELECT n FROM app_ic_hidden_dom_view", "ic_boom", id="domain-cast-hidden-in-view"),
        pytest.param("SELECT n FROM app_ic_sorted_view", "ic_cmp", id="opclass-of-a-view-base-table"),
        pytest.param("SELECT '[1,2]'::app_ic_rr AS r", "ic_boom", id="range-over-domain-input"),
        pytest.param("SELECT id FROM app_plan_items WHERE id #~ 1", "ic_sel", id="operator-estimator"),
    ],
)
async def test_implicit_call_of_a_type_is_rejected_before_it_runs(
    db_implicit_calls: DbAccess, sql: str, function: str
) -> None:
    """Приведение (явное и присваивания), ввод-вывод типа и атрибута составного типа, сравнение класса операторов
    (ORDER BY, GREATEST), CHECK домена (в том числе в представлении и подтипа диапазона), оценка селективности
    оператора: ни одного имени функции в тексте. Без проверки PREPARE (ввод), EXPLAIN (свёртка, печать констант)
    или выполнение вызвали бы функцию secret и получили бы её ошибку; отказ приходит раньше.

    Типы, которых нет ни в SQL агента, ни в колонках названных отношений (приведение внутри представления, колонка
    базовой таблицы представления), находит чтение определений после PREPARE — по зависимостям правила и колонкам
    заблокированных отношений."""
    with pytest.raises(PlanAccessError, match=rf"function 'secret\.{function}'"):
        await db_implicit_calls.sql_driver.execute(sql, readonly=False)


@pytest.mark.asyncio
async def test_type_with_builtin_machinery_passes(db_implicit_calls: DbAccess) -> None:
    """Перечисление public: ввод-вывод и сравнение — встроенные функции pg_catalog."""
    rows = await db_implicit_calls.sql_driver.execute("SELECT e FROM app_ic_ok_t ORDER BY e", readonly=True)
    assert [row.cells["e"] for row in rows] == ["x", "y"]
```

Сверить статически: объекты создаются, пока `secret.ic_boom()` возвращает 0 (`CREATE TYPE … AS RANGE` над доменом и представления его не вызывают, но так безопаснее), и после — заменяется на `RAISE`. `DROP TYPE … CASCADE` у базовых типов удаляет и их функции ввода-вывода, класс операторов и операторы над ними. Операторы `#…` и `#~` не совпадают по имени ни с одним оператором `_SETUP` и других тестов: остальные тесты они не затрагивают. `secret.ic_to_e`, `secret.ic_cmp`, `secret.ic_boom` в `_DROP_IMPLICIT_CALLS` не удаляются — их пересоздаёт `CREATE OR REPLACE`.

Run (живой PG, суперпользователь): `uv run pytest tests/integration/test_plan_check.py -q`
Expected: PASS (без Docker — пропуск).

- [ ] **Step 2: README**

В разделе «Проверка по плану (`plan_check`)»:

1. После пункта «**Умолчания аргументов функций `public`.**» добавить пункт:

```markdown
- **Машинерия типов (неявные вызовы).** Для значения типа Postgres сам вызывает функции, имён которых нет ни в SQL агента, ни в плане: функцию ввода (`'x'::app_t` и `INSERT` литерала в колонку типа — уже при `PREPARE`) и вывода (печать константы в `EXPLAIN VERBOSE`, выдача результата), двоичного обмена и модификатора типа, обработчик индексирования `x[1]`; функции приведения (`CREATE CAST … WITH FUNCTION`: явные `1::app_e`, неявные и присваивания — `INSERT` в колонку типа без имени типа в тексте); опорные функции и операторы классов операторов типа (сравнение btree и хеш-функцию вызывают `ORDER BY`, `DISTINCT`, `GROUP BY`, `UNION`, `GREATEST`/`LEAST`, сравнение массивов, соединения; с константами — уже при `EXPLAIN`) и их функции оценки селективности (`RESTRICT`/`JOIN`, их вызывает планировщик); `CHECK` домена при приведении к нему (`SELECT 1::app_rd`: планировщик сворачивает его `IMMUTABLE`-вызовы), у диапазона — `canonical`, `subdiff` и класс операторов подтипа. Всё это проверяется для типов, до которых доходит оператор, — и для типов, вложенных в них: базовый тип домена, элемент массива, атрибуты составного типа, подтип диапазона, диапазон мультидиапазона (так закрыт и `CHECK` домена — подтипа диапазона: его выполняет ввод `'[1,2]'::app_rr`). До `PREPARE` — типы, названные в SQL агента, типы колонок названных в нём отношений и представлений, типы аргументов и результатов его операторов и функций `public` (все перегрузки с именем); после `PREPARE` и после всех `EXPLAIN` — типы колонок всех заблокированных отношений, целей DML и их потомков и типы из зависимостей правил и определений; ещё — типы аргументов и результатов каждой функции `public`, до которой доходит проверка (тела, реализации операторов и агрегатов). Правила: функция машинерии — `pg_catalog` (любая: её сигнатуру задаёт Postgres) или `public` (с проверкой тела и умолчаний), функция приведения — как функция выражения (`pg_catalog` — только из списка basic), оператор семейства — `public` или `pg_catalog` с проверкой функции оператора `public`, `CHECK` домена — как текст определения. Типы `pg_catalog` не проверяются (встроенная машинерия); функции и операторы расширений (`CREATE EXTENSION`: `citext`, `hstore`, PostGIS — в том числе в схеме вне `public`) — тоже: их ставит скрипт расширения. Так закрыты `SELECT 1::app_e` и `INSERT INTO app_t (e) VALUES (1)` с приведением `secret.int_to_e`, колонка типа с функцией вывода `secret.t_out`, `ORDER BY` и `GREATEST` по типу с функцией сравнения `secret.cmp`, `SELECT 1::app_rd` с `CHECK (VALUE > secret.boom())` — и те же приведения и сортировки внутри представлений.
```

2. В «**Что `plan_check` не закрывает.**» три пункта — «функции приведения `CREATE CAST … WITH FUNCTION` …», «функции, которых план не называет: …» и «опорные функции классов операторов (opclass) типов `public` …» — заменить на:

```markdown
- схема оператора `USING` в ключе сортировки, collation и метода `TABLESAMPLE` (план печатает их без схемы). В представлениях и правилах функции и операторы не из `pg_catalog` видит проверка зависимостей;
- машинерия встроенных типов (`pg_catalog`), изменённая суперпользователем: приведение между двумя встроенными типами (`CREATE CAST (int4 AS text) WITH FUNCTION secret.f`), функция или оператор, добавленные в семейство операторов `pg_catalog`, классы операторов `public` для встроенных типов (`CREATE OPERATOR CLASS … FOR TYPE int4`: их вызывают индексы и ограничения-исключения с этим классом) — типы `pg_catalog` проверка не раскрывает; функции и операторы расширений в машинерии типов доверенные — в том числе функция, которую добавили в расширение явно (`ALTER EXTENSION … ADD FUNCTION`);
- неявное приведение к reg*-типу (`regclass` и подобные: поиск по имени при выполнении) и функции преобразования кодировок (`CREATE CONVERSION`);
```

   Пункт «`WITH CHECK OPTION` представлений, операторы ограничений-исключений и опорные функции классов операторов `public` (…), `CHECK` доменов внутри составных типов колонок;» заменить на «- `WITH CHECK OPTION` представлений;».

3. В «**Что `plan_check` отклоняет, хотя это легитимно:**» после пункта про секции и дочерние таблицы добавить:

```markdown
- запросы к отношению, у которого есть колонка (даже не прочитанная запросом) типа, чья машинерия вызывает функцию другой схемы (не из расширения) или чей домен (в том числе вложенный: элемент массива, атрибут составного типа, подтип диапазона) имеет `CHECK` на функции другой схемы или встроенной вне списка basic, — даже если `SELECT` не приводит значение к домену; приведения таких типов к встроенным функциям `pg_catalog` вне списка basic; любое использование оператора, одна из перегрузок которого в `public` (все перегрузки с именем) принимает или возвращает такой тип — например, оператор `public.=` над таким типом отклоняет любое `=`;
```

4. В пункте «Цена» слова «и один запрос на реализации операторов и агрегатов, тела SQL-функций и умолчания аргументов функций `public`» заменить на «и один запрос на реализации операторов и агрегатов, тела SQL-функций, умолчания аргументов функций `public` и машинерию типов», «он нужен почти любому запросу с `=`» — на «он нужен почти любому запросу с отношением, именем типа или оператором», а «на `EXPLAIN (GENERIC_PLAN) … WHERE $1 IS NULL` — шесть» — на «на `EXPLAIN (GENERIC_PLAN) SELECT … FROM app_t WHERE $1 IS NULL` — семь».

- [ ] **Step 3: Проверки и коммиты**

Run: `uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check . && uv run pytest tests/unit -q && uv run pytest tests/integration -q`
Expected: чисто; PASS; интеграция собирается.

```bash
git add tests/integration/test_plan_check.py
git commit -m "test(security): prove implicit type calls are rejected before they run"
git add README.md
git commit -m "docs(security): describe the type machinery check of plan_check"
```

---

## Заметки к PR (изменения поведения)

- С `plan_check` запрос отклоняется до `PREPARE` или до `EXPLAIN`, если доходит до типа с опасной машинерией. Тип достигается, если он:
  - назван в SQL агента;
  - тип колонки названного или заблокированного отношения;
  - тип аргумента или результата функции или оператора `public`;
  - тип из зависимостей определений;
  - вложен в любой из них (база домена, элемент, атрибут, подтип диапазона).

  Опасная машинерия — функции ввода-вывода, приведения, опорные функции и операторы классов операторов, оценка селективности, `canonical`/`subdiff`, которые вызывают функцию другой схемы (не члена расширения) или функцию `public`, не проходящую проверки. К ней же относятся приведение с функцией `pg_catalog` вне списка basic и `CHECK` домена с такой функцией.
- Оператор `public`, одна из перегрузок которого принимает или возвращает такой тип, отклоняет любое использование своего имени.
- Колонка такого типа отклоняет любой запрос к отношению, даже если запрос её не читает. Домен с таким `CHECK` отклоняет и `SELECT`.
- Операторы `public`, названные в запросе, проверяются и по функциям оценки селективности.
- Запрос реализаций уходит и для оператора с одними отношениями или типами: `SELECT * FROM app_t` — пять запросов проверки вместо четырёх.
- Функции и операторы расширений в машинерии типов доверенные, в том числе в схеме вне `public`.
