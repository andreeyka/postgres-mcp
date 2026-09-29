# Проверка выражений плана в `plan_check` — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** С `plan_check=true` (basic) `PlanGuard` проверяет не только узлы сканирования, но и выражения плана: функции и операторы чужих схем, встроенные функции вне списка basic, приведения к типам чужих схем и строковые типы таблиц `public` без префикса отклоняются.

**Architecture:** Новый модуль `postgres/security/plan_expressions.py` (чистый, без I/O) разбирает строки выражений `EXPLAIN (VERBOSE, FORMAT JSON)` через pglast — после замены ссылок планировщика на `NULL` — и возвращает `ExpressionNames` (функции, операторы, типы, последовательности `nextval`). Новый модуль `postgres/security/plan_catalog.py` — запросы каталога сервера через исполнитель транзакции `PlanGuard` (тот же `StatementRunner`, что для EXPLAIN): какие имена — функции `pg_catalog`, какие — строковые типы `public`, плюс кэш имён типов `pg_catalog` `BuiltinTypeNames`. `PlanGuard._check_plan` становится асинхронным и идёт в три прохода: узлы (как сейчас, те же ошибки), выражения (статические правила, отложенные имена), каталог (не больше трёх запросов на оператор, обычно ноль). `BuiltinTypeNames` создаёт `DbAccessService` (один пул = одно соединение/БД) и передаёт каждому `SafeSqlExecutor`, тот — каждому `PlanGuard`. Спека: `docs/superpowers/specs/2026-09-29-plan-check-expressions-design.md`.

**Tech Stack:** Python 3.12, uv, pglast v8.2 (`parse_sql`, `Visitor`), psycopg 3.3.4 (`psycopg.sql.SQL`/`Literal`), pytest (asyncio auto).

## Spec corrections

Факты сверены по `src/backend/commands/explain.c` и `src/backend/utils/adt/ruleutils.c` (PG 15/16/17); разбор форм проверен `uv run python -c` на pglast v8.2 (результаты — ниже и в тестах Task 1).

- **(a) Ключи выражений и формы значений.** Спека §2.1 верна, дополнено: `Conflict Filter` (ON CONFLICT DO UPDATE WHERE), `Group Keys`/`Hash Keys` (группы `Grouping Sets`: список списков строк; одиночные `Group Key`/`Hash Key` — список), `Sampling Parameters` (список) и `Repeatable Seed` (строка) у TABLESAMPLE. Списки: `Output`, `Sort Key`, `Presorted Key`, `Group Key`, `Hash Key`, `Sampling Parameters`; `Cache Key` (Memoize) — одна строка с выражениями через запятую (разбирается как список целей); остальные — строка. `Table Function Call` (XMLTABLE/JSON_TABLE) — не выражение, а элемент FROM: разбирается как `SELECT * FROM <text>`. Не выражения и не проверяются: `Subplan Name` (там `InitPlan 1 (returns $0)`), `Sampling Method` (имя без схемы), `Remote SQL`, `Relations`. Ключа `Window` в PG 15–17 нет (PG 18). Неизвестная форма значения (не строка/список) → `PlanUnverifiableError`.
- **`Function Call` — не «единый путь».** Спека §2.1 объединяет его с выражениями, но строгий путь (`_check_function_calls`) отклоняет функцию без схемы вне списка basic (`my_srf(my_helper(1))`), а правило §2.3 пропустило бы функцию `public` — это ослабление существующего правила. Решение: имена функций `Function Call` по-прежнему проверяет только строгий путь, без изменений; общий разбор добавляет к нему типы, операторы и `nextval`. Строгий путь и тест `generate_series(1, (SubPlan 1))` → `PlanUnverifiableError` не меняются (строгий путь идёт первым и без замен).
- **(b) Формы ruleutils.** pglast разбирает как есть: `$N` (и `$0`), `ROW(...)`, `CURRENT_TIMESTAMP`/`CURRENT_USER`/`LOCALTIMESTAMP(3)` (SQLValueFunction), `SYSTEM_USER`, `x COLLATE "C"`, `IS DISTINCT FROM`, `= ANY ('{...}'::integer[])`, `ANY (ARRAY[...])`, `CASE`, `(x)::text`, `'x'::"char"`, `(b)::bpchar`, `character varying`/`character(3)`/`numeric(10,2)`/`timestamp with time zone` (pglast квалифицирует `pg_catalog.`), `EXTRACT`/`SUBSTRING`/`TRIM`/`AT TIME ZONE`/`IS NORMALIZED` (→ `FuncCall pg_catalog.extract`/`substring`/`btrim`/`timezone`/`is_normalized`), `COALESCE`/`GREATEST`/`NULLIF`/`XMLELEMENT`/`JSON_OBJECT` (не FuncCall — встроенные конструкции), `count(*) FILTER (...)`, `WITHIN GROUP`, `t.*`. Не разбираются и заменяются (по порядку): `(alternatives: SubPlan N or hashed SubPlan M)` → `NULL`; `[EXISTS|ARRAY|CTE](<hashed |rescan >SubPlan|InitPlan N)[.colM]` → `NULL` (PG 15–16: `(SubPlan 1)`, `(hashed SubPlan 1)`; PG 17: `(InitPlan 1).col1`, `(hashed SubPlan 1).col1`, `EXISTS(SubPlan 1)`, `ARRAY(SubPlan 1)`, `(rescan SubPlan 1)`); PG 17 `(ANY <проверка>)`/`(ALL <проверка>)` → `(<проверка>)`; `PARTIAL ` перед частичным агрегатом → удаляется; `OVER (?)` (в EXPLAIN нет определения окна) → `OVER ()`. `(returns $N)` в выражениях не встречается — только в `Subplan Name`. В шаблонах замен нет кавычек: замена не сдвигает границы литералов и имён в кавычках, внутри литерала меняется только значение (`'(SubPlan 1)'` → `'NULL'`), структура — нет.
- **(c) Ключи сортировки.** `show_sortorder_options`: `<expr>[ COLLATE <имя>][ DESC| USING <op>][ NULLS FIRST| NULLS LAST]`, где `USING <op>` — `get_opname` (без схемы) и `COLLATE` — имя без схемы. Разбор — `SELECT 1 ORDER BY <key>` (ровно одна цель и один ключ). Схему оператора `USING` и collation план не показывает — не проверяется (см. «Не закрывается»).
- **(d) reg*-типы.** Константы `reg*` печатаются как `'имя'::regclass` (`regclassout`, со схемой, если не видно через `search_path`) — это `TypeName regclass` без схемы. Проверка их пропускает без запроса к каталогу (`NAME_LOOKUP_TYPES`), как требует спека §2.3.
- **Функции `pg_catalog` не кэшируются.** Спека §2.4 кэширует список функций `pg_catalog` на процесс. Устаревший список небезопасен: функция, появившаяся в `pg_catalog` после загрузки (расширение со `schema = pg_catalog`, например `adminpack` в PG 15/16 — `pg_file_write`), считалась бы функцией `public` и проходила. Вместо кэша — один запрос на оператор и только для имён без схемы вне списка basic (`p.proname IN (...)`), в порядке обхода. Кэшируются только имена типов `pg_catalog` (`BuiltinTypeNames`): имя без схемы, которое есть в `pg_catalog`, всегда означает тип `pg_catalog` (он неявно первый в `search_path`), так что устаревание даёт лишь лишний запрос.
- **Где живёт кэш.** Спека: «кэш в процессе по `connection_id`». У `DbAccessService` ровно один пул на всё время жизни (`connection_id` = его URL), поэтому `BuiltinTypeNames` создаётся в `DbAccessService.__init__` и передаётся каждому `SafeSqlExecutor` (`builtin_types=`), а тот — каждому `PlanGuard`; глобального словаря по `connection_id` не нужно. `SafeSqlExecutor` без аргумента создаёт свой (тесты, `CatalogSqlExecutor` — там `plan_check` не действует).
- **Строковые типы — один запрос на оператор.** Спека: по запросу на имя. Имена собираются по всему плану и проверяются одним `t.typname IN (...)`. Кандидаты: тип без схемы вне `pg_catalog` и без префикса, а также тип с явной схемой `public` без префикса (ruleutils квалифицирует тип `public`, когда его имя заслонено одноимённым типом `pg_catalog`). Без `table_prefix` строковые типы не проверяются (все таблицы `public` доступны).
- **`nextval` из `DEFAULT`.** Спека не учитывает: `INSERT` в таблицу с `serial`/identity печатает в `Output` `nextval('app_t_id_seq'::regclass)` (serial) или `nextval('app_t_id_seq')` без приведения (identity, `NextValueExpr`: ruleutils PG 15–17 печатает его как `nextval('%s')`), а `nextval` нет в списке basic — без исключения `plan_check` ломал бы запись в любую таблицу с автоключом. `nextval` (без схемы или `pg_catalog`) с единственным аргументом `'литерал'` или `'литерал'::regclass` проверяется как отношение: имя из литерала (разбор идентификатора pglast), без схемы — `allowed_schema`, дальше `_check_relation` (схема, системность, префикс). Иной аргумент — обычный вызов `nextval` (функция `pg_catalog` вне basic → отказ).
- **Порядок проверок.** Сначала все узлы (прежние отказы и их тексты не меняются — например, `information_schema.tables` по-прежнему отклоняется как `relation 'pg_catalog.…'`, а не по типу `information_schema.sql_identifier` в `Output` выше по плану), затем выражения, затем каталог.
- **Ошибки.** Функция/оператор чужой схемы → `PlanAccessError("function", "secret.f" | "secret.+")`; функция без схемы, найденная в `pg_catalog`, → `function 'pg_catalog.<имя>'`; тип чужой схемы → новый вид `type` (`"type", "secret.t"`, подсказка `Only types from 'public' or built-in types are permitted.`); строковый тип таблицы без префикса → `relation 'public.<имя>'`. Неразборчивое выражение → `PlanUnverifiableError(node_type, key=<ключ>)`: `an expression in <ключ> of a <узел> node cannot be verified`; текст плана в сообщение не попадает.
- **Поведение RLS и DEFAULT меняется.** Выражения политик RLS попадают в `Filter`, `DEFAULT` — в `Output` `INSERT`: функции вне списка basic в них (`current_setting('app.tenant')`) теперь дают отказ. Это следствие спеки, фиксируется в README и заметках к PR.
- **Не закрывается (README).** Схема collation, оператора `USING` в ключе сортировки и метода TABLESAMPLE (план печатает без схемы); функции, которых план не называет: неявные приведения и `CREATE CAST ... WITH FUNCTION`, операторы за `IS DISTINCT FROM`/`NULLIF`/`IN`.
- Интеграция в CI — PG 15/16: формы PG 17 (`(InitPlan 1).col1`, `(ANY ...)`) покрыты только юнит-тестами Task 1/2.

