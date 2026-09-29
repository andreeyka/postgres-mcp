# `plan_check`: путь записи и тела SQL-функций `public` — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** С `plan_check=true` (basic) DML отклоняется, если триггеры, CHECK, значения по умолчанию и генерируемые колонки, выражения индексов, CHECK доменов или политики RLS целевой таблицы вызывают функции чужих схем или встроенные вне basic; тела SQL-функций `public`, до которых доходит запрос, проверяются теми же правилами, включая отношения.

**Architecture:** Запрос правил из PR A становится запросом определений `DEFINITION_DEPENDENCIES_SQL`: из тех же блокировок `pg_locks` он берёт и цели DML (`RowExclusiveLock` и строже, с секциями/наследниками и таблицами каскадных внешних ключей) и отдаёт тексты их триггеров, CHECK, умолчаний, индексов, доменных CHECK и политик плюс зависимости из `pg_depend` — строками того же вида, что и правила. `plan_expressions` получает разбор этих текстов (выражение `pg_get_expr`, `CREATE TRIGGER`, `CREATE INDEX`, тело SQL-функции), в котором отношения законны и собираются. Запрос реализаций из PR A (`ALLOWED_IMPLEMENTATIONS_SQL`) отдаёт и тела SQL-функций `allowed_schema`. `PlanGuard._check_catalog_names` становится циклом не больше пяти кругов: реализации и тела → функции `pg_catalog` → строковые типы → отношения из текстов (`PREPARE … AS SELECT FROM <отношение>` и новое чтение определений). Спека: `docs/superpowers/specs/2026-09-30-plan-check-wave-3-design.md` §4–§5.

**Tech Stack:** Python 3.12, uv, pglast v8.2 (`parse_sql`, `Visitor`; узлы `CreateTrigStmt`, `IndexStmt`, `IndexElem`, `CreateFunctionStmt.sql_body`, `CommonTableExpr`), psycopg 3.3.4 (`psycopg.sql.SQL`/`Identifier`/`Literal`), pytest (asyncio auto).

## Spec corrections

Проверено на живом PostgreSQL 17.10 (временный сервер из `.deb`, `localhost:55432`); PG 15/16 — по документации и исходникам. План опробован: код и тесты задач применены к копии репозитория — `pytest tests/unit/postgres` проходит, `mypy src/` чист, `tests/integration/test_plan_check.py` проходит на живом PG 17 (PR A: 36, PR A + PR B: 44). Оба запроса каталога этого плана выполнены живьём на тестовой схеме (триггер, CHECK, домен над доменом, умолчание, генерируемая колонка, индекс с выражением и предикатом, политика, секция, каскадный FK) и проходят `_unqualified_names` из `test_plan_catalog.py`.

- **Путь записи — до `EXPLAIN`, в том же запросе, что правила.** Живьём: `EXPLAIN INSERT INTO w(id) VALUES (1)` выполняет `DEFAULT secret.api_key()` (IMMUTABLE свёрнут: `Output: …, 'k'::text`). Поэтому объекты пути записи читаются тем же запросом после `PREPARE`, что и правила (`RULE_DEPENDENCIES_SQL` → `DEFINITION_DEPENDENCIES_SQL`), а не отдельным шагом после `EXPLAIN`.
- **(d) Цели DML по `RowExclusiveLock` — верно, но недостаточно.** Живьём после `PREPARE`: `INSERT`/`UPDATE` — `RowExclusiveLock` на цель, `AccessShareLock` на читаемое; вставка в автообновляемое представление — `RowExclusiveLock` на представление и базовую таблицу; `SELECT … FOR UPDATE` (`RowShareLock`) валидатор basic запрещает (`LockingClauseProhibitedError`). Но `PREPARE INSERT INTO pp …` (секционированная) блокирует только `pp`, а триггер секции `pp1` срабатывает при маршрутизации — цели дополняются всеми потомками (`pg_inherits`, рекурсивно). Внешний ключ `ON DELETE/UPDATE CASCADE | SET NULL | SET DEFAULT` выполняет DML в ссылающейся таблице (её триггеры, CHECK, умолчания) — ссылающиеся таблицы тоже цели, рекурсивно (живьём: `DELETE FROM parent_t` даёт CHECK `child_t`).
- **(c) Источники текста.** Триггер — `pg_get_triggerdef(oid)` (`CREATE TRIGGER … [WHEN (…)] EXECUTE FUNCTION f(args)`; `NOT tgisinternal`, `tgenabled <> 'D'`); CHECK таблицы — `pg_get_expr(conbin, conrelid)` (голое выражение; `pg_get_constraintdef` дал бы `CHECK (…) [NOT VALID]`); умолчания и генерируемые колонки — `pg_get_expr(adbin, adrelid)` (`attgenerated = 's'` — тот же `pg_attrdef`); индексы — `pg_get_indexdef(indexrelid)` для `indexprs`/`indpred IS NOT NULL` (разбор `IndexStmt`: выражения ключей, предикат, класс операторов со схемой); CHECK доменов — `pg_get_expr(conbin, 0)` (с `VALUE`), домены — рекурсивно по `typbasetype` и `typelem` (атрибуты составных типов колонок не обходятся: живьём оценка запроса с этой рекурсией — 107795, выше `jit_above_cost` = 100000, и JIT компилировал бы каждый запрос проверки; без неё — 15812); `NOT NULL` домена в PG 17 (`contype = 'n'`, `conbin IS NULL`) пропускается; политики — `pg_get_expr(polqual|polwithcheck, polrelid)` при `relrowsecurity`, все политики таблицы, независимо от команды, роли и `relforcerowsecurity` (консервативно). Живьём все тексты печатают имена вне `search_path` со схемой (`secret.valid(id)`, `FROM secret.t`).
- **Генерируемая колонка на `current_setting` невозможна.** Живьём: `generation expression is not immutable` (`current_setting` — STABLE). Интеграционный тест — генерируемая колонка на `secret.valid(id)` (IMMUTABLE PL/pgSQL). Генерируемые колонки в `Output` плана не печатаются (живьём: `NULL::text` на месте `g`) — закрываются только этой проверкой.
- **Отношения в текстах.** Политика может читать таблицы (`EXISTS (SELECT 1 FROM secret.t …)`); тело SQL-функции — тем более. Отношения текстов проверяются как отношения плана (`allowed_schema`, не системные, с префиксом; без схемы — `allowed_schema`), а представления среди них блокируются `PREPARE … AS SELECT FROM "схема"."имя"; DEALLOCATE …` — их правила (и вложенные представления) читает следующее чтение определений. Имя без схемы, совпадающее с именем CTE того же текста, — CTE.
- **(c) Тела SQL-функций.** Живьём: для `BEGIN ATOMIC`/`RETURN` `prosrc` пуст, `prosqlbody IS NOT NULL`, текст — `pg_get_function_sqlbody(oid)` (`BEGIN ATOMIC … END` или `RETURN …`, имена вне `search_path` со схемой); иначе — `prosrc` (текст как написан). Язык — `pg_language.lanname = 'sql'` (не константа oid). `pg_get_functiondef` не нужен. `SECURITY DEFINER` (`prosecdef`) ничего не меняет — тело проверяется так же.
- **`SET search_path` функции.** Живьём: `CREATE FUNCTION public.withpath() … SET search_path = secret AS 'SELECT x FROM t'` — `proconfig = ['search_path=secret']`, тело читает `secret.t`. Тело `prosrc` с `search_path` в `proconfig`, отличным от ровно `allowed_schema`, не проверить → `PlanUnverifiableError`. Для `BEGIN ATOMIC` имена связаны при создании, `proconfig` не важен.
- **Тела, меняющие данные, — отказ.** У тела с `INSERT`/`UPDATE`/`DELETE`/`MERGE` (в том числе в `WITH`) или служебной командой свои цели записи, их путь записи не проверить → `PlanUnverifiableError` (fail closed; в README).
- **Какие функции проверяются.** Все имена функций без схемы или со схемой `allowed_schema` — из SQL агента (до `PREPARE`: живьём встраиваемая SQL-функция `srf2()` над представлением `v_sec` выполняет его `secret.api_key()` уже при `EXPLAIN`), выражений плана, узлов `Function Scan` со `Schema = allowed_schema`, зависимостей правил и путей записи, функций операторов и опорных функций агрегатов `allowed_schema`, текстов определений. Все перегрузки с именем — один запрос реализаций (PR A), расширенный строками тел. Триггерные функции SQL не бывают (только PL/pgSQL и прочие) — функция триггера проверяется по правилу функций, тело непрозрачно.
- **Глубина ≤ 5 и циклы.** Круг `_check_catalog_names` = один запрос реализаций для новых имён; имя, уже спрошенное в этой проверке, не спрашивается снова (цикл `f → g → f` останавливается). Если после пяти кругов остались новые имена или отношения — `PlanUnverifiableError`.
- **Валидатор.** SQL агента в basic вызывает только функции из списка basic, поэтому интеграционные тесты тел идут через представления (`SELECT n FROM app_secret_count_view`), как и требует спека §7.

## Global Constraints

- Всё, что видит агент или внешняя система (ошибки, логи, коммиты), — на английском; docstring и комментарии — по-русски. README — по-русски.
- Нет `from __future__ import annotations`.
- Ломающие изменения разрешены; описываются только в заметках к PR. В коде — никаких шимов и упоминаний старого поведения.
- Безопасность: ни одно правило валидатора и существующей проверки по плану не ослабляется (правила представлений, узлы, выражения, реализации операторов и агрегатов — как в PR A); `full` и `plan_check=false` не затрагиваются. Весь SQL каталога — с `pg_catalog.` у каждого отношения, функции и типа и `OPERATOR(pg_catalog.…)` у каждого оператора (в том числе на смешанных типах: `int2 > 0::pg_catalog.int2`, `"char" = ANY (…::pg_catalog."char"[])`); имена — `Literal`/`Identifier`.
- Перед каждым коммитом: `uv run ruff check --fix --show-fixes`, `uv run mypy src/`, `uv run ruff format`; затем `uv run ruff check .`.
- Юнит-тесты: `uv run pytest tests/unit -q`. Интеграция: `uv run pytest tests/integration -q` — должна собираться; без Docker пропускается, в CI — Postgres 15/16. Сверять интеграционные тесты статически с кодом.
- Коммиты: `type(scope): message`, повелительное наклонение, английский.
- **Никогда не запускать никакие `git config` и `git stash`.** Не пушить.
- Ветка: `claude/plan-check-write-path` от вершины PR A (`claude/plan-check-prepare-first` после Task 2 плана `2026-09-30-18-plan-check-prepare-first.md`): `git switch -c claude/plan-check-write-path claude/plan-check-prepare-first`.

---

### Task 1: Путь записи целей DML

**Files:**
- Modify: `src/postgres_fastmcp/postgres/security/plan_catalog.py` (`RULE_DEPENDENCIES_SQL` → `DEFINITION_DEPENDENCIES_SQL` с путём записи)
- Modify: `src/postgres_fastmcp/postgres/security/plan_expressions.py` (`ExpressionNames.relations`, `_Names.relations`, `_CteNames`, `_DefinitionNames`, `_collect_definition`, `_single_statement`, `parse_definition_expression`, `parse_trigger_definition`, `parse_index_definition`; `parse_rule_definition` через `_single_statement`)
- Modify: `src/postgres_fastmcp/postgres/security/plan_guard.py` (`_DEFINITION_PARSERS`, `_MAX_DEFINITION_DEPTH`, `_CatalogNames.relations/take/empty`, `_locked_relations`, `_check_definitions`/`_read_definitions` вместо `_check_rules`, `_lock_relations`, `_check_implementations`, `_check_builtin_functions`, `_check_row_types`, цикл `_check_catalog_names`, `_check_names`, `_check_rule_row`, docstring модуля)
- Modify: `src/postgres_fastmcp/shared/errors.py` (текст `PlanUnverifiableError(rules=True)`)
- Modify: `README.md`
- Test: `tests/unit/postgres/test_plan_expressions.py`, `tests/unit/postgres/test_plan_catalog.py`, `tests/unit/postgres/test_plan_guard.py`, `tests/unit/postgres/test_safe_sql_executor.py`, `tests/integration/test_plan_check.py`

