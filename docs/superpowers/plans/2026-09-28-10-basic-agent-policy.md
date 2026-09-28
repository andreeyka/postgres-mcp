# Политика агента в basic — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** В `access_mode=basic` SQL агента не достаёт системные отношения, функции интроспекции сервера, произвольные параметры `SHOW`, приведения к `reg*`-типам и таблицы чужих схем через `hypopg_create_index`.

**Architecture:** Политика basic включается тем же признаком, что ограничение схемы (`QueryValidator(allowed_schema=...)` задан). Правила живут в `postgres/security/`: списки — в `_allowed_functions.py`/`policies.py`, проверки — в `schema_guard.py` (R1) и `query_validator.py` (R2–R5). Full не меняется. Спека: `docs/superpowers/specs/2026-09-28-basic-confinement-design.md`, §4–§6 (ветка 2).

**Tech Stack:** Python 3.12, uv, pglast, psycopg 3.3, pytest (asyncio auto).

## Global Constraints

- Всё, что видит агент или внешняя система (ошибки, логи, коммиты), — на английском; docstring и комментарии — по-русски. README — по-русски.
- Нет `from __future__ import annotations`.
- Ломающие изменения разрешены; никаких шимов, deprecation-предупреждений и упоминаний старого поведения в коде.
- `shared/` — нижний слой: `shared/errors.py` не импортирует `postgres/`.
- Перед каждым коммитом: `uv run ruff check --fix --show-fixes`, `uv run mypy src/`, `uv run ruff format`; затем `uv run ruff check .` без ошибок.
- Юнит-тесты: `uv run pytest tests/unit -q`. Интеграция: `uv run pytest tests/integration -q` — локально пропускается (нет Docker), сверять статически с реальным кодом.
- Коммиты: `type(scope): message`, повелительное наклонение, английский.
- **Никогда не запускать `git config` и `git stash`.** Не пушить.
- Ветка: `claude/basic-agent-policy` (создана от `main` `3664226`).

---

### Task 1: списки политики basic и новые ошибки

**Files:**
- Modify: `src/postgres_fastmcp/postgres/security/_allowed_functions.py`, `src/postgres_fastmcp/postgres/security/policies.py`
- Modify: `src/postgres_fastmcp/shared/errors.py`
- Test: `tests/unit/postgres/test_allowed_functions.py`, `tests/unit/shared/test_errors.py`

**Interfaces:**
- Produces (all re-exported from `postgres_fastmcp.postgres.security.policies`): `INTROSPECTION_FUNCTIONS: frozenset[str]`, `BASIC_ALLOWED_FUNCTIONS: frozenset[str]`, `BASIC_SHOW_PARAMETERS: frozenset[str]`, `REG_TYPES: frozenset[str]`.
- Produces (in `postgres_fastmcp.shared.errors`): `SystemRelationAccessError(relation: str)`, `ShowParameterNotAllowedError(name: str, allowed: Sequence[str])`, `TypeCastNotAllowedError(type_name: str)` — all `UserFacingError`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/postgres/test_allowed_functions.py` (extend the import to `from postgres_fastmcp.postgres.security.policies import ALLOWED_FUNCTIONS, BASIC_ALLOWED_FUNCTIONS, INTROSPECTION_FUNCTIONS`):

```python
# Единственные функции pg_* в basic: работают со значениями, не с объектами сервера.
# Новая pg_*-функция в общем списке без решения по basic роняет тест.
BASIC_PG_FUNCTIONS = frozenset(
    {
        "pg_typeof",
        "pg_column_size",
        "pg_column_compression",
        "pg_size_pretty",
        "pg_size_bytes",
        "pg_client_encoding",
        "pg_encoding_to_char",
        "pg_char_to_encoding",
        "pg_basetype",
        "pg_get_keywords",
        "pg_trigger_depth",
    }
)


def test_basic_pg_functions_are_an_explicit_decision() -> None:
    assert {f for f in BASIC_ALLOWED_FUNCTIONS if f.startswith("pg_")} == BASIC_PG_FUNCTIONS


def test_introspection_functions_exist_in_the_full_list() -> None:
    """Опечатка в списке исключений молча оставила бы функцию в basic."""
    assert INTROSPECTION_FUNCTIONS <= ALLOWED_FUNCTIONS, sorted(INTROSPECTION_FUNCTIONS - ALLOWED_FUNCTIONS)


def test_basic_list_is_full_list_without_introspection() -> None:
    assert BASIC_ALLOWED_FUNCTIONS == ALLOWED_FUNCTIONS - INTROSPECTION_FUNCTIONS
    assert {"current_setting", "pg_get_functiondef", "to_regclass", "pg_input_is_valid"} <= INTROSPECTION_FUNCTIONS
    assert {"current_user", "version", "hypopg_create_index", "lower"} <= BASIC_ALLOWED_FUNCTIONS
```

In `tests/unit/shared/test_errors.py`: add to `_SAMPLES`

```python
    "SystemRelationAccessError": lambda: errors.SystemRelationAccessError("pg_stats"),
    "ShowParameterNotAllowedError": lambda: errors.ShowParameterNotAllowedError(
        "app.secret", ["search_path", "timezone"]
    ),
    "TypeCastNotAllowedError": lambda: errors.TypeCastNotAllowedError("regclass"),