## Global Constraints

- Всё, что видит агент или внешняя система (ошибки, логи, коммиты), — на английском; docstring и комментарии — по-русски. README — по-русски.
- Нет `from __future__ import annotations`.
- Ломающие изменения разрешены; описываются только в заметках к PR. В коде — никаких шимов и упоминаний старого поведения.
- Безопасность: ни одно правило валидатора и существующей проверки по плану не ослабляется (узлы проверяются первыми и как раньше, строгий путь `Function Call` не меняется); `full` и `plan_check=false` не затрагиваются (`PlanGuard` создаётся только в `precheck` при `plan_check` с `allowed_schema`); SQL каталога — только сервера, имена — `Literal`.
- Перед каждым коммитом: `uv run ruff check --fix --show-fixes`, `uv run mypy src/`, `uv run ruff format`; затем `uv run ruff check .`.
- Юнит-тесты: `uv run pytest tests/unit -q`. Интеграция: `uv run pytest tests/integration -q` — должна собираться; локально пропускается (нет Docker), в CI — Postgres 15/16 с hypopg. Сверять интеграционные тесты статически с кодом.
- Коммиты: `type(scope): message`, повелительное наклонение, английский.
- **Никогда не запускать никакие `git config` и `git stash`.** Не пушить.
- Ветка: `claude/plan-check-expressions` (уже создана от `main`, на ней коммит спеки `aa57286`).

---

### Task 1: Разбор выражений плана (`plan_expressions.py`)

**Files:**
- Create: `src/postgres_fastmcp/postgres/security/plan_expressions.py`
- Modify: `src/postgres_fastmcp/postgres/security/plan_guard.py` (`_function_call_names` использует `parse_target_list`; `_NON_TARGET_SELECT_PARTS` переезжает в новый модуль; импорты)
- Test: `tests/unit/postgres/test_plan_expressions.py`

**Interfaces:**
- Produces (в `postgres_fastmcp.postgres.security.plan_expressions`):
  - `QualifiedName = tuple[str | None, str]`;
  - `@dataclass(frozen=True, slots=True) class ExpressionNames` с полями `functions`, `operators`, `types`, `sequences: tuple[QualifiedName, ...] = ()`;
  - `parse_target_list(text: object) -> SelectStmt | None` — `SELECT <text>` без замен, только список целей;
  - `parse_expression(text: str) -> ExpressionNames | None`, `parse_sort_key(text: str) -> ExpressionNames | None`, `parse_table_function(text: str) -> ExpressionNames | None` — `None` = не разбирается;
  - `sequence_name(text: str) -> QualifiedName | None`;
  - `expression_texts(value: object) -> list[str] | None` — строки значения ключа, `None` = другая форма;
  - `FUNCTION_CALL_KEY = "Function Call"`;
  - `EXPRESSION_PARSERS: dict[str, Callable[[str], ExpressionNames | None]]` — ключ узла плана → разбор.

- [ ] **Step 1: Write the failing test** — создать `tests/unit/postgres/test_plan_expressions.py`:

```python
"""Тесты разбора выражений плана EXPLAIN (VERBOSE): формы ruleutils PG 15–17 и имена, которые они называют."""

import pytest

from postgres_fastmcp.postgres.security.plan_expressions import (
    EXPRESSION_PARSERS,
    ExpressionNames,
    expression_texts,
    parse_expression,
    parse_sort_key,
    parse_table_function,
    sequence_name,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("secret.f(c)", ExpressionNames(functions=(("secret", "f"),))),
        (
            "current_setting('app.jwt_secret'::text)",
            ExpressionNames(functions=((None, "current_setting"),), types=((None, "text"),)),
        ),
        ("(a OPERATOR(secret.+) b)", ExpressionNames(operators=(("secret", "+"),))),
        ("NULL::secret.t", ExpressionNames(types=(("secret", "t"),))),
        ("NULL::users[]", ExpressionNames(types=((None, "users"),))),
        ("(a)::character varying", ExpressionNames(types=(("pg_catalog", "varchar"),))),
        ("EXTRACT(year FROM d)", ExpressionNames(functions=(("pg_catalog", "extract"),))),
        ("CURRENT_USER", ExpressionNames()),
        ("COALESCE(a, b)", ExpressionNames()),
        ("t.a, t.b", ExpressionNames()),
        ("$1", ExpressionNames()),
    ],
)
def test_names_of_an_expression(text: str, expected: ExpressionNames) -> None:
    assert parse_expression(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "(SubPlan 1)",
        "(NOT (hashed SubPlan 1))",
        "(alternatives: SubPlan 1 or hashed SubPlan 2)",
        "$0",
        "(InitPlan 1).col1",
        "(NOT EXISTS(SubPlan 1))",
        "ARRAY(SubPlan 1)",
        "(rescan SubPlan 1)",
        "(ANY (t.a = (hashed SubPlan 1).col1))",
        "(ALL (t.a > (SubPlan 1).col1))",
        "(ROW(t.a, t.b) < ROW((SubPlan 1).col1, (SubPlan 1).col2))",
    ],
)
def test_planner_references_become_null(text: str) -> None:
    """Ссылки на подпланы и параметры (PG 15–16 и PG 17) разбираются; сами подпланы — узлы плана."""
    names = parse_expression(text)

    assert names is not None
    assert names.functions == ()


@pytest.mark.parametrize(
    ("text", "function"),
    [("PARTIAL count(*)", "count"), ("(row_number() OVER (?) <= 10)", "row_number")],
)
def test_partial_aggregate_and_window_without_definition_are_parsed(text: str, function: str) -> None:
    names = parse_expression(text)

    assert names is not None
    assert names.functions == ((None, function),)


def test_substitution_inside_a_literal_changes_only_its_value() -> None:
    assert parse_expression("secret.f('(SubPlan 1)'::text)") == ExpressionNames(
        functions=(("secret", "f"),), types=((None, "text"),)
    )


@pytest.mark.parametrize(
    ("text", "sequence"),
    [
        ("nextval('app_t_id_seq'::regclass)", (None, "app_t_id_seq")),
        ("nextval('app_t_id_seq'::bigint)", (None, "app_t_id_seq")),
        ("nextval('secret.s'::regclass)", ("secret", "s")),
        ("""nextval('"App"."S q"'::regclass)""", ("App", "S q")),
    ],
)
def test_nextval_of_a_literal_is_a_sequence(text: str, sequence: tuple[str | None, str]) -> None:
    names = parse_expression(text)

    assert names is not None
    assert names.sequences == (sequence,)
    assert names.functions == ()


def test_nextval_of_an_expression_is_a_function_call() -> None:
    names = parse_expression("nextval(('x'::text)::regclass)")

    assert names is not None
    assert names.functions == ((None, "nextval"),)
    assert names.sequences == ()


@pytest.mark.parametrize("text", ["app_t_id_seq", "public.s", '"A""b".c'])
def test_sequence_name_accepts_identifiers(text: str) -> None:
    assert sequence_name(text) is not None


@pytest.mark.parametrize("text", ["a.b.c", "x; SELECT 1", "x AS y", "(SELECT 1) s", ""])
def test_sequence_name_rejects_anything_else(text: str) -> None:
    assert sequence_name(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "not a (call",
        "a FROM secret.t",
        "a; SELECT 1",
        "1 UNION SELECT 2",
        "x::a.b.c",
        "nextval('a.b.c'::regclass)",
    ],
)
def test_unparsable_expression_is_none(text: str) -> None:
    assert parse_expression(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("t.a DESC NULLS LAST", ExpressionNames()),
        ("t.b USING <", ExpressionNames(operators=((None, "<"),))),
        ('(lower(t.name)) COLLATE "C"', ExpressionNames(functions=((None, "lower"),))),
        ("t.a USING OPERATOR(secret.<)", ExpressionNames(operators=(("secret", "<"),))),
        ("(SubPlan 1) DESC", ExpressionNames()),
    ],
)
def test_sort_key(text: str, expected: ExpressionNames) -> None:
    assert parse_sort_key(text) == expected


@pytest.mark.parametrize("text", ["", "a, b", "a LIMIT 1", "a DESC DESC"])
def test_unparsable_sort_key_is_none(text: str) -> None:
    assert parse_sort_key(text) is None


def test_table_function_call_is_parsed_as_a_from_item() -> None:
    names = parse_table_function(
        "XMLTABLE(('/r'::text) PASSING (d.x) COLUMNS id integer PATH ('@id'::text), s secret.t)"
    )

    assert names is not None
    assert ("secret", "t") in names.types


@pytest.mark.parametrize("text", ["", "secret.f()", "XMLTABLE(", "app_t"])
def test_unparsable_table_function_call_is_none(text: str) -> None:
    assert parse_table_function(text) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("a", ["a"]),
        (["a", "b"], ["a", "b"]),
        ([["a", "b"], ["c"]], ["a", "b", "c"]),
        ([], []),
        (1, None),
        (["a", 1], None),
        ({"a": "b"}, None),
    ],
)
def test_expression_texts(value: object, expected: list[str] | None) -> None:
    assert expression_texts(value) == expected


def test_sort_keys_and_table_functions_have_their_own_parsers() -> None:
    assert EXPRESSION_PARSERS["Sort Key"] is parse_sort_key
    assert EXPRESSION_PARSERS["Presorted Key"] is parse_sort_key
    assert EXPRESSION_PARSERS["Table Function Call"] is parse_table_function
    assert EXPRESSION_PARSERS["Output"] is parse_expression
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/postgres/test_plan_expressions.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'postgres_fastmcp.postgres.security.plan_expressions'`.