**Interfaces:**
- Consumes (PR A): `PlanGuard._prepare(text, *, generic)`, `_row_key`, `_seen_rows`, `_note_implementation`, `_looked_up`, `allowed_implementations`, `_CatalogNames.implementations`, `_OPERATOR_KIND`.
- Produces:
  - `plan_catalog.DEFINITION_DEPENDENCIES_SQL: str` — строки `kind ∈ {rule, trigger, check, domain, default, index, policy, function, aggregate_function, operator, operator_function, type}`, колонки `kind, schema, name, parent_schema, relation_schema, relation_name, definition`;
  - `ExpressionNames.relations: tuple[QualifiedName, ...] = ()`;
  - `parse_definition_expression(text: object) -> ExpressionNames | None`, `parse_trigger_definition(text: object) -> ExpressionNames | None`, `parse_index_definition(text: object) -> ExpressionNames | None`;
  - `plan_expressions._DefinitionNames(ctes: frozenset[str])`, `_collect_definition(root: Node | tuple[Node, ...], names_type: type[_DefinitionNames] = _DefinitionNames) -> ExpressionNames | None` (Task 2 наследует `_BodyNames`);
  - `plan_guard._MAX_DEFINITION_DEPTH = 5`; `_CatalogNames.relations: dict[tuple[str, str], None]`, `_CatalogNames.take() -> Self`, `_CatalogNames.empty() -> bool`;
  - `PlanGuard._check_definitions() -> None`, `PlanGuard._read_definitions(pending) -> None`, `PlanGuard._lock_relations(relations: list[tuple[str, str]]) -> None`, `PlanGuard._locked_relations: set[tuple[str, str]]`.

- [ ] **Step 1: Падающие тесты разбора текстов**

В `tests/unit/postgres/test_plan_expressions.py` импорт дополнить `parse_definition_expression, parse_index_definition, parse_trigger_definition` и добавить:

```python
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("secret.valid(id)", ExpressionNames(functions=(("secret", "valid"),))),
        (
            "((VALUE)::integer > 0)",
            ExpressionNames(operators=((None, ">"),), types=(("pg_catalog", "int4"),)),
        ),
        (
            "nextval('app_t_id_seq'::regclass)",
            ExpressionNames(types=((None, "regclass"),), sequences=((None, "app_t_id_seq"),)),
        ),
        (
            "(EXISTS ( SELECT 1\n   FROM secret.t\n  WHERE (t.x = w.id)))",
            ExpressionNames(operators=((None, "="),), relations=(("secret", "t"),)),
        ),
        ("(id IN ( SELECT app_owners.id FROM app_owners))", ExpressionNames(relations=((None, "app_owners"),))),
        (
            "(id < ALL ( SELECT app_owners.id FROM app_owners))",
            ExpressionNames(operators=((None, "<"),), relations=((None, "app_owners"),)),
        ),
        (
            "((WITH x AS (SELECT 1 AS a) SELECT count(*) AS count FROM x) > 0)",
            ExpressionNames(functions=((None, "count"),), operators=((None, ">"),)),
        ),
    ],
)
def test_definition_expression_names(text: str, expected: ExpressionNames) -> None:
    """pg_get_expr: CHECK, DEFAULT, генерируемая колонка, CHECK домена (VALUE), USING/WITH CHECK политики."""
    assert parse_definition_expression(text) == expected


@pytest.mark.parametrize("text", [None, 42, "", "1; DROP TABLE x", "a.b.c.f(1)", "(SELECT 1 FROM db.s.t)"])
def test_unparsable_definition_expression_is_rejected(text: object) -> None:
    assert parse_definition_expression(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "CREATE TRIGGER w_tr BEFORE INSERT ON public.w FOR EACH ROW WHEN (secret.valid(new.id)) "
            "EXECUTE FUNCTION secret.audit('a')",
            ExpressionNames(functions=(("secret", "audit"), ("secret", "valid"))),
        ),
        (
            "CREATE TRIGGER t AFTER UPDATE ON public.app_t FOR EACH STATEMENT EXECUTE FUNCTION app_audit()",
            ExpressionNames(functions=((None, "app_audit"),)),
        ),
    ],
)
def test_trigger_definition_names(text: str, expected: ExpressionNames) -> None:
    """Функция триггера и имена WHEN; аргументы триггера — строковые константы."""
    assert parse_trigger_definition(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "CREATE INDEX w_expr ON public.w USING btree (lower(g), ((id + 1)), v secret.myops) WHERE secret.valid(id)",
            ExpressionNames(
                functions=((None, "lower"), ("secret", "valid")), operators=((None, "+"), ("secret", "myops"))
            ),
        ),
        ("CREATE UNIQUE INDEX i ON public.app_t USING btree (v text_pattern_ops)", ExpressionNames()),
    ],
)
def test_index_definition_names(text: str, expected: ExpressionNames) -> None:
    """Выражения ключей, предикат и класс операторов со схемой (без схемы он виден в search_path)."""
    assert parse_index_definition(text) == expected


@pytest.mark.parametrize(
    ("parse", "text"),
    [
        (parse_trigger_definition, "SELECT 1"),
        (parse_trigger_definition, None),
        (parse_index_definition, "CREATE TRIGGER t AFTER UPDATE ON t EXECUTE FUNCTION f()"),
        (parse_index_definition, "CREATE INDEX i ON t (x); CREATE INDEX j ON t (y)"),
    ],
)
def test_foreign_statement_text_is_rejected(parse: Callable[[object], ExpressionNames | None], text: object) -> None:
    assert parse(text) is None
```

(добавить `from collections.abc import Callable`, если его нет). Run: `uv run pytest tests/unit/postgres/test_plan_expressions.py -q` — Expected: FAIL, `ImportError`.

- [ ] **Step 2: Разбор текстов в `plan_expressions.py`**

Импорты: `from dataclasses import dataclass, replace`; к `pglast.ast` добавить `CommonTableExpr`, `CreateTrigStmt`, `IndexElem`, `IndexStmt`, `SubLink`. (Разбор этого шага и Task 2 Step 2 опробован на pglast v8.2 копией модуля: все ожидания тестов Step 1 и Task 2 Step 1 совпадают.)

`ExpressionNames` — последнее поле:

```python
    relations: tuple[QualifiedName, ...] = ()
```

`_Names.__init__` — после `self.sequences`:

```python
        # Отношения текста определения (заполняет _DefinitionNames; у выражений плана и правил пусто).
        self.relations: list[QualifiedName] = []
```

`_collect` целиком:

```python
def _collect(statement: Node | tuple[Node, ...], names: _Names | None = None) -> ExpressionNames | None:
    """Имена разобранного выражения (или кортежа узлов); None — среди них есть неразборчивое."""
    names = names or _Names()
    names(statement)
    if not names.verifiable:
        return None
    return ExpressionNames(
        functions=tuple(names.functions),
        operators=tuple(names.operators),
        types=tuple(names.types),
        sequences=tuple(names.sequences),
        relations=tuple(names.relations),
    )
```

После `_RuleNames`:

```python
class _CteNames(Visitor):
    """Имена CTE текста: отношение без схемы с таким именем — CTE, а не таблица."""

    def __init__(self) -> None:
        """Пустое множество имён."""
        super().__init__()
        self.names: set[str] = set()

    def visit_CommonTableExpr(self, _ancestors: object, node: CommonTableExpr) -> None:  # noqa: N802
        """Запомнить имя CTE."""
        if node.ctename:
            self.names.add(node.ctename)


class _DefinitionNames(_Names):
    """Имена текста определения (политика, CHECK, DEFAULT, триггер, индекс, домен) вместе с отношениями.

    Подзапросы законны, отношения собираются — план их не покажет. Имя без схемы, совпадающее с именем CTE текста, — CTE (без учёта области видимости: одноимённая таблица
    в другой области того же текста не проверяется; тексты пишет владелец схемы, не агент).
    """

    def __init__(self, ctes: frozenset[str]) -> None:
        """Пустые списки имён; ctes — имена CTE всего текста."""
        super().__init__()
        self._ctes = ctes

    def visit_SelectStmt(self, _ancestors: object, _node: SelectStmt) -> None:  # noqa: N802
        """Подзапрос определения законен."""

    def visit_SubLink(self, _ancestors: object, node: SubLink) -> None:  # noqa: N802
        """Оператор сравнения с подзапросом (x < ALL (SELECT ...)); у IN и EXISTS оператора нет."""
        self._add(self.operators, node.operName)

    def visit_RangeVar(self, _ancestors: object, node: RangeVar) -> None:  # noqa: N802
        """Отношение определения: (схема или None, имя); имя с базой данных не проверить."""
        if node.catalogname or not node.relname:
            self.verifiable = False
        elif node.schemaname is not None or node.relname not in self._ctes:
            self.relations.append((node.schemaname, node.relname))


def _collect_definition(
    root: Node | tuple[Node, ...], names_type: type[_DefinitionNames] = _DefinitionNames
) -> ExpressionNames | None:
    """Имена текста определения с учётом его CTE; None — неразборчиво."""
    ctes = _CteNames()
    ctes(root)
    return _collect(root, names_type(frozenset(ctes.names)))


def _single_statement[T: Node](text: object, kind: type[T]) -> T | None:
    """Ровно один оператор вида kind; None — не строка, не разбирается или не он."""
    if not isinstance(text, str):
        return None
    try:
        statements = pglast.parse_sql(text)
    except ParseError:
        return None
    statement = statements[0].stmt if len(statements) == 1 else None
    return statement if isinstance(statement, kind) else None
```

`parse_rule_definition` — тело:

```python
    statement = _single_statement(text, RuleStmt)
    return None if statement is None else _collect(statement, _RuleNames())
```

После `parse_rule_definition`:

```python
def parse_definition_expression(text: object) -> ExpressionNames | None:
    """Выражение определения (pg_get_expr): CHECK, DEFAULT, генерируемая колонка, домен, политика RLS.

    CHECK домена — с VALUE; политика — USING и WITH CHECK. Разбирается как "SELECT <text>"; подзапросы
    и отношения законны (политика).
    """
    if not isinstance(text, str) or not text.strip():
        return None
    statement = _single_select(f"SELECT {text}")
    if statement is None or not _only(statement) or len(statement.targetList or ()) != 1:
        return None
    return _collect_definition(statement)


def parse_trigger_definition(text: object) -> ExpressionNames | None:
    """Триггер (pg_get_triggerdef): функция триггера и имена условия WHEN; аргументы — строковые константы."""
    statement = _single_statement(text, CreateTrigStmt)
    if statement is None:
        return None
    function = _qualified(statement.funcname or ())
    names = ExpressionNames() if statement.whenClause is None else _collect_definition(statement.whenClause)
    if function is None or names is None:
        return None
    return replace(names, functions=(function, *names.functions))


def parse_index_definition(text: object) -> ExpressionNames | None:
    """Индекс (pg_get_indexdef): выражения ключей, предикат WHERE и класс операторов со схемой.

    Выражения и опорные функции класса вычисляются при записи. Класс без схемы виден в search_path (pg_catalog
    или allowed_schema); со схемой он проверяется как оператор (схема — allowed_schema или pg_catalog).
    """
    statement = _single_statement(text, IndexStmt)
    if statement is None:
        return None
    elements = statement.indexParams or ()
    if not all(isinstance(element, IndexElem) for element in elements):
        return None
    roots = tuple(element.expr for element in elements if element.expr is not None)
    if statement.whereClause is not None:
        roots = (*roots, statement.whereClause)
    names = _collect_definition(roots) if roots else ExpressionNames()
    if names is None:
        return None
    opclasses: list[QualifiedName] = []
    for element in elements:
        if element.opclass:
            opclass = _qualified(element.opclass)
            if opclass is None:
                return None
            if opclass[0] is not None:
                opclasses.append(opclass)
    return replace(names, operators=(*names.operators, *opclasses))
```