```

and to the `test_correctable_error_ends_with_hint` parameters

```python
        ("SystemRelationAccessError", "Use list_objects and get_object_details"),
        ("ShowParameterNotAllowedError", "Allowed parameters: search_path, timezone."),
        ("TypeCastNotAllowedError", "Rewrite the query without object identifier types"),
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/postgres/test_allowed_functions.py tests/unit/shared/test_errors.py -q`
Expected: FAIL — `ImportError: cannot import name 'BASIC_ALLOWED_FUNCTIONS'`; `AttributeError: module ... has no attribute 'SystemRelationAccessError'`.

- [ ] **Step 3: Add the lists**

At the end of `src/postgres_fastmcp/postgres/security/_allowed_functions.py`:

```python
# Интроспекция сервера и объектов по имени/OID: раскрывает настройки, сессии и объекты чужих схем.
# В basic недоступна; full использует полный ALLOWED_FUNCTIONS.
INTROSPECTION_FUNCTIONS: frozenset[str] = frozenset(
    (
        # настройки и сервер
        "current_setting",
        "pg_conf_load_time",
        "pg_current_logfile",
        "pg_postmaster_start_time",
        "pg_is_in_recovery",
        "pg_jit_available",
        "pg_settings_get_flags",
        "pg_control_checkpoint",
        "pg_control_system",
        "pg_control_init",
        "pg_control_recovery",
        "pg_available_wal_summaries",
        "pg_wal_summary_contents",
        "pg_get_wal_summarizer_state",
        "pg_tablespace_location",
        "pg_tablespace_databases",
        "pg_tablespace_size",
        "pg_database_size",
        "inet_client_addr",
        "inet_client_port",
        "inet_server_addr",
        "inet_server_port",
        # сессии и транзакции
        "pg_backend_pid",
        "pg_blocking_pids",
        "pg_safe_snapshot_blocking_pids",
        "pg_listening_channels",
        "pg_notification_queue_usage",
        "pg_my_temp_schema",
        "pg_is_other_temp_schema",
        "pg_current_xact_id",
        "pg_current_snapshot",
        "pg_snapshot_xip",
        "pg_xact_commit_timestamp",
        # объекты по имени или OID
        "pg_get_expr",
        "pg_get_functiondef",
        "pg_get_function_arguments",
        "pg_get_function_identity_arguments",
        "pg_get_function_result",
        "pg_get_catalog_foreign_keys",
        "pg_get_constraintdef",
        "pg_get_userbyid",
        "pg_get_partkeydef",
        "pg_get_serial_sequence",
        "pg_get_viewdef",
        "pg_get_ruledef",
        "pg_get_triggerdef",
        "pg_get_statisticsobjdef",
        "pg_get_indexdef",
        "pg_relation_size",
        "pg_table_size",
        "pg_indexes_size",
        "pg_total_relation_size",
        "pg_relation_filenode",
        "pg_column_toast_chunk_id",
        "has_any_column_privilege",
        "has_column_privilege",
        "has_database_privilege",
        "has_foreign_data_wrapper_privilege",
        "has_function_privilege",
        "has_language_privilege",
        "has_parameter_privilege",
        "has_schema_privilege",
        "has_sequence_privilege",
        "has_server_privilege",
        "has_table_privilege",
        "has_tablespace_privilege",
        "has_type_privilege",
        "pg_has_role",
        "row_security_active",
        "to_regtype",
        "to_regtypemod",
        "to_regclass",
        "to_regcollation",
        "to_regnamespace",
        "to_regoper",
        "to_regoperator",
        "to_regproc",
        "to_regprocedure",
        "to_regrole",
        "regclass",
        "pg_index_column_has_property",
        "pg_index_has_property",
        "pg_indexam_has_property",
        "pg_collation_is_visible",
        "pg_conversion_is_visible",
        "pg_function_is_visible",
        "pg_opclass_is_visible",
        "pg_operator_is_visible",
        "pg_opfamily_is_visible",
        "pg_statistics_obj_is_visible",
        "pg_table_is_visible",
        "pg_ts_config_is_visible",
        "pg_ts_dict_is_visible",
        "pg_ts_parser_is_visible",
        "pg_ts_template_is_visible",
        "pg_type_is_visible",
        "acldefault",
        "aclexplode",
        "makeaclitem",
        "pg_options_to_table",
        # с типом reg* во втором аргументе разрешают имена объектов через функцию ввода типа (обход R4)
        "pg_input_is_valid",
        "pg_input_error_info",
    )
)

# Функции basic: всё разрешённое, кроме интроспекции.
BASIC_ALLOWED_FUNCTIONS: frozenset[str] = ALLOWED_FUNCTIONS - INTROSPECTION_FUNCTIONS
```

In `src/postgres_fastmcp/postgres/security/policies.py`: import `BASIC_ALLOWED_FUNCTIONS, INTROSPECTION_FUNCTIONS` from `_allowed_functions` next to `ALLOWED_FUNCTIONS`, and add after `ALLOWED_EXTENSIONS`:

```python
# Параметры, которые basic разрешает читать через SHOW: формат, кодировки, версия, свои лимиты сессии.
# Остальные (в том числе пользовательские app.*, где приложения держат секреты) закрыты, как current_setting.
BASIC_SHOW_PARAMETERS: frozenset[str] = frozenset(
    {
        "server_version",
        "server_version_num",
        "timezone",
        "datestyle",
        "intervalstyle",
        "search_path",
        "client_encoding",
        "server_encoding",
        "standard_conforming_strings",
        "statement_timeout",
        "transaction_isolation",
        "transaction_read_only",
    }
)