- [ ] **Step 3: Implement the module** — создать `src/postgres_fastmcp/postgres/security/plan_expressions.py`:

```python
"""Разбор выражений плана EXPLAIN (VERBOSE): функции, операторы, типы и последовательности, которые они называют.

Выражения плана печатает ruleutils. Ссылки планировщика на подпланы ((SubPlan 1), (hashed SubPlan 1),
(InitPlan 1).col1, EXISTS(SubPlan 1), (ANY ...), (alternatives: ...)) — не SQL: перед разбором они
заменяются на NULL, сами подпланы проверяются как узлы плана. Параметры $N pglast разбирает как есть.
В шаблонах замен нет кавычек, поэтому замена не сдвигает границы строк и имён в кавычках: внутри
литерала меняется только его значение, а не структура выражения.
"""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import pglast
from pglast.ast import (
    A_Const,
    A_Expr,
    FuncCall,
    JsonTable,
    Node,
    RangeTableFunc,
    RangeVar,
    SelectStmt,
    SortBy,
    String,
    TypeCast,
    TypeName,
)
from pglast.enums.parsenodes import SetOperation
from pglast.parser import ParseError
from pglast.visitors import Visitor


# Имя из плана: (схема или None, имя).
QualifiedName = tuple[str | None, str]

# Части SelectStmt, которых в "SELECT <список выражений>" быть не должно: только список целей.
_NON_TARGET_SELECT_PARTS = (
    "fromClause",
    "whereClause",
    "groupClause",
    "havingClause",
    "withClause",
    "distinctClause",
    "sortClause",
    "limitCount",
    "limitOffset",
    "lockingClause",
    "windowClause",
    "valuesLists",
    "intoClause",
)

# Формы ruleutils внутри выражений, которые не SQL (PG 15–17), и их замены по порядку: альтернативы
# подпланов; ссылка на подплан или его колонку (PG 17: (SubPlan 1).col1, (InitPlan 1).col1, EXISTS(SubPlan 1),
# ARRAY(SubPlan 1)); PG 17 (ANY <проверка>)/(ALL <проверка>); PARTIAL у частичного агрегата; OVER (?) у оконной
# функции (в EXPLAIN нет определения окна).
_PLANNER_REFERENCES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\(alternatives: SubPlan \d+ or hashed SubPlan \d+\)"), "NULL"),
    (
        re.compile(r"(?:\b(?:EXISTS|ARRAY|CTE))?\((?:hashed |rescan )?(?:SubPlan|InitPlan) \d+\)(?:\.col\d+)?"),
        "NULL",
    ),
    (re.compile(r"\((?:ANY|ALL) "), "("),
    (re.compile(r"\bPARTIAL (?=[\w\"])"), ""),
    (re.compile(r"\bOVER \(\?\)"), "OVER ()"),
)

# nextval('последовательность'::тип): так план печатает DEFAULT serial ('...'::regclass) и identity
# ('...'::bigint). Литерал — имя отношения, проверяется как отношение, а не как вызов функции.
_NEXTVAL: frozenset[QualifiedName] = frozenset({(None, "nextval"), ("pg_catalog", "nextval")})


@dataclass(frozen=True, slots=True)
class ExpressionNames:
    """Имена, которые выражение плана резолвит по каталогу."""

    functions: tuple[QualifiedName, ...] = ()
    operators: tuple[QualifiedName, ...] = ()
    types: tuple[QualifiedName, ...] = ()
    sequences: tuple[QualifiedName, ...] = ()


def _qualified(parts: Iterable[Node]) -> QualifiedName | None:
    """(схема или None, имя) из частей имени; None — части не строки, пустые или их больше двух."""
    names = tuple(part.sval if isinstance(part, String) else None for part in parts)
    match names:
        case (str(name),) if name:
            return None, name
        case (str(schema), str(name)) if schema and name:
            return schema, name
        case _:
            return None


def sequence_name(text: str) -> QualifiedName | None:
    """Имя отношения из литерала nextval: regclassout печатает его в кавычках по правилам идентификаторов."""
    # Текст только разбирается pglast, в Postgres не отправляется.
    statement = _single_select(f"SELECT 1 FROM {text}")  # noqa: S608
    relations = statement.fromClause or () if statement is not None else ()
    if statement is None or not _only(statement, "fromClause") or len(relations) != 1:
        return None
    relation = relations[0]
    if not isinstance(relation, RangeVar) or relation.catalogname or relation.alias or not relation.relname:
        return None
    return relation.schemaname, relation.relname


def _literal_argument(call: FuncCall) -> str | None:
    """Строка единственного аргумента вида 'литерал'::тип, иначе None."""
    args = call.args or ()
    if len(args) != 1 or not isinstance(args[0], TypeCast) or not isinstance(args[0].arg, A_Const):
        return None
    value = args[0].arg.val
    return value.sval if isinstance(value, String) else None


class _Names(Visitor):
    """Собирает имена выражения; verifiable=False — встретилось имя, которое не разложить на схему и имя."""

    def __init__(self) -> None:
        """Пустые списки имён."""
        super().__init__()
        self.functions: list[QualifiedName] = []
        self.operators: list[QualifiedName] = []
        self.types: list[QualifiedName] = []
        self.sequences: list[QualifiedName] = []
        self.verifiable = True

    def _add(self, found: list[QualifiedName], parts: Iterable[Node] | None) -> None:
        """Добавить имя; пустое имя (A_Expr без оператора, SortBy без USING) пропускается."""
        if not parts:
            return
        name = _qualified(parts)
        if name is None:
            self.verifiable = False
        else:
            found.append(name)

    def visit_FuncCall(self, _ancestors: object, node: FuncCall) -> None:  # noqa: N802
        """Вызов функции или nextval по литералу последовательности."""
        name = _qualified(node.funcname or ())
        literal = _literal_argument(node) if name in _NEXTVAL else None
        if literal is None:
            self._add(self.functions, node.funcname)
            return
        sequence = sequence_name(literal)
        if sequence is None:
            self.verifiable = False
        else:
            self.sequences.append(sequence)

    def visit_A_Expr(self, _ancestors: object, node: A_Expr) -> None:  # noqa: N802
        """Оператор выражения (OPERATOR(schema.op) — со схемой)."""
        self._add(self.operators, node.name)

    def visit_SortBy(self, _ancestors: object, node: SortBy) -> None:  # noqa: N802
        """Оператор USING ключа сортировки."""
        self._add(self.operators, node.useOp)

    def visit_TypeName(self, _ancestors: object, node: TypeName) -> None:  # noqa: N802
        """Тип приведения или колонки табличной функции."""
        self._add(self.types, node.names)


def _collect(statement: SelectStmt) -> ExpressionNames | None:
    """Имена разобранного выражения; None — среди них есть неразборчивое."""
    names = _Names()
    names(statement)
    if not names.verifiable:
        return None
    return ExpressionNames(
        functions=tuple(names.functions),
        operators=tuple(names.operators),
        types=tuple(names.types),
        sequences=tuple(names.sequences),
    )


def _nullify_planner_references(text: str) -> str:
    """Заменить ссылки планировщика на подпланы и формы ruleutils, которые не SQL."""
    for pattern, replacement in _PLANNER_REFERENCES:
        text = pattern.sub(replacement, text)
    return text


def _single_select(sql: str) -> SelectStmt | None:
    """Ровно один простой SELECT (без UNION и подобного); None — не разбирается или не он."""
    try:
        statements = pglast.parse_sql(sql)
    except ParseError:
        return None
    statement = statements[0].stmt if len(statements) == 1 else None
    if not isinstance(statement, SelectStmt) or statement.op != SetOperation.SETOP_NONE:
        return None
    return statement


def _only(statement: SelectStmt, *allowed: str) -> bool:
    """Кроме списка целей в SELECT есть только части из allowed."""
    return not any(getattr(statement, part) for part in _NON_TARGET_SELECT_PARTS if part not in allowed)


def parse_target_list(text: object) -> SelectStmt | None:
    """Разобрать текст как список целей "SELECT <text>" без замен; None — пусто, не разбирается или не только цели."""
    if not isinstance(text, str) or not text.strip():
        return None
    statement = _single_select(f"SELECT {text}")
    if statement is None or not _only(statement) or not statement.targetList:
        return None
    return statement


def parse_expression(text: str) -> ExpressionNames | None:
    """Выражение (или список выражений через запятую, как Cache Key) плана."""
    statement = parse_target_list(_nullify_planner_references(text))
    return None if statement is None else _collect(statement)


def parse_sort_key(text: str) -> ExpressionNames | None:
    """Ключ сортировки: выражение с COLLATE, DESC, NULLS FIRST/LAST или USING <оператор>."""
    if not text.strip():
        return None
    statement = _single_select(f"SELECT 1 ORDER BY {_nullify_planner_references(text)}")
    if (
        statement is None
        or not _only(statement, "sortClause")
        or len(statement.targetList or ()) != 1
        or len(statement.sortClause or ()) != 1
    ):
        return None
    return _collect(statement)


def parse_table_function(text: str) -> ExpressionNames | None:
    """Table Function Call: XMLTABLE(...) или JSON_TABLE(...) — разбирается как элемент FROM."""
    if not text.strip():
        return None
    # Текст только разбирается pglast, в Postgres не отправляется.
    statement = _single_select(f"SELECT * FROM {_nullify_planner_references(text)}")  # noqa: S608
    sources = statement.fromClause or () if statement is not None else ()
    if (
        statement is None
        or not _only(statement, "fromClause")
        or len(statement.targetList or ()) != 1
        or len(sources) != 1
        or not isinstance(sources[0], (RangeTableFunc, JsonTable))
    ):
        return None
    return _collect(statement)


def expression_texts(value: object) -> list[str] | None:
    """Строки выражений значения ключа: строка, список или список списков (Group Keys); None — другая форма."""
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        return None
    texts: list[str] = []
    for item in value:
        nested = expression_texts(item)
        if nested is None:
            return None
        texts.extend(nested)
    return texts


# Function Call узла Function Scan: имена его функций проверяет строгий путь PlanGuard (_check_function_calls),
# здесь — только типы, операторы и последовательности.
FUNCTION_CALL_KEY = "Function Call"

# Ключи узла плана с выражениями (explain.c, VERBOSE, PG 15–17) и разбор их строк.
EXPRESSION_PARSERS: dict[str, Callable[[str], ExpressionNames | None]] = {
    **dict.fromkeys(
        (
            "Output",
            "Filter",
            "Join Filter",
            "Hash Cond",
            "Merge Cond",
            "Index Cond",
            "Recheck Cond",
            "TID Cond",
            "One-Time Filter",
            "Run Condition",
            "Order By",
            "Cache Key",
            "Conflict Filter",
            "Group Key",
            "Group Keys",
            "Hash Key",
            "Hash Keys",
            "Sampling Parameters",
            "Repeatable Seed",
            FUNCTION_CALL_KEY,
        ),
        parse_expression,
    ),
    "Sort Key": parse_sort_key,
    "Presorted Key": parse_sort_key,
    "Table Function Call": parse_table_function,
}
```