Run: `uv run pytest tests/unit/postgres/test_plan_expressions.py -q` — Expected: PASS (старые тесты тоже: у `ExpressionNames` новое поле со значением по умолчанию).

- [ ] **Step 3: SQL определений**

В `tests/unit/postgres/test_plan_catalog.py` заменить импорт `RULE_DEPENDENCIES_SQL` на `DEFINITION_DEPENDENCIES_SQL` (и в `_catalog_sql`); тесты `test_rule_dependencies_sql_skips_materialized_views` и `test_rule_dependencies_sql_gates_non_select_rules_on_a_dml_lock` переписать:

```python
def test_definition_sql_skips_materialized_views() -> None:
    """Матвью читает уже скопированные данные: чтение не выполняет "_RETURN", определение проверять незачем."""
    assert "k.relkind OPERATOR(pg_catalog.<>) 'm'" in DEFINITION_DEPENDENCIES_SQL


def test_definition_sql_gates_non_select_rules_and_write_targets_on_a_dml_lock() -> None:
    """Правило не ON SELECT и путь записи — только у отношений с блокировкой DML (RowExclusiveLock и строже)."""
    assert "r.ev_type OPERATOR(pg_catalog.=) '1'" in DEFINITION_DEPENDENCIES_SQL
    assert DEFINITION_DEPENDENCIES_SQL.count("k.mode OPERATOR(pg_catalog.=) ANY (") == 2
    for mode in (
        "RowExclusiveLock",
        "ShareUpdateExclusiveLock",
        "ShareLock",
        "ShareRowExclusiveLock",
        "ExclusiveLock",
        "AccessExclusiveLock",
    ):
        assert f"'{mode}'" in DEFINITION_DEPENDENCIES_SQL
    assert "'AccessShareLock'" not in DEFINITION_DEPENDENCIES_SQL
    assert "'RowShareLock'" not in DEFINITION_DEPENDENCIES_SQL


def test_write_targets_include_descendants_and_cascading_references() -> None:
    """PREPARE блокирует только названную таблицу: секции и таблицы каскадных внешних ключей добавляет рекурсия."""
    for fragment in ("pg_catalog.pg_inherits", "i.inhparent", "f.confrelid", "f.confdeltype", "f.confupdtype"):
        assert fragment in DEFINITION_DEPENDENCIES_SQL


def test_write_path_objects_and_their_texts() -> None:
    for fragment in (
        "NOT g.tgisinternal",
        "g.tgenabled OPERATOR(pg_catalog.<>) 'D'",
        "pg_catalog.pg_get_triggerdef(g.oid)",
        "k.contype OPERATOR(pg_catalog.=) 'c'",
        "pg_catalog.pg_get_expr(k.conbin, k.conrelid)",
        "k.contypid",
        "pg_catalog.pg_get_expr(k.conbin, 0::pg_catalog.oid)",
        "pg_catalog.pg_get_expr(d.adbin, d.adrelid)",
        "x.indexprs IS NOT NULL OR x.indpred IS NOT NULL",
        "pg_catalog.pg_get_indexdef(x.oid)",
        "c.relrowsecurity",
        "(p.polqual), (p.polwithcheck)",
    ):
        assert fragment in DEFINITION_DEPENDENCIES_SQL
```

Run: `uv run pytest tests/unit/postgres/test_plan_catalog.py -q` — Expected: FAIL, `ImportError`.

В `plan_catalog.py` три правки (SQL проверен живьём на PG 17).

(а) Комментарий над `_PG_REWRITE` (от `# Правила (pg_rewrite) отношений, которые эта транзакция уже заблокировала …` до `# pg_depend не хранит зависимостей от закреплённых …` включительно) заменить на:

```python
# Определения, до которых дошёл разбор запросов агента (PREPARE) и планировщик (EXPLAIN): всё, что эта транзакция
# заблокировала (pg_locks — и fast-path блокировки), кроме pg_catalog и pg_toast (их блокируют и собственные
# запросы каталога).
#
# Правила (pg_rewrite). Материализованные представления (relkind = 'm') пропускаются: их данные уже скопированы,
# чтение не вычисляет определение. Правило не ON SELECT (ev_type <> '1', оно есть только у DML-таблиц) берётся,
# только если транзакция держит на отношении блокировку DML (RowExclusiveLock и строже): только тогда оно может
# сработать. Правило ON SELECT берётся всегда: чтение представления выполняет его "_RETURN".
#
# Путь записи. Цели — отношения с блокировкой DML, их потомки (pg_inherits: PREPARE блокирует только названную
# секционированную таблицу, а триггеры и ограничения секций срабатывают при маршрутизации) и таблицы, которые
# ссылаются на цель внешним ключом с каскадом (CASCADE, SET NULL, SET DEFAULT меняют ссылающуюся таблицу), —
# рекурсивно. Их объекты: включённые пользовательские триггеры, CHECK, умолчания и генерируемые колонки
# (pg_attrdef), индексы с выражениями или предикатом, CHECK доменов колонок (рекурсивно по базовым доменам
# и элементам массивов; NOT NULL домена без conbin пропускается), политики RLS — все политики таблицы
# с включённым RLS (независимо от команды и роли). Атрибуты составных типов колонок не обходятся: рекурсия
# по pg_attribute поднимает оценку запроса выше jit_above_cost (100000; живьём 107795 против 15812 без неё),
# и JIT компилировал бы каждый запрос проверки.
#
# Строки (kind): rule, trigger, check, domain, default, index, policy — текст определения (definition; имена вне
# search_path — со схемой); function — функция или агрегат из pg_depend этих объектов; aggregate_function —
# опорная функция агрегата (parent_schema — схема агрегата); operator и operator_function — оператор и его
# функция (oprcode); type — тип и каждый тип, до которого он ведёт через typbasetype/typelem, с отношением
# строкового типа (relation_*). pg_depend не хранит зависимостей от закреплённых (встроенных) объектов
# pg_catalog: они видны только в тексте.
```

(б) Константы `_PG_REWRITE`, `_PG_PROC`, `_PG_OPERATOR`, `_PG_TYPE`, `_NO_PARENT`, `_NO_RELATION`, `_NO_DEFINITION`, `_DML_LOCK_MODES` (с их комментариями) не меняются; сразу после `_DML_LOCK_MODES` добавить:

```python
_PG_TRIGGER = "'pg_catalog.pg_trigger'::pg_catalog.regclass::pg_catalog.oid"
_PG_CONSTRAINT = "'pg_catalog.pg_constraint'::pg_catalog.regclass::pg_catalog.oid"
_PG_ATTRDEF = "'pg_catalog.pg_attrdef'::pg_catalog.regclass::pg_catalog.oid"
_PG_CLASS = "'pg_catalog.pg_class'::pg_catalog.regclass::pg_catalog.oid"
_PG_POLICY = "'pg_catalog.pg_policy'::pg_catalog.regclass::pg_catalog.oid"
_NO_NAME = "NULL::pg_catalog.name"
# Действия внешнего ключа, которые меняют ссылающуюся таблицу: CASCADE, SET NULL, SET DEFAULT.
_CASCADE_ACTIONS = "ARRAY['c', 'n', 'd']::pg_catalog.\"char\"[]"
# Живая пользовательская колонка pg_attribute (не системная, не удалённая).
_LIVE_COLUMN = "a.attnum OPERATOR(pg_catalog.>) 0::pg_catalog.int2 AND NOT a.attisdropped"


def _definition_rows(kind: str, text: str, source: str) -> str:
    """Строки текста определения вида kind: text — выражение текста над source."""
    return f"SELECT '{kind}', {_NO_NAME}, {_NO_NAME}, {_NO_PARENT}, {_NO_RELATION}, {text} FROM {source} "  # noqa: S608
```

(в) `RULE_DEPENDENCIES_SQL = (…)` целиком (комментарий `# Подстановки …` над ним остаётся) заменить на:

```python
DEFINITION_DEPENDENCIES_SQL = (
    "WITH RECURSIVE locked AS ("  # noqa: S608
    "SELECT DISTINCT c.oid, c.relkind, l.mode FROM pg_catalog.pg_locks l "
    "JOIN pg_catalog.pg_database db ON db.oid OPERATOR(pg_catalog.=) l.database "
    "JOIN pg_catalog.pg_class c ON c.oid OPERATOR(pg_catalog.=) l.relation "
    "JOIN pg_catalog.pg_namespace cn ON cn.oid OPERATOR(pg_catalog.=) c.relnamespace "
    "WHERE l.locktype OPERATOR(pg_catalog.=) 'relation' "
    "AND l.pid OPERATOR(pg_catalog.=) pg_catalog.pg_backend_pid() "
    "AND db.datname OPERATOR(pg_catalog.=) pg_catalog.current_database() "
    "AND cn.nspname OPERATOR(pg_catalog.<>) ALL (ARRAY['pg_catalog', 'pg_toast']::pg_catalog.name[])"
    "), rules AS ("
    "SELECT DISTINCT r.oid FROM locked k JOIN pg_catalog.pg_rewrite r ON r.ev_class OPERATOR(pg_catalog.=) k.oid "
    "WHERE k.relkind OPERATOR(pg_catalog.<>) 'm' "
    f"AND (r.ev_type OPERATOR(pg_catalog.=) '1' OR k.mode OPERATOR(pg_catalog.=) ANY ({_DML_LOCK_MODES}))"
    "), targets(oid) AS ("
    f"SELECT k.oid FROM locked k WHERE k.mode OPERATOR(pg_catalog.=) ANY ({_DML_LOCK_MODES}) "
    "UNION "
    "SELECT n.oid FROM targets t CROSS JOIN LATERAL ("
    "SELECT i.inhrelid FROM pg_catalog.pg_inherits i WHERE i.inhparent OPERATOR(pg_catalog.=) t.oid "
    "UNION ALL "
    "SELECT f.conrelid FROM pg_catalog.pg_constraint f WHERE f.confrelid OPERATOR(pg_catalog.=) t.oid "
    "AND f.contype OPERATOR(pg_catalog.=) 'f' "
    f"AND (f.confdeltype OPERATOR(pg_catalog.=) ANY ({_CASCADE_ACTIONS}) "
    f"OR f.confupdtype OPERATOR(pg_catalog.=) ANY ({_CASCADE_ACTIONS}))"
    ") AS n(oid)"
    "), column_types(oid) AS ("
    "SELECT a.atttypid FROM targets t "
    f"JOIN pg_catalog.pg_attribute a ON a.attrelid OPERATOR(pg_catalog.=) t.oid WHERE {_LIVE_COLUMN} "
    "UNION "
    "SELECT n.oid FROM column_types y JOIN pg_catalog.pg_type ty ON ty.oid OPERATOR(pg_catalog.=) y.oid "
    "CROSS JOIN LATERAL (VALUES (ty.typbasetype), (ty.typelem)) AS n(oid) "
    "WHERE n.oid OPERATOR(pg_catalog.<>) 0::pg_catalog.oid"
    "), triggers AS ("
    "SELECT g.oid FROM targets t JOIN pg_catalog.pg_trigger g ON g.tgrelid OPERATOR(pg_catalog.=) t.oid "
    "WHERE NOT g.tgisinternal AND g.tgenabled OPERATOR(pg_catalog.<>) 'D'"
    "), checks AS ("
    "SELECT k.oid, k.conbin, k.conrelid FROM targets t "
    "JOIN pg_catalog.pg_constraint k ON k.conrelid OPERATOR(pg_catalog.=) t.oid "
    "WHERE k.contype OPERATOR(pg_catalog.=) 'c'"
    "), domain_checks AS ("
    "SELECT k.oid, k.conbin FROM column_types y "
    "JOIN pg_catalog.pg_constraint k ON k.contypid OPERATOR(pg_catalog.=) y.oid WHERE k.conbin IS NOT NULL"
    "), defaults AS ("
    "SELECT d.oid, d.adbin, d.adrelid FROM targets t "
    "JOIN pg_catalog.pg_attrdef d ON d.adrelid OPERATOR(pg_catalog.=) t.oid"
    "), indexes AS ("
    "SELECT x.indexrelid AS oid FROM targets t JOIN pg_catalog.pg_index x ON x.indrelid OPERATOR(pg_catalog.=) t.oid "
    "WHERE x.indexprs IS NOT NULL OR x.indpred IS NOT NULL"
    "), policies AS ("
    "SELECT p.oid, p.polrelid, p.polqual, p.polwithcheck FROM targets t "
    "JOIN pg_catalog.pg_class c ON c.oid OPERATOR(pg_catalog.=) t.oid "
    "JOIN pg_catalog.pg_policy p ON p.polrelid OPERATOR(pg_catalog.=) t.oid WHERE c.relrowsecurity"
    "), objects(classid, objid) AS ("
    f"SELECT {_PG_REWRITE}, u.oid FROM rules u UNION ALL "
    f"SELECT {_PG_TRIGGER}, g.oid FROM triggers g UNION ALL "
    f"SELECT {_PG_CONSTRAINT}, k.oid FROM checks k UNION ALL "
    f"SELECT {_PG_CONSTRAINT}, k.oid FROM domain_checks k UNION ALL "
    f"SELECT {_PG_ATTRDEF}, d.oid FROM defaults d UNION ALL "
    f"SELECT {_PG_CLASS}, x.oid FROM indexes x UNION ALL "
    f"SELECT {_PG_POLICY}, p.oid FROM policies p"
    "), dependencies AS ("
    "SELECT DISTINCT d.refclassid, d.refobjid FROM objects o "
    "JOIN pg_catalog.pg_depend d ON d.classid OPERATOR(pg_catalog.=) o.classid "
    "AND d.objid OPERATOR(pg_catalog.=) o.objid"
    "), types(oid) AS ("
    f"SELECT x.refobjid FROM dependencies x WHERE x.refclassid OPERATOR(pg_catalog.=) {_PG_TYPE} "
    "UNION "
    "SELECT v.next FROM types y JOIN pg_catalog.pg_type t ON t.oid OPERATOR(pg_catalog.=) y.oid "
    "CROSS JOIN LATERAL (VALUES (t.typbasetype), (t.typelem)) AS v(next) "
    "WHERE v.next OPERATOR(pg_catalog.<>) 0::pg_catalog.oid"
    ") "
    f"SELECT 'rule' AS kind, {_NO_NAME} AS schema, {_NO_NAME} AS name, {_NO_PARENT} AS parent_schema, "
    f"{_NO_NAME} AS relation_schema, {_NO_NAME} AS relation_name, "
    "pg_catalog.pg_get_ruledef(u.oid) AS definition FROM rules u "
    "UNION ALL "
    + _definition_rows("trigger", "pg_catalog.pg_get_triggerdef(g.oid)", "triggers g")
    + "UNION ALL "
    + _definition_rows("check", "pg_catalog.pg_get_expr(k.conbin, k.conrelid)", "checks k")
    + "UNION ALL "
    + _definition_rows("domain", "pg_catalog.pg_get_expr(k.conbin, 0::pg_catalog.oid)", "domain_checks k")
    + "UNION ALL "
    + _definition_rows("default", "pg_catalog.pg_get_expr(d.adbin, d.adrelid)", "defaults d")
    + "UNION ALL "
    + _definition_rows("index", "pg_catalog.pg_get_indexdef(x.oid)", "indexes x")
    + "UNION ALL "
    + _definition_rows(
        "policy",
        "pg_catalog.pg_get_expr(e.expr, p.polrelid)",
        "policies p CROSS JOIN LATERAL (VALUES (p.polqual), (p.polwithcheck)) AS e(expr) WHERE e.expr IS NOT NULL",
    )
    + "UNION ALL "
    f"SELECT 'function', pn.nspname, p.proname, {_NO_PARENT}, {_NO_RELATION}, {_NO_DEFINITION} "  # noqa: S608
    "FROM dependencies x JOIN pg_catalog.pg_proc p ON p.oid OPERATOR(pg_catalog.=) x.refobjid "
    "JOIN pg_catalog.pg_namespace pn ON pn.oid OPERATOR(pg_catalog.=) p.pronamespace "
    f"WHERE x.refclassid OPERATOR(pg_catalog.=) {_PG_PROC} "
    "UNION ALL "
    f"SELECT 'aggregate_function', fn.nspname, f.proname, an.nspname, {_NO_RELATION}, {_NO_DEFINITION} "
    "FROM dependencies x "
    "JOIN pg_catalog.pg_aggregate a ON a.aggfnoid::pg_catalog.oid OPERATOR(pg_catalog.=) x.refobjid "
    "JOIN pg_catalog.pg_proc ap ON ap.oid OPERATOR(pg_catalog.=) x.refobjid "
    "JOIN pg_catalog.pg_namespace an ON an.oid OPERATOR(pg_catalog.=) ap.pronamespace "
    f"CROSS JOIN LATERAL (VALUES {_AGGREGATE_SUPPORT_FUNCTIONS}) AS s(fn) "
    "JOIN pg_catalog.pg_proc f ON f.oid OPERATOR(pg_catalog.=) s.fn::pg_catalog.oid "
    "JOIN pg_catalog.pg_namespace fn ON fn.oid OPERATOR(pg_catalog.=) f.pronamespace "
    f"WHERE x.refclassid OPERATOR(pg_catalog.=) {_PG_PROC} "
    "UNION ALL "
    f"SELECT 'operator', opn.nspname, o.oprname, {_NO_PARENT}, {_NO_RELATION}, {_NO_DEFINITION} "
    "FROM dependencies x JOIN pg_catalog.pg_operator o ON o.oid OPERATOR(pg_catalog.=) x.refobjid "
    "JOIN pg_catalog.pg_namespace opn ON opn.oid OPERATOR(pg_catalog.=) o.oprnamespace "
    f"WHERE x.refclassid OPERATOR(pg_catalog.=) {_PG_OPERATOR} "
    "UNION ALL "
    f"SELECT 'operator_function', fn.nspname, f.proname, opn.nspname, {_NO_RELATION}, {_NO_DEFINITION} "
    "FROM dependencies x JOIN pg_catalog.pg_operator o ON o.oid OPERATOR(pg_catalog.=) x.refobjid "
    "JOIN pg_catalog.pg_namespace opn ON opn.oid OPERATOR(pg_catalog.=) o.oprnamespace "
    "JOIN pg_catalog.pg_proc f ON f.oid OPERATOR(pg_catalog.=) o.oprcode::pg_catalog.oid "
    "JOIN pg_catalog.pg_namespace fn ON fn.oid OPERATOR(pg_catalog.=) f.pronamespace "
    f"WHERE x.refclassid OPERATOR(pg_catalog.=) {_PG_OPERATOR} "
    "UNION ALL "
    f"SELECT 'type', tn.nspname, t.typname, {_NO_PARENT}, cn.nspname, c.relname, {_NO_DEFINITION} "
    "FROM types y JOIN pg_catalog.pg_type t ON t.oid OPERATOR(pg_catalog.=) y.oid "
    "JOIN pg_catalog.pg_namespace tn ON tn.oid OPERATOR(pg_catalog.=) t.typnamespace "
    "LEFT JOIN pg_catalog.pg_class c ON c.oid OPERATOR(pg_catalog.=) t.typrelid "
    "LEFT JOIN pg_catalog.pg_namespace cn ON cn.oid OPERATOR(pg_catalog.=) c.relnamespace"
)
```

Docstring модуля `plan_catalog.py` не меняется. Run: `uv run pytest tests/unit/postgres/test_plan_catalog.py -q` — Expected: PASS (в том числе `test_catalog_sql_resolves_nothing_through_the_search_path`).

- [ ] **Step 4: Падающие тесты guard**

В `tests/unit/postgres/test_plan_guard.py`:
- `match="views or rules"` (два теста) → `match="definitions of views"`;
- в конец файла:

```python
_INSERT = "INSERT INTO app_t (id) VALUES (1)"
_TRIGGER = "CREATE TRIGGER t BEFORE INSERT ON public.app_t FOR EACH ROW {when}EXECUTE FUNCTION {function}()"


@pytest.mark.parametrize(
    ("row", "kind", "name"),
    [
        ({"kind": "trigger", "definition": _TRIGGER.format(when="", function="secret.audit")}, "function", "secret.audit"),
        (
            {
                "kind": "trigger",
                "definition": _TRIGGER.format(
                    when="WHEN ((new.id > (current_setting('x'::text))::integer)) ", function="app_audit"
                ),
            },
            "function",
            "pg_catalog.current_setting",
        ),
        ({"kind": "check", "definition": "secret.valid(id)"}, "function", "secret.valid"),
        ({"kind": "default", "definition": "current_setting('app.tenant'::text)"}, "function", "pg_catalog.current_setting"),
        ({"kind": "default", "definition": "(secret.valid(id))::text"}, "function", "secret.valid"),
        ({"kind": "default", "definition": "nextval('users_id_seq'::regclass)"}, "relation", "public.users_id_seq"),
        (
            {"kind": "index", "definition": "CREATE INDEX i ON public.app_t USING btree (secret.norm(v))"},
            "function",
            "secret.norm",
        ),
        (
            {"kind": "index", "definition": "CREATE INDEX i ON public.app_t USING btree (id) WHERE secret.valid(id)"},
            "function",
            "secret.valid",
        ),
        ({"kind": "index", "definition": "CREATE INDEX i ON public.app_t USING btree (v secret.ops)"}, "function", "secret.ops"),
        ({"kind": "domain", "definition": "secret.valid(VALUE)"}, "function", "secret.valid"),
        (
            {"kind": "policy", "definition": "(EXISTS ( SELECT 1 FROM secret.t WHERE (t.x = app_t.id)))"},
            "relation",
            "secret.t",
        ),
        ({"kind": "policy", "definition": "(id IN ( SELECT users.id FROM users))"}, "relation", "public.users"),
        (
            {"kind": "policy", "definition": "(tenant = (current_setting('app.tenant'::text))::integer)"},
            "function",
            "pg_catalog.current_setting",
        ),
    ],
)
async def test_write_path_definition_outside_basic_is_rejected_before_explain(
    row: dict[str, Any], kind: str, name: str
) -> None:
    """Триггер, CHECK, DEFAULT/генерируемая колонка, индекс, домен, политика цели DML — по правилам basic."""
    explain = _Explain(pg_catalog_functions=frozenset({"current_setting"}), rules=[row])

    with pytest.raises(PlanAccessError) as exc_info:
        await _guard(explain, table_prefix="app_").check(_INSERT)

    assert (exc_info.value.kind, exc_info.value.qualified_name) == (kind, name)
    assert [sql for sql in explain.log if sql.startswith("EXPLAIN")] == []


@pytest.mark.parametrize(
    "row",
    [
        {"kind": "trigger", "definition": _TRIGGER.format(when="", function="app_audit")},
        {"kind": "check", "definition": "(id > 0)"},
        {"kind": "default", "definition": "nextval('app_t_id_seq'::regclass)"},
        {"kind": "index", "definition": "CREATE INDEX i ON public.app_t USING btree (lower(v) text_pattern_ops) WHERE (id > 0)"},
        {
            "kind": "domain",
            "definition": "((VALUE)::text = ANY ((ARRAY['a'::character varying, 'b'::character varying])::text[]))",
        },
        {"kind": "policy", "definition": "(owner = CURRENT_USER)"},
    ],
)
async def test_allowed_write_path_definitions_pass(row: dict[str, Any]) -> None:
    explain = _Explain(rules=[row])

    await _guard(explain, table_prefix="app_").check(_INSERT)

    assert explain.sent[-1] == _EXPLAIN + _INSERT


@pytest.mark.parametrize(
    "row",
    [
        {"kind": "trigger", "definition": "SELECT 1"},
        {"kind": "index", "definition": None},
        {"kind": "check", "definition": "(id > 0"},
        {"kind": "policy", "definition": "(id IN ( SELECT 1 FROM db.s.t))"},
    ],
)
async def test_unparsable_write_path_definition_is_rejected(row: dict[str, Any]) -> None:
    explain = _Explain(rules=[row])

    with pytest.raises(PlanUnverifiableError, match="definitions of views"):
        await _guard(explain).check(_INSERT)


async def test_relation_of_a_definition_is_prepared_and_the_definitions_are_read_again() -> None:
    """Политика читает app_owners: если это представление, его правила видны только после его PREPARE."""
    explain = _Explain(rules=[{"kind": "policy", "definition": "(id IN ( SELECT app_owners.id FROM app_owners))"}])

    await _guard(explain, table_prefix="app_").check(_INSERT)

    locks = [command for command in explain.prepared if "AS SELECT FROM " in command]
    assert len(locks) == 1
    assert 'AS SELECT FROM "public"."app_owners"; DEALLOCATE _pgmcp_check_' in locks[0]
    assert len(explain.rule_queries) == 3


async def test_relation_seen_twice_is_prepared_once() -> None:
    rows = [
        {"kind": "policy", "definition": "(id IN ( SELECT app_owners.id FROM app_owners))"},
        {"kind": "check", "definition": "(id > ( SELECT 0 FROM public.app_owners LIMIT 1))"},
    ]
    explain = _Explain(rules=rows)

    await _guard(explain).check(_INSERT)

    assert len([command for command in explain.prepared if "AS SELECT FROM " in command]) == 1
```