# Типы идентификаторов объектов: приведение 'other.t'::regclass сообщает о существовании объекта без прав на него.
REG_TYPES: frozenset[str] = frozenset(
    {
        "regclass",
        "regproc",
        "regprocedure",
        "regoper",
        "regoperator",
        "regtype",
        "regrole",
        "regnamespace",
        "regconfig",
        "regdictionary",
        "regcollation",
    }
)
```

and extend `__all__` with `"BASIC_ALLOWED_FUNCTIONS"`, `"BASIC_SHOW_PARAMETERS"`, `"INTROSPECTION_FUNCTIONS"`, `"REG_TYPES"` (keep it sorted).

- [ ] **Step 4: Add the errors**

In `src/postgres_fastmcp/shared/errors.py`, after `TablePrefixAccessError`:

```python
class SystemRelationAccessError(UserFacingError):
    """Доступ к системному отношению (pg_*, _pg_*) в basic запрещён (валидация SQL)."""

    def __init__(self, relation: str) -> None:
        """Инициализация с именем отношения.

        Args:
            relation: Имя системного отношения из запроса.
        """
        message = (
            f"Access to system relation '{relation}' is not allowed in basic mode. "
            "Use list_objects and get_object_details to inspect tables in 'public'."
        )
        super().__init__(message)
        self.relation = relation


class ShowParameterNotAllowedError(UserFacingError):
    """SHOW параметра вне разрешённого списка basic (валидация SQL)."""

    def __init__(self, name: str, allowed: Sequence[str]) -> None:
        """Инициализация с именем параметра и разрешённым списком.

        Args:
            name: Имя параметра из SHOW.
            allowed: Параметры, которые basic разрешает читать.
        """
        message = f"SHOW {name} is not allowed in basic mode. Allowed parameters: {', '.join(sorted(allowed))}."
        super().__init__(message)
        self.name = name


class TypeCastNotAllowedError(UserFacingError):
    """Приведение к типу идентификатора объекта (reg*) в basic запрещено (валидация SQL)."""

    def __init__(self, type_name: str) -> None:
        """Инициализация с именем типа.

        Args:
            type_name: Имя reg*-типа из приведения.
        """
        message = (
            f"Casts to {type_name} are not allowed in basic mode. Rewrite the query without object identifier types."
        )
        super().__init__(message)
        self.type_name = type_name
```

(`Sequence` is already imported in this module.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit -q`
Expected: PASS (nothing uses the new lists yet).

- [ ] **Step 6: Lint, type-check, commit**

```bash
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
git add src/postgres_fastmcp/postgres/security src/postgres_fastmcp/shared/errors.py tests/unit/postgres/test_allowed_functions.py tests/unit/shared/test_errors.py
git commit -m "feat(security): define the basic-mode function, SHOW and reg-type policies"
```

---

### Task 2: валидатор применяет R1–R4 в basic

**Files:**
- Modify: `src/postgres_fastmcp/postgres/security/schema_guard.py`, `src/postgres_fastmcp/postgres/security/query_validator.py`
- Test: `tests/unit/postgres/test_query_validator_corpus.py`, `tests/unit/postgres/test_query_validator.py`
- Modify (expectations that change): `tests/unit/domains/test_catalog_service.py` (`test_agent_sql_driver_is_still_restricted`), `tests/integration/test_table_prefix.py` (`test_table_prefix_blocks_system_schemas`, `test_agent_sql_still_cannot_read_system_catalogs`)

**Interfaces:**
- Consumes (Task 1): `BASIC_ALLOWED_FUNCTIONS`, `BASIC_SHOW_PARAMETERS`, `REG_TYPES` from `postgres.security.policies`; `SystemRelationAccessError`, `ShowParameterNotAllowedError`, `TypeCastNotAllowedError` from `shared.errors`.
- Produces: `QueryValidator` with unchanged constructor; basic ⇔ `allowed_schema is not None`.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/postgres/test_query_validator_corpus.py`:

1. Move `"SELECT current_setting('server_version')",` out of `MUST_ALLOW_EVERYWHERE` into a new list (and add its test) placed after `MUST_ALLOW_EVERYWHERE`:

```python
# Интроспекция и системные отношения: проходят в full, в basic блокируются (спека basic-confinement §4).
BASIC_BLOCKED_FULL_ALLOWED = [
    "SELECT * FROM pg_class",
    "SELECT * FROM pg_stats",
    "SELECT * FROM pg_catalog.pg_stats",
    "SELECT * FROM PG_ROLES",
    "SELECT * FROM public.pg_stat_statements",
    "SELECT * FROM information_schema._pg_user_mappings",
    "WITH x AS (SELECT * FROM pg_stat_activity) SELECT * FROM x",
    "SELECT current_setting('server_version')",
    "SELECT current_setting('app.jwt_secret')",
    "SELECT pg_catalog.current_setting('app.jwt_secret')",
    "SELECT pg_get_functiondef(1)",
    "SELECT pg_get_viewdef('secret.v'::text)",
    "SELECT inet_server_addr()",
    "SELECT to_regclass('secret.t')",
    "SELECT pg_input_is_valid('secret.t', 'regclass')",
    "SHOW ALL",
    "SHOW app.jwt_secret",
    "SHOW data_directory",
    "SELECT 't'::regclass",
    "SELECT CAST('t' AS pg_catalog.regclass)",
    "SELECT 'secret.f'::regproc",
    "SELECT regclass 't'",
    "SELECT 'r'::REGROLE",
]