- [ ] **Step 4: Reuse `parse_target_list` in `plan_guard.py`** — строгий путь `Function Call` ведёт себя как раньше (без замен ссылок планировщика), дублирование разбора уходит:
  - удалить импорты `from pglast.enums.parsenodes import SetOperation` и `from pglast.parser import ParseError`, добавить `from postgres_fastmcp.postgres.security.plan_expressions import parse_target_list`;
  - удалить блок `# Части SelectStmt, которых в "SELECT <Function Call>" быть не должно…` с `_NON_TARGET_SELECT_PARTS`;
  - в `_function_call_names` заменить всё от `if not isinstance(call, str) or not call.strip():` до `raise PlanUnverifiableError(_FUNCTION_SCAN_TYPE)` после проверки формы, а также строку с `outermost`, на:

```python
    statement = parse_target_list(call)
    if statement is None:
        raise PlanUnverifiableError(_FUNCTION_SCAN_TYPE)
    collector = _FunctionCalls()
    collector(statement)
    calls = collector.calls
    if skip_outermost:
        targets = statement.targetList or ()
        outermost = targets[0].val if len(targets) == 1 else None
        if not isinstance(outermost, FuncCall):
            raise PlanUnverifiableError(_FUNCTION_SCAN_TYPE)
        calls = [found for found in calls if found is not outermost]
    elif not calls:
        raise PlanUnverifiableError(_FUNCTION_SCAN_TYPE)
    return [_call_name(found) for found in calls]
```

  (`targets = … or ()` — mypy не сужает `targetList` через границу функции.) `SelectStmt` остаётся в импорте `pglast.ast` — он нужен `_PLANNABLE_TYPES`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/postgres/test_plan_expressions.py tests/unit/postgres/test_plan_guard.py -q`
Expected: PASS (68 новых + все прежние `test_plan_guard.py`, в том числе `test_function_call_with_more_than_a_target_list_is_rejected` и `test_multi_function_scan_with_an_unparsable_call_is_rejected`).

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
uv run pytest tests/unit -q
git add src/postgres_fastmcp/postgres/security/plan_expressions.py src/postgres_fastmcp/postgres/security/plan_guard.py tests/unit/postgres/test_plan_expressions.py
git commit -m "feat(security): parse plan expressions for plan_check"
```

`# noqa: S608` в `sequence_name`/`parse_table_function` обязателен: ruff видит `SELECT … FROM {…}` в f-строке; текст только разбирается pglast и в Postgres не уходит (комментарий над строкой это и говорит).

---

### Task 2: `PlanGuard` проверяет выражения; каталог и кэш типов

**Files:**
- Create: `src/postgres_fastmcp/postgres/security/plan_catalog.py`
- Modify: `src/postgres_fastmcp/shared/errors.py` (`PlanAccessError` — вид `type`; `PlanUnverifiableError` — `key`)
- Modify: `src/postgres_fastmcp/postgres/security/plan_guard.py` (docstring модуля, импорты, `TYPE_KIND`, `_CatalogNames`, класс `PlanGuard`)
- Modify: `src/postgres_fastmcp/postgres/security/driver.py` (`SafeSqlExecutor.__init__`, `_checked_run`)
- Modify: `src/postgres_fastmcp/domains/db_access.py` (один `BuiltinTypeNames` на сервис)
- Test: `tests/unit/postgres/test_plan_guard.py`, `tests/unit/shared/test_errors.py`, `tests/unit/postgres/test_safe_sql_executor.py`, `tests/unit/domains/test_db_access.py`

**Interfaces:**
- Consumes (Task 1): `EXPRESSION_PARSERS`, `FUNCTION_CALL_KEY`, `ExpressionNames`, `expression_texts`, `parse_target_list` из `plan_expressions`.
- Produces:
  - `postgres_fastmcp.postgres.security.plan_catalog`: `class BuiltinTypeNames` с `async load(run: StatementRunner) -> frozenset[str]`; `async pg_catalog_functions(run, names: Collection[str]) -> frozenset[str]`; `async row_types(run, schema: str, names: Collection[str]) -> frozenset[str]`. SQL: колонка результата `name`; типы `pg_catalog` — `…FROM pg_catalog.pg_type t JOIN pg_catalog.pg_namespace n … WHERE n.nspname = 'pg_catalog'` (без `typrelid`), функции — `…FROM pg_catalog.pg_proc p … AND p.proname IN (…)`, строковые типы — `… AND t.typrelid <> 0 AND t.typname IN (…)`.
  - `PlanGuard(run, *, allowed_schema, table_prefix, builtin_types: BuiltinTypeNames | None = None)`; `plan_guard.TYPE_KIND = "type"`.
  - `PlanAccessError(kind="type", …)` — подсказка `Only types from '<schema>' or built-in types are permitted.`
  - `PlanUnverifiableError(node_type: str | None = None, *, key: str | None = None)`, атрибут `key`.
  - `SafeSqlExecutor(delegate, validator, config, *, builtin_types: BuiltinTypeNames | None = None)`, атрибут `_builtin_types`; `DbAccessService._builtin_types`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/postgres/test_plan_guard.py`: добавить импорт `from postgres_fastmcp.postgres.security.plan_catalog import BuiltinTypeNames` (после импорта `RowResult`); заменить класс `_Explain` и функцию `_guard` целиком на:

```python
# Типы pg_catalog, которые встречаются в тестовых выражениях.
_BUILTIN_TYPES = frozenset({"text", "json", "regclass", "bpchar", "char", "int4", "int8"})


class _Explain:
    """Исполнитель транзакции в миниатюре: план по тексту EXPLAIN, ответы каталога, журнал отправленного.

    Каталог отвечает именами из заготовленных множеств, которые встречаются в запросе литералом.
    """

    def __init__(
        self,
        plans: dict[str, dict[str, Any]] | None = None,
        *,
        as_text: bool = False,
        pg_catalog_functions: frozenset[str] = frozenset(),
        row_types: frozenset[str] = frozenset(),
    ) -> None:
        self._plans = plans or {}
        self._as_text = as_text
        self._pg_catalog_functions = pg_catalog_functions
        self._row_types = row_types
        self.sent: list[str] = []

    async def __call__(self, sql: str) -> list[RowResult] | None:
        self.sent.append(sql)
        if "pg_catalog.pg_proc" in sql:
            return self._catalog(sql, self._pg_catalog_functions)
        if "typrelid" in sql:
            return self._catalog(sql, self._row_types)
        if "pg_catalog.pg_type" in sql:
            return [RowResult(cells={"name": name}) for name in sorted(_BUILTIN_TYPES)]
        document: Any = [{"Plan": self._plans.get(sql, _RESULT)}]
        return [RowResult(cells={"QUERY PLAN": json.dumps(document) if self._as_text else document})]

    @staticmethod
    def _catalog(sql: str, names: frozenset[str]) -> list[RowResult]:
        return [RowResult(cells={"name": name}) for name in sorted(names) if f"'{name}'" in sql]

    def catalog_queries(self) -> list[str]:
        return [sql for sql in self.sent if not sql.startswith("EXPLAIN")]


def _guard(
    explain: _Explain, table_prefix: str | None = None, builtin_types: BuiltinTypeNames | None = None
) -> PlanGuard:
    return PlanGuard(explain, allowed_schema="public", table_prefix=table_prefix, builtin_types=builtin_types)
```

и дописать в конец файла:

```python
def _with(**expressions: Any) -> dict[str, Any]:
    """Скан разрешённой таблицы с выражениями плана."""
    return {**_scan("public", "app_t"), **expressions}


@pytest.mark.parametrize(
    ("node", "kind", "name"),
    [
        (_with(Output=["secret.decrypt(app_t.c)"]), "function", "secret.decrypt"),
        (_with(Filter="(pg_catalog.pg_read_file('x'::text) IS NOT NULL)"), "function", "pg_catalog.pg_read_file"),
        (_with(Output=["(app_t.a OPERATOR(secret.+) 1)"]), "function", "secret.+"),
        (_with(**{"Sort Key": ["app_t.a USING OPERATOR(secret.<)"]}), "function", "secret.<"),
        (_with(Output=["(app_t.c)::secret.t"]), "type", "secret.t"),
        (_with(Output=["nextval('secret.s'::regclass)"]), "relation", "secret.s"),
        (_with(**{"Group Keys": [["app_t.a"], ["secret.f(app_t.b)"]]}), "function", "secret.f"),
        (
            {
                "Node Type": "Function Scan",
                "Function Name": "unnest",
                "Schema": "pg_catalog",
                "Alias": "u",
                "Function Call": "unnest(ARRAY[NULL::secret.t])",
            },
            "type",
            "secret.t",
        ),
    ],
)
async def test_expression_outside_basic_is_rejected(node: dict[str, Any], kind: str, name: str) -> None:
    explain = _Explain({_EXPLAIN + _SELECT: node})

    with pytest.raises(PlanAccessError) as exc_info:
        await _guard(explain).check(_SELECT)

    assert (exc_info.value.kind, exc_info.value.qualified_name) == (kind, name)
    assert explain.catalog_queries() == []