В `tests/unit/postgres/test_safe_sql_executor.py` (`test_plan_is_checked_in_the_statement_transaction`) `startswith("/* t */ WITH RECURSIVE rules AS")` → `startswith("/* t */ WITH RECURSIVE locked AS")`: запрос определений начинается с блокировок.

Run: `uv run pytest tests/unit/postgres/test_plan_guard.py -q` — Expected: FAIL (`PlanUnverifiableError` на незнакомом `kind`, `ImportError` на `RULE_DEPENDENCIES_SQL` после Step 3).

- [ ] **Step 5: Реализация в `plan_guard.py` и `errors.py`**

`errors.py`, `PlanUnverifiableError.__init__`: строку `reason = "the definitions of views or rules the query reaches cannot be verified"` заменить на

```python
            reason = "the definitions of views, rules, tables or functions the query reaches cannot be verified"
```

и описание аргумента `rules` — «Не проверить определения представлений, правил, таблиц (путь записи) или функций, до которых дошёл запрос.»

`plan_guard.py`. Импорты: `from typing import Any, Self`; `from psycopg.sql import SQL, Identifier`; из `plan_catalog` — `DEFINITION_DEPENDENCIES_SQL` вместо `RULE_DEPENDENCIES_SQL`; из `plan_expressions` — `parse_definition_expression`, `parse_index_definition`, `parse_trigger_definition`; `from collections.abc import Awaitable, Callable, Iterator` (уже есть).

Константы — после `_SAVEPOINT`:

```python
# Разбор текстов пути записи по виду строки DEFINITION_DEPENDENCIES_SQL.
_DEFINITION_PARSERS: dict[str, Callable[[object], ExpressionNames | None]] = {
    "trigger": parse_trigger_definition,
    "index": parse_index_definition,
    **dict.fromkeys(("check", "default", "domain", "policy"), parse_definition_expression),
}

# Кругов имён, которые решает каталог: тело функции -> функция в нём -> …, отношение текста -> его правила -> ….
_MAX_DEFINITION_DEPTH = 5
```

`_CatalogNames` целиком:

```python
@dataclass(slots=True)
class _CatalogNames:
    """Имена, которые решает только каталог; словари — упорядоченные множества в порядке обхода."""

    functions: dict[str, None] = field(default_factory=dict)
    unqualified_types: dict[str, None] = field(default_factory=dict)
    schema_types: dict[str, None] = field(default_factory=dict)
    # (вид, имя) операторов и функций без схемы или со схемой allowed_schema: чем они реализованы.
    implementations: dict[tuple[str, str], None] = field(default_factory=dict)
    # (схема, имя) отношений из текстов определений: их представления блокирует PREPARE.
    relations: dict[tuple[str, str], None] = field(default_factory=dict)

    def take(self) -> Self:
        """Забрать накопленное: self пустеет, имена следующего круга копятся в нём заново."""
        taken = type(self)(
            dict(self.functions),
            dict(self.unqualified_types),
            dict(self.schema_types),
            dict(self.implementations),
            dict(self.relations),
        )
        for names in (self.functions, self.unqualified_types, self.schema_types, self.implementations, self.relations):
            names.clear()
        return taken

    def empty(self) -> bool:
        """Спрашивать каталог больше нечего."""
        return not (
            self.functions or self.unqualified_types or self.schema_types or self.implementations or self.relations
        )
```

`PlanGuard.__init__` — в конец:

```python
        # Отношения текстов определений, уже заблокированные PREPARE в этой проверке.
        self._locked_relations: set[tuple[str, str]] = set()
```

`check`: оба вызова `await self._check_rules()` → `await self._check_definitions()`.

`_check_rules` заменить на:

```python
    async def _check_definitions(self) -> None:
        """Определения того, что заблокировала транзакция: правила представлений и путь записи целей DML.

        План не показывает всего, что вычисляет правило (IMMUTABLE-вызов с константами свёрнут, LIMIT/OFFSET
        и рамки окна не печатаются, оператор или агрегат public называет себя), и не показывает вовсе триггеры,
        CHECK, генерируемые колонки, выражения индексов, CHECK доменов и WITH CHECK политик целей DML. Читается
        дважды: после PREPARE (до планирования) и после всех EXPLAIN (планировщик блокирует представления
        встраиваемых SQL-функций). Тексты проверяются как выражения плана плюс их отношения, зависимости
        из pg_depend — по правилам basic.

        Raises:
            PlanAccessError: Определение вызывает функцию, оператор, тип или отношение вне разрешённого.
            PlanUnverifiableError: Ответа нет, текст не разбирается или строка незнакомого вида.
        """
        pending = _CatalogNames()
        await self._read_definitions(pending)
        await self._check_catalog_names(pending)

    async def _read_definitions(self, pending: _CatalogNames) -> None:
        """Одно чтение DEFINITION_DEPENDENCIES_SQL; строка, проверенная раньше в этой проверке, пропускается."""
        rows = await self._run(DEFINITION_DEPENDENCIES_SQL)
        if rows is None:
            raise PlanUnverifiableError(rules=True)
        for row in rows:
            key = _row_key(row.cells)
            if key in self._seen_rows:
                continue
            self._seen_rows.add(key)
            self._check_rule_row(row.cells, pending)

    async def _lock_relations(self, relations: list[tuple[str, str]]) -> None:
        """Отношения из текстов определений: PREPARE SELECT по каждому, чтобы прочитать их правила.

        PREPARE блокирует отношение и представления, к которым оно ведёт (без планирования); их правила прочитает
        следующее чтение определений. Имя уже проверено _check_relation и встраивается как Identifier.
        """
        for relation in relations:
            self._locked_relations.add(relation)
            text = SQL("SELECT FROM {}").format(Identifier(*relation)).as_string()
            await self._prepare(text, generic=False)
```

`_check_rule_row` — в ветвление после `if kind == "rule": …` добавить:

```python
        elif kind in _DEFINITION_PARSERS:
            names = _DEFINITION_PARSERS[kind](cells.get("definition"))
            if names is None:
                raise PlanUnverifiableError(rules=True)
            self._check_names(names, pending, check_functions=True)
```

и поправить docstring: «Одна строка DEFINITION_DEPENDENCIES_SQL или ALLOWED_IMPLEMENTATIONS_SQL; то, что решает только каталог, откладывается в pending.»

`_check_names` — в конец:

```python
        for schema, name in names.relations:
            relation = (schema or self._allowed_schema, name)
            self._check_relation(*relation)
            if relation not in self._locked_relations:
                pending.relations[relation] = None
```

`_check_catalog_names` заменить на цикл и три шага:

```python
    async def _check_catalog_names(self, pending: _CatalogNames) -> None:
        """Имена, которые решает только каталог, — кругами, пока новые не кончатся.

        Круг: реализации операторов и агрегатов allowed_schema; функции без схемы вне списка basic — не из
        pg_catalog; строковые типы — не отношения без префикса; отношения из текстов — PREPARE и новое чтение
        определений. Тексты дают новые имена — следующий круг.

        Raises:
            PlanUnverifiableError: Новые имена не кончились за _MAX_DEFINITION_DEPTH кругов.
        """
        for _ in range(_MAX_DEFINITION_DEPTH):
            current = pending.take()
            await self._check_implementations(current.implementations, pending)
            await self._check_builtin_functions(current.functions)
            await self._check_row_types(current.unqualified_types, current.schema_types)
            if current.relations:
                await self._lock_relations(list(current.relations))
                await self._read_definitions(pending)
            if pending.empty():
                return
        raise PlanUnverifiableError(rules=True)

    async def _check_implementations(
        self, implementations: dict[tuple[str, str], None], pending: _CatalogNames
    ) -> None:
        """Функции, которыми реализованы операторы и агрегаты allowed_schema с этими именами, — по правилам basic."""
        if not implementations:
            return
        keys = list(implementations)
        self._looked_up.update(keys)
        rows = await allowed_implementations(
            self._run,
            self._allowed_schema,
            operators=[name for kind, name in keys if kind == _OPERATOR_KIND],
            functions=[name for kind, name in keys if kind == FUNCTION_KIND],
        )
        if rows is None:
            raise PlanUnverifiableError(rules=True)
        for row in rows:
            self._check_rule_row(row.cells, pending)

    async def _check_builtin_functions(self, functions: dict[str, None]) -> None:
        """Функции без схемы вне списка basic: найденная в pg_catalog — отказ, иначе это функция allowed_schema."""
        if not functions:
            return
        builtin = await pg_catalog_functions(self._run, list(functions))
        for name in functions:
            if name in builtin:
                qualified_name = f"{_BUILTIN_FUNCTION_SCHEMA}.{name}"
                raise self._function_error(qualified_name)

    async def _check_row_types(self, unqualified: dict[str, None], schema_types: dict[str, None]) -> None:
        """Строковые типы: не отношения без префикса (без схемы — только имена вне кэша типов pg_catalog)."""
        candidates = dict(schema_types)
        if unqualified:
            builtin_types = await self._builtin_types.load(self._run)
            candidates.update((name, None) for name in unqualified if name not in builtin_types)
        if not candidates:
            return
        found = await row_types(self._run, self._allowed_schema, list(candidates))
        for name in candidates:
            for relation_schema, relation_name in found.get(name, ()):
                self._check_relation(relation_schema, relation_name)
```