# Разрешено в basic, несмотря на соседство с закрытыми правилами.
BASIC_ALLOWED_EXTRA = [
    "SELECT current_user, session_user, current_database(), version()",
    "SELECT pg_typeof(1), pg_size_pretty(1024::bigint)",
    "SHOW search_path",
    "SHOW TIME ZONE",
    "SHOW TRANSACTION ISOLATION LEVEL",
    "SHOW server_version",
    "SELECT '1'::int, 'a'::text",
]


@pytest.mark.parametrize("sql", BASIC_BLOCKED_FULL_ALLOWED)
def test_basic_blocks_introspection(sql: str) -> None:
    with pytest.raises(UserFacingError):
        BASIC_READ_ONLY.validate(sql)
    with pytest.raises(UserFacingError):
        BASIC_WRITE.validate(sql)


@pytest.mark.parametrize("sql", BASIC_BLOCKED_FULL_ALLOWED)
def test_full_keeps_introspection(sql: str) -> None:
    FULL_READ_ONLY.validate(sql)


@pytest.mark.parametrize("sql", BASIC_ALLOWED_EXTRA)
def test_basic_allows_value_functions_and_safe_show(sql: str) -> None:
    BASIC_READ_ONLY.validate(sql)
    BASIC_WRITE.validate(sql)
```

2. In `tests/unit/postgres/test_query_validator.py`, append a class pinning error types and messages (add imports `ShowParameterNotAllowedError`, `SystemRelationAccessError`, `TypeCastNotAllowedError`, `FunctionNotAllowedError` from `postgres_fastmcp.shared.errors` if missing):

```python
class TestBasicPolicy:
    """basic: системные отношения, интроспекция, SHOW и reg*-приведения (спека basic-confinement §4)."""

    BASIC = QueryValidator(read_only=True, allowed_schema="public")

    @pytest.mark.parametrize(
        "sql", ["SELECT * FROM pg_stats", "SELECT * FROM pg_catalog.pg_stats", "SELECT * FROM public.pg_stats"]
    )
    def test_system_relation_rejected_with_its_name(self, sql: str) -> None:
        with pytest.raises(SystemRelationAccessError, match="'pg_stats'"):
            self.BASIC.validate(sql)

    def test_system_relation_checked_before_prefix(self) -> None:
        validator = QueryValidator(read_only=True, allowed_schema="public", table_prefix="app_")
        with pytest.raises(SystemRelationAccessError):
            validator.validate("SELECT * FROM pg_indexes")

    def test_introspection_function_rejected(self) -> None:
        with pytest.raises(FunctionNotAllowedError, match="current_setting"):
            self.BASIC.validate("SELECT current_setting('app.jwt_secret')")

    def test_show_outside_the_list_names_allowed_parameters(self) -> None:
        with pytest.raises(ShowParameterNotAllowedError) as exc_info:
            self.BASIC.validate("SHOW app.jwt_secret")
        assert "SHOW app.jwt_secret is not allowed" in str(exc_info.value)
        assert "search_path" in str(exc_info.value)

    @pytest.mark.parametrize("sql", ["SELECT 't'::regclass", "SELECT 't'::pg_catalog.REGCLASS[]"])
    def test_reg_cast_rejected(self, sql: str) -> None:
        with pytest.raises(TypeCastNotAllowedError, match="regclass"):
            self.BASIC.validate(sql)

    def test_full_is_unchanged(self) -> None:
        full = QueryValidator(read_only=True)
        full.validate("SELECT * FROM pg_stats")
        full.validate("SHOW app.jwt_secret")
        full.validate("SELECT 't'::regclass, current_setting('work_mem')")