@pytest.mark.parametrize(
    "expressions",
    [
        {"Output": ["lower(app_t.name)", "count(*)", "(app_t.a)::text", "'app_t'::regclass", "$0"]},
        {"Filter": "(NOT (hashed SubPlan 1))", "Output": ["(InitPlan 1).col1", "PARTIAL count(*)"]},
        {"Filter": "(ANY (app_t.a = (hashed SubPlan 1).col1))", "Run Condition": "(row_number() OVER (?) <= 10)"},
        {"Sort Key": ["app_t.a DESC NULLS LAST", "app_t.b USING <", '(lower(app_t.name)) COLLATE "C"']},
        {"Cache Key": "app_t.a, app_t.b", "Output": ["public.app_f(app_t.a)", "(app_t.a OPERATOR(public.===) 1)"]},
        {"Output": ["EXTRACT(year FROM app_t.d)", "CURRENT_USER", "COALESCE(app_t.a, 0)"]},
        {"Output": ["nextval('app_t_id_seq'::regclass)", "nextval('app_t_id_seq'::bigint)"]},
    ],
)
async def test_allowed_expressions_pass_without_catalog_queries(expressions: dict[str, Any]) -> None:
    explain = _Explain({_EXPLAIN + _SELECT: _with(**expressions)})

    await _guard(explain).check(_SELECT)

    assert explain.catalog_queries() == []


async def test_unqualified_builtin_outside_basic_is_rejected_by_the_catalog() -> None:
    """current_setting печатается без схемы (search_path = public); каталог говорит: это pg_catalog."""
    node = _with(Output=["current_setting('app.jwt_secret'::text)", "my_public_fn(app_t.a)"])
    explain = _Explain({_EXPLAIN + _SELECT: node}, pg_catalog_functions=frozenset({"current_setting"}))

    with pytest.raises(PlanAccessError) as exc_info:
        await _guard(explain).check(_SELECT)

    assert (exc_info.value.kind, exc_info.value.qualified_name) == ("function", "pg_catalog.current_setting")
    [query] = explain.catalog_queries()
    assert "'current_setting'" in query
    assert "'my_public_fn'" in query


async def test_unqualified_public_function_passes_after_one_catalog_query() -> None:
    node = _with(Output=["my_public_fn(app_t.a)", "other_fn(app_t.b)"], Filter="(my_public_fn(app_t.a) > 0)")
    explain = _Explain({_EXPLAIN + _SELECT: node}, pg_catalog_functions=frozenset({"current_setting"}))

    await _guard(explain).check(_SELECT)

    assert len(explain.catalog_queries()) == 1


async def test_nextval_of_an_expression_is_checked_as_a_function() -> None:
    node = _with(Output=["nextval(('x'::text)::regclass)"])
    explain = _Explain({_EXPLAIN + _SELECT: node}, pg_catalog_functions=frozenset({"nextval"}))

    with pytest.raises(PlanAccessError, match=r"function 'pg_catalog\.nextval'"):
        await _guard(explain).check(_SELECT)


async def test_sequence_without_the_prefix_is_rejected() -> None:
    explain = _Explain({_EXPLAIN + _SELECT: _with(Output=["nextval('users_id_seq'::regclass)"])})

    with pytest.raises(PlanAccessError, match=r"relation 'public\.users_id_seq'"):
        await _guard(explain, table_prefix="app_").check(_SELECT)


@pytest.mark.parametrize(
    "call",
    ["json_populate_record(NULL::users, '{}'::json)", "json_populate_record(NULL::public.users, '{}'::json)"],
)
async def test_row_type_of_a_table_without_the_prefix_is_rejected(call: str) -> None:
    node = {**_function_scan("pg_catalog", "json_populate_record"), "Function Call": call}
    explain = _Explain({_EXPLAIN + _SELECT: node}, row_types=frozenset({"users"}))

    with pytest.raises(PlanAccessError) as exc_info:
        await _guard(explain, table_prefix="app_").check(_SELECT)

    assert (exc_info.value.kind, exc_info.value.qualified_name) == ("relation", "public.users")


async def test_row_types_are_not_checked_without_a_prefix() -> None:
    explain = _Explain({_EXPLAIN + _SELECT: _with(Output=["NULL::users"])}, row_types=frozenset({"users"}))

    await _guard(explain).check(_SELECT)

    assert explain.catalog_queries() == []


async def test_prefixed_builtin_and_reg_types_need_no_row_type_query() -> None:
    node = _with(Output=["NULL::app_users", 'NULL::"APP_Orders"', "(app_t.a)::text", "'app_t'::regclass"])
    explain = _Explain({_EXPLAIN + _SELECT: node}, row_types=frozenset({"users"}))

    await _guard(explain, table_prefix="app_").check(_SELECT)

    assert [q for q in explain.catalog_queries() if "typrelid" in q] == []


async def test_builtin_type_names_are_loaded_once() -> None:
    node = _with(Output=["(app_t.a)::text", "NULL::my_enum"])
    explain = _Explain({_EXPLAIN + _SELECT: node})
    builtin_types = BuiltinTypeNames()

    for _ in range(3):
        await _guard(explain, table_prefix="app_", builtin_types=builtin_types).check(_SELECT)

    queries = explain.catalog_queries()
    assert len([q for q in queries if "pg_catalog.pg_type" in q and "typrelid" not in q]) == 1
    assert len([q for q in queries if "typrelid" in q]) == 3


@pytest.mark.parametrize(
    ("node", "key"),
    [
        (_with(Output=["foo bar ("]), "Output"),
        (_with(Filter="(a) FROM secret.t"), "Filter"),
        (_with(Output=[42]), "Output"),
        (_with(**{"Sort Key": ["a, b"]}), "Sort Key"),
        (_with(Output=["x::a.b.c"]), "Output"),
        ({"Node Type": "Table Function Scan", "Table Function Call": "secret.f()"}, "Table Function Call"),
    ],
)
async def test_unparsable_expression_is_rejected(node: dict[str, Any], key: str) -> None:
    explain = _Explain({_EXPLAIN + _SELECT: node})

    with pytest.raises(PlanUnverifiableError, match=rf"an expression in {key} of a") as exc_info:
        await _guard(explain).check(_SELECT)

    assert exc_info.value.key == key
    assert "secret" not in str(exc_info.value)


async def test_relation_errors_keep_precedence_over_expressions() -> None:
    """Узлы проверяются до выражений: запрещённое отношение отклоняется прежней ошибкой."""
    plan = {
        "Node Type": "Hash Join",
        "Output": ["(c.relname)::information_schema.sql_identifier"],
        "Plans": [_scan("pg_catalog", "pg_class")],
    }
    explain = _Explain({_EXPLAIN + _SELECT: plan})

    with pytest.raises(PlanAccessError, match=r"relation 'pg_catalog\.pg_class'"):
        await _guard(explain).check(_SELECT)


async def test_type_hint_names_the_allowed_schema() -> None:
    explain = _Explain({_EXPLAIN + _SELECT: _with(Output=["NULL::secret.t"])})

    with pytest.raises(PlanAccessError) as exc_info:
        await _guard(explain).check(_SELECT)

    assert "Only types from 'public' or built-in types are permitted." in str(exc_info.value)
```

`tests/unit/shared/test_errors.py`: в параметры `test_plan_access_hint_follows_kind_and_rules` добавить `("type", "app_", "Only types from 'main' or built-in types are permitted."),`; перед `test_plan_access_message_names_the_object_without_guessing_the_path` добавить:

```python
@pytest.mark.parametrize(
    ("node_type", "reason"),
    [
        ("Seq Scan", "an expression in Filter of a Seq Scan node cannot be verified"),
        (None, "an expression in Filter of a plan node cannot be verified"),
    ],
)
def test_plan_unverifiable_expression_names_the_key_not_the_text(node_type: str | None, reason: str) -> None:
    error = errors.PlanUnverifiableError(node_type, key="Filter")
    assert str(error) == (
        f"The query plan cannot be verified in basic mode: {reason}. "
        "Rewrite the query to read the permitted tables directly."
    )
    assert error.key == "Filter"
```

`tests/unit/postgres/test_safe_sql_executor.py`: в конец класса `TestSafeSqlExecutorPlanCheck`:

```python
    async def test_builtin_type_names_are_loaded_once_per_executor(self) -> None:
        """Кэш типов pg_catalog живёт в исполнителе: второй вызов не повторяет запрос каталога."""
        plan = {"Node Type": "Seq Scan", "Relation Name": "app_t", "Schema": "public", "Output": ["NULL::my_type"]}
        delegate = _precheck_delegate([RowResult(cells={"QUERY PLAN": [{"Plan": plan}]})])
        executor = _basic_executor(delegate)

        await executor.execute("SELECT * FROM app_t")
        await executor.execute("SELECT * FROM app_t")

        builtin = [q for q in delegate.sent if "pg_catalog.pg_type" in q and "typrelid" not in q]
        row_types = [q for q in delegate.sent if "typrelid" in q]
        assert len(builtin) == 1
        assert builtin[0].startswith("/* t */ SELECT t.typname")
        assert len(row_types) == 2
```

(`_precheck_delegate` отдаёт `None` на всё, кроме EXPLAIN, — каталог пуст, `my_type` не встроенный и не строковый тип: проверка проходит, но запросы видны в `delegate.sent`.)

`tests/unit/domains/test_db_access.py`: в конец файла:

```python
def test_basic_executors_share_one_builtin_type_cache() -> None:
    """Пул один — и кэш имён типов pg_catalog для plan_check один на все исполнители."""
    service = _service(access_mode=AccessMode.FULL, write_mode=True, plan_check=True)
    drivers = [service.view(access).sql_driver for access in _ALL_ACCESS[:3]]
    assert all(isinstance(d, SafeSqlExecutor) for d in drivers)
    assert {id(d._builtin_types) for d in drivers} == {id(service._builtin_types)}  # type: ignore[attr-defined]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/postgres/test_plan_guard.py tests/unit/shared/test_errors.py tests/unit/postgres/test_safe_sql_executor.py tests/unit/domains/test_db_access.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'postgres_fastmcp.postgres.security.plan_catalog'` при сборке `test_plan_guard.py`; в `test_errors.py` — `TypeError: … unexpected keyword argument 'key'`.