Docstring модуля: в абзаце «Порядок.» слова «читаются правила заблокированного — текст правила и его зависимости (pg_depend)» заменить на «читаются определения заблокированного: правила представлений и путь записи целей DML (триггеры, CHECK, умолчания и генерируемые колонки, индексы, CHECK доменов, политики RLS) — их тексты и зависимости (pg_depend)»; добавить предложение: «Отношения из текстов (подзапрос политики) проверяются как отношения плана и тоже готовятся (PREPARE SELECT FROM …): их представления читает следующее чтение определений.»

`security/driver.py`, docstring `_checked_run`: «читает их правила» → «читает их определения (правила представлений, путь записи целей DML)», «ещё раз читает правила» → «ещё раз читает определения».

Run: `uv run pytest tests/unit -q` — Expected: PASS.

- [ ] **Step 6: Интеграционные тесты**

В `_SETUP` добавить:

```sql
CREATE OR REPLACE FUNCTION secret.audit() RETURNS trigger LANGUAGE plpgsql AS $$BEGIN RETURN NEW; END$$;
CREATE TABLE IF NOT EXISTS public.app_audited (id int);
CREATE OR REPLACE TRIGGER app_audited_audit BEFORE INSERT ON public.app_audited
    FOR EACH ROW EXECUTE FUNCTION secret.audit();
CREATE OR REPLACE FUNCTION secret.valid(n int) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE AS 'BEGIN RETURN n > 0; END';
CREATE TABLE IF NOT EXISTS public.app_checked (id int CHECK (secret.valid(id)));
CREATE TABLE IF NOT EXISTS public.app_generated (id int, flag boolean GENERATED ALWAYS AS (secret.valid(id)) STORED);
CREATE TABLE IF NOT EXISTS public.app_policy_items (id int);
ALTER TABLE public.app_policy_items ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS app_policy_items_check ON public.app_policy_items;
CREATE POLICY app_policy_items_check ON public.app_policy_items WITH CHECK (secret.valid(id));
```

Тесты:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("table", "function"),
    [
        ("app_audited", r"secret\.audit"),
        ("app_checked", r"secret\.valid"),
        ("app_generated", r"secret\.valid"),
        ("app_policy_items", r"secret\.valid"),
    ],
)
async def test_insert_into_a_table_whose_write_path_calls_a_foreign_function_is_rejected(
    db_plan_check: DbAccess, table: str, function: str
) -> None:
    """Триггер, CHECK, генерируемая колонка и WITH CHECK политики в плане не видны: их читает путь записи."""
    with pytest.raises(PlanAccessError, match=rf"function '{function}'"):
        await db_plan_check.sql_driver.execute(f"INSERT INTO {table} (id) VALUES (1)", readonly=False)


@pytest.mark.asyncio
async def test_select_from_a_table_with_a_foreign_trigger_passes(db_plan_check: DbAccess) -> None:
    """SELECT держит AccessShareLock: путь записи не срабатывает и не проверяется."""
    rows = await db_plan_check.sql_driver.execute("SELECT count(*) AS n FROM app_audited", readonly=True)
    assert rows[0].cells["n"] >= 0
```

Сверить статически: `test_insert_with_a_sequence_default_passes_with_plan_check` (умолчание `nextval('app_serial_items_id_seq'::regclass)` → последовательность с префиксом; у identity умолчания в `pg_attrdef` нет), `test_write_statement_is_planned_and_run_once_in_one_transaction` (у `app_plan_items` пути записи нет) и `test_insert_that_can_fire_a_rule_outside_basic_is_rejected` должны проходить без изменений.

- [ ] **Step 7: README**

В разделе «Проверка по плану»:

1. В пункте «**Проверка зависимостей представлений и правил.**» предложение «Тела SQL-функций, политики RLS и триггеры эта проверка не читает.» удалить.
2. После этого пункта добавить пункт:

«- **Путь записи.** Тем же запросом (после `PREPARE`, до `EXPLAIN`) читаются определения целей DML: отношений, на которые транзакция держит блокировку DML, их секций и наследников и таблиц, ссылающихся на них внешним ключом с `ON DELETE`/`ON UPDATE CASCADE | SET NULL | SET DEFAULT`. Проверяются их включённые триггеры (функция и условие `WHEN`), `CHECK`, значения по умолчанию и генерируемые колонки, выражения и предикаты индексов (и класс операторов со схемой), `CHECK` доменов колонок (с базовыми доменами) и, если на таблице включён RLS, выражения всех её политик — текст (`pg_get_triggerdef`, `pg_get_expr`, `pg_get_indexdef`) по правилам выражений плана и зависимости по `pg_depend`. Отношения из текстов (подзапрос политики) проверяются как отношения плана, а их представления — по правилам представлений (они готовятся `PREPARE … AS SELECT FROM …`). Так закрыты `INSERT` в таблицу с триггером `secret.audit()`, с `CHECK (secret.valid(id))`, генерируемой колонкой или `WITH CHECK` политики на функциях чужой схемы.»

3. В «Что `plan_check` не закрывает»: пункт «триггеры на таблицах, которые меняет DML агента;» заменить на «тела триггерных функций (функция триггера из чужой схемы отклоняется, её тело на PL/pgSQL непрозрачно);»; пункт «выражения времени записи, которых нет в плане: …» заменить на «`WITH CHECK OPTION` представлений, выражения ключей секционирования, операторы ограничений-исключений и опорные функции классов операторов `public` (классы других схем в индексах отклоняются), `CHECK` доменов внутри составных типов колонок;».
4. В «отклоняет, хотя это легитимно» добавить: «DML в таблицы, чьи триггеры, `CHECK`, умолчания, индексы, домены или политики (все политики таблицы с включённым RLS, даже неприменимые к роли) вызывают функции других схем или встроенные вне списка basic (в том числе встроенные триггерные функции вроде `suppress_redundant_updates_trigger`), — и в таблицы, которые ссылаются на цель каскадным внешним ключом;».

- [ ] **Step 8: Проверки и коммит**

Run: `uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check . && uv run pytest tests/unit -q && uv run pytest tests/integration -q`
Expected: чисто; PASS; интеграция собирается.

```bash
git add src/postgres_fastmcp/postgres/security/plan_catalog.py src/postgres_fastmcp/postgres/security/plan_expressions.py \
    src/postgres_fastmcp/postgres/security/plan_guard.py src/postgres_fastmcp/postgres/security/driver.py \
    src/postgres_fastmcp/shared/errors.py README.md tests/unit/postgres/test_plan_expressions.py \
    tests/unit/postgres/test_plan_catalog.py tests/unit/postgres/test_plan_guard.py \
    tests/unit/postgres/test_safe_sql_executor.py tests/integration/test_plan_check.py
git commit -m "feat(security): check the write path of DML targets before planning"
```

---

### Task 2: Тела SQL-функций `allowed_schema`

**Files:**
- Modify: `src/postgres_fastmcp/postgres/security/plan_catalog.py` (`ALLOWED_IMPLEMENTATIONS_SQL` — строки тел, колонки `definition`, `config`)
- Modify: `src/postgres_fastmcp/postgres/security/plan_expressions.py` (`_BodyNames`, `parse_function_body`)
- Modify: `src/postgres_fastmcp/postgres/security/plan_guard.py` (`_check_rule_row`: виды `sql_body`/`sql_atomic_body`, отметка функций строк `function`/`operator_function`/`aggregate_function`; `_check_function_config`; `_check_plan` отмечает `Function Scan` allowed_schema)
- Modify: `README.md`
- Test: `tests/unit/postgres/test_plan_expressions.py`, `tests/unit/postgres/test_plan_catalog.py`, `tests/unit/postgres/test_plan_guard.py`, `tests/integration/test_plan_check.py`

**Interfaces:**
- Consumes (Task 1 и PR A): `_DefinitionNames`, `_collect_definition(root, names_type)`, `_check_catalog_names` (цикл), `_note_implementation`, `_lock_relations`, `_MAX_DEFINITION_DEPTH`.
- Produces:
  - `ALLOWED_IMPLEMENTATIONS_SQL` — строки также `sql_body` (definition = `prosrc`, config = `proconfig`) и `sql_atomic_body` (definition = `pg_get_function_sqlbody(oid)`); колонки `kind, schema, name, parent_schema, definition, config`;
  - `parse_function_body(text: object, *, atomic: bool) -> ExpressionNames | None`;
  - `PlanGuard._check_function_config(self, config: object) -> None`;
  - в тестах: `_rule_row(..., config: list[str] | None = None)`; `_Explain(implementations=…)` принимает и `dict[str, list[dict[str, Any]]]` — строки для имён, которые встречаются в запросе литералом.

- [ ] **Step 1: Падающие тесты разбора тел**

В `tests/unit/postgres/test_plan_expressions.py` импорт дополнить `parse_function_body`:

```python
@pytest.mark.parametrize(
    ("text", "atomic", "expected"),
    [
        (
            "SELECT count(*) FROM secret.accounts",
            False,
            ExpressionNames(functions=((None, "count"),), relations=(("secret", "accounts"),)),
        ),
        (
            "SELECT x FROM app_t WHERE id = $1; SELECT 1",
            False,
            ExpressionNames(operators=((None, "="),), relations=((None, "app_t"),)),
        ),
        ("", False, ExpressionNames()),
        (
            "WITH w AS (SELECT id FROM app_t) SELECT count(*) FROM w",
            False,
            ExpressionNames(functions=((None, "count"),), relations=((None, "app_t"),)),
        ),
        (
            "RETURN ((secret.valid($1))::integer + 1)",
            True,
            ExpressionNames(
                functions=(("secret", "valid"),), operators=((None, "+"),), types=(("pg_catalog", "int4"),)
            ),
        ),
        (
            "BEGIN ATOMIC\n SELECT t.x\n    FROM secret.t\n  LIMIT 1;\nEND",
            True,
            ExpressionNames(relations=(("secret", "t"),)),
        ),
    ],
)
def test_function_body_names(text: str, atomic: bool, expected: ExpressionNames) -> None:
    """Тело prosrc (операторы через ;) и pg_get_function_sqlbody (BEGIN ATOMIC / RETURN)."""
    assert parse_function_body(text, atomic=atomic) == expected


@pytest.mark.parametrize(
    ("text", "atomic"),
    [
        (None, False),
        ("SELEC 1", False),
        ("INSERT INTO app_t VALUES (1)", False),
        ("SELECT 1; DELETE FROM app_t", False),
        ("WITH d AS (DELETE FROM app_t RETURNING id) SELECT count(*) FROM d", False),
        ("CREATE TABLE app_x (id int)", False),
        ("BEGIN ATOMIC\n INSERT INTO app_t VALUES (1);\nEND", True),
        ("BEGIN ATOMIC\n UPDATE app_t SET id = 2;\nEND", True),
        ("SELECT 1", True),
    ],
)
def test_data_modifying_or_unparsable_body_is_rejected(text: object, atomic: bool) -> None:
    """Изменение данных в теле — свои цели записи, их путь записи не проверить; служебные команды — тоже."""
    assert parse_function_body(text, atomic=atomic) is None