```

3. Expectations that change (pg_catalog.* now answers `SystemRelationAccessError`, raised first):
- `tests/unit/postgres/test_query_validator_corpus.py`: `"SELECT * FROM pg_catalog.pg_class"` in `TABLE_QUERIES_BASIC_BLOCKED` stays (still blocked).
- `tests/unit/domains/test_catalog_service.py`, `test_agent_sql_driver_is_still_restricted` parameters: `("SELECT * FROM pg_indexes", SystemRelationAccessError)`, `("SELECT * FROM pg_catalog.pg_indexes", SystemRelationAccessError)`, `("SELECT * FROM pg_catalog.pg_class", SystemRelationAccessError)`, `("SELECT * FROM other_users", TablePrefixAccessError)`; fix imports (`SchemaNotAllowedError` may become unused there).
- `tests/integration/test_table_prefix.py`: in `test_table_prefix_blocks_system_schemas` and `test_agent_sql_still_cannot_read_system_catalogs` expect `SystemRelationAccessError` for `pg_indexes`, `pg_catalog.pg_indexes`, `pg_catalog.pg_class`; fix imports.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/postgres/test_query_validator_corpus.py tests/unit/postgres/test_query_validator.py tests/unit/domains/test_catalog_service.py -q`
Expected: FAIL — `test_basic_blocks_introspection` (`DID NOT RAISE`) for `pg_class`, `current_setting`, `SHOW`, `::regclass`; `TestBasicPolicy` cases; changed error types in `test_agent_sql_driver_is_still_restricted`. `test_full_keeps_introspection` and `test_basic_allows_value_functions_and_safe_show` pass already.

- [ ] **Step 3: R1 in `schema_guard.py`**

```python
# Системные отношения: все имена pg_catalog начинаются с pg_ (создать там отношение нельзя без
# allow_system_table_mods), _pg_* — внутренние представления information_schema. Представления
# расширений в public (pg_stat_statements и т. п.) попадают сюда же.
_SYSTEM_RELATION_PREFIXES = ("pg_", "_pg_")
```

In `validate_schema_access`, right after `if not allowed_schema: return`:

```python
    relname = range_var.relname or ""
    if relname.lower().startswith(_SYSTEM_RELATION_PREFIXES):
        raise SystemRelationAccessError(relname)
```

Import `SystemRelationAccessError`; add `SystemRelationAccessError: Если в basic запрошено системное отношение (pg_*, _pg_*).` first in the docstring's `Raises:`. The existing `pg_catalog` branch stays (it still guards a qualified name that does not start with `pg_`, which Postgres does not have today).

- [ ] **Step 4: R2–R4 in `query_validator.py`**

1. Imports: add `TypeCast`, `VariableShowStmt` to the `pglast.ast` import; from `policies` import `BASIC_ALLOWED_FUNCTIONS`, `BASIC_SHOW_PARAMETERS`, `REG_TYPES` next to `ALLOWED_FUNCTIONS`; from `shared.errors` import `ShowParameterNotAllowedError`, `TypeCastNotAllowedError`.
2. `_NodeValidationVisitor.__init__` gets keyword `allowed_functions: frozenset[str]` (stored as `self._allowed_functions`); `self._basic = allowed_schema is not None`. Docstring Args: `allowed_functions: Разрешённые имена функций (для basic — без интроспекции).`
3. In `visit`, the FuncCall check uses `self._allowed_functions` instead of `ALLOWED_FUNCTIONS`, and after it add:

```python
        if self._basic and isinstance(node, VariableShowStmt):
            name = node.name or ""
            if name.lower() not in BASIC_SHOW_PARAMETERS:
                raise ShowParameterNotAllowedError(name, sorted(BASIC_SHOW_PARAMETERS))

        if self._basic and isinstance(node, TypeCast):
            type_name = _cast_type_name(node)
            if type_name in REG_TYPES:
                raise TypeCastNotAllowedError(type_name)
```

and a module-level helper above the class:

```python
def _cast_type_name(node: TypeCast) -> str:
    """Имя типа приведения без схемы, в нижнем регистре ('pg_catalog.regclass[]' -> 'regclass')."""
    names = node.typeName.names if node.typeName is not None and node.typeName.names else ()
    last = names[-1] if names else None
    return str(getattr(last, "sval", "") or "").lower()
```