- [ ] **Step 3: Errors** — `src/postgres_fastmcp/shared/errors.py`:
  - `PlanAccessError.__init__`: в docstring `kind: Вид объекта из плана: relation, function или type.`; ветку подсказки дополнить после `if kind == "function": …`:

```python
        elif kind == "type":
            hint = f"Only types from '{allowed_schema}' or built-in types are permitted."
```

  - `PlanUnverifiableError.__init__` целиком:

```python
    def __init__(self, node_type: str | None = None, *, key: str | None = None) -> None:
        """Инициализация с типом узла плана; текст плана в сообщение не попадает.

        Args:
            node_type: Узел без имени читаемого (Foreign Scan, Custom Scan, Function Scan); None — плана нет.
            key: Ключ узла с неразборчивым выражением (Output, Filter, ...); node_type тогда — его узел
                (None — вложенная группа без Node Type, например Grouping Sets).
        """
        if key is not None:
            reason = f"an expression in {key} of a {node_type or 'plan'} node cannot be verified"
        elif node_type is None:
            reason = "EXPLAIN returned no plan"
        else:
            objects = "functions" if node_type == "Function Scan" else "relations"
            reason = f"a {node_type} whose {objects} cannot be verified"
        message = (
            f"The query plan cannot be verified in basic mode: {reason}. "
            "Rewrite the query to read the permitted tables directly."
        )
        super().__init__(message)
        self.node_type = node_type
        self.key = key
```

- [ ] **Step 4: Catalog module** — создать `src/postgres_fastmcp/postgres/security/plan_catalog.py`:

```python
"""Запросы каталога для проверки выражений плана: SQL сервера в транзакции оператора агента.

Идут через исполнитель транзакции PlanGuard (search_path = allowed_schema, SET LOCAL уже выставлен), без
валидатора агента: это SQL сервера. Имена встраиваются как Literal. Все отношения — с pg_catalog.;
операторы (=, <>, IN) без схемы резолвятся в pg_catalog: он неявно первый в search_path.
"""

from collections.abc import Collection

from psycopg.sql import SQL, Composable, Literal

from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.postgres.ports import StatementRunner


_BUILTIN_TYPES_SQL = (
    "SELECT t.typname AS name FROM pg_catalog.pg_type t "
    "JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace WHERE n.nspname = 'pg_catalog'"
)
_PG_CATALOG_FUNCTIONS_SQL = (
    "SELECT DISTINCT p.proname AS name FROM pg_catalog.pg_proc p "
    "JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace "
    "WHERE n.nspname = 'pg_catalog' AND p.proname IN ({names})"
)
_ROW_TYPES_SQL = (
    "SELECT t.typname AS name FROM pg_catalog.pg_type t "
    "JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace "
    "WHERE n.nspname = {schema} AND t.typrelid <> 0 AND t.typname IN ({names})"
)


def _names(rows: list[RowResult] | None) -> frozenset[str]:
    """Значения колонки name."""
    return frozenset(str(row.cells["name"]) for row in rows or ())


def _literals(names: Collection[str]) -> Composable:
    """Список Literal через запятую для IN (...)."""
    return SQL(", ").join(Literal(name) for name in names)


class BuiltinTypeNames:
    """Имена типов pg_catalog: один запрос на время жизни объекта (DbAccessService держит один на пул).

    Устаревание безопасно: имя без схемы, которое есть в pg_catalog, всегда означает тип pg_catalog (он
    первый в search_path), а тип, появившийся в pg_catalog после загрузки, лишь даёт лишний запрос строковых
    типов. Функции так не кэшируются: пропущенная функция pg_catalog считалась бы функцией allowed_schema.
    """

    def __init__(self) -> None:
        """Пустой кэш: загрузка при первой проверке, где нужен."""
        self._names: frozenset[str] | None = None

    async def load(self, run: StatementRunner) -> frozenset[str]:
        """Имена типов pg_catalog; запрос — только при первом вызове."""
        if self._names is None:
            self._names = _names(await run(_BUILTIN_TYPES_SQL))
        return self._names


async def pg_catalog_functions(run: StatementRunner, names: Collection[str]) -> frozenset[str]:
    """Какие из имён — функции pg_catalog (одним запросом)."""
    sql = SQL(_PG_CATALOG_FUNCTIONS_SQL).format(names=_literals(names)).as_string()
    return _names(await run(sql))


async def row_types(run: StatementRunner, schema: str, names: Collection[str]) -> frozenset[str]:
    """Какие из имён — строковые типы отношений схемы schema (таблицы, представления, составные типы)."""
    sql = SQL(_ROW_TYPES_SQL).format(schema=Literal(schema), names=_literals(names)).as_string()
    return _names(await run(sql))
```

- [ ] **Step 5: `PlanGuard`** — `src/postgres_fastmcp/postgres/security/plan_guard.py`:
  - в docstring модуля после первого абзаца добавить абзац:

```text
Выражения узлов (Output, Filter, условия, ключи сортировки и группировки — ключи EXPLAIN VERBOSE) разбираются
pglast (plan_expressions) и проверяются по тем же правилам: функции и операторы — allowed_schema или pg_catalog
из списка basic, типы — allowed_schema или pg_catalog, строковый тип таблицы без префикса — как сама таблица.
Что решает только каталог (функция или тип без схемы), спрашивается SQL сервера в той же транзакции (plan_catalog).
```

  - импорты: добавить `from dataclasses import dataclass, field`; блок импортов проекта заменить на:

```python
from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.postgres.security.plan_catalog import BuiltinTypeNames, pg_catalog_functions, row_types
from postgres_fastmcp.postgres.security.plan_expressions import (
    EXPRESSION_PARSERS,
    FUNCTION_CALL_KEY,
    ExpressionNames,
    expression_texts,
    parse_target_list,
)
from postgres_fastmcp.postgres.security.policies import BASIC_ALLOWED_FUNCTIONS, NAME_LOOKUP_TYPES
from postgres_fastmcp.postgres.security.schema_guard import is_system_relation_name
from postgres_fastmcp.shared.errors import PlanAccessError, PlanUnverifiableError
```

  - комментарий над `ExplainRunner`: `# Выполняет одну строку SQL (EXPLAIN или запрос каталога сервера) на курсоре транзакции оператора и` / `# возвращает её строки.`; после `FUNCTION_KIND = "function"` добавить `TYPE_KIND = "type"`;
  - заменить всё от `class PlanGuard:` до `def _check_relation(` (не включая) на:

```python
@dataclass(slots=True)
class _CatalogNames:
    """Имена плана, которые решает только каталог; словари — упорядоченные множества в порядке обхода."""

    functions: dict[str, None] = field(default_factory=dict)
    unqualified_types: dict[str, None] = field(default_factory=dict)
    schema_types: dict[str, None] = field(default_factory=dict)


class PlanGuard:
    """Проверяет план каждого оператора: отношения — allowed_schema (с префиксом), функции — она и список basic.

    Выражения узлов (Output, Filter, ключи сортировки и прочие) проверяются после узлов: функции, операторы и
    типы — те же правила, строковый тип отношения allowed_schema без префикса — как само отношение.
    """

    def __init__(
        self,
        run: ExplainRunner,
        *,
        allowed_schema: str,
        table_prefix: str | None,
        builtin_types: BuiltinTypeNames | None = None,
    ) -> None:
        """Инициализация с исполнителем транзакции и правилами basic.

        Args:
            run: Выполняет строку SQL (EXPLAIN или запрос каталога) в транзакции проверяемого оператора
                (SET LOCAL уже выставлен) и возвращает строки.
            allowed_schema: Единственная схема отношений плана (public).
            table_prefix: Если задан, имена отношений плана должны начинаться с него (без учёта регистра).
            builtin_types: Кэш имён типов pg_catalog; None — свой на эту проверку.
        """
        self._run = run
        self._allowed_schema = allowed_schema
        self._table_prefix = table_prefix.lower() if table_prefix else None
        self._prefix_for_hint = table_prefix or None
        self._builtin_types = builtin_types or BuiltinTypeNames()

    async def check(self, query: str) -> None:
        """Построить план каждого планируемого оператора запроса и проверить его узлы и выражения.

        Запрос уже прошёл валидатор, поэтому разбирается без ошибок. Ошибка планирования (отношения нет)
        приходит из исполнителя как ошибка Postgres — та же, что дало бы выполнение.

        Args:
            query: SQL агента после валидации (с тегом-комментарием).

        Raises:
            PlanAccessError: План читает отношение, функцию или тип вне разрешённого.
            PlanUnverifiableError: Плана нет, узел сканирования не называет, что читает, или выражение
                не разбирается.
        """
        for raw in pglast.parse_sql(query):
            target = _plannable(raw.stmt)
            if target is None:
                continue
            statement, generic = target
            options = "VERBOSE, FORMAT JSON, GENERIC_PLAN" if generic else "VERBOSE, FORMAT JSON"
            rows = await self._run(f"EXPLAIN ({options}) {RawStream()(statement)}")
            await self._check_plan(_plan_document(rows))

    async def _check_plan(self, plan: object) -> None:
        """Сначала узлы (отношения и функции сканов), затем выражения, затем имена, которые решает каталог.

        Порядок сохраняет прежние отказы: узел, запрещённый и раньше, отклоняется с той же ошибкой, даже
        если выражение выше по плану тоже запрещено.
        """
        nodes = list(_plan_nodes(plan))
        for node in nodes:
            self._check_node(node)
        pending = _CatalogNames()
        for node in nodes:
            self._check_expressions(node, pending)
        await self._check_catalog_names(pending)

    def _check_node(self, node: dict[str, Any]) -> None:
        """Отношение или функция узла сканирования."""
        node_type = node.get("Node Type")
        if node_type in _RELATION_SCAN_TYPES and "Relation Name" not in node:
            raise PlanUnverifiableError(node_type)
        if node_type == _FUNCTION_SCAN_TYPE and "Function Name" not in node:
            self._check_function_calls(node.get("Function Call"), skip_outermost=False)
        elif node_type == _FUNCTION_SCAN_TYPE and "Function Call" in node:
            self._check_function_calls(node["Function Call"], skip_outermost=True)
        if "Relation Name" in node:
            self._check_relation(node.get("Schema"), str(node["Relation Name"]))
        if "Function Name" in node:
            self._check_function(node.get("Schema"), str(node["Function Name"]))
```

  - в конец класса (после `_function_error`) добавить:

```python
    def _check_expressions(self, node: dict[str, Any], pending: _CatalogNames) -> None:
        """Выражения узла: неразборчивое — PlanUnverifiableError, имена — по правилам basic."""
        node_type = node.get("Node Type")
        for key, value in node.items():
            parse = EXPRESSION_PARSERS.get(key)
            if parse is None:
                continue
            texts = expression_texts(value)
            if texts is None:
                raise PlanUnverifiableError(node_type if isinstance(node_type, str) else None, key=key)
            for text in texts:
                names = parse(text)
                if names is None:
                    raise PlanUnverifiableError(node_type if isinstance(node_type, str) else None, key=key)
                self._check_names(names, pending, check_functions=key != FUNCTION_CALL_KEY)

    def _check_names(self, names: ExpressionNames, pending: _CatalogNames, *, check_functions: bool) -> None:
        """Имена одного выражения; то, что решает только каталог, откладывается в pending."""
        if check_functions:
            for schema, name in names.functions:
                if schema is not None:
                    self._check_function(schema, name)
                elif name.lower() not in BASIC_ALLOWED_FUNCTIONS:
                    pending.functions[name] = None
        for schema, name in names.sequences:
            self._check_relation(schema or self._allowed_schema, name)
        for schema, name in names.operators:
            if schema is not None and schema not in (self._allowed_schema, _BUILTIN_FUNCTION_SCHEMA):
                qualified_name = f"{schema}.{name}"
                raise self._function_error(qualified_name)
        for schema, name in names.types:
            self._check_type(schema, name, pending)

    def _check_type(self, schema: str | None, name: str, pending: _CatalogNames) -> None:
        """Тип выражения: pg_catalog и allowed_schema; строковый тип отношения без префикса — через каталог.

        reg*-типы не отклоняются: их литерал уже разрешён по имени при создании объекта или планировании.
        """
        if schema == _BUILTIN_FUNCTION_SCHEMA:
            return
        if schema is not None and schema != self._allowed_schema:
            raise PlanAccessError(
                TYPE_KIND,
                f"{schema}.{name}",
                allowed_schema=self._allowed_schema,
                table_prefix=self._prefix_for_hint,
            )
        if self._table_prefix is None or name.lower().startswith(self._table_prefix):
            return
        if schema is None and name in NAME_LOOKUP_TYPES:
            return
        (pending.unqualified_types if schema is None else pending.schema_types)[name] = None

    async def _check_catalog_names(self, pending: _CatalogNames) -> None:
        """Функции без схемы вне списка basic — не из pg_catalog; строковые типы — не отношения без префикса."""
        if pending.functions:
            builtin = await pg_catalog_functions(self._run, list(pending.functions))
            for name in pending.functions:
                if name in builtin:
                    qualified_name = f"{_BUILTIN_FUNCTION_SCHEMA}.{name}"
                    raise self._function_error(qualified_name)
        candidates = dict(pending.schema_types)
        if pending.unqualified_types:
            builtin_types = await self._builtin_types.load(self._run)
            candidates.update((name, None) for name in pending.unqualified_types if name not in builtin_types)
        if not candidates:
            return
        found = await row_types(self._run, self._allowed_schema, list(candidates))
        for name in candidates:
            if name in found:
                raise PlanAccessError(
                    RELATION_KIND,
                    f"{self._allowed_schema}.{name}",
                    allowed_schema=self._allowed_schema,
                    table_prefix=self._prefix_for_hint,
                )
```

  `_check_relation`, `_check_function`, `_check_function_calls`, `_function_error` не меняются.

- [ ] **Step 6: `SafeSqlExecutor` и `DbAccessService`**

`src/postgres_fastmcp/postgres/security/driver.py`: импорт `from postgres_fastmcp.postgres.security.plan_catalog import BuiltinTypeNames` (перед импортом `PlanGuard`); `__init__`:

```python
    def __init__(
        self,
        delegate: PrecheckSqlDriverPort,
        validator: QueryValidator,
        config: SafeSqlConfig,
        *,
        builtin_types: BuiltinTypeNames | None = None,
    ) -> None:
        """Инициализация с делегирующим исполнителем, валидатором и конфигурацией.

        Args:
            delegate: Исполнитель без проверок (SqlExecutor): execute и execute_statement с precheck.
            validator: Валидатор, используемый для валидации каждого запроса перед выполнением.
            config: Конфигурация безопасного SQL (тег, таймаут, схема, read_only, префикс).
            builtin_types: Кэш имён типов pg_catalog для plan_check (один на пул); None — свой.
        """
        self._delegate = delegate
        self._validator = validator
        self._config = config
        self._builtin_types = builtin_types or BuiltinTypeNames()
        # Проверка по плану — только для basic (allowed_schema задан); full и канал сервера её не получают.
        self._plan_check_schema = config.allowed_schema if config.plan_check else None
```

в `_checked_run` — в docstring «строит план каждого оператора» → «строит план каждого оператора (и, если выражения плана этого требуют, спрашивает каталог)»; тело после `table_prefix = self._config.table_prefix`:

```python
        builtin_types = self._builtin_types

        async def precheck(runner: StatementRunner) -> None:
            if settings:
                await runner(settings)

            async def run(sql: str) -> list[RowResult] | None:
                # EXPLAIN (deparse pglast; standard_conforming_strings = on закрепляет SqlExecutor в BEGIN)
                # и запросы каталога PlanGuard — с тегом, в той же транзакции.
                return await runner(f"/* {tag} */ {sql}")

            guard = PlanGuard(
                run, allowed_schema=allowed_schema, table_prefix=table_prefix, builtin_types=builtin_types
            )
            await guard.check(query)

        return await self._run(query, run, readonly=self._config.read_only, precheck=precheck)
```

Внимание: внутренняя `run` затеняет параметр `run` метода `_checked_run` только внутри `precheck`; последняя строка метода по-прежнему передаёт в `self._run` параметр метода (она вне `precheck`).

`src/postgres_fastmcp/domains/db_access.py`: импорт `from postgres_fastmcp.postgres.security.plan_catalog import BuiltinTypeNames`; в `__init__` после `self._executors: dict[…] = {}`:

```python
        # Имена типов pg_catalog для plan_check: пул один (одна БД), поэтому и кэш один на все исполнители.
        self._builtin_types = BuiltinTypeNames()
```

в `_executor`:

```python
            executor = SafeSqlExecutor(
                delegate=base, validator=validator, config=safe_config, builtin_types=self._builtin_types
            )
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/unit/postgres/test_plan_guard.py tests/unit/postgres/test_plan_expressions.py tests/unit/shared/test_errors.py tests/unit/postgres/test_safe_sql_executor.py tests/unit/domains/test_db_access.py -q`
Expected: PASS. Прежние тесты `test_plan_guard.py` проходят без изменений: в их планах нет ключей выражений, кроме `Function Call`, где имена функций проверяет только строгий путь, а типы — `pg_catalog.int4`/`text` без префикса (каталог не спрашивается).

- [ ] **Step 8: Lint and commit**

```bash
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
uv run pytest tests/unit -q
git add src/postgres_fastmcp/postgres/security src/postgres_fastmcp/shared/errors.py src/postgres_fastmcp/domains/db_access.py tests/unit/postgres/test_plan_guard.py tests/unit/shared/test_errors.py tests/unit/postgres/test_safe_sql_executor.py tests/unit/domains/test_db_access.py
git commit -m "feat(security): check plan expressions in plan_check"
```

---

### Task 3: Интеграция на живом Postgres и README

**Files:**
- Modify: `tests/integration/test_plan_check.py` (`_SETUP`, новые тесты)
- Modify: `README.md` (раздел «Проверка по плану (`plan_check`)»)

**Interfaces:**
- Consumes: фикстуры `db_plan_check` (basic + `app_` + запись + `plan_check`), `db_full`; `PlanAccessError`.

- [ ] **Step 1: Setup** — в `_SETUP` дописать в конец (перед закрывающими `"""`; представления ссылаются на `public.app_plan_items`, поэтому после его создания):

```sql
CREATE OR REPLACE FUNCTION secret.reveal(t text) RETURNS text
    LANGUAGE plpgsql IMMUTABLE AS 'BEGIN RETURN upper(t); END';
CREATE OR REPLACE VIEW public.app_expr_secret_fn_view AS SELECT secret.reveal(id::text) AS r FROM public.app_plan_items;
CREATE OR REPLACE VIEW public.app_expr_setting_view AS
    SELECT id, current_setting('application_name') AS s FROM public.app_plan_items;
CREATE OR REPLACE VIEW public.app_expr_lower_view AS SELECT lower(id::text) AS l FROM public.app_plan_items;
CREATE OR REPLACE FUNCTION public.app_double(n int) RETURNS int
    LANGUAGE plpgsql IMMUTABLE AS 'BEGIN RETURN n * 2; END';
CREATE OR REPLACE VIEW public.app_expr_public_fn_view AS SELECT app_double(id) AS d FROM public.app_plan_items;
CREATE TABLE IF NOT EXISTS public.other_users (id int, secret_note text);
CREATE TABLE IF NOT EXISTS public.app_serial_items (id serial PRIMARY KEY, v text);
CREATE TABLE IF NOT EXISTS public.app_identity_items (id int GENERATED ALWAYS AS IDENTITY, v text);
```

`plpgsql` — чтобы функции не встраивались: вызов остаётся в `Output` плана. `IMMUTABLE` с аргументом-колонкой не сворачивается при планировании.

- [ ] **Step 2: Tests** — дописать в конец `tests/integration/test_plan_check.py`:

```python
@pytest.mark.asyncio
async def test_view_calling_a_foreign_function_is_rejected_with_plan_check(db_plan_check: DbAccess) -> None:
    """Функция plpgsql не встраивается: вызов secret.reveal виден только в Output плана."""
    with pytest.raises(PlanAccessError, match=r"function 'secret\.reveal'"):
        await db_plan_check.sql_driver.execute("SELECT r FROM app_expr_secret_fn_view", readonly=True)


@pytest.mark.asyncio
async def test_view_calling_a_builtin_outside_basic_is_rejected_with_plan_check(db_plan_check: DbAccess) -> None:
    """current_setting печатается без схемы; каталог подтверждает, что это функция pg_catalog."""
    with pytest.raises(PlanAccessError, match=r"function 'pg_catalog\.current_setting'"):
        await db_plan_check.sql_driver.execute("SELECT s FROM app_expr_setting_view", readonly=True)


@pytest.mark.asyncio
async def test_views_with_allowed_expressions_return_data(db_plan_check: DbAccess) -> None:
    lower_rows = await db_plan_check.sql_driver.execute("SELECT l FROM app_expr_lower_view", readonly=True)
    public_rows = await db_plan_check.sql_driver.execute("SELECT d FROM app_expr_public_fn_view", readonly=True)
    assert "1" in [row.cells["l"] for row in lower_rows]
    assert 2 in [row.cells["d"] for row in public_rows]


@pytest.mark.asyncio
async def test_row_type_of_a_table_without_the_prefix_is_rejected(db_plan_check: DbAccess) -> None:
    """NULL::other_users — строковый тип таблицы public без префикса: оракул её структуры."""
    with pytest.raises(PlanAccessError, match=r"relation 'public\.other_users'"):
        await db_plan_check.sql_driver.execute(
            "SELECT * FROM json_populate_record(NULL::other_users, '{}')", readonly=True
        )


@pytest.mark.asyncio
async def test_row_type_of_a_prefixed_table_passes(db_plan_check: DbAccess) -> None:
    rows = await db_plan_check.sql_driver.execute(
        """SELECT id FROM json_populate_record(NULL::app_plan_items, '{"id": 7}')""", readonly=True
    )
    assert rows[0].cells["id"] == 7


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM app_plan_items WHERE id = (SELECT max(id) FROM app_plan_items WHERE id < 100)",
        "SELECT id FROM app_plan_items WHERE id NOT IN (SELECT id FROM app_plan_items WHERE id > 100)",
        "SELECT id, count(*) AS n, row_number() OVER (ORDER BY id DESC) AS r "
        "FROM app_plan_items GROUP BY id ORDER BY id DESC NULLS LAST",
    ],
)
async def test_subqueries_sorting_and_windows_pass_with_plan_check(db_plan_check: DbAccess, sql: str) -> None:
    """Реальные формы плана: $0/(InitPlan 1).col1, (hashed SubPlan 1), Sort Key, Group Key, OVER (?)."""
    rows = await db_plan_check.sql_driver.execute(sql, readonly=True)
    assert 1 in [row.cells["id"] for row in rows]


@pytest.mark.asyncio
@pytest.mark.parametrize("table", ["app_serial_items", "app_identity_items"])
async def test_insert_with_a_sequence_default_passes_with_plan_check(db_plan_check: DbAccess, table: str) -> None:
    """DEFAULT serial и identity план печатает как nextval('app_..._seq'::тип): это отношение, а не вызов."""
    rows = await db_plan_check.sql_driver.execute(f"INSERT INTO {table} (v) VALUES ('x') RETURNING id", readonly=False)
    await db_plan_check.sql_driver.execute(f"DELETE FROM {table} WHERE v = 'x'", readonly=False)
    assert rows[0].cells["id"] >= 1
```

Сверить статически:
- `SELECT r FROM app_expr_secret_fn_view` проходит валидатор (имя с префиксом); план — Seq Scan `public.app_plan_items` с `Output: secret.reveal((app_plan_items.id)::text)` → статический отказ `function 'secret.reveal'`.
- `app_expr_setting_view`: `Output` содержит `current_setting('application_name'::text)` без схемы → запрос `pg_proc` → `function 'pg_catalog.current_setting'`.
- `app_expr_lower_view`: `lower(...)` в списке basic; `app_expr_public_fn_view`: `app_double(...)` не в basic → запрос `pg_proc` пуст → проходит.
- `json_populate_record(NULL::other_users, '{}')`: валидатор пропускает (`json_populate_record` в basic, тип без схемы не проверяется), строгий путь `Function Call` пропускает (вложенных вызовов нет), общий разбор даёт тип `other_users` → не в `pg_catalog`, не `app_` → `row_types` находит таблицу → `relation 'public.other_users'`. С `NULL::app_plan_items` — префикс, каталог не нужен.
- Подзапросы на PG 15/16: `$0` (InitPlan) и `(NOT (hashed SubPlan 1))`; сортировка и окно — `Sort Key` с `DESC NULLS LAST`, `Group Key`, `row_number() OVER (?)`.
- `INSERT` в `app_serial_items` печатает `nextval('app_serial_items_id_seq'::regclass)`, в `app_identity_items` — `nextval('app_identity_items_id_seq')`: последовательности `public` с префиксом `app_`.

Run: `uv run pytest tests/integration/test_plan_check.py -q`
Expected: локально — тесты собраны и пропущены (нет Docker); в CI — PASS на 15/16.

- [ ] **Step 3: README** — раздел «Проверка по плану (`plan_check`)»:
  - после пункта «- Закрывает представления, правила и встраиваемые SQL-функции поверх чужих схем, которые валидатор по тексту запроса не видит.» добавить пункт:

```markdown
- Проверяет и выражения плана — `Output`, `Filter`, условия соединений и индексов, ключи сортировки и группировки, аргументы табличных функций и прочие выражения `EXPLAIN VERBOSE`. Отклоняются функции и операторы других схем (`secret.decrypt(c)`, `OPERATOR(secret.+)`), встроенные функции вне списка basic (`current_setting('app.jwt_secret')` в представлении), приведения к типам других схем (`::secret.t`) и, при `table_prefix`, строковый тип таблицы `public` без префикса (`json_populate_record(NULL::users, '{}')`). Функция без схемы вне списка basic проверяется по каталогу: функция `public` проходит, функция `pg_catalog` — нет. `nextval('app_t_id_seq'::regclass)` из `DEFAULT` serial/identity проверяется как отношение — последовательность `public` с префиксом. Ссылки на подпланы (`(SubPlan 1)`, `(InitPlan 1).col1`, `$0`) пропускаются: подпланы проверяются как узлы плана. Выражение, которое не удаётся разобрать, — `PlanUnverifiableError`.
```

  - в пункт «- Цена — …» дописать в конец: «Проверка выражений спрашивает каталог в той же транзакции только при необходимости: имена типов `pg_catalog` — один раз на процесс, функции без схемы вне списка basic и строковые типы без префикса — не больше чем по одному запросу на оператор.»
  - в «Что `plan_check` не закрывает» заменить пункт «- функции в выражениях представлений (`SELECT secret.f(x)`), а также операторы, приведения типов и агрегаты, реализованные функциями других схем;» на «- функции, которых план не называет: неявные приведения и приведения `CREATE CAST … WITH FUNCTION`, операторы за `IS DISTINCT FROM`, `NULLIF` и `IN`; схема оператора `USING` в ключе сортировки, collation и метода `TABLESAMPLE` (план печатает их без схемы);» и удалить пункт «- функции политик RLS.» (выражения политик теперь в `Filter` и проверяются);
  - в «Что `plan_check` отклоняет, хотя это легитимно» пункт «- политики RLS с подзапросами к другим схемам;» заменить на «- политики RLS с подзапросами к другим схемам или с функциями вне списка basic (`current_setting('app.tenant')`): выражения политик попадают в `Filter` плана;» и добавить после него:

```markdown
- `DEFAULT` столбцов с функциями вне списка basic в `INSERT` (кроме `nextval` последовательности `public` с префиксом);
- функции `public`, одноимённые встроенным функциям `pg_catalog` (перегрузки): без схемы их не отличить от встроенных;
```

- [ ] **Step 4: Full verification, lint, commit**

```bash
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
git add tests/integration/test_plan_check.py README.md
git commit -m "test(security): cover plan expression checks on live Postgres"
```

---

## Заметки к PR

- С `plan_check` отклоняются запросы, план которых вызывает в выражениях функции/операторы чужих схем или встроенные функции вне списка basic, приводит к типам чужих схем или использует строковый тип таблицы `public` без префикса. Неразборчивое выражение плана → `PlanUnverifiableError` (`an expression in <key> of a <node> node cannot be verified`).
- Политики RLS и `DEFAULT` с функциями вне списка basic (например, `current_setting`) теперь дают отказ; `nextval` последовательностей `public` с префиксом (serial/identity) проходит.
- Новый вид отказа `PlanAccessError` — `type`.
- Лишние запросы к каталогу в транзакции оператора: имена типов `pg_catalog` — один раз на процесс; функции без схемы вне списка basic и строковые типы без префикса — не больше чем по одному на оператор, только когда такие имена есть в плане.

## Self-review

- Спека §2.1 → Task 1 (`EXPRESSION_PARSERS`, `expression_texts`, формы значений — тест `test_expression_texts`); `Function Call` — см. Spec corrections, Task 2 `check_functions=key != FUNCTION_CALL_KEY`. §2.2 → Task 1 (`_PLANNER_REFERENCES`, `parse_sort_key`, неразборчивое → `None`) и Task 2 (`PlanUnverifiableError(..., key=...)`). §2.3 → Task 2 `_check_names`/`_check_type`/`_check_catalog_names` (FuncCall со схемой, без схемы, операторы, типы, reg*, агрегаты и окна — те же `FuncCall`). §2.4 → Task 2 `plan_catalog` (с поправками про кэш и пакетный запрос). §4 юнит → Task 1/2, интеграция → Task 3. §5 → README и заметки к PR.
- Имена согласованы: `ExpressionNames`, `QualifiedName`, `parse_target_list`, `parse_expression`, `parse_sort_key`, `parse_table_function`, `sequence_name`, `expression_texts`, `EXPRESSION_PARSERS`, `FUNCTION_CALL_KEY`, `BuiltinTypeNames.load`, `pg_catalog_functions`, `row_types`, `TYPE_KIND`, `_CatalogNames`, `_builtin_types`.
- Код Task 1–2 и тесты прогнаны на копии репозитория (unit: всё зелёное, `mypy src/` и `ruff` чистые); интеграция — только сборка.