```

Run: `uv run pytest tests/unit/postgres/test_plan_expressions.py -q` — Expected: FAIL, `ImportError`.

- [ ] **Step 2: `parse_function_body`**

В `plan_expressions.py` к `pglast.ast` добавить `CreateFunctionStmt`, `DeleteStmt`, `InsertStmt`, `MergeStmt`, `UpdateStmt`. После `_collect_definition`:

```python
class _BodyNames(_DefinitionNames):
    """Имена тела SQL-функции: как у определения, но изменение данных (в том числе в WITH) — неразборчиво."""

    def _modifies(self) -> None:
        """У изменения данных свои цели записи: их путь записи не проверить."""
        self.verifiable = False

    def visit_InsertStmt(self, _ancestors: object, _node: InsertStmt) -> None:  # noqa: N802
        """INSERT в теле."""
        self._modifies()

    def visit_UpdateStmt(self, _ancestors: object, _node: UpdateStmt) -> None:  # noqa: N802
        """UPDATE в теле."""
        self._modifies()

    def visit_DeleteStmt(self, _ancestors: object, _node: DeleteStmt) -> None:  # noqa: N802
        """DELETE в теле."""
        self._modifies()

    def visit_MergeStmt(self, _ancestors: object, _node: MergeStmt) -> None:  # noqa: N802
        """MERGE в теле."""
        self._modifies()


def parse_function_body(text: object, *, atomic: bool) -> ExpressionNames | None:
    """Тело SQL-функции: имена, как у выражений плана, и отношения.

    atomic=False — prosrc: операторы через «;», только SELECT (и VALUES); пустое тело законно (функция void).
    atomic=True — pg_get_function_sqlbody: BEGIN ATOMIC ... END или RETURN <выражение>, разбирается как тело
    CREATE FUNCTION. Изменение данных и служебные команды — None.
    """
    if not isinstance(text, str):
        return None
    source = f"CREATE FUNCTION _pgmcp_body() RETURNS void LANGUAGE sql {text}" if atomic else text
    try:
        statements = [raw.stmt for raw in pglast.parse_sql(source)]
    except ParseError:
        return None
    if atomic:
        statement = statements[0] if len(statements) == 1 else None
        if not isinstance(statement, CreateFunctionStmt) or statement.sql_body is None:
            return None
        return _collect_definition(statement.sql_body, _BodyNames)
    if not all(isinstance(statement, SelectStmt) for statement in statements):
        return None
    return _collect_definition(tuple(statements), _BodyNames)
```

(`_collect_definition` на пустом кортеже возвращает `ExpressionNames()`.) Run: `uv run pytest tests/unit/postgres/test_plan_expressions.py -q` — Expected: PASS.

- [ ] **Step 3: Строки тел в запросе реализаций**

`test_plan_catalog.py`:

```python
def test_allowed_implementations_return_sql_bodies_with_their_settings() -> None:
    """Тело SQL-функции: prosrc или (BEGIN ATOMIC/RETURN) pg_get_function_sqlbody; proconfig — для search_path."""
    for fragment in (
        "'sql_body'",
        "'sql_atomic_body'",
        "pg_catalog.pg_get_function_sqlbody(p.oid)",
        "p.prosrc",
        "p.proconfig",
        "p.prosqlbody IS NOT NULL",
        "l.lanname OPERATOR(pg_catalog.=) 'sql'",
    ):
        assert fragment in ALLOWED_IMPLEMENTATIONS_SQL
```

Run — FAIL. Затем в `plan_catalog.py` заменить `ALLOWED_IMPLEMENTATIONS_SQL` и комментарий над ним (SQL проверен живьём на PG 17: `f_sql` → `sql_body`, `f_atomic`/`ret` → `sql_atomic_body` с текстом, `withpath` → `config = ['search_path=secret']`):

```python
# Реализации операторов и функций allowed_schema по именам. План и SQL агента печатают их без схемы и без типов
# аргументов, поэтому берутся все перегрузки с этим именем. Строки — того же вида, что у
# DEFINITION_DEPENDENCIES_SQL: operator_function — функция оператора (oprcode); aggregate_function — опорная
# функция агрегата; operator и operator_function — оператор сортировки агрегата (aggsortop) и его функция;
# sql_body — тело SQL-функции как написано (prosrc; config — proconfig: SET search_path меняет разрешение имён
# тела); sql_atomic_body — тело BEGIN ATOMIC / RETURN (pg_get_function_sqlbody: имена вне search_path — со
# схемой). parent_schema — схема оператора или агрегата.
ALLOWED_IMPLEMENTATIONS_SQL = (
    "WITH operators AS ("  # noqa: S608
    "SELECT o.oprcode FROM pg_catalog.pg_operator o "
    "JOIN pg_catalog.pg_namespace n ON n.oid OPERATOR(pg_catalog.=) o.oprnamespace "
    "WHERE n.nspname OPERATOR(pg_catalog.=) {schema} AND o.oprname OPERATOR(pg_catalog.=) ANY ({operators})"
    "), functions AS ("
    "SELECT p.oid, p.proname, p.prolang, p.prosrc, p.prosqlbody IS NOT NULL AS atomic, p.proconfig "
    "FROM pg_catalog.pg_proc p "
    "JOIN pg_catalog.pg_namespace n ON n.oid OPERATOR(pg_catalog.=) p.pronamespace "
    "WHERE n.nspname OPERATOR(pg_catalog.=) {schema} AND p.proname OPERATOR(pg_catalog.=) ANY ({functions})"
    "), aggregates AS ("
    "SELECT a.* FROM functions p "
    "JOIN pg_catalog.pg_aggregate a ON a.aggfnoid::pg_catalog.oid OPERATOR(pg_catalog.=) p.oid"
    "), sort_operators AS ("
    "SELECT o.oprname, o.oprnamespace, o.oprcode FROM aggregates a "
    "JOIN pg_catalog.pg_operator o ON o.oid OPERATOR(pg_catalog.=) a.aggsortop"
    ") "
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
    "WHERE l.lanname OPERATOR(pg_catalog.=) 'sql'"
)
```

Run: `uv run pytest tests/unit/postgres/test_plan_catalog.py -q` — Expected: PASS.

- [ ] **Step 4: Падающие тесты guard**

Фальшивый исполнитель: `_rule_row` — параметр `config: list[str] | None = None` и ключ `"config": config` в словаре; в `_Explain.__init__` тип `implementations: list[dict[str, Any]] | dict[str, list[dict[str, Any]]] | None = None`; ветка `pg_catalog.pg_aggregate`:

```python
        if "pg_catalog.pg_aggregate" in sql:
            self.implementation_queries.append(sql)
            if isinstance(self._implementations, dict):
                found = [row for name, rows in self._implementations.items() if f"'{name}'" in sql for row in rows]
            else:
                found = self._implementations
            return [RowResult(cells=_rule_row(**row)) for row in found]
```

Тесты:

```python
def _body(name: str, text: str, *, atomic: bool = False, config: list[str] | None = None) -> dict[str, Any]:
    """Строка тела SQL-функции public из ALLOWED_IMPLEMENTATIONS_SQL."""
    kind = "sql_atomic_body" if atomic else "sql_body"
    return {"kind": kind, "schema": "public", "name": name, "definition": text, "config": config}


_VIEW_CALLS = [{"kind": "function", "schema": "public", "name": "app_count"}]


@pytest.mark.parametrize(
    ("body", "kind", "name"),
    [
        (_body("app_count", "SELECT count(*) FROM secret.accounts"), "relation", "secret.accounts"),
        (_body("app_count", "SELECT count(*) FROM users"), "relation", "public.users"),
        (_body("app_count", "SELECT current_setting('x')::bigint"), "function", "pg_catalog.current_setting"),
        (_body("app_count", "RETURN ((secret.valid($1))::integer + 1)", atomic=True), "function", "secret.valid"),
        (
            _body("app_count", "BEGIN ATOMIC\n SELECT count(*) AS count\n    FROM secret.t;\nEND", atomic=True),
            "relation",
            "secret.t",
        ),
    ],
)
async def test_sql_function_body_outside_basic_is_rejected_before_explain(
    body: dict[str, Any], kind: str, name: str
) -> None:
    """Представление вызывает app_count: тело SQL-функции public проверяется теми же правилами, с отношениями."""
    explain = _Explain(
        pg_catalog_functions=frozenset({"current_setting"}), rules=_VIEW_CALLS, implementations={"app_count": [body]}
    )

    with pytest.raises(PlanAccessError) as exc_info:
        await _guard(explain, table_prefix="app_").check(_SELECT)

    assert (exc_info.value.kind, exc_info.value.qualified_name) == (kind, name)
    assert [sql for sql in explain.log if sql.startswith("EXPLAIN")] == []


async def test_allowed_sql_function_body_passes_and_its_relations_are_prepared() -> None:
    body = _body("app_count", "SELECT count(*) FROM public.app_items")
    explain = _Explain(rules=_VIEW_CALLS, implementations={"app_count": [body]})

    await _guard(explain, table_prefix="app_").check(_SELECT)

    locks = [command for command in explain.prepared if "AS SELECT FROM " in command]
    assert len(locks) == 1
    assert '"public"."app_items"' in locks[0]


@pytest.mark.parametrize(
    "body",
    [
        _body("app_count", "SELECT count(*) FROM t", config=["search_path=secret"]),
        _body("app_count", "SELECT count(*) FROM app_t", config=["search_path=public, secret"]),
        _body("app_count", "INSERT INTO app_t VALUES (1)"),
        _body("app_count", "SELECT count(*) FROM"),
    ],
)
async def test_unverifiable_sql_function_body_is_rejected(body: dict[str, Any]) -> None:
    explain = _Explain(rules=_VIEW_CALLS, implementations={"app_count": [body]})

    with pytest.raises(PlanUnverifiableError, match="definitions of views"):
        await _guard(explain).check(_SELECT)


@pytest.mark.parametrize(
    "body",
    [
        _body("app_count", "SELECT count(*) FROM app_t", config=["search_path=public"]),
        _body("app_count", "SELECT count(*) FROM app_t", config=["work_mem=64MB"]),
        _body("app_count", "RETURN 1", atomic=True, config=["search_path=secret"]),
    ],
)
async def test_function_settings_that_keep_the_names_pass(body: dict[str, Any]) -> None:
    """search_path = allowed_schema не меняет разрешение имён; у BEGIN ATOMIC/RETURN имена связаны при создании."""
    explain = _Explain(rules=_VIEW_CALLS, implementations={"app_count": [body]})

    await _guard(explain).check(_SELECT)


def _chain(length: int) -> dict[str, list[dict[str, Any]]]:
    """f0 -> f1 -> ... -> f<length-1>, последняя ничего не вызывает."""
    bodies = {f"f{index}": [_body(f"f{index}", f"SELECT f{index + 1}(1)")] for index in range(length - 1)}
    bodies[f"f{length - 1}"] = [_body(f"f{length - 1}", "SELECT 1")]
    return bodies


async def test_chain_of_bodies_within_the_depth_passes() -> None:
    explain = _Explain(rules=[{"kind": "function", "schema": "public", "name": "f0"}], implementations=_chain(4))

    await _guard(explain).check(_SELECT)

    assert len(explain.implementation_queries) == 4


async def test_chain_of_bodies_beyond_the_depth_is_unverifiable() -> None:
    explain = _Explain(rules=[{"kind": "function", "schema": "public", "name": "f0"}], implementations=_chain(6))

    with pytest.raises(PlanUnverifiableError, match="definitions of views"):
        await _guard(explain).check(_SELECT)


async def test_recursive_body_is_looked_up_once() -> None:
    explain = _Explain(
        rules=[{"kind": "function", "schema": "public", "name": "f0"}],
        implementations={"f0": [_body("f0", "SELECT f0(1)")]},
    )

    await _guard(explain).check(_SELECT)

    assert len(explain.implementation_queries) == 1