4. `visit` docstring `Raises:` gains `SystemRelationAccessError`, `ShowParameterNotAllowedError`, `TypeCastNotAllowedError`.
5. `QueryValidator.validate` builds the visitor with `allowed_functions=BASIC_ALLOWED_FUNCTIONS if self.allowed_schema is not None else ALLOWED_FUNCTIONS`; its docstring `Raises:` gains the three new errors.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit -q`
Expected: PASS. If another existing test breaks because it relied on a now-blocked construct in basic (e.g. an explain/replacer test using `pg_stats` through a basic validator), report it in the task report with the test name and fix the expectation only if the new behavior is the one the spec requires.

- [ ] **Step 6: Lint, type-check, commit**

```bash
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
git add src/postgres_fastmcp/postgres/security tests
git commit -m "fix(security): block system relations and server introspection in basic mode"
```

---

### Task 3: R5 — цель `hypopg_create_index` в basic

**Files:**
- Modify: `src/postgres_fastmcp/postgres/security/query_validator.py`
- Modify: `src/postgres_fastmcp/postgres/models.py` (`IndexDefinition.name`)
- Test: `tests/unit/postgres/test_query_validator.py`, `tests/unit/postgres/test_query_validator_corpus.py`, `tests/unit/domains/test_explain_service.py`, `tests/unit/postgres/test_models.py` (create if absent; otherwise append)

**Interfaces:**
- Consumes: `validate_schema_access(range_var, *, allowed_schema, table_prefix)` from `schema_guard.py` (with R1 from Task 2); visitor fields `_basic`, `_allowed_schema`, `_table_prefix`.
- Produces: in basic, `hypopg_create_index` accepts only one string constant holding exactly one `CREATE INDEX` on an allowed table. `explain_query` needs no own check: `ExplainPlanBuilder.generate_explain_plan_with_hypothetical_indexes` sends `SELECT hypopg_create_index('<IndexDefinition.definition>')` through `sql_driver`, so the validator covers `hypothetical_indexes` too.

- [ ] **Step 1: Write the failing tests**

1. `tests/unit/postgres/test_query_validator.py`, add to `TestBasicPolicy`:

```python
    PREFIXED = QueryValidator(read_only=True, allowed_schema="public", table_prefix="app_")

    @pytest.mark.parametrize(
        ("sql", "error"),
        [
            ("SELECT hypopg_create_index('CREATE INDEX ON secret.t (c)')", SchemaNotAllowedError),
            ("SELECT hypopg_create_index('CREATE INDEX ON users (c)')", TablePrefixAccessError),
            ("SELECT hypopg_create_index('CREATE INDEX ON pg_class (relname)')", SystemRelationAccessError),
            ("SELECT hypopg_create_index('SELECT 1')", FunctionNotAllowedError),
            ("SELECT hypopg_create_index('CREATE INDEX ON app_t (c); CREATE INDEX ON secret.t (c)')", FunctionNotAllowedError),
            ("SELECT hypopg_create_index('not sql')", FunctionNotAllowedError),
            ("SELECT hypopg_create_index(concat('CREATE INDEX ON ', 'secret.t (c)'))", FunctionNotAllowedError),
            ("SELECT hypopg_create_index(stmt) FROM app_defs", FunctionNotAllowedError),
        ],
    )
    def test_hypopg_create_index_target_is_checked(self, sql: str, error: type[Exception]) -> None:
        with pytest.raises(error):
            self.PREFIXED.validate(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT hypopg_create_index('CREATE INDEX ON app_t (c)')",
            "SELECT hypopg_create_index('CREATE INDEX idx ON public.app_t USING btree (a, b)')",
        ],
    )
    def test_hypopg_create_index_on_allowed_table_passes(self, sql: str) -> None:
        self.PREFIXED.validate(sql)

    def test_full_does_not_parse_hypopg_argument(self) -> None:
        QueryValidator(read_only=True).validate("SELECT hypopg_create_index('CREATE INDEX ON secret.t (c)')")
```

(import `SchemaNotAllowedError`, `TablePrefixAccessError` if missing.)

2. `tests/unit/postgres/test_query_validator_corpus.py`: add to `BASIC_BLOCKED_FULL_ALLOWED`: `"SELECT hypopg_create_index('CREATE INDEX ON secret.t (c)')"`.

3. `tests/unit/domains/test_explain_service.py`: next to `test_hypothetical_explain_works_in_basic_with_table_prefix`, add (same imports and delegate pattern):

```python
@pytest.mark.parametrize(
    ("table", "error"),
    [("secret.accounts", SchemaNotAllowedError), ("users", TablePrefixAccessError), ("pg_stats", SystemRelationAccessError)],
)
async def test_hypothetical_index_on_a_forbidden_table_is_rejected_before_explain(
    monkeypatch: pytest.MonkeyPatch, table: str, error: type[Exception]
) -> None:
    """Определение гипотетического индекса проходит валидатор агента: чужая таблица не доходит до hypopg."""

    async def execute(query, params=None, *, readonly=True):
        if "pg_catalog.pg_extension" in query:
            return [RowResult(cells={"extversion": "1.4.1"})]
        return []

    delegate = MagicMock()
    delegate.execute = AsyncMock(side_effect=execute)
    monkeypatch.setattr(db_access_module, "SqlExecutor", lambda conn: delegate)
    config = DatabaseConfig(
        host="h", user="u", password="p", name="d", access_mode=AccessMode.BASIC, write_mode=False, table_prefix="app_"
    )
    db = DbAccessService(config).view(EffectiveAccess(AccessMode.BASIC, write_mode=False))

    with pytest.raises(error):
        await ExplainService(db=db).explain(
            "SELECT * FROM app_users", hypothetical_indexes=[{"table": table, "columns": ["id"]}]
        )

    sent = [c.args[0] for c in delegate.execute.await_args_list]
    assert not any("hypopg_create_index" in q for q in sent)
```

(import `SchemaNotAllowedError`, `SystemRelationAccessError`, `TablePrefixAccessError` from `postgres_fastmcp.shared.errors`.)

4. `IndexDefinition.name` embeds the table name as-is, so for `secret.accounts` the definition is `CREATE INDEX dba_idx_secret.accounts_id_1 ON …` — a syntax error (an index name cannot be schema-qualified). Hypothetical indexes on schema-qualified tables therefore never worked, in full mode too, and in basic R5 would answer with a parse error instead of the schema error. Test (in `tests/unit/postgres/test_models.py`; check `grep -rn "IndexDefinition" tests/unit` for an existing test module of `postgres/models.py` and append there instead if one exists):

```python
import pglast
from pglast.ast import IndexStmt

from postgres_fastmcp.postgres.models import IndexDefinition


def test_definition_on_a_schema_qualified_table_parses() -> None:
    definition = IndexDefinition(table="secret.accounts", columns=("id",)).definition
    statements = pglast.parse_sql(definition)
    assert len(statements) == 1
    assert isinstance(statements[0].stmt, IndexStmt)
    assert (statements[0].stmt.relation.schemaname, statements[0].stmt.relation.relname) == ("secret", "accounts")
    assert statements[0].stmt.idxname == "dba_idx_secret_accounts_id_1"


def test_name_of_an_unqualified_table_is_unchanged() -> None:
    assert IndexDefinition(table="app_users", columns=("name",)).name == "dba_idx_app_users_name_1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/postgres/test_query_validator.py tests/unit/postgres/test_query_validator_corpus.py tests/unit/domains/test_explain_service.py -q`
Run also: `uv run pytest tests/unit/postgres/test_models.py -q`
Expected: FAIL — every forbidden-target case in `test_hypopg_create_index_target_is_checked` passes validation (`DID NOT RAISE`); the explain test sends `hypopg_create_index` to the delegate; `test_definition_on_a_schema_qualified_table_parses` fails with a pglast `ParseError`. The two "passes" tests, `test_full_does_not_parse_hypopg_argument` and `test_name_of_an_unqualified_table_is_unchanged` pass already.

- [ ] **Step 3: Implement**

In `query_validator.py`: add `IndexStmt`, `String` to the `pglast.ast` import. In `visit`, inside the `FuncCall` branch after the allowlist check:

```python
            if self._basic and unqualified == "hypopg_create_index":
                self._validate_hypopg_create_index(node)
```

and the method on `_NodeValidationVisitor`:

```python
    def _validate_hypopg_create_index(self, node: FuncCall) -> None:
        """В basic аргумент hypopg_create_index — одна строковая константа с одним CREATE INDEX по разрешённой таблице.

        Строку hypopg разбирает сам, валидатор её иначе не видит: без этой проверки агент узнавал бы,
        существуют ли таблицы и колонки чужих схем, и получал бы оценку их размера.

        Raises:
            FunctionNotAllowedError: Аргумент не строковая константа или не ровно один CREATE INDEX.
            SystemRelationAccessError: Индекс на системном отношении.
            SchemaNotAllowedError: Индекс на таблице другой схемы.
            TablePrefixAccessError: Имя таблицы не соответствует префиксу.
        """
        args = node.args or ()
        value = args[0].val if len(args) == 1 and isinstance(args[0], A_Const) else None
        if not isinstance(value, String) or value.sval is None:
            raise FunctionNotAllowedError("hypopg_create_index")
        try:
            statements = pglast.parse_sql(value.sval)
        except pglast.parser.ParseError as e:
            raise FunctionNotAllowedError("hypopg_create_index") from e
        index = statements[0].stmt if len(statements) == 1 else None
        if not isinstance(index, IndexStmt) or index.relation is None:
            raise FunctionNotAllowedError("hypopg_create_index")
        validate_schema_access(index.relation, allowed_schema=self._allowed_schema, table_prefix=self._table_prefix)
```