async def test_function_scan_of_the_allowed_schema_is_checked_by_its_body() -> None:
    explain = _Explain(
        {_EXPLAIN + _SELECT: _function_scan("public", "app_rows")},
        implementations={"app_rows": [_body("app_rows", "SELECT * FROM secret.accounts")]},
    )

    with pytest.raises(PlanAccessError, match=r"relation 'secret\.accounts'"):
        await _guard(explain).check(_SELECT)


async def test_function_of_a_public_operator_is_checked_by_its_body() -> None:
    """Оператор public реализован SQL-функцией public: её тело вызывает current_setting."""
    explain = _Explain(
        pg_catalog_functions=frozenset({"current_setting"}),
        implementations={
            "<~>": [{"kind": "operator_function", "schema": "public", "name": "app_close_to", "parent_schema": "public"}],
            "app_close_to": [_body("app_close_to", "SELECT current_setting('x') IS NULL")],
        },
    )

    with pytest.raises(PlanAccessError, match=r"function 'pg_catalog\.current_setting'"):
        await _guard(explain).check("SELECT id <~> 1 FROM app_t")
```

Тест PR A `test_public_operator_with_a_public_function_passes` теперь видит второй запрос — за телом `app_close_to` (фальшивый каталог-список отвечает той же строкой оператора, её имя уже спрошено — третьего запроса нет); строки `[query] = explain.implementation_queries` и `assert "'<~>'" in query` заменить на:

```python
    assert len(explain.implementation_queries) == 2
    assert "'<~>'" in explain.implementation_queries[0]
    assert "'app_close_to'" in explain.implementation_queries[1]
```

Счёт в `test_chain_of_bodies_within_the_depth_passes`: круги f0, f1, f2, f3 — четыре запроса, после четвёртого новых имён нет. В `…beyond…`: пять кругов (f0…f4), после пятого остаётся f5 → отказ.

Run: `uv run pytest tests/unit/postgres/test_plan_guard.py -q` — Expected: FAIL (`PlanUnverifiableError` на незнакомом виде `sql_body`; отметки функций нет).

- [ ] **Step 5: Реализация в `plan_guard.py`**

Импорт `parse_function_body` из `plan_expressions`.

`_check_rule_row` — ветки `function` и `operator_function`/`aggregate_function` заменить, добавить ветку тел:

```python
        elif kind == "function":
            self._check_function(schema, name)
            self._note_implementation(FUNCTION_KIND, schema, name, pending)
        elif kind in ("operator_function", "aggregate_function"):
            # Встроенные операторы и агрегаты (pg_catalog) реализованы функциями вне списка basic (int4eq,
            # int4_sum) — проверяются сами; функции проверяются у операторов и агрегатов других схем.
            if cells.get("parent_schema") != _BUILTIN_FUNCTION_SCHEMA:
                self._check_function(schema, name)
                self._note_implementation(FUNCTION_KIND, schema, name, pending)
        elif kind in ("sql_body", "sql_atomic_body"):
            atomic = kind == "sql_atomic_body"
            if not atomic:
                self._check_function_config(cells.get("config"))
            names = parse_function_body(cells.get("definition"), atomic=atomic)
            if names is None:
                raise PlanUnverifiableError(rules=True)
            self._check_names(names, pending, check_functions=True)
```

(ветка `function` идёт до ветки `_DEFINITION_PARSERS` — порядок `elif` не важен, виды не пересекаются.) Новый метод:

```python
    def _check_function_config(self, config: object) -> None:
        """Настройки (proconfig) SQL-функции с телом prosrc: search_path — только allowed_schema.

        SET search_path, отличный от allowed_schema, резолвит имена тела при выполнении в другой схеме
        (search_path = secret: SELECT x FROM t читает secret.t) — такое тело не проверить. SECURITY DEFINER ничего
        не меняет: права владельца делают утечку опаснее, тело проверяется так же.
        """
        if config is None:
            return
        if not isinstance(config, list):
            raise PlanUnverifiableError(rules=True)
        for entry in config:
            if not isinstance(entry, str):
                raise PlanUnverifiableError(rules=True)
            setting, _, value = entry.partition("=")
            if setting.lower() == "search_path" and value != self._allowed_schema:
                raise PlanUnverifiableError(rules=True)
```

`_check_plan` целиком:

```python
    async def _check_plan(self, plan: object) -> None:
        """Сначала узлы (отношения и функции сканов), затем выражения, затем имена, которые решает каталог.

        Порядок сохраняет прежние отказы: узел, запрещённый и раньше, отклоняется с той же ошибкой, даже
        если выражение выше по плану тоже запрещено. Табличная функция allowed_schema — кандидат в SQL-функцию:
        её тело проверяет каталог.
        """
        nodes = list(_plan_nodes(plan))
        pending = _CatalogNames()
        for node in nodes:
            self._check_node(node)
            if node.get("Node Type") == _FUNCTION_SCAN_TYPE and "Function Name" in node:
                self._note_implementation(FUNCTION_KIND, node.get("Schema"), str(node["Function Name"]), pending)
        for node in nodes:
            self._check_expressions(node, pending)
        await self._check_catalog_names(pending)
```

(`_check_node` уже отклонил функцию вне `allowed_schema`/`pg_catalog`; `_note_implementation` берёт только `allowed_schema`.)

`_note_implementation` — docstring дополнить: «Функция — кандидат и в агрегат (опорные функции), и в SQL-функцию (тело).»

Run: `uv run pytest tests/unit -q` — Expected: PASS.

- [ ] **Step 6: Интеграционные тесты**

В `_SETUP` добавить:

```sql
CREATE OR REPLACE FUNCTION public.app_secret_count() RETURNS bigint
    LANGUAGE sql STABLE AS 'SELECT count(*) FROM secret.accounts';
CREATE OR REPLACE VIEW public.app_secret_count_view AS SELECT public.app_secret_count() AS n;
CREATE OR REPLACE FUNCTION public.app_secret_count_atomic() RETURNS bigint
    LANGUAGE sql STABLE BEGIN ATOMIC SELECT count(*) FROM secret.accounts; END;
CREATE OR REPLACE VIEW public.app_secret_count_atomic_view AS SELECT public.app_secret_count_atomic() AS n;
CREATE OR REPLACE FUNCTION public.app_item_count() RETURNS bigint
    LANGUAGE sql STABLE AS 'SELECT count(*) FROM public.app_plan_items';
CREATE OR REPLACE VIEW public.app_item_count_view AS SELECT public.app_item_count() AS n;
```

Тесты:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("view", ["app_secret_count_view", "app_secret_count_atomic_view"])
async def test_view_over_a_public_sql_function_reading_a_foreign_table_is_rejected(
    db_plan_check: DbAccess, view: str
) -> None:
    """Скалярная SQL-функция с FROM не встраивается: план видит только app_secret_count(), тело — проверка тел."""
    with pytest.raises(PlanAccessError, match=r"relation 'secret\.accounts'"):
        await db_plan_check.sql_driver.execute(f"SELECT n FROM {view}", readonly=True)


@pytest.mark.asyncio
async def test_view_over_a_public_sql_function_reading_a_prefixed_table_passes(db_plan_check: DbAccess) -> None:
    rows = await db_plan_check.sql_driver.execute("SELECT n FROM app_item_count_view", readonly=True)
    assert rows[0].cells["n"] >= 1
```

Сверить статически: `app_expr_public_fn_view` (`app_double` на PL/pgSQL) — строк тела нет, проходит; `app_setting_operator_view` (`public.!!` над `current_setting`) по-прежнему отклоняется по `pg_catalog.current_setting`.

- [ ] **Step 7: README**

В разделе «Проверка по плану»:

1. После пункта «**Путь записи.**» добавить:

«- **Тела SQL-функций `public`.** Функция `public` на языке `sql`, до которой доходит запрос (SQL агента, выражения плана, табличная функция во `FROM`, правила представлений, путь записи, функции операторов и агрегатов `public`, другие тела), проверяется по тексту тела: `prosrc` или, для `BEGIN ATOMIC`/`RETURN`, `pg_get_function_sqlbody`. Правила — как у выражений плана, плюс отношения тела (`public`, не системные, с префиксом; их представления — по правилам представлений); вызванные в теле функции `public` проверяются так же, до пяти уровней вложенности. `SECURITY DEFINER` проверяется так же. Отклоняются как непроверяемые (`PlanUnverifiableError`): тело, которое меняет данные (`INSERT`/`UPDATE`/`DELETE`/`MERGE`, в том числе в `WITH`) или содержит служебную команду; функция с `SET search_path`, отличным от `public` (для тела `prosrc`); цепочка вызовов глубже пяти уровней.»

2. В «Что `plan_check` не закрывает»: пункт «невстраиваемые функции (`VOLATILE`, `SECURITY DEFINER`, PL/pgSQL) и тела функций `public` — их тело плану непрозрачно; блокировка на функцию …» заменить на «функции PL/pgSQL и других процедурных языков — их тело непрозрачно; блокировка на функцию не держится до конца транзакции (в отличие от `AccessShareLock` на отношения), поэтому функцию `public` можно конкурентно пересоздать (`CREATE OR REPLACE FUNCTION`) между проверкой её тела и оператором;». В пунктах про `IMMUTABLE`-функции с константами, `(SubPlan N)` на PG 15/16 и `LIMIT`/`OFFSET` слова «не закрыто для встраиваемых SQL-функций (их тело проверка не читает)» / «внутри встраиваемых SQL-функций — нет» / «во встраиваемых SQL-функциях — никто» заменить на «в телах SQL-функций `public` их проверяет проверка тел». Пункт из PR A «`IMMUTABLE`-функция с константными аргументами внутри встраиваемой SQL-функции `public` выполняется планировщиком …» удалить: тело функции, названной в SQL агента или в правиле представления, проверяется до `PREPARE`/`EXPLAIN`.
3. В «отклоняет, хотя это легитимно» добавить: «SQL-функции `public`, меняющие данные, с собственным `SET search_path` (кроме `public`) или с цепочкой вызовов глубже пяти; тела, которые при `table_prefix` читают таблицы `public` без префикса или отношения, которых нет на момент проверки; все перегрузки функции `public` с тем же именем проверяются вместе — одна перегрузка с запрещённым телом отклоняет вызов любой;».

- [ ] **Step 8: Проверки и коммит**

Run: `uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check . && uv run pytest tests/unit -q && uv run pytest tests/integration -q`
Expected: чисто; PASS; интеграция собирается.

```bash
git add src/postgres_fastmcp/postgres/security/plan_catalog.py src/postgres_fastmcp/postgres/security/plan_expressions.py \
    src/postgres_fastmcp/postgres/security/plan_guard.py README.md tests/unit/postgres/test_plan_expressions.py \
    tests/unit/postgres/test_plan_catalog.py tests/unit/postgres/test_plan_guard.py tests/integration/test_plan_check.py
git commit -m "feat(security): check bodies of public SQL functions the query reaches"
```

---

## Заметки к PR (изменения поведения)

- С `plan_check` DML отклоняется, если триггеры, CHECK, умолчания и генерируемые колонки, индексы, домены или политики целевой таблицы (и её секций, и таблиц с каскадным FK на неё) вызывают функции чужих схем или встроенные вне basic, либо читают отношения вне разрешённого.
- Представления и выражения, вызывающие SQL-функции `public` с такими телами, отклоняются; тела с изменением данных, собственным `search_path` или вложенностью глубже пяти — `PlanUnverifiableError`.
- Текст `PlanUnverifiableError` для определений: «the definitions of views, rules, tables or functions the query reaches cannot be verified».
- Запрос определений читает больше каталога (цели DML, их объекты) — по-прежнему один запрос на чтение; отношения из текстов добавляют по `PREPARE … AS SELECT FROM …` на отношение.