In `src/postgres_fastmcp/postgres/models.py`, `IndexDefinition.name`: build `base` from `self.table.replace(".", "_")` instead of `self.table` (comment: `# Имя индекса не может быть квалифицировано схемой: точку из "schema.table" заменяем.`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit -q`
Expected: PASS. If an existing test pins an index name for a schema-qualified table, update it and mention it in the report.

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
git add src/postgres_fastmcp/postgres/security/query_validator.py src/postgres_fastmcp/postgres/models.py tests/unit
git commit -m "fix(security): check the target table of hypothetical indexes in basic mode"
```

---

### Task 4: инвариант на живом Postgres, описание тула, README, спека

**Files:**
- Test: `tests/integration/test_table_prefix.py`
- Modify: `src/postgres_fastmcp/tools/registry.py` (`_execute_sql_desc`), tests pinning it (find with `grep -rn "only SELECT, EXPLAIN and SHOW" tests`)
- Modify: `README.md`, `docs/superpowers/specs/2026-09-28-basic-confinement-design.md`

**Interfaces:**
- Consumes: fixtures `db_full` (FULL+write, unrestricted `SqlExecutor`) and `db_user_prefix` (BASIC read-only, `table_prefix="app_"`); `QueryValidator`; `SystemRelationAccessError`, `FunctionNotAllowedError`, `ShowParameterNotAllowedError`, `SchemaNotAllowedError` from `shared.errors`.

- [ ] **Step 1: Integration tests**

Append to `tests/integration/test_table_prefix.py` (extend imports: `from postgres_fastmcp.postgres.security.query_validator import QueryValidator` and the errors above):

```python
@pytest.mark.asyncio
async def test_basic_rejects_every_system_relation_of_the_server(db_full: DbAccess) -> None:
    """Инвариант R1 на живом сервере: каждое отношение pg_catalog и каждое information_schema._pg_* закрыто в basic.

    Новые системные представления следующих версий Postgres не откроют дыру молча: тест перечисляет их сам.
    """
    rows = await db_full.sql_driver.execute(
        """
        SELECT n.nspname AS schema_name, c.relname AS relation
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r', 'v', 'm', 'p', 'f')
          AND (n.nspname = 'pg_catalog' OR (n.nspname = 'information_schema' AND c.relname LIKE '\\_pg\\_%'))
        """,
        readonly=True,
    )
    relations = [(row.cells["schema_name"], row.cells["relation"]) for row in rows or []]
    assert ("pg_catalog", "pg_stats") in relations
    validator = QueryValidator(read_only=True, allowed_schema="public")

    for schema, relation in relations:
        for sql in (f'SELECT * FROM "{relation}"', f'SELECT * FROM {schema}."{relation}"'):
            with pytest.raises(SystemRelationAccessError):
                validator.validate(sql)


@pytest.mark.asyncio
async def test_basic_agent_sql_introspection_is_closed(db_full: DbAccess, db_user_prefix: DbAccess) -> None:
    await setup_test_tables(db_full)
    sql = db_user_prefix.sql_driver

    with pytest.raises(FunctionNotAllowedError):
        await sql.execute("SELECT current_setting('server_version')", readonly=True)
    with pytest.raises(ShowParameterNotAllowedError):
        await sql.execute("SHOW data_directory", readonly=True)
    with pytest.raises(SchemaNotAllowedError):
        await sql.execute("SELECT hypopg_create_index('CREATE INDEX ON secret.t (c)')", readonly=True)

    rows = await sql.execute("SELECT current_user AS who, version() AS v", readonly=True)
    assert rows and rows[0].cells["who"]
    shown = await sql.execute("SHOW search_path", readonly=True)
    assert shown and "public" in str(shown[0].cells["search_path"])
```

Check statically and record in the report: the LIKE pattern escaping reaches Postgres as `'\_pg\_%'` (the SQL is a plain Python string sent by `SqlExecutor`; `standard_conforming_strings=on`, so `\_` is a literal underscore escape for LIKE); `SHOW search_path` returns column `search_path`; in basic `SafeSqlExecutor` prefixes `SET LOCAL search_path = public;`, so the value contains `public`.

- [ ] **Step 2: Tool description**

In `src/postgres_fastmcp/tools/registry.py` `_execute_sql_desc`, after `"in read-only mode only SELECT, EXPLAIN and SHOW are accepted; "` keep the rest and append before `"A rejected statement returns an explicit error. "`:

```python
        "In basic access, system catalogs (pg_*), server-introspection functions, SHOW of arbitrary "
        "settings and casts to reg* types are rejected. "
```

Update any test that pins the full description text (`grep -rn "A rejected statement returns an explicit error" tests`).

- [ ] **Step 3: README**

In `README.md`, right after the line starting `**Для access_mode=basic опционально:**`, add a paragraph:

```markdown
**Что закрыто в access_mode=basic для SQL агента:** системные отношения `pg_*` (в том числе `pg_catalog.*`, `pg_stats`, `pg_stat_activity` и представления расширений в `public`, например `pg_stat_statements`), функции интроспекции сервера и объектов (`current_setting`, `pg_get_functiondef`, `pg_relation_size`, `has_*_privilege`, `to_regclass` и др.), `SHOW` параметров вне короткого списка (`search_path`, `TimeZone`, `server_version` и т. п.), приведения к `reg*`-типам, `hypopg_create_index` по таблицам вне `public` или без префикса. Функции `ts_stat`/`ts_rewrite` закрыты во всех режимах. Основная граница доступа — права роли в БД; basic — защита в глубину поверх них. Расширения лучше устанавливать в отдельную схему, а не в `public`.
```

- [ ] **Step 4: Spec**

In `docs/superpowers/specs/2026-09-28-basic-confinement-design.md`:
1. Status line → `Статус: реализовано (ветки 1 и 2).`
2. §4.1: error text → `Access to system relation '{relation}' is not allowed in basic mode. Use list_objects and get_object_details to inspect tables in 'public'.`
3. §4.4: error text → `Casts to {type_name} are not allowed in basic mode. Rewrite the query without object identifier types.`
4. §4.5, first bullet (`explain_query в basic: ExplainService до построения запроса проверяет …`) → replace with: `- \`explain_query\` в basic: определение гипотетического индекса собирает сервер (\`IndexDefinition.definition\`) и отправляет в \`hypopg_create_index\` через \`sql_driver\`, поэтому проверка валидатора ниже покрывает и \`hypothetical_indexes\`; ошибки — те же, что у \`execute_sql\` (\`SchemaNotAllowedError\`, \`TablePrefixAccessError\`, \`SystemRelationAccessError\`), до обращения к hypopg.`
5. §5, «Ветка 2», add a bullet: `- Все режимы: гипотетические индексы на таблицах со схемой (\`schema.table\`) работают — имя индекса раньше включало точку и не разбиралось.`
6. §4.6 item 6 → `6. Юнит: \`explain_query\` в basic + \`app_\` с \`hypothetical_indexes\` на \`secret.accounts\`, \`users\`, \`pg_stats\` отклоняется, \`hypopg_create_index\` до делегата не доходит.`

- [ ] **Step 5: Full verification, lint, commit**

```bash
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff check .
git add tests src/postgres_fastmcp/tools/registry.py README.md docs/superpowers/specs/2026-09-28-basic-confinement-design.md
git commit -m "docs(security): describe the basic-mode SQL policy and pin it against Postgres"
```
