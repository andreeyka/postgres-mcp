# Agent Output Budget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Удержать ответ тулов в контексте агента (бюджет токенов, Markdown-таблица по умолчанию, `output="json"`) и сократить неудачные вызовы из-за формы ввода (нормализация параметров, границы в JSON-схеме, английские ошибки с подсказкой).

**Architecture:** План по спеке `docs/superpowers/specs/2026-09-26-agent-output-budget-design.md`. Новые модули: `tools/rendering.py` (Markdown/JSON в `ToolResult`, не зависит от регистрации тулов), `tools/params.py` (общие `Annotated`-типы параметров с нормализацией), `app/middleware/response_budget.py` (внешний middleware, который заменяет слишком большой ответ ошибкой `ResponseTooLargeError`). Пять тулов со строками (`execute_sql`, `list_schemas`, `list_objects`, `get_top_queries`, `get_object_details`) получают `output` и регистрируются с `output_schema=None`; `explain_query` и `analyze_*` по формату не меняются.

**Tech Stack:** Python 3.12+, uv, FastMCP 4.0.10, pydantic 2.13, pytest + pytest-asyncio (asyncio_mode=auto), ruff (select=ALL), mypy strict.

## Global Constraints

- Язык: всё, что видит агент или внешняя система, пишется на английском: описания тулов, `Field(description=...)`, docstring функций тулов, сообщения ошибок, логи, коммиты. По-русски только комментарии и docstring, которые видит лишь разработчик.
- Нигде нет `from __future__ import annotations`.
- Перед каждым коммитом: `uv run ruff check --fix --show-fixes`, `uv run mypy src/`, `uv run ruff format` — все три с нулём ошибок.
- `<env>` ниже означает `MCP_DATABASE_HOST=localhost MCP_DATABASE_PORT=5432 MCP_DATABASE_USER=u MCP_DATABASE_PASSWORD=p MCP_DATABASE_NAME=d FASTMCP_MCP_CAMELCASE_COMPAT=false`. Юнит-тесты: `<env> uv run pytest tests/unit -q`. Базовая линия до плана: `542 passed`.
- Docker локально недоступен: `<env> uv run pytest tests/integration -q` только собирает и пропускает тесты (`skipped`). Интеграционные тесты, чьи проверки зависят от формы ответа, всё равно правятся в этом плане (переход на `output="json"` и `structured_content`); их прогонит CI.
- Ломающие изменения разрешены, легаси-формы ответа не сохраняются (спека, раздел 9).
- Работа идёт в текущей ветке `claude/spec-auth-fastmcp4-hardening`. Отдельных веток, push и PR в плане нет; последняя задача — полная локальная проверка CI.
- Формат коммитов: `type(scope): message`, повелительное наклонение, английский.
- Ошибки для агента наследуют `UserFacingError`, иначе `mask_error_details=True` скроет текст.
- Бюджет ответа: `BYTES_PER_TOKEN = 3`, `ServerSettings.response_max_tokens` по умолчанию `20000`, `ge=1000`, переменная `MCP_RESPONSE_MAX_TOKENS`; `TOP_QUERIES_MAX_LIMIT = 100` (спека, разделы 3 и 5).

## Проверено на установленных версиях (FastMCP 4.0.10, pydantic 2.13)

Справка для исполнителя; каждое утверждение проверено экспериментом до написания плана.

- `WithJsonSchema(...)` заменяет схему параметра целиком и **теряет** `Field(description=...)`; без описания FastMCP подставляет строку из `Args:` docstring. Поэтому схема расширяется через `Field(json_schema_extra=callable)`: callable правит сгенерированную схему, описание остаётся. Это отступление от буквы спеки (раздел 5 называет `WithJsonSchema`), цель та же: клиент не отклоняет ввод раньше сервера.
- Исключение-наследник `ToolError`, брошенное из `BeforeValidator`, pydantic не заворачивает: клиент получает его текст как есть, даже при `mask_error_details=True`. `ValueError` из валидатора превратился бы в многострочный дамп pydantic.
- `ToolError` из `Middleware.on_call_tool` доходит до клиента своим текстом. Middleware, добавленный первым через `add_middleware`, — внешний.
- Функция с аннотацией возврата `ToolResult` и `output_schema=None` отдаёт клиенту ровно `content` и `structured_content` из `ToolResult`; `Client.call_tool(...).data` при этом равен `structured_content` (или `None` для `table`).
- Размер `tools/list` в режиме FULL до плана: 8360 символов.

## Файлы

| Файл | Ответственность |
| --- | --- |
| `src/postgres_fastmcp/shared/errors.py` | Английские тексты ошибок с подсказкой; новые `InvalidHealthTypeError`, `InvalidOutputFormatError`, `PgStatStatementsNotInstalledError`, `ResponseTooLargeError` |
| `src/postgres_fastmcp/tools/rendering.py` (новый) | `OutputFormat`, `rows_result`, `sections_result` |
| `src/postgres_fastmcp/tools/params.py` (новый) | Общие типы параметров, нормализация, `TOP_QUERIES_MAX_LIMIT`, `HEALTH_TYPE_VALUES` |
| `src/postgres_fastmcp/tools/definitions.py` | Тулы на общих типах, `output`, `ToolResult`, английские docstring |
| `src/postgres_fastmcp/tools/registry.py` | `output_schema=None` для пяти тулов, импорт `HEALTH_TYPE_VALUES` из `params` |
| `src/postgres_fastmcp/domains/querying.py` | `None` для оператора без результата |
| `src/postgres_fastmcp/domains/top_queries.py` | Строки вместо `str(list)`, `LIMIT` для `resources`, ошибка вместо текста про установку |
| `src/postgres_fastmcp/app/middleware/response_budget.py` (новый) | `BYTES_PER_TOKEN`, `estimate_tokens`, `ResponseBudgetMiddleware` |
| `src/postgres_fastmcp/app/config/server.py`, `app/config/__init__.py`, `app/server.py` | Настройка `response_max_tokens` и подключение middleware первым |
| `src/postgres_fastmcp/tools/AGENTS.md`, `tools/CLAUDE.md` (новые), `AGENTS.md` | Правила для авторов тулов и ссылка на них |
| `README.md`, `env.example`, `tests/README.md` | Документация `output`, бюджета и переменной окружения |

Порядок задач отличается от предложенного разбиения в одном месте: `rendering.py` (Task 2) идёт раньше `params.py` (Task 3), потому что `OutputParam` в `params.py` импортирует `OutputFormat` из `rendering.py`.

---

### Task 1: Английские ошибки с подсказкой и проверка на кириллицу

**Files:**
- Modify: `src/postgres_fastmcp/shared/errors.py:7` (импорты и два хелпера), `:112-117`, `:147-158`, `:161-172`, `:175-180`, `:183-194`, `:197-202`, `:205-210`, `:213-229`, `:232-243`, `:272-309`
- Modify: `src/postgres_fastmcp/domains/top_queries.py:288`
- Test: `tests/unit/shared/test_errors.py` (переписать целиком)

**Interfaces:**
- Consumes: `ObjectType`, `TopQueriesSortBy` из `postgres_fastmcp.shared.enums`.
- Produces:
  - `UnsupportedObjectTypeError(object_type: str)` — сигнатура прежняя, текст `Unsupported object type: '<v>'. Did you mean '<x>'? Use one of: 'table', 'view', 'sequence', 'extension'.`
  - `InvalidSortCriteriaError(sort_by: str)` — **новый обязательный аргумент**, атрибут `.sort_by`.
  - `InvalidHealthTypeError(health_type: str, allowed: Sequence[str])`, атрибут `.health_type`.
  - `InvalidOutputFormatError(output: str)`, атрибут `.output`.
  - Все подсказки «Did you mean» строит `difflib.get_close_matches(value.strip().lower(), allowed, n=1)`.
  - `tests/unit/shared/test_errors.py::_SAMPLES: dict[str, Callable[[], UserFacingError]]` — Task 4 и Task 5 добавляют туда свои классы.

- [ ] **Step 1: Переписать тест ошибок**

Заменить `tests/unit/shared/test_errors.py` целиком:

```python
"""Тесты: user-facing исключения доходят до клиента и написаны по-английски с подсказкой."""

import re
from collections.abc import Callable

import pytest
from fastmcp.exceptions import ToolError

from postgres_fastmcp.shared import errors


# Пример каждого подкласса UserFacingError с тестовыми аргументами.
# Новый подкласс без записи здесь роняет test_every_user_facing_error_has_a_sample.
_SAMPLES: dict[str, Callable[[], errors.UserFacingError]] = {
    "SchemaAccessError": lambda: errors.SchemaAccessError("private"),
    "SchemaNotAllowedError": lambda: errors.SchemaNotAllowedError("private", "public"),
    "TablePrefixAccessError": lambda: errors.TablePrefixAccessError("foo", "bar_"),
    "SchemataTableAccessError": lambda: errors.SchemataTableAccessError("information_schema", "schemata"),
    "SqlParseError": errors.SqlParseError,
    "StatementTypeNotAllowedError": lambda: errors.StatementTypeNotAllowedError(
        read_only=True, stmt_type_name="CreateStmt"
    ),
    "DdlNotAllowedError": lambda: errors.DdlNotAllowedError("CreateStmt"),
    "DisallowedNodeTypeError": lambda: errors.DisallowedNodeTypeError(int),
    "LikePatternNotConstantError": errors.LikePatternNotConstantError,
    "FunctionNotAllowedError": lambda: errors.FunctionNotAllowedError("pg_sleep"),
    "LockingClauseProhibitedError": errors.LockingClauseProhibitedError,
    "ExplainAnalyzeNotSupportedError": errors.ExplainAnalyzeNotSupportedError,
    "CreateExtensionNotSupportedError": lambda: errors.CreateExtensionNotSupportedError("dblink"),
    "UnsupportedObjectTypeError": lambda: errors.UnsupportedObjectTypeError("tabel"),
    "ExplainAnalyzeWithHypotheticalError": errors.ExplainAnalyzeWithHypotheticalError,
    "EmptyQueriesError": errors.EmptyQueriesError,
    "QueriesLimitError": lambda: errors.QueriesLimitError(10),
    "InvalidSortCriteriaError": lambda: errors.InvalidSortCriteriaError("mean"),
    "InvalidHealthTypeError": lambda: errors.InvalidHealthTypeError("indx", ["all", "index", "vacuum"]),
    "InvalidOutputFormatError": lambda: errors.InvalidOutputFormatError("jsn"),
    "HypopgNotInstalledError": lambda: errors.HypopgNotInstalledError("The hypopg extension is not installed."),
    "QueryTimeoutError": lambda: errors.QueryTimeoutError(5.0),
    "QueryCancelledError": errors.QueryCancelledError,
}

_CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def _all_subclasses(cls: type) -> set[type]:
    direct = set(cls.__subclasses__())
    return direct.union(*(_all_subclasses(sub) for sub in direct))


def test_every_user_facing_error_has_a_sample() -> None:
    """Каждый подкласс UserFacingError попадает в проверку языка."""
    assert {cls.__name__ for cls in _all_subclasses(errors.UserFacingError)} == set(_SAMPLES)


@pytest.mark.parametrize("name", sorted(_SAMPLES))
def test_user_facing_error_is_tool_error_in_english(name: str) -> None:
    """Сообщение доходит до клиента (ToolError) и не содержит кириллицы."""
    error = _SAMPLES[name]()
    assert isinstance(error, ToolError)
    assert str(error)
    assert not _CYRILLIC.search(str(error)), str(error)


@pytest.mark.parametrize(
    ("name", "hint"),
    [
        ("SqlParseError", "Check the SQL syntax"),
        ("DdlNotAllowedError", "Use SELECT"),
        ("DisallowedNodeTypeError", "Rewrite the query"),
        ("LikePatternNotConstantError", "string literal"),
        ("FunctionNotAllowedError", "Rewrite the query"),
        ("LockingClauseProhibitedError", "Remove FOR UPDATE"),
        ("ExplainAnalyzeNotSupportedError", "explain_query"),
        ("CreateExtensionNotSupportedError", "hypopg and pg_stat_statements"),
        ("UnsupportedObjectTypeError", "Did you mean 'table'?"),
        ("ExplainAnalyzeWithHypotheticalError", "Call explain_query twice"),
        ("EmptyQueriesError", "at least one SQL query"),
        ("QueriesLimitError", "Split the list"),
        ("InvalidSortCriteriaError", "Did you mean 'mean_time'?"),
        ("InvalidHealthTypeError", "Did you mean 'index'?"),
        ("InvalidOutputFormatError", "Did you mean 'json'?"),
    ],
)
def test_correctable_error_ends_with_hint(name: str, hint: str) -> None:
    """Ошибка, которую агент может исправить сам, подсказывает, что сделать вместо этого."""
    assert hint in str(_SAMPLES[name]())


@pytest.mark.parametrize(
    "cls_name",
    [
        "ConnectionNotEstablishedError",
        "ExplainPlanError",
        "ExplainPlanExecutionError",
        "ConnectionFailedError",
    ],
)
def test_internal_errors_do_not_inherit_tool_error(cls_name: str) -> None:
    """Internal классы не должны наследовать ToolError (будут маскированы)."""
    cls = getattr(errors, cls_name)
    assert not issubclass(cls, ToolError)
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `<env> uv run pytest tests/unit/shared/test_errors.py -q`
Expected: FAIL. `test_every_user_facing_error_has_a_sample` падает (в `_SAMPLES` есть `InvalidHealthTypeError` и `InvalidOutputFormatError`, которых ещё нет); `[InvalidHealthTypeError]`, `[InvalidOutputFormatError]` — `AttributeError`; `[InvalidSortCriteriaError]` — `TypeError` (лишний аргумент); `[ExplainAnalyzeWithHypotheticalError]`, `[EmptyQueriesError]`, `[QueriesLimitError]` — найдена кириллица; все строки `test_correctable_error_ends_with_hint` — подсказки нет. `test_internal_errors_do_not_inherit_tool_error` проходит.

- [ ] **Step 3: Импорты и хелперы подсказки**

В `src/postgres_fastmcp/shared/errors.py` строку `from fastmcp.exceptions import ToolError` заменить на:

```python
import difflib
from collections.abc import Sequence
from typing import get_args

from fastmcp.exceptions import ToolError

from postgres_fastmcp.shared.enums import ObjectType, TopQueriesSortBy


def _did_you_mean(value: str, allowed: Sequence[str]) -> str:
    """Подсказка ближайшего допустимого значения (пустая строка, если похожих нет)."""
    matches = difflib.get_close_matches(value.strip().lower(), allowed, n=1)
    return f" Did you mean '{matches[0]}'?" if matches else ""


def _one_of(allowed: Sequence[str]) -> str:
    """Список допустимых значений для текста ошибки: 'a', 'b', 'c'."""
    return ", ".join(f"'{item}'" for item in allowed)
```

`shared/enums.py` ничего не импортирует, цикла нет.

- [ ] **Step 4: Подсказки в ошибках валидатора SQL**

Там же заменить тексты сообщений (класс, строка до → после). Существующие тесты валидатора проверяют подстроки `not allowed`, `parse`, `not supported`, имя расширения и опцию; они остаются в новых текстах.

`SqlParseError.__init__`:

```python
        super().__init__("Failed to parse SQL statement. Check the SQL syntax and send a single valid statement.")
```

`DdlNotAllowedError.__init__`:

```python
        message = (
            f"DDL operations are not allowed. Received: {stmt_type_name}. "
            "Use SELECT to read data; schema changes must be made outside this server."
        )
```

`DisallowedNodeTypeError.__init__` (имя класса узла вместо `<class '...'>`):

```python
        message = f"Node type {node_type.__name__} is not allowed. Rewrite the query without this SQL construct."
```

`LikePatternNotConstantError.__init__`:

```python
        super().__init__(
            "LIKE pattern must be a constant string. Put the pattern into a string literal, e.g. LIKE 'abc%'."
        )
```

`FunctionNotAllowedError.__init__`:

```python
        message = f"Function {func_name} is not allowed. Rewrite the query without this function."
```

`LockingClauseProhibitedError.__init__`:

```python
        super().__init__("Locking clause on select is prohibited. Remove FOR UPDATE / FOR SHARE from the query.")
```

`ExplainAnalyzeNotSupportedError.__init__`:

```python
        super().__init__("EXPLAIN ANALYZE is not supported. Use the explain_query tool with analyze=true instead.")
```

`CreateExtensionNotSupportedError.__init__`, обе ветки:

```python
        if option is None:
            message = (
                f"CREATE EXTENSION {extname} is not supported. "
                "Only hypopg and pg_stat_statements can be created, and only with write_mode enabled."
            )
        else:
            message = (
                f"CREATE EXTENSION {extname} with the {option} option is not supported. "
                f"Run CREATE EXTENSION {extname} without {option}."
            )
```

- [ ] **Step 5: Ошибки тулов: перевод, подсказка, новые классы**

`UnsupportedObjectTypeError.__init__` — заменить строку `message = f"Unsupported object type: {object_type}"` на:

```python
        allowed = get_args(ObjectType)
        message = (
            f"Unsupported object type: '{object_type}'.{_did_you_mean(object_type, allowed)} "
            f"Use one of: {_one_of(allowed)}."
        )
```

`ExplainAnalyzeWithHypotheticalError.__init__`:

```python
        super().__init__(
            "analyze=true cannot be combined with hypothetical_indexes. "
            "Call explain_query twice: once with analyze=true, once with hypothetical_indexes."
        )
```

`EmptyQueriesError.__init__`:

```python
        super().__init__("The queries list is empty. Pass at least one SQL query to analyze.")
```

`QueriesLimitError.__init__` — строку `message = ...`:

```python
        message = (
            f"Too many queries: at most {limit} can be analyzed in one call. "
            "Split the list into several calls, or use analyze_workload_indexes for the whole workload."
        )
```

Класс `InvalidSortCriteriaError` заменить целиком и сразу после него добавить два новых класса:

```python
class InvalidSortCriteriaError(UserFacingError):
    """Неверный критерий сортировки для топ-запросов."""

    def __init__(self, sort_by: str) -> None:
        """Инициализация с отклонённым значением.

        Args:
            sort_by: Значение sort_by, которое не удалось распознать.
        """
        allowed = get_args(TopQueriesSortBy)
        message = f"Invalid sort_by: '{sort_by}'.{_did_you_mean(sort_by, allowed)} Use one of: {_one_of(allowed)}."
        super().__init__(message)
        self.sort_by = sort_by


class InvalidHealthTypeError(UserFacingError):
    """Неизвестный тип health-проверки в health_type."""

    def __init__(self, health_type: str, allowed: Sequence[str]) -> None:
        """Инициализация с отклонённым значением и допустимыми типами.

        Args:
            health_type: Значение, которое не удалось распознать.
            allowed: Допустимые типы проверок (HealthType живёт в домене, shared его не импортирует).
        """
        message = (
            f"Invalid health_type: '{health_type}'.{_did_you_mean(health_type, allowed)} "
            f"Use one or more of: {_one_of(allowed)}, e.g. 'index,vacuum'."
        )
        super().__init__(message)
        self.health_type = health_type


class InvalidOutputFormatError(UserFacingError):
    """Неизвестный формат вывода в параметре output."""

    def __init__(self, output: str) -> None:
        """Инициализация с отклонённым значением.

        Args:
            output: Значение output, которое не удалось распознать.
        """
        allowed = ("table", "json")
        message = f"Invalid output: '{output}'.{_did_you_mean(output, allowed)} Use one of: {_one_of(allowed)}."
        super().__init__(message)
        self.output = output
```

- [ ] **Step 6: Передать значение в InvalidSortCriteriaError**

В `src/postgres_fastmcp/domains/top_queries.py` последнюю строку `get_top_queries` заменить на:

```python
    raise InvalidSortCriteriaError(sort_by)
```

- [ ] **Step 7: Тесты зелёные, кириллицы в ошибках нет**

Run: `<env> uv run pytest tests/unit -q`
Expected: `561 passed`.

Run: `grep -nP '^\s*(super\(\)\.__init__\("(?!"")|message = |f?"(?!""))[^#]*[\x{0400}-\x{04FF}]' src/postgres_fastmcp/shared/errors.py`
Expected: пусто (до задачи команда находила 4 строки 277, 285, 297, 308; кириллица осталась только в docstring).

- [ ] **Step 8: Линтеры и коммит**

Run: `uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format`
Expected: ноль ошибок.

```bash
git add src/postgres_fastmcp/shared/errors.py src/postgres_fastmcp/domains/top_queries.py tests/unit/shared/test_errors.py
git commit -m "fix(errors): write user-facing errors in English with a hint on what to do instead"
```

---

### Task 2: Рендер результата: Markdown-таблица и JSON

**Files:**
- Create: `src/postgres_fastmcp/tools/rendering.py`
- Test: `tests/unit/tools/test_rendering.py`

**Interfaces:**
- Consumes: `fastmcp.tools.ToolResult`, `mcp.types.TextContent`.
- Produces (Task 3, 4 полагаются на эти имена):
  - `OutputFormat = Literal["table", "json"]`
  - `rows_result(rows: list[dict[str, Any]], output: OutputFormat, *, title: str | None = None) -> ToolResult`
  - `sections_result(sections: Mapping[str, list[dict[str, Any]]], output: OutputFormat, *, header: Mapping[str, Any] | None = None) -> ToolResult`
  - Формы: `table` — один `TextContent`, `structured_content is None`; строки — GFM-таблица, пустая строка, `{N} rows.`; пустой результат — `0 rows.`; `title` идёт абзацем над таблицей. `json` — один `TextContent` с `json.dumps(data, ensure_ascii=False, default=str)` и `structured_content == json.loads(того же текста)`; `data = {"rows": rows, "row_count": N}` для строк, `{**header, **sections}` для разделов.

- [ ] **Step 1: Написать тесты рендера**

Создать `tests/unit/tools/test_rendering.py`:

```python
"""Тесты рендера результата тулов: Markdown-таблица и JSON."""

import datetime as dt
import json
from decimal import Decimal

from mcp.types import TextContent

from postgres_fastmcp.tools.rendering import rows_result, sections_result


def _text(result: object) -> str:
    content = result.content  # type: ignore[attr-defined]
    assert len(content) == 1
    assert isinstance(content[0], TextContent)
    return content[0].text


def test_table_lists_columns_once_and_counts_rows() -> None:
    result = rows_result([{"id": 1, "name": "a"}, {"id": 2, "name": "b"}], "table")
    assert _text(result) == "| id | name |\n| --- | --- |\n| 1 | a |\n| 2 | b |\n\n2 rows."
    assert result.structured_content is None


def test_table_escapes_pipe_and_flattens_newlines() -> None:
    text = _text(rows_result([{"v": "a|b\nc\r\nd"}], "table"))
    assert "| a\\|b c d |" in text


def test_table_renders_none_as_empty_cell() -> None:
    text = _text(rows_result([{"a": None, "b": 1}], "table"))
    assert "|  | 1 |" in text


def test_table_uses_union_of_keys_in_first_seen_order() -> None:
    text = _text(rows_result([{"a": 1}, {"b": 2}], "table"))
    assert text.splitlines()[0] == "| a | b |"
    assert "| 1 |  |" in text
    assert "|  | 2 |" in text


def test_empty_rows_render_without_table() -> None:
    assert _text(rows_result([], "table")) == "0 rows."


def test_title_goes_above_the_table() -> None:
    text = _text(rows_result([], "table", title="Statement executed."))
    assert text == "Statement executed.\n\n0 rows."


def test_json_has_rows_and_row_count_without_markdown() -> None:
    rows = [{"id": 1, "amount": Decimal("1.50"), "at": dt.date(2026, 9, 26), "note": None}]
    result = rows_result(rows, "json")
    text = _text(result)
    assert "|" not in text
    expected = {"rows": [{"id": 1, "amount": "1.50", "at": "2026-09-26", "note": None}], "row_count": 1}
    assert json.loads(text) == expected
    assert result.structured_content == expected


def test_json_empty_rows() -> None:
    result = rows_result([], "json", title="ignored in json")
    assert result.structured_content == {"rows": [], "row_count": 0}
    assert json.loads(_text(result)) == {"rows": [], "row_count": 0}


def test_json_keeps_non_ascii_readable() -> None:
    assert "имя" in _text(rows_result([{"name": "имя"}], "json"))


def test_sections_table_has_header_lines_and_one_table_per_non_empty_section() -> None:
    result = sections_result(
        {
            "columns": [{"column": "id", "data_type": "integer"}],
            "constraints": [{"name": "pk", "type": "PRIMARY KEY", "columns": ["id", "tenant"]}],
            "indexes": [],
        },
        "table",
        header={"schema": "public", "name": "users", "type": "table"},
    )
    text = _text(result)
    assert text.startswith("schema: public\nname: users\ntype: table\n\n### columns\n| column | data_type |")
    assert "### constraints" in text
    assert "| pk | PRIMARY KEY | id, tenant |" in text
    assert "### indexes" not in text
    assert result.structured_content is None


def test_sections_json_merges_header_and_sections() -> None:
    result = sections_result(
        {"columns": [{"column": "id"}], "indexes": []},
        "json",
        header={"schema": "public", "name": "users"},
    )
    expected = {"schema": "public", "name": "users", "columns": [{"column": "id"}], "indexes": []}
    assert result.structured_content == expected
    assert json.loads(_text(result)) == expected


def test_sections_without_header_and_rows_is_zero_rows() -> None:
    assert _text(sections_result({"columns": []}, "table")) == "0 rows."
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `<env> uv run pytest tests/unit/tools/test_rendering.py -q`
Expected: ошибка сбора `ModuleNotFoundError: No module named 'postgres_fastmcp.tools.rendering'`.

- [ ] **Step 3: Реализовать rendering.py**

Создать `src/postgres_fastmcp/tools/rendering.py`:

```python
"""Рендер строк результата тула: Markdown-таблица (output='table') или JSON (output='json').

Модуль не зависит от регистрации тулов: функции получают готовые строки и
возвращают ToolResult, поэтому переживают перенос тулов в ToolSet/PostgresProvider.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from fastmcp.tools import ToolResult
from mcp.types import TextContent


OutputFormat = Literal["table", "json"]


def rows_result(rows: list[dict[str, Any]], output: OutputFormat, *, title: str | None = None) -> ToolResult:
    """Результат тула из списка строк.

    Args:
        rows: Строки результата (одинаковые или разные наборы ключей).
        output: 'table' — Markdown-таблица, 'json' — {"rows": [...], "row_count": N}.
        title: Строка над таблицей (только для 'table').

    Returns:
        ToolResult с Markdown-текстом или с JSON-текстом и structured_content.
    """
    if output == "json":
        return _json_result({"rows": rows, "row_count": len(rows)})
    parts = [title] if title else []
    parts.append(f"{_table(rows)}\n\n{len(rows)} rows." if rows else "0 rows.")
    return _text_result("\n\n".join(parts))


def sections_result(
    sections: Mapping[str, list[dict[str, Any]]],
    output: OutputFormat,
    *,
    header: Mapping[str, Any] | None = None,
) -> ToolResult:
    """Результат тула из нескольких таблиц (разделов) и скалярного заголовка.

    Args:
        sections: Имя раздела -> строки раздела.
        output: 'table' — строки 'key: value' и по таблице на непустой раздел; 'json' — один словарь.
        header: Скалярные поля объекта (schema, name, type, ...).

    Returns:
        ToolResult с Markdown-текстом или с JSON-текстом и structured_content.
    """
    head = dict(header or {})
    if output == "json":
        return _json_result({**head, **{name: list(rows) for name, rows in sections.items()}})
    blocks = ["\n".join(f"{key}: {_cell(value)}" for key, value in head.items())] if head else []
    blocks.extend(f"### {name}\n{_table(rows)}" for name, rows in sections.items() if rows)
    return _text_result("\n\n".join(blocks) if blocks else "0 rows.")


def _table(rows: Sequence[Mapping[str, Any]]) -> str:
    """GFM-таблица: колонки один раз в шапке, в порядке первого появления ключа."""
    columns = list(dict.fromkeys(key for row in rows for key in row))
    lines = [
        _line(columns),
        _line(["---"] * len(columns)),
        *(_line([_cell(row.get(column)) for column in columns]) for row in rows),
    ]
    return "\n".join(lines)


def _line(cells: Sequence[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _cell(value: object) -> str:
    """Значение ячейки: None — пусто, список — через запятую, '|' экранирован, переводы строк — пробел."""
    if value is None:
        return ""
    if isinstance(value, list | tuple):
        text = ", ".join(_cell(item) for item in value)
    elif isinstance(value, dict):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    return text.replace("|", "\\|").replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def _text_result(text: str) -> ToolResult:
    return ToolResult(content=[TextContent(type="text", text=text)])


def _json_result(data: dict[str, Any]) -> ToolResult:
    """JSON-текст и structured_content из одной сериализации: Decimal, datetime и т.п. совпадают в обоих."""
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return ToolResult(content=[TextContent(type="text", text=payload)], structured_content=json.loads(payload))
```

Решения, которые спека оставила открытыми: список в ячейке выводится через запятую (`columns` констрейнта), словарь — компактным JSON; `ensure_ascii=False`, чтобы кириллица в данных не раздувала оценку токенов в 3 раза; `structured_content` берётся из `json.loads` того же текста, поэтому `Decimal`, даты и UUID в обоих представлениях совпадают (строками). В разделах `sections_result` строки `N rows.` нет: это шум для объекта из нескольких таблиц.

- [ ] **Step 4: Тесты зелёные**

Run: `<env> uv run pytest tests/unit/tools/test_rendering.py -q`
Expected: `12 passed`.

Run: `<env> uv run pytest tests/unit -q`
Expected: `573 passed`.

- [ ] **Step 5: Линтеры и коммит**

Run: `uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format`
Expected: ноль ошибок.

```bash
git add src/postgres_fastmcp/tools/rendering.py tests/unit/tools/test_rendering.py
git commit -m "feat(tools): render tool rows as a Markdown table or JSON"
```

---

### Task 3: Параметры тулов: нормализация, границы в схеме, limit для resources

**Files:**
- Create: `src/postgres_fastmcp/tools/params.py`
- Modify: `src/postgres_fastmcp/tools/definitions.py:17-23` (импорты, `HEALTH_TYPE_VALUES` уходит в `params`), `:52-128` (параметры `object_type`, `health_type`, `sort_by`, `limit`, `queries`)
- Modify: `src/postgres_fastmcp/tools/registry.py:20-31` (импорт `HEALTH_TYPE_VALUES`)
- Modify: `src/postgres_fastmcp/domains/top_queries.py:161-258, 281-282` (`LIMIT` в ветке `resources`)
- Test: `tests/unit/tools/test_params.py` (новый), `tests/unit/tools/test_definitions.py:118-127`, `tests/unit/domains/test_top_queries.py`

**Interfaces:**
- Consumes: `OutputFormat` из `tools/rendering.py` (Task 2); `UnsupportedObjectTypeError(object_type)`, `InvalidSortCriteriaError(sort_by)`, `InvalidHealthTypeError(health_type, allowed)`, `InvalidOutputFormatError(output)` из Task 1; `HealthType` из `domains/health/database_health.py`; `MAX_NUM_INDEX_TUNING_QUERIES` (10) из `domains/index_tuning/models.py`.
- Produces (Task 4 и Task 6 полагаются на эти имена):
  - `TOP_QUERIES_MAX_LIMIT = 100`, `HEALTH_TYPE_VALUES: str` (прежняя строка из `definitions.py`).
  - `ObjectTypeParam` — `ObjectType` после нормализации; схема `{"type": "string"}` без `enum`.
  - `HealthTypesParam` — `tuple[HealthType, ...]`; схема `anyOf` массив строк / строка; `'all'` поглощает остальные. По умолчанию в тулах `(HealthType.ALL,)`.
  - `TopQueriesSortByParam` — `TopQueriesSortBy`; синонимы `total`, `mean`, `avg`, `resource`.
  - `TopQueriesLimitParam` — `int`, `minimum: 1` в схеме, значения больше 100 урезаются до 100.
  - `IndexQueriesParam` — `list[str]`, `minItems: 1`, `maxItems: 10`.
  - `OutputParam` — `OutputFormat` после нормализации регистра; схема `{"type": "string"}`.
  - `TopQueriesCalc.get_top_resource_queries(limit: int = 10, frac_threshold: float = 0.05)`.

Справка: в тестах функции тулов вызываются напрямую, мимо pydantic, поэтому значения по умолчанию в сигнатурах уже канонические (`"table"`, `(HealthType.ALL,)`), а тесты передают нормализованные значения. Нормализацию проверяют через `TypeAdapter` и через MCP-клиент.

- [ ] **Step 1: Написать тесты параметров**

Создать `tests/unit/tools/test_params.py`:

```python
"""Тесты общих типов параметров тулов: нормализация ввода агента и отказ с подсказкой."""

from typing import Any

import pytest
from fastmcp import Client, FastMCP
from pydantic import TypeAdapter, ValidationError

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.domains.health.database_health import HealthType
from postgres_fastmcp.domains.index_tuning.models import MAX_NUM_INDEX_TUNING_QUERIES
from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.shared.errors import (
    InvalidHealthTypeError,
    InvalidOutputFormatError,
    InvalidSortCriteriaError,
    UnsupportedObjectTypeError,
)
from postgres_fastmcp.tools.params import (
    TOP_QUERIES_MAX_LIMIT,
    HealthTypesParam,
    IndexQueriesParam,
    ObjectTypeParam,
    OutputParam,
    TopQueriesLimitParam,
    TopQueriesSortByParam,
)
from postgres_fastmcp.tools.registry import register_tools


def _validate(param: Any, value: object) -> object:
    return TypeAdapter(param).validate_python(value)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("table", "table"),
        ("Tables", "table"),
        ("TABLE", "table"),
        ("views", "view"),
        ("sequence", "sequence"),
        ("Sequences", "sequence"),
        ("extension", "extension"),
        (" extensions ", "extension"),
    ],
)
def test_object_type_normalized(raw: str, expected: str) -> None:
    assert _validate(ObjectTypeParam, raw) == expected


def test_object_type_rejected_with_hint() -> None:
    with pytest.raises(UnsupportedObjectTypeError, match=r"Did you mean 'table'\?.*'view'"):
        _validate(ObjectTypeParam, "tabel")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("all", (HealthType.ALL,)),
        ("INDEX", (HealthType.INDEX,)),
        ("index, Vacuum", (HealthType.INDEX, HealthType.VACUUM)),
        (["buffer", "BUFFER", "constraint"], (HealthType.BUFFER, HealthType.CONSTRAINT)),
        ("index,all", (HealthType.ALL,)),
        (["connection", ""], (HealthType.CONNECTION,)),
    ],
)
def test_health_types_normalized(raw: object, expected: tuple[HealthType, ...]) -> None:
    assert _validate(HealthTypesParam, raw) == expected


@pytest.mark.parametrize(("raw", "bad"), [("indx", "indx"), (["index", "vacum"], "vacum")])
def test_health_type_rejected_with_hint(raw: object, bad: str) -> None:
    with pytest.raises(InvalidHealthTypeError, match=rf"'{bad}'.*Did you mean") as exc_info:
        _validate(HealthTypesParam, raw)
    assert "'replication'" in str(exc_info.value)


@pytest.mark.parametrize("raw", ["", " , ", []])
def test_empty_health_type_rejected(raw: object) -> None:
    with pytest.raises(InvalidHealthTypeError):
        _validate(HealthTypesParam, raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("resources", "resources"),
        ("resource", "resources"),
        ("total_time", "total_time"),
        ("total", "total_time"),
        ("Mean_Time", "mean_time"),
        ("mean", "mean_time"),
        ("avg", "mean_time"),
        ("mean-time", "mean_time"),
    ],
)
def test_sort_by_normalized(raw: str, expected: str) -> None:
    assert _validate(TopQueriesSortByParam, raw) == expected


def test_sort_by_rejected_with_hint() -> None:
    with pytest.raises(InvalidSortCriteriaError, match=r"Did you mean 'total_time'\?"):
        _validate(TopQueriesSortByParam, "totl_time")


@pytest.mark.parametrize(("raw", "expected"), [(1, 1), (10, 10), (100, 100), (500, TOP_QUERIES_MAX_LIMIT)])
def test_top_queries_limit_capped(raw: int, expected: int) -> None:
    assert _validate(TopQueriesLimitParam, raw) == expected


def test_top_queries_limit_below_one_rejected() -> None:
    with pytest.raises(ValidationError):
        _validate(TopQueriesLimitParam, 0)


def test_index_queries_bounds() -> None:
    assert _validate(IndexQueriesParam, ["SELECT 1"]) == ["SELECT 1"]
    with pytest.raises(ValidationError):
        _validate(IndexQueriesParam, [])
    with pytest.raises(ValidationError):
        _validate(IndexQueriesParam, ["SELECT 1"] * (MAX_NUM_INDEX_TUNING_QUERIES + 1))


@pytest.mark.parametrize(("raw", "expected"), [("table", "table"), ("JSON", "json"), (" Table ", "table")])
def test_output_normalized(raw: str, expected: str) -> None:
    assert _validate(OutputParam, raw) == expected


def test_output_rejected_with_hint() -> None:
    with pytest.raises(InvalidOutputFormatError, match=r"Did you mean 'json'\?"):
        _validate(OutputParam, "jsn")


@pytest.mark.parametrize(
    ("tool", "arguments", "hint"),
    [
        ("list_objects", {"schema_name": "public", "object_type": "tabel"}, "Did you mean 'table'?"),
        ("analyze_db_health", {"health_type": "indx"}, "Did you mean 'index'?"),
        ("get_top_queries", {"sort_by": "totl_time"}, "Did you mean 'total_time'?"),
    ],
)
async def test_rejected_input_reaches_client_with_hint(tool: str, arguments: dict[str, Any], hint: str) -> None:
    """Ошибка нормализации проходит mask_error_details: агент видит текст с подсказкой, а не 'Error calling tool'."""
    mcp = FastMCP(name="t", mask_error_details=True)
    settings = Settings()
    settings.database = settings.database.model_copy(update={"access_mode": AccessMode.FULL})
    register_tools(mcp, settings)
    async with Client(mcp) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error is True
    assert hint in result.content[0].text
```

- [ ] **Step 2: Тест LIMIT для resources и health_type в тестах тулов**

В `tests/unit/domains/test_top_queries.py`:

Импорт `from postgres_fastmcp.domains.top_queries import get_top_queries` заменить на:

```python
from postgres_fastmcp.domains.top_queries import TopQueriesCalc, get_top_queries
```

В `test_get_top_queries_sort_by_resources_returns_resource_report` вызов и проверку заменить на:

```python
        result = await get_top_queries(mock_db_access, sort_by="resources", limit=7)
        assert result == "Resource report"
        mock_tool.get_top_resource_queries.assert_awaited_once_with(limit=7)
```

Перед `test_top_queries_sql_filters_self_queries_and_zero_calls` добавить:

```python
async def test_resource_queries_sql_is_limited(mock_executor: MagicMock) -> None:
    """sort_by=resources honours limit: the SQL ends with LIMIT and limit is passed as a parameter."""
    calc = TopQueriesCalc(sql_driver=mock_executor, connection_id="test")
    calc._ext_inspector = MagicMock()
    calc._ext_inspector.check_extension = AsyncMock(return_value=MagicMock(is_installed=True))
    calc._ext_inspector.get_postgres_version = AsyncMock(return_value=16)

    await calc.get_top_resource_queries(limit=7)

    query = mock_executor.execute.call_args.args[0]
    assert query.rstrip().rstrip(";").endswith("LIMIT {}")
    assert mock_executor.execute.call_args.kwargs["params"] == [7]
```

В `tests/unit/tools/test_definitions.py` после `import pytest` добавить импорт:

```python
from postgres_fastmcp.domains.health.database_health import HealthType
```

и в `test_analyze_db_health_custom_type` вызов и проверку заменить на:

```python
    result = await defs.analyze_db_health(
        health_type=(HealthType.INDEX, HealthType.VACUUM),
        ctx=make_ctx(db_mock),
    )

    assert result == "INDEX_REPORT"
    fake_tool.health.assert_awaited_once_with(health_type="index,vacuum")
```

- [ ] **Step 3: Запустить и убедиться, что падает**

Run: `<env> uv run pytest tests/unit/tools/test_params.py tests/unit/domains/test_top_queries.py tests/unit/tools/test_definitions.py -q`
Expected: ошибка сбора `test_params.py` (`No module named 'postgres_fastmcp.tools.params'`); если запустить без него — FAIL `test_resource_queries_sql_is_limited` (`TypeError: ... unexpected keyword argument 'limit'`), `test_get_top_queries_sort_by_resources_returns_resource_report` (вызван без `limit`), `test_analyze_db_health_custom_type` (health получил `health_type=('index', 'vacuum')` вместо строки).

- [ ] **Step 4: Реализовать params.py**

Создать `src/postgres_fastmcp/tools/params.py`:

```python
"""Общие типы параметров MCP-тулов: принимают свободный ввод агента и приводят его к каноническому виду.

Правило: JSON-схема описывает всё, что сервер принимает (иначе клиент отклонит
допустимый ввод ещё до вызова), а валидаторы приводят принятое к каноническому
значению. Непоправимый ввод отклоняется UserFacingError с подсказкой.

Алиасы — обычные присваивания, не PEP 695 ``type X = ...``: у такого алиаса
docstring функции перебивает описание Field. Схему расширяем через
``Field(json_schema_extra=callable)``, а не ``WithJsonSchema``: WithJsonSchema
заменяет схему целиком и теряет ``Field(description=...)``.
"""

from typing import Annotated, Any, get_args

from pydantic import AfterValidator, BeforeValidator, Field

from postgres_fastmcp.domains.health.database_health import HealthType
from postgres_fastmcp.domains.index_tuning.models import MAX_NUM_INDEX_TUNING_QUERIES
from postgres_fastmcp.shared.enums import ObjectType, TopQueriesSortBy
from postgres_fastmcp.shared.errors import (
    InvalidHealthTypeError,
    InvalidOutputFormatError,
    InvalidSortCriteriaError,
    UnsupportedObjectTypeError,
)
from postgres_fastmcp.tools.rendering import OutputFormat


TOP_QUERIES_MAX_LIMIT = 100
HEALTH_TYPE_VALUES = ", ".join(sorted(ht.value for ht in HealthType))

_OBJECT_TYPES: tuple[str, ...] = get_args(ObjectType)
_SORT_BY: tuple[str, ...] = get_args(TopQueriesSortBy)
_SORT_BY_SYNONYMS = {"total": "total_time", "mean": "mean_time", "avg": "mean_time", "resource": "resources"}
_HEALTH_TYPES: tuple[str, ...] = tuple(ht.value for ht in HealthType)
_OUTPUT_FORMATS: tuple[str, ...] = get_args(OutputFormat)
_INDEX_QUERIES_EXAMPLE = "Example: ['SELECT * FROM orders WHERE customer_id = 42']."


def _any_string(schema: dict[str, Any]) -> None:
    """Схема «строка»: регистр и синонимы нормализует сервер, поэтому enum клиенту не отдаём."""
    schema.pop("enum", None)
    schema.pop("const", None)
    schema["type"] = "string"


def _string_or_list(schema: dict[str, Any]) -> None:
    """Схема «массив строк или строка через запятую» вместо массива enum-значений."""
    for key in ("type", "items", "minItems", "maxItems"):
        schema.pop(key, None)
    schema["anyOf"] = [{"type": "array", "items": {"type": "string"}, "minItems": 1}, {"type": "string"}]


def _key(value: object) -> object:
    """Строку привести к ключу сравнения: без пробелов по краям, нижний регистр, '-'/' ' -> '_'."""
    if not isinstance(value, str):
        return value
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _normalize_object_type(value: object) -> object:
    """'Tables', 'VIEW', 'sequences' -> каноническое значение ObjectType."""
    key = _key(value)
    if not isinstance(key, str):
        return value
    if key in _OBJECT_TYPES:
        return key
    if key.endswith("s") and key[:-1] in _OBJECT_TYPES:
        return key[:-1]
    raise UnsupportedObjectTypeError(str(value))


def _normalize_sort_by(value: object) -> object:
    """'Total', 'avg', 'resource' -> каноническое значение TopQueriesSortBy."""
    key = _key(value)
    if not isinstance(key, str):
        return value
    key = _SORT_BY_SYNONYMS.get(key, key)
    if key in _SORT_BY:
        return key
    raise InvalidSortCriteriaError(str(value))


def _normalize_health_types(value: object) -> object:
    """Список или CSV-строку привести к списку HealthType; 'all' поглощает остальные значения."""
    items = value.split(",") if isinstance(value, str) else value
    if not isinstance(items, list | tuple):
        return value
    keys = list(dict.fromkeys(key for item in items if isinstance(key := _key(item), str) and key))
    if not keys:
        raise InvalidHealthTypeError(str(value), _HEALTH_TYPES)
    for key in keys:
        if key not in _HEALTH_TYPES:
            raise InvalidHealthTypeError(key, _HEALTH_TYPES)
    if HealthType.ALL.value in keys:
        return [HealthType.ALL.value]
    return keys


def _normalize_output(value: object) -> object:
    """'JSON', ' Table ' -> 'json', 'table'."""
    key = _key(value)
    if not isinstance(key, str):
        return value
    if key in _OUTPUT_FORMATS:
        return key
    raise InvalidOutputFormatError(str(value))


def _cap_top_queries_limit(value: int) -> int:
    """Больше TOP_QUERIES_MAX_LIMIT строк агенту не отдаём: лишнее урезается, а не отклоняется."""
    return min(value, TOP_QUERIES_MAX_LIMIT)


ObjectTypeParam = Annotated[
    ObjectType,
    BeforeValidator(_normalize_object_type),
    Field(
        description=(
            "Object kind: 'table', 'view', 'sequence' or 'extension'. "
            "Any case and plural forms are accepted, e.g. 'Tables'."
        ),
        json_schema_extra=_any_string,
    ),
]
HealthTypesParam = Annotated[
    tuple[HealthType, ...],
    BeforeValidator(_normalize_health_types),
    Field(
        description=(
            f"Health checks to run: {HEALTH_TYPE_VALUES}. A list or a comma-separated string, any case; "
            "'all' runs every check. Example: ['index', 'vacuum'] or 'index,vacuum'."
        ),
        json_schema_extra=_string_or_list,
    ),
]
TopQueriesSortByParam = Annotated[
    TopQueriesSortBy,
    BeforeValidator(_normalize_sort_by),
    Field(
        description=(
            "Ranking criteria: 'resources' (share of time, buffers and WAL), 'total_time' or 'mean_time'. "
            "Synonyms 'total', 'mean', 'avg', 'resource' and any case are accepted. Example: 'mean_time'."
        ),
        json_schema_extra=_any_string,
    ),
]
TopQueriesLimitParam = Annotated[
    int,
    Field(
        ge=1,
        description=(
            f"Number of queries to return, 1-{TOP_QUERIES_MAX_LIMIT}; "
            f"larger values are capped at {TOP_QUERIES_MAX_LIMIT}. Example: 10."
        ),
    ),
    AfterValidator(_cap_top_queries_limit),
]
IndexQueriesParam = Annotated[
    list[str],
    Field(
        min_length=1,
        max_length=MAX_NUM_INDEX_TUNING_QUERIES,
        description=f"SQL queries to analyze, 1-{MAX_NUM_INDEX_TUNING_QUERIES} items. {_INDEX_QUERIES_EXAMPLE}",
    ),
]
OutputParam = Annotated[
    OutputFormat,
    BeforeValidator(_normalize_output),
    Field(
        description=(
            "'table' (default): Markdown table for reading. "
            "'json': {'rows': [...], 'row_count': N} as structured content, for code that processes the result. "
            "Any case is accepted."
        ),
        json_schema_extra=_any_string,
    ),
]
```

Почему так:
- Валидаторы бросают `UserFacingError`, а не `ValueError`: pydantic пропускает такое исключение насквозь, и агент видит одну строку с подсказкой вместо дампа `1 validation error for call[...]`.
- `_any_string` убирает `enum` из схемы `Literal`: сервер принимает `'Tables'` и `'JSON'`, значит схема не должна их запрещать.
- Для `limit` в схеме нет `maximum`: `limit=500` — однозначный ввод, его урезает `AfterValidator`.
- Пример с `SELECT ... FROM` вынесен в `_INDEX_QUERIES_EXAMPLE`: внутри f-строки ruff принимает его за SQL-инъекцию (S608).

- [ ] **Step 5: Перевести тулы на общие типы**

В `src/postgres_fastmcp/tools/definitions.py`:

Импорты с `...health.database_health` по `HEALTH_TYPE_VALUES = ...` заменить на:

```python
from postgres_fastmcp.domains.health.database_health import DatabaseHealthAnalyzer, HealthType
from postgres_fastmcp.domains.index_tuning.service import IndexAnalysisService
from postgres_fastmcp.tools.params import (
    HealthTypesParam,
    IndexQueriesParam,
    ObjectTypeParam,
    TopQueriesLimitParam,
    TopQueriesSortByParam,
)
```

(импорты `MAX_NUM_INDEX_TUNING_QUERIES`, `ObjectType`, `TopQueriesSortBy` и константа `HEALTH_TYPE_VALUES` удаляются.)

В `list_objects` и `get_object_details` параметр `object_type: Annotated[ObjectType, Field(...)] = "table"` заменить на:

```python
    object_type: ObjectTypeParam = "table",
```

Функцию `analyze_db_health` заменить на:

```python
async def analyze_db_health(
    health_type: HealthTypesParam = (HealthType.ALL,),
    ctx: Context = CurrentContext(),
) -> str:
    """Run database health checks and return a text report."""
    health_tool = DatabaseHealthAnalyzer(get_db(ctx).sql_driver)
    return await health_tool.health(health_type=",".join(health_type))
```

В `get_top_queries` параметры `sort_by` и `limit` заменить на:

```python
    sort_by: TopQueriesSortByParam = "resources",
    limit: TopQueriesLimitParam = 10,
```

В `analyze_query_indexes` параметр `queries` заменить на:

```python
    queries: IndexQueriesParam,
```

В `src/postgres_fastmcp/tools/registry.py` убрать `HEALTH_TYPE_VALUES,` из импорта `postgres_fastmcp.tools.definitions` и сразу после этого импорта добавить:

```python
from postgres_fastmcp.tools.params import HEALTH_TYPE_VALUES
```

- [ ] **Step 6: LIMIT в ветке resources**

В `src/postgres_fastmcp/domains/top_queries.py`:

Сигнатуру и `Args` метода `get_top_resource_queries` заменить на:

```python
    async def get_top_resource_queries(self, limit: int = 10, frac_threshold: float = 0.05) -> str:
        """Reports the most time consuming queries based on a resource blend.

        Args:
            limit: Maximum number of queries to return
            frac_threshold: Fraction threshold for filtering queries (default: 0.05)
```

В SQL этого метода после строки `ORDER BY total_exec_time DESC` добавить строку `LIMIT {{}};` (двойные скобки: запрос — f-строка, а `{}` — плейсхолдер `SqlDriver.render`), а в вызове `self.sql_driver.execute(...)` заменить `params=None` на `params=[limit]`:

```python
                ORDER BY total_exec_time DESC
                LIMIT {{}};
            """  # noqa: E501, S608

            logger.debug("Executing query: %s", query)
            slow_query_rows = await self.sql_driver.execute(
                query,
                params=[limit],
                readonly=True,
            )
```

В `get_top_queries` ветку `resources` заменить на:

```python
    if sort_by == "resources":
        return await calc.get_top_resource_queries(limit=limit)
```

- [ ] **Step 7: Тесты зелёные**

Run: `<env> uv run pytest tests/unit/tools/test_params.py -q`
Expected: `42 passed`.

Run: `<env> uv run pytest tests/unit -q`
Expected: `616 passed`.

- [ ] **Step 8: Линтеры и коммит**

Run: `uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format`
Expected: ноль ошибок.

```bash
git add src/postgres_fastmcp/tools/params.py src/postgres_fastmcp/tools/definitions.py src/postgres_fastmcp/tools/registry.py src/postgres_fastmcp/domains/top_queries.py tests/unit/tools/test_params.py tests/unit/tools/test_definitions.py tests/unit/domains/test_top_queries.py
git commit -m "feat(tools): normalize tool parameters and expose their bounds in the JSON schema"
```

---

### Task 4: Параметр `output` и `ToolResult` у пяти тулов

**Files:**
- Modify: `src/postgres_fastmcp/tools/definitions.py` (переписать целиком)
- Modify: `src/postgres_fastmcp/tools/registry.py:105-213` (`"output_schema": None` у пяти спек)
- Modify: `src/postgres_fastmcp/domains/querying.py:12-34`
- Modify: `src/postgres_fastmcp/domains/top_queries.py` (переписать целиком)
- Modify: `src/postgres_fastmcp/shared/errors.py` (новый `PgStatStatementsNotInstalledError` перед `HypopgNotInstalledError`)
- Test: `tests/unit/tools/test_definitions.py` (переписать целиком), `tests/unit/tools/test_registry.py`, `tests/unit/domains/test_querying.py:54-63`, `tests/unit/domains/test_top_queries.py` (переписать целиком), `tests/unit/shared/test_errors.py`
- Test (интеграция, только правка формы ответа): `tests/integration/test_tools_integration.py` (переписать целиком), `tests/integration/test_write_mode.py:37-46`, `tests/integration/test_top_queries_integration.py:9, 82-91, 97-119`, `tests/README.md:40-57`

**Interfaces:**
- Consumes: `OutputParam`, `ObjectTypeParam`, `HealthTypesParam`, `TopQueriesSortByParam`, `TopQueriesLimitParam`, `IndexQueriesParam` из `tools/params.py` (Task 3); `rows_result`, `sections_result` из `tools/rendering.py` (Task 2); `_SAMPLES` в `tests/unit/shared/test_errors.py` (Task 1).
- Produces:
  - Тулы `execute_sql`, `list_schemas`, `list_objects`, `get_top_queries`, `get_object_details` возвращают `ToolResult` и принимают `output` (по умолчанию `"table"`); регистрируются с `output_schema=None`.
  - `get_object_details`: заголовок `schema`, `name`, `type` плюс скалярные поля сервиса (вложенный `basic` раскрывается в заголовок), списковые поля — разделы. JSON — `{**header, **sections}`.
  - `querying.execute_sql(db, sql) -> list[dict[str, Any]] | None` (`None` — оператор без результата; тул выводит `SUCCESS_NO_ROWS` над `0 rows.`).
  - `top_queries.get_top_queries(db, sort_by, limit) -> list[dict[str, Any]]`; без `pg_stat_statements` — `PgStatStatementsNotInstalledError` (прежде тул возвращал текст с инструкцией; широкий `except Exception`, превращавший любую ошибку в строку, удалён).
  - `PgStatStatementsNotInstalledError()` в `shared/errors.py`.

- [ ] **Step 1: Переписать тесты тулов**

Заменить `tests/unit/tools/test_definitions.py` целиком:

```python
# mypy: ignore-errors
"""Тесты всех MCP-тулов: тонкие функции из tools.definitions."""

from unittest import mock

import pytest

from postgres_fastmcp.domains.health.database_health import HealthType
from postgres_fastmcp.domains.querying import SUCCESS_NO_ROWS
from postgres_fastmcp.tools import definitions as defs


def _text(result) -> str:
    """Текст единственного TextContent из ToolResult."""
    [block] = result.content
    return block.text


@pytest.mark.asyncio
async def test_execute_sql_returns_markdown_table_by_default(monkeypatch, db_mock, make_ctx) -> None:
    fake_querying = mock.AsyncMock()
    fake_querying.execute_sql.return_value = [{"col": 1}]
    monkeypatch.setattr(defs, "querying", fake_querying)

    result = await defs.execute_sql(sql="SELECT 1", ctx=make_ctx(db_mock))

    assert _text(result) == "| col |\n| --- |\n| 1 |\n\n1 rows."
    assert result.structured_content is None
    fake_querying.execute_sql.assert_awaited_once_with(db_mock, "SELECT 1")


@pytest.mark.asyncio
async def test_execute_sql_json_output(monkeypatch, db_mock, make_ctx) -> None:
    fake_querying = mock.AsyncMock()
    fake_querying.execute_sql.return_value = [{"col": 1}]
    monkeypatch.setattr(defs, "querying", fake_querying)

    result = await defs.execute_sql(sql="SELECT 1", output="json", ctx=make_ctx(db_mock))

    assert result.structured_content == {"rows": [{"col": 1}], "row_count": 1}


@pytest.mark.asyncio
async def test_execute_sql_statement_without_rows(monkeypatch, db_mock, make_ctx) -> None:
    fake_querying = mock.AsyncMock()
    fake_querying.execute_sql.return_value = None
    monkeypatch.setattr(defs, "querying", fake_querying)

    table = await defs.execute_sql(sql="INSERT INTO t VALUES (1)", ctx=make_ctx(db_mock))
    as_json = await defs.execute_sql(sql="INSERT INTO t VALUES (1)", output="json", ctx=make_ctx(db_mock))

    assert _text(table) == f"{SUCCESS_NO_ROWS}\n\n0 rows."
    assert as_json.structured_content == {"rows": [], "row_count": 0}


@pytest.mark.asyncio
async def test_execute_sql_propagates_errors(monkeypatch, db_mock, make_ctx) -> None:
    fake_querying = mock.AsyncMock()
    fake_querying.execute_sql.side_effect = RuntimeError("boom")
    monkeypatch.setattr(defs, "querying", fake_querying)

    with pytest.raises(RuntimeError, match="boom"):
        await defs.execute_sql(sql="SELECT 1", ctx=make_ctx(db_mock))


@pytest.mark.asyncio
async def test_explain_query_plain(monkeypatch, db_mock, make_ctx) -> None:
    fake_service = mock.AsyncMock()
    fake_service.explain.return_value = "PLAN"
    monkeypatch.setattr(defs, "ExplainService", lambda **kw: fake_service)

    result = await defs.explain_query(sql="SELECT 1", ctx=make_ctx(db_mock))

    assert result == "PLAN"
    fake_service.explain.assert_awaited_once_with("SELECT 1", analyze=False, hypothetical_indexes=None)


@pytest.mark.asyncio
async def test_explain_query_with_analyze_and_hypothetical(monkeypatch, db_mock, make_ctx) -> None:
    fake_service = mock.AsyncMock()
    fake_service.explain.return_value = "ANALYZED"
    monkeypatch.setattr(defs, "ExplainService", lambda **kw: fake_service)

    indexes = [{"table": "t", "columns": ["c"]}]
    result = await defs.explain_query(
        sql="SELECT 1",
        analyze=True,
        hypothetical_indexes=indexes,
        ctx=make_ctx(db_mock),
    )

    assert result == "ANALYZED"
    fake_service.explain.assert_awaited_once_with("SELECT 1", analyze=True, hypothetical_indexes=indexes)


@pytest.mark.asyncio
async def test_list_objects_returns_rows(monkeypatch, db_mock, make_ctx) -> None:
    fake_service = mock.AsyncMock()
    fake_service.list_objects.return_value = [{"name": "users"}]
    monkeypatch.setattr(defs, "CatalogService", lambda **kw: fake_service)

    result = await defs.list_objects(schema_name="public", object_type="table", output="json", ctx=make_ctx(db_mock))

    assert result.structured_content == {"rows": [{"name": "users"}], "row_count": 1}
    fake_service.list_objects.assert_awaited_once_with(schema_name="public", object_type="table")


@pytest.mark.asyncio
async def test_get_object_details_table_sections(monkeypatch, db_mock, make_ctx) -> None:
    """Таблица: basic уходит в заголовок, columns/constraints/indexes — разделы."""
    fake_service = mock.AsyncMock()
    fake_service.get_object_details.return_value = {
        "basic": {"schema": "public", "name": "users", "type": "table"},
        "columns": [{"column": "id", "data_type": "integer", "is_nullable": "NO", "default": None}],
        "constraints": [{"name": "users_pkey", "type": "PRIMARY KEY", "columns": ["id"]}],
        "indexes": [],
    }
    monkeypatch.setattr(defs, "CatalogService", lambda **kw: fake_service)

    table = await defs.get_object_details(schema_name="public", object_name="users", ctx=make_ctx(db_mock))
    as_json = await defs.get_object_details(
        schema_name="public", object_name="users", output="json", ctx=make_ctx(db_mock)
    )

    text = _text(table)
    assert text.startswith("schema: public\nname: users\ntype: table\n\n### columns\n")
    assert "### constraints" in text
    assert "### indexes" not in text
    assert as_json.structured_content == {
        "schema": "public",
        "name": "users",
        "type": "table",
        "columns": [{"column": "id", "data_type": "integer", "is_nullable": "NO", "default": None}],
        "constraints": [{"name": "users_pkey", "type": "PRIMARY KEY", "columns": ["id"]}],
        "indexes": [],
    }
    fake_service.get_object_details.assert_awaited_with(schema_name="public", object_name="users", object_type="table")


@pytest.mark.asyncio
async def test_get_object_details_sequence_is_header_only(monkeypatch, db_mock, make_ctx) -> None:
    """Последовательность: все поля скалярные, поэтому только заголовок."""
    fake_service = mock.AsyncMock()
    fake_service.get_object_details.return_value = {
        "schema": "public",
        "name": "users_id_seq",
        "data_type": "bigint",
        "start_value": 1,
        "increment": 1,
    }
    monkeypatch.setattr(defs, "CatalogService", lambda **kw: fake_service)

    result = await defs.get_object_details(
        schema_name="public", object_name="users_id_seq", object_type="sequence", ctx=make_ctx(db_mock)
    )

    assert _text(result) == (
        "schema: public\nname: users_id_seq\ntype: sequence\ndata_type: bigint\nstart_value: 1\nincrement: 1"
    )


@pytest.mark.asyncio
async def test_list_schemas_returns_rows(monkeypatch, db_mock, make_ctx) -> None:
    fake_service = mock.AsyncMock()
    fake_service.list_schemas.return_value = [{"schema_name": "public"}]
    monkeypatch.setattr(defs, "CatalogService", lambda **kw: fake_service)

    result = await defs.list_schemas(ctx=make_ctx(db_mock))

    assert _text(result) == "| schema_name |\n| --- |\n| public |\n\n1 rows."
    fake_service.list_schemas.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_analyze_db_health_default(monkeypatch, db_mock, make_ctx) -> None:
    fake_tool = mock.AsyncMock()
    fake_tool.health.return_value = "OK"
    monkeypatch.setattr(defs, "DatabaseHealthAnalyzer", lambda sql_driver: fake_tool)

    result = await defs.analyze_db_health(ctx=make_ctx(db_mock))

    assert result == "OK"
    fake_tool.health.assert_awaited_once_with(health_type="all")


@pytest.mark.asyncio
async def test_analyze_db_health_custom_type(monkeypatch, db_mock, make_ctx) -> None:
    fake_tool = mock.AsyncMock()
    fake_tool.health.return_value = "INDEX_REPORT"
    monkeypatch.setattr(defs, "DatabaseHealthAnalyzer", lambda sql_driver: fake_tool)

    result = await defs.analyze_db_health(
        health_type=(HealthType.INDEX, HealthType.VACUUM),
        ctx=make_ctx(db_mock),
    )

    assert result == "INDEX_REPORT"
    fake_tool.health.assert_awaited_once_with(health_type="index,vacuum")


@pytest.mark.asyncio
async def test_get_top_queries_default(monkeypatch, db_mock, make_ctx) -> None:
    fake_top_queries = mock.AsyncMock()
    fake_top_queries.get_top_queries.return_value = [{"query": "SELECT 1", "calls": 3}]
    monkeypatch.setattr(defs, "top_queries", fake_top_queries)

    result = await defs.get_top_queries(ctx=make_ctx(db_mock))

    assert _text(result) == "| query | calls |\n| --- | --- |\n| SELECT 1 | 3 |\n\n1 rows."
    fake_top_queries.get_top_queries.assert_awaited_once_with(db_mock, sort_by="resources", limit=10)


@pytest.mark.asyncio
async def test_get_top_queries_custom_args(monkeypatch, db_mock, make_ctx) -> None:
    fake_top_queries = mock.AsyncMock()
    fake_top_queries.get_top_queries.return_value = []
    monkeypatch.setattr(defs, "top_queries", fake_top_queries)

    result = await defs.get_top_queries(sort_by="total_time", limit=5, output="json", ctx=make_ctx(db_mock))

    assert result.structured_content == {"rows": [], "row_count": 0}
    fake_top_queries.get_top_queries.assert_awaited_once_with(db_mock, sort_by="total_time", limit=5)


@pytest.mark.asyncio
async def test_analyze_workload_indexes_default(monkeypatch, db_mock, make_ctx) -> None:
    fake_service = mock.AsyncMock()
    fake_service.analyze_workload_indexes.return_value = {"recommendations": []}
    monkeypatch.setattr(defs, "IndexAnalysisService", lambda **kw: fake_service)

    result = await defs.analyze_workload_indexes(ctx=make_ctx(db_mock))

    assert result == {"recommendations": []}
    fake_service.analyze_workload_indexes.assert_awaited_once_with(max_index_size_mb=10000)


@pytest.mark.asyncio
async def test_analyze_query_indexes_default(monkeypatch, db_mock, make_ctx) -> None:
    fake_service = mock.AsyncMock()
    fake_service.analyze_query_indexes.return_value = {"recommendations": ["idx"]}
    monkeypatch.setattr(defs, "IndexAnalysisService", lambda **kw: fake_service)

    result = await defs.analyze_query_indexes(queries=["SELECT 1"], ctx=make_ctx(db_mock))

    assert result == {"recommendations": ["idx"]}
    fake_service.analyze_query_indexes.assert_awaited_once_with(queries=["SELECT 1"], max_index_size_mb=10000)
```

- [ ] **Step 2: Тесты регистрации: без output_schema и формат по MCP**

В `tests/unit/tools/test_registry.py` импорты в начале файла заменить на:

```python
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import Client, FastMCP

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.tools.registry import DESTRUCTIVE, READ_ONLY_IDEMPOTENT, READ_ONLY_NON_IDEMPOTENT, register_tools
```

В конец файла добавить:

```python
_ROW_TOOLS = ("execute_sql", "list_objects", "get_object_details", "list_schemas", "get_top_queries")


async def test_row_tools_have_no_output_schema() -> None:
    """Тулы со строками отдают ToolResult сами: FastMCP не должен заворачивать ответ в {'result': ...}."""
    mcp = FastMCP(name="test")
    register_tools(mcp, _build_settings(AccessMode.FULL))
    for name in _ROW_TOOLS:
        tool = await mcp.get_tool(name)
        assert tool is not None
        assert tool.output_schema is None, name
        assert "output" in tool.parameters["properties"], name


async def test_execute_sql_output_over_mcp() -> None:
    """По MCP: 'table' — только Markdown, 'JSON' (любой регистр) — JSON-текст и structured_content."""

    @asynccontextmanager
    async def lifespan(server: object) -> AsyncIterator[dict[str, object]]:  # noqa: ARG001
        db = MagicMock()
        db.write_mode = False
        db.sql_driver.execute = AsyncMock(return_value=[RowResult(cells={"n": 1})])
        yield {"db": db}

    mcp = FastMCP(name="test", lifespan=lifespan)
    register_tools(mcp, _build_settings(AccessMode.FULL))
    async with Client(mcp) as client:
        table = await client.call_tool("execute_sql", {"sql": "SELECT 1 AS n"})
        as_json = await client.call_tool("execute_sql", {"sql": "SELECT 1 AS n", "output": "JSON"})

    assert [block.text for block in table.content] == ["| n |\n| --- |\n| 1 |\n\n1 rows."]
    assert table.structured_content is None
    assert [block.text for block in as_json.content] == ['{"rows": [{"n": 1}], "row_count": 1}']
    assert as_json.structured_content == {"rows": [{"n": 1}], "row_count": 1}
```

- [ ] **Step 3: Тесты доменов: None для оператора без результата, строки топ-запросов**

В `tests/unit/domains/test_querying.py` импорт заменить на `from postgres_fastmcp.domains.querying import execute_sql`, а тест `test_execute_sql_none_returns_success_status` заменить на:

```python
    async def test_execute_sql_none_means_no_result_set(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """When driver returns None (e.g. DDL/DML without RETURNING), service returns None, not an error."""
        mock_db_access.write_mode = True
        mock_executor.execute.return_value = None
        result = await execute_sql(mock_db_access, "CREATE TABLE t (id int)")
        assert result is None
```

Заменить `tests/unit/domains/test_top_queries.py` целиком:

```python
# mypy: ignore-errors
"""Unit tests for domains.top_queries: rows from pg_stat_statements."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from postgres_fastmcp.domains.top_queries import TopQueriesCalc, get_top_queries
from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.shared.errors import InvalidSortCriteriaError, PgStatStatementsNotInstalledError


def _calc(mock_executor: MagicMock, *, installed: bool = True) -> TopQueriesCalc:
    """TopQueriesCalc с подменённой проверкой расширения и версии PostgreSQL 16."""
    calc = TopQueriesCalc(sql_driver=mock_executor, connection_id="test")
    calc._ext_inspector = MagicMock()
    calc._ext_inspector.check_extension = AsyncMock(return_value=MagicMock(is_installed=installed))
    calc._ext_inspector.get_postgres_version = AsyncMock(return_value=16)
    return calc


class TestGetTopQueries:
    """Tests for top_queries.get_top_queries dispatch."""

    @patch("postgres_fastmcp.domains.top_queries.TopQueriesCalc")
    async def test_sort_by_resources_passes_limit(self, mock_calc_cls: MagicMock, mock_db_access: MagicMock) -> None:
        """sort_by=resources returns resource rows and honours limit."""
        mock_tool = MagicMock()
        mock_tool.get_top_resource_queries = AsyncMock(return_value=[{"query": "q"}])
        mock_calc_cls.return_value = mock_tool

        result = await get_top_queries(mock_db_access, sort_by="resources", limit=7)

        assert result == [{"query": "q"}]
        mock_tool.get_top_resource_queries.assert_awaited_once_with(limit=7)

    @pytest.mark.parametrize(("sort_by", "criteria"), [("mean_time", "mean"), ("total_time", "total")])
    @patch("postgres_fastmcp.domains.top_queries.TopQueriesCalc")
    async def test_sort_by_time(
        self, mock_calc_cls: MagicMock, mock_db_access: MagicMock, sort_by: str, criteria: str
    ) -> None:
        """sort_by=mean_time/total_time returns time-ranked rows."""
        mock_tool = MagicMock()
        mock_tool.get_top_queries_by_time = AsyncMock(return_value=[{"query": "q"}])
        mock_calc_cls.return_value = mock_tool

        result = await get_top_queries(mock_db_access, sort_by=sort_by, limit=5)

        assert result == [{"query": "q"}]
        mock_tool.get_top_queries_by_time.assert_awaited_once_with(limit=5, sort_by=criteria)

    @patch("postgres_fastmcp.domains.top_queries.TopQueriesCalc")
    async def test_invalid_sort_by_raises(self, mock_calc_cls: MagicMock, mock_db_access: MagicMock) -> None:
        """get_top_queries(sort_by=invalid) raises InvalidSortCriteriaError."""
        mock_calc_cls.return_value = MagicMock()

        with pytest.raises(InvalidSortCriteriaError):
            await get_top_queries(mock_db_access, sort_by="invalid")


async def test_resource_queries_sql_is_limited(mock_executor: MagicMock) -> None:
    """sort_by=resources honours limit: the SQL ends with LIMIT and limit is passed as a parameter."""
    mock_executor.execute.return_value = [RowResult(cells={"query": b"SELECT 1", "calls": 2})]

    rows = await _calc(mock_executor).get_top_resource_queries(limit=7)

    query = mock_executor.execute.call_args.args[0]
    assert query.rstrip().rstrip(";").endswith("LIMIT {}")
    assert mock_executor.execute.call_args.kwargs["params"] == [7]
    assert rows == [{"query": "SELECT 1", "calls": 2}]


async def test_time_queries_return_rows(mock_executor: MagicMock) -> None:
    """Time ranking returns decoded rows, not a formatted string."""
    mock_executor.execute.return_value = [RowResult(cells={"query": "SELECT 1", "calls": 3})]

    rows = await _calc(mock_executor).get_top_queries_by_time(limit=3, sort_by="total")

    assert rows == [{"query": "SELECT 1", "calls": 3}]
    assert "ORDER BY total_exec_time DESC" in mock_executor.execute.call_args.args[0]
    assert mock_executor.execute.call_args.kwargs["params"] == [3]


@pytest.mark.parametrize("method", ["get_top_resource_queries", "get_top_queries_by_time"])
async def test_missing_extension_raises(mock_executor: MagicMock, method: str) -> None:
    """Without pg_stat_statements both rankings raise a user-facing error with the install hint."""
    with pytest.raises(PgStatStatementsNotInstalledError, match="CREATE EXTENSION pg_stat_statements"):
        await getattr(_calc(mock_executor, installed=False), method)()
    mock_executor.execute.assert_not_called()


def test_top_queries_sql_filters_self_queries_and_zero_calls() -> None:
    """Generated SQL must filter out pg_stat_statements self-queries and zero-call entries."""
    from pathlib import Path

    from postgres_fastmcp.domains import top_queries as mod

    src = Path(mod.__file__).read_text()
    assert "calls > 0" in src
    assert "NOT LIKE '%pg_stat_statements%'" in src
```

В `tests/unit/shared/test_errors.py` в `_SAMPLES` перед строкой `"HypopgNotInstalledError": ...` добавить:

```python
    "PgStatStatementsNotInstalledError": errors.PgStatStatementsNotInstalledError,
```

и в параметры `test_correctable_error_ends_with_hint` после строки `("InvalidOutputFormatError", "Did you mean 'json'?"),` добавить:

```python
        ("PgStatStatementsNotInstalledError", "CREATE EXTENSION pg_stat_statements"),
```

- [ ] **Step 4: Запустить и убедиться, что падает**

Run: `<env> uv run pytest tests/unit -q`
Expected: FAIL. `test_errors.py` — `AttributeError: ... PgStatStatementsNotInstalledError`; в `test_top_queries.py` — ошибка импорта того же класса; `test_definitions.py` — тесты `execute_sql`, `list_objects`, `get_object_details`, `list_schemas`, `get_top_queries` (результат — список или строка, а не `ToolResult`: `AttributeError: ... has no attribute 'content'` / `'structured_content'`); `test_registry.py::test_row_tools_have_no_output_schema` (`output_schema` задан); `test_querying.py::test_execute_sql_none_means_no_result_set`.

- [ ] **Step 5: Ошибка «pg_stat_statements не установлено»**

В `src/postgres_fastmcp/shared/errors.py` перед классом `HypopgNotInstalledError` добавить:

```python
class PgStatStatementsNotInstalledError(UserFacingError):
    """Расширение pg_stat_statements не установлено: топ запросов недоступен."""

    def __init__(self) -> None:
        """Инициализация с фиксированным сообщением и подсказкой по установке."""
        super().__init__(
            "The pg_stat_statements extension is not installed, so query statistics are unavailable. "
            "Ask a database administrator to add pg_stat_statements to shared_preload_libraries "
            "and run CREATE EXTENSION pg_stat_statements."
        )
```

- [ ] **Step 6: Домены отдают строки**

В `src/postgres_fastmcp/domains/querying.py` функцию `execute_sql` заменить на:

```python
async def execute_sql(db: DbAccessService, sql: str) -> list[dict[str, Any]] | None:
    """Выполнить SQL запрос к базе данных.

    Режим транзакции (только чтение / чтение-запись) определяется write_mode
    сервера, а не вызывающим кодом: при write_mode=True транзакция открывается
    на запись, поэтому DML/DDL реально применяются. В режиме только чтения
    (write_mode=False) запись блокируется на уровне валидатора и транзакции.

    Операторы без результирующего набора (INSERT/UPDATE/DELETE/DDL без RETURNING)
    считаются успешно выполненными и возвращают None, а не ошибку; тул выводит
    для них SUCCESS_NO_ROWS.

    Args:
        db: Сервис доступа к базе данных.
        sql: SQL запрос для выполнения.

    Returns:
        Список строк результата (list[dict]) либо None для оператора без результирующего набора.
    """
    rows = await db.sql_driver.execute(sql, params=None, readonly=not db.write_mode)
    if rows is None:
        return None
    return [decode_bytes_to_utf8(r.cells) for r in rows]
```

Заменить `src/postgres_fastmcp/domains/top_queries.py` целиком (константа `install_pg_stat_statements_message` удаляется, проверка расширения и версии вынесена в `_time_columns`, строки декодируются как в остальных доменах):

```python
"""Домен топ-запросов: отчёты по pg_stat_statements (тул get_top_queries)."""

import logging
from typing import Any, Literal

from postgres_fastmcp.domains.db_access import DbAccessService
from postgres_fastmcp.postgres.extensions import ExtensionInspectorAdapter
from postgres_fastmcp.postgres.ports import SqlDriverPort
from postgres_fastmcp.shared.errors import InvalidSortCriteriaError, PgStatStatementsNotInstalledError
from postgres_fastmcp.shared.utils import decode_bytes_to_utf8


logger = logging.getLogger(__name__)

PG_STAT_STATEMENTS = "pg_stat_statements"
# PostgreSQL version where column names changed in pg_stat_statements
PG_VERSION_COLUMN_CHANGE = 13


class TopQueriesCalc:
    """Строки pg_stat_statements: самые медленные и самые ресурсоёмкие запросы."""

    def __init__(
        self,
        sql_driver: SqlDriverPort,
        connection_id: str = "",
    ) -> None:
        """Инициализация.

        Args:
            sql_driver: SQL-драйвер для запросов (и шаблон для проверки расширений).
            connection_id: Стабильный id подключения для кэша расширений и версии.
        """
        self.sql_driver = sql_driver
        self._ext_inspector = ExtensionInspectorAdapter(sql_driver, sql_driver, connection_id)

    async def _time_columns(self) -> tuple[str, str]:
        """Имена колонок общего и среднего времени для версии сервера.

        Raises:
            PgStatStatementsNotInstalledError: Если pg_stat_statements не установлено.
        """
        extension_status = await self._ext_inspector.check_extension(PG_STAT_STATEMENTS, include_messages=False)
        if not extension_status.is_installed:
            logger.warning("Extension %s is not installed", PG_STAT_STATEMENTS)
            raise PgStatStatementsNotInstalledError
        pg_version = await self._ext_inspector.get_postgres_version()
        logger.debug("PostgreSQL version: %s", pg_version)
        # Колонки переименованы в PostgreSQL 13
        if pg_version >= PG_VERSION_COLUMN_CHANGE:
            return "total_exec_time", "mean_exec_time"
        return "total_time", "mean_time"

    async def get_top_queries_by_time(
        self, limit: int = 10, sort_by: Literal["total", "mean"] = "mean"
    ) -> list[dict[str, Any]]:
        """Самые медленные запросы по общему или среднему времени выполнения.

        Args:
            limit: Максимум строк.
            sort_by: 'total' — по общему времени, 'mean' — по среднему на вызов.

        Returns:
            Строки pg_stat_statements: query, calls, время, rows.
        """
        total_time_col, mean_time_col = await self._time_columns()
        order_by_column = total_time_col if sort_by == "total" else mean_time_col
        # Имена колонок берутся из проверки версии, а не из ввода пользователя
        query = f"""
            SELECT
                query,
                calls,
                {total_time_col},
                {mean_time_col},
                rows
            FROM pg_stat_statements
            WHERE calls > 0
              AND query NOT LIKE '%pg_stat_statements%'
            ORDER BY {order_by_column} DESC
            LIMIT {{}};
        """  # noqa: S608
        rows = await self.sql_driver.execute(query, params=[limit], readonly=True)
        result = [decode_bytes_to_utf8(row.cells) for row in rows] if rows else []
        logger.info("Found %s slow queries", len(result))
        return result

    async def get_top_resource_queries(self, limit: int = 10, frac_threshold: float = 0.05) -> list[dict[str, Any]]:
        """Самые ресурсоёмкие запросы: доля времени, буферов и WAL выше порога.

        Args:
            limit: Максимум строк.
            frac_threshold: Порог доли ресурса (по умолчанию 0.05).

        Returns:
            Строки pg_stat_statements с долями ресурсов.
        """
        total_time_col, mean_time_col = await self._time_columns()
        # Имена колонок из проверки версии, frac_threshold — float-параметр, а не SQL от пользователя
        query = f"""
            WITH resource_fractions AS (
                SELECT
                    query,
                    calls,
                    rows,
                    {total_time_col} total_exec_time,
                    {mean_time_col} mean_exec_time,
                    stddev_exec_time,
                    shared_blks_hit,
                    shared_blks_read,
                    shared_blks_dirtied,
                    wal_bytes,
                    total_exec_time / SUM(total_exec_time) OVER () AS total_exec_time_frac,
                    (shared_blks_hit + shared_blks_read) / SUM(shared_blks_hit + shared_blks_read) OVER () AS shared_blks_accessed_frac,
                    shared_blks_read / SUM(shared_blks_read) OVER () AS shared_blks_read_frac,
                    shared_blks_dirtied / SUM(shared_blks_dirtied) OVER () AS shared_blks_dirtied_frac,
                    wal_bytes / SUM(wal_bytes) OVER () AS total_wal_bytes_frac
                FROM pg_stat_statements
                WHERE calls > 0
                  AND query NOT LIKE '%pg_stat_statements%'
            )
            SELECT
                query,
                calls,
                rows,
                total_exec_time,
                mean_exec_time,
                stddev_exec_time,
                total_exec_time_frac,
                shared_blks_accessed_frac,
                shared_blks_read_frac,
                shared_blks_dirtied_frac,
                total_wal_bytes_frac,
                shared_blks_hit,
                shared_blks_read,
                shared_blks_dirtied,
                wal_bytes
            FROM resource_fractions
            WHERE
                total_exec_time_frac > {frac_threshold}
                OR shared_blks_accessed_frac > {frac_threshold}
                OR shared_blks_read_frac > {frac_threshold}
                OR shared_blks_dirtied_frac > {frac_threshold}
                OR total_wal_bytes_frac > {frac_threshold}
            ORDER BY total_exec_time DESC
            LIMIT {{}};
        """  # noqa: E501, S608
        rows = await self.sql_driver.execute(query, params=[limit], readonly=True)
        result = [decode_bytes_to_utf8(row.cells) for row in rows] if rows else []
        logger.info("Found %s resource-intensive queries", len(result))
        return result


async def get_top_queries(
    db: DbAccessService,
    sort_by: str = "resources",
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Самые медленные или ресурсоёмкие запросы из pg_stat_statements.

    Args:
        db: Сервис доступа к базе данных.
        sort_by: Критерий: 'resources', 'mean_time' или 'total_time'.
        limit: Максимум строк (по умолчанию 10).

    Returns:
        Строки pg_stat_statements.

    Raises:
        InvalidSortCriteriaError: Если указан недопустимый sort_by.
        PgStatStatementsNotInstalledError: Если pg_stat_statements не установлено.
    """
    calc = TopQueriesCalc(sql_driver=db.sql_driver, connection_id=db.connection_id)

    if sort_by == "resources":
        return await calc.get_top_resource_queries(limit=limit)
    if sort_by in {"mean_time", "total_time"}:
        return await calc.get_top_queries_by_time(
            limit=limit,
            sort_by="mean" if sort_by == "mean_time" else "total",
        )
    raise InvalidSortCriteriaError(sort_by)
```

- [ ] **Step 7: Тулы: output и ToolResult, английские docstring**

Заменить `src/postgres_fastmcp/tools/definitions.py` целиком:

```python
"""Все MCP-тулы сервера: тонкие функции над доменными сервисами.

Каждая функция достаёт DbAccessService из lifespan-контекста и делегирует
домену; описания и аннотации задаются при регистрации в ``tools.registry``.
Docstring функций тулов на английском: FastMCP может показать их агенту.
Тулы, которые возвращают строки, принимают ``output`` и отдают ToolResult
через ``tools.rendering``.
"""

from typing import Annotated, Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from fastmcp.tools import ToolResult
from pydantic import Field

from postgres_fastmcp.app.context import get_db
from postgres_fastmcp.domains import querying, top_queries
from postgres_fastmcp.domains.catalog.service import CatalogService
from postgres_fastmcp.domains.explain.service import ExplainService
from postgres_fastmcp.domains.health.database_health import DatabaseHealthAnalyzer, HealthType
from postgres_fastmcp.domains.index_tuning.service import IndexAnalysisService
from postgres_fastmcp.domains.querying import SUCCESS_NO_ROWS
from postgres_fastmcp.tools.params import (
    HealthTypesParam,
    IndexQueriesParam,
    ObjectTypeParam,
    OutputParam,
    TopQueriesLimitParam,
    TopQueriesSortByParam,
)
from postgres_fastmcp.tools.rendering import rows_result, sections_result


async def execute_sql(
    sql: Annotated[
        str,
        Field(description="SQL statement to execute, e.g. 'SELECT id, name FROM users WHERE active LIMIT 50'."),
    ],
    output: OutputParam = "table",
    ctx: Context = CurrentContext(),
) -> ToolResult:
    """Execute a SQL statement and return the result rows."""
    rows = await querying.execute_sql(get_db(ctx), sql)
    if rows is None:
        return rows_result([], output, title=SUCCESS_NO_ROWS)
    return rows_result(rows, output)


async def explain_query(
    sql: Annotated[str, Field(description="SQL query to explain.")],
    *,
    analyze: Annotated[
        bool,
        Field(default=False, description="If True, actually run the query for real stats."),
    ] = False,
    hypothetical_indexes: Annotated[
        list[dict[str, Any]] | None,
        Field(default=None, description="Optional hypothetical indexes to simulate via hypopg."),
    ] = None,
    ctx: Context = CurrentContext(),
) -> str:
    """Show the execution plan of a SQL query: plain, analyze or with hypothetical indexes."""
    service = ExplainService(db=get_db(ctx))
    return await service.explain(sql, analyze=analyze, hypothetical_indexes=hypothetical_indexes)


async def list_objects(
    schema_name: Annotated[str, Field(description="Schema name to inspect.")],
    object_type: ObjectTypeParam = "table",
    output: OutputParam = "table",
    ctx: Context = CurrentContext(),
) -> ToolResult:
    """List objects of the given kind in a schema."""
    service = CatalogService(db=get_db(ctx))
    return rows_result(await service.list_objects(schema_name=schema_name, object_type=object_type), output)


async def get_object_details(
    schema_name: Annotated[str, Field(description="Schema name.")],
    object_name: Annotated[str, Field(description="Object name.")],
    object_type: ObjectTypeParam = "table",
    output: OutputParam = "table",
    ctx: Context = CurrentContext(),
) -> ToolResult:
    """Show object details: columns, constraints, indexes and other metadata."""
    service = CatalogService(db=get_db(ctx))
    details = await service.get_object_details(
        schema_name=schema_name, object_name=object_name, object_type=object_type
    )
    header: dict[str, Any] = {"schema": schema_name, "name": object_name, "type": object_type}
    sections: dict[str, list[dict[str, Any]]] = {}
    for key, value in details.items():
        if isinstance(value, list):
            sections[key] = value
        elif isinstance(value, dict):
            header.update(value)
        else:
            header[key] = value
    return sections_result(sections, output, header=header)


async def list_schemas(output: OutputParam = "table", ctx: Context = CurrentContext()) -> ToolResult:
    """List database schemas."""
    service = CatalogService(db=get_db(ctx))
    return rows_result(await service.list_schemas(), output)


async def analyze_db_health(
    health_type: HealthTypesParam = (HealthType.ALL,),
    ctx: Context = CurrentContext(),
) -> str:
    """Run database health checks and return a text report."""
    health_tool = DatabaseHealthAnalyzer(get_db(ctx).sql_driver)
    return await health_tool.health(health_type=",".join(health_type))


async def get_top_queries(
    sort_by: TopQueriesSortByParam = "resources",
    limit: TopQueriesLimitParam = 10,
    output: OutputParam = "table",
    ctx: Context = CurrentContext(),
) -> ToolResult:
    """Report top queries from pg_stat_statements by the chosen criteria."""
    rows = await top_queries.get_top_queries(get_db(ctx), sort_by=sort_by, limit=limit)
    return rows_result(rows, output)


async def analyze_query_indexes(
    queries: IndexQueriesParam,
    max_index_size_mb: Annotated[
        int,
        Field(default=10000, ge=1, description="Max recommended index size (MB)."),
    ] = 10000,
    ctx: Context = CurrentContext(),
) -> dict[str, Any]:
    """Recommend indexes for a list of queries (cost-based DTA, requires hypopg)."""
    service = IndexAnalysisService(db=get_db(ctx))
    return await service.analyze_query_indexes(queries=queries, max_index_size_mb=max_index_size_mb)


async def analyze_workload_indexes(
    max_index_size_mb: Annotated[
        int,
        Field(default=10000, ge=1, description="Max recommended index size (MB)."),
    ] = 10000,
    ctx: Context = CurrentContext(),
) -> dict[str, Any]:
    """Recommend indexes for the aggregated database workload (cost-based DTA, requires hypopg)."""
    service = IndexAnalysisService(db=get_db(ctx))
    return await service.analyze_workload_indexes(max_index_size_mb=max_index_size_mb)
```

В `src/postgres_fastmcp/tools/registry.py` в спеках `execute_sql`, `list_objects`, `get_object_details` (`_basic_specs`), `list_schemas` и `get_top_queries` (`_full_specs`) сразу после строки `"name": "<tool>",` добавить:

```python
            "output_schema": None,
```

- [ ] **Step 8: Юнит-тесты зелёные**

Run: `<env> uv run pytest tests/unit -q`
Expected: `626 passed`.

Run: `grep -rn "install_pg_stat_statements_message\|\"status\": \"success\"" src tests`
Expected: пусто.

- [ ] **Step 9: Интеграционные тесты на output="json" и structured_content**

Заменить `tests/integration/test_tools_integration.py` целиком:

```python
# mypy: ignore-errors
"""Integration tests for MCP tools with real DB: Client(mcp).call_tool inside server lifespan.

Covers all tools and main call variants. Row tools are called with output="json"
and checked through structured_content ({"rows": [...], "row_count": N}):
- list_schemas (json, default table)
- execute_sql (SELECT with rows, SELECT returning 0 rows)
- list_objects (object_type: table, view, sequence, extension, 'Tables')
- get_object_details
- explain_query (default, analyze=True)
- analyze_db_health (all, list with any case)
- analyze_workload_indexes
- analyze_query_indexes
- get_top_queries (sort_by: total_time, mean_time, resources, avg)
"""

import pytest
from fastmcp import Client

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.server import create_server


def _tool_content(result: object) -> object:
    """Extract content from MCP call_tool result."""
    return result.data if hasattr(result, "data") else getattr(result, "content", None)


def _rows(result: object) -> list[dict]:
    """Rows of a tool called with output='json': structured_content is {'rows': [...], 'row_count': N}."""
    data = result.structured_content
    assert isinstance(data, dict), data
    assert data["row_count"] == len(data["rows"])
    return data["rows"]


@pytest.mark.asyncio
async def test_tools_list_schemas(integration_settings: Settings) -> None:
    """list_schemas returns rows with schema_name; public is present."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool("list_schemas", {"output": "json"})
    assert result.is_error is False
    assert "public" in {row["schema_name"] for row in _rows(result)}


@pytest.mark.asyncio
async def test_tools_list_schemas_table(integration_settings: Settings) -> None:
    """list_schemas default output is a Markdown table without structured content."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool("list_schemas", {})
    assert result.is_error is False
    assert result.structured_content is None
    text = result.content[0].text
    assert text.startswith("| schema_name |")
    assert "public" in text


@pytest.mark.asyncio
async def test_tools_execute_sql(integration_settings: Settings) -> None:
    """execute_sql tool runs SELECT 1 and returns the row."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool("execute_sql", {"sql": "SELECT 1 AS num", "output": "json"})
    assert result.is_error is False
    assert _rows(result) == [{"num": 1}]


@pytest.mark.asyncio
async def test_tools_execute_sql_empty_result(integration_settings: Settings) -> None:
    """execute_sql with query returning 0 rows returns an empty row list / '0 rows.'."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        as_json = await client.call_tool("execute_sql", {"sql": "SELECT 1 WHERE FALSE", "output": "json"})
        table = await client.call_tool("execute_sql", {"sql": "SELECT 1 WHERE FALSE"})
    assert as_json.is_error is False
    assert _rows(as_json) == []
    assert table.content[0].text == "0 rows."


@pytest.mark.asyncio
@pytest.mark.parametrize("object_type", ["table", "view", "sequence", "extension", "Tables"])
async def test_tools_list_objects(integration_settings: Settings, object_type: str) -> None:
    """list_objects returns a row list (may be empty) for each object type, plural/any case accepted."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "list_objects",
            {"schema_name": "public", "object_type": object_type, "output": "json"},
        )
    assert result.is_error is False
    assert isinstance(_rows(result), list)


@pytest.mark.asyncio
async def test_tools_get_object_details(integration_settings: Settings) -> None:
    """get_object_details returns header fields and column rows for information_schema.tables."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_object_details",
            {
                "schema_name": "information_schema",
                "object_name": "tables",
                "object_type": "view",
                "output": "json",
            },
        )
    assert result.is_error is False
    data = result.structured_content
    assert data["schema"] == "information_schema"
    assert data["name"] == "tables"
    assert "table_name" in {column["column"] for column in data["columns"]}


@pytest.mark.asyncio
async def test_tools_explain_query(integration_settings: Settings) -> None:
    """explain_query tool returns plan for SELECT 1 (default plain)."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool("explain_query", {"sql": "SELECT 1"})
    assert result.is_error is False
    content = _tool_content(result)
    assert content is not None
    assert "Plan" in str(content) or "plan" in str(content).lower() or "Result" in str(content)


@pytest.mark.asyncio
async def test_tools_explain_query_analyze(integration_settings: Settings) -> None:
    """explain_query with analyze=True runs query and returns plan with actual stats."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool("explain_query", {"sql": "SELECT 1", "analyze": True})
    assert result.is_error is False
    content = _tool_content(result)
    assert content is not None
    assert "Plan" in str(content) or "plan" in str(content).lower() or "Result" in str(content)


@pytest.mark.asyncio
async def test_tools_analyze_db_health_all(integration_settings: Settings) -> None:
    """analyze_db_health with health_type=all returns report string."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool("analyze_db_health", {"health_type": "all"})
    assert result.is_error is False
    content = _tool_content(result)
    assert content is not None
    assert isinstance(content, str) and len(content) > 0


@pytest.mark.asyncio
async def test_tools_analyze_db_health_single(integration_settings: Settings) -> None:
    """analyze_db_health with a list in any case (['Connection']) returns report."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool("analyze_db_health", {"health_type": ["Connection"]})
    assert result.is_error is False
    content = _tool_content(result)
    assert content is not None
    assert isinstance(content, str) and len(content) > 0


@pytest.mark.asyncio
async def test_tools_analyze_workload_indexes_dta(integration_settings: Settings) -> None:
    """analyze_workload_indexes returns dict (may contain error if hypopg missing)."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "analyze_workload_indexes",
            {"max_index_size_mb": 100},
        )
    assert result.is_error is False
    content = _tool_content(result)
    assert content is not None
    assert isinstance(content, dict)
    # Either recommendations or error (e.g. hypopg not installed)
    assert "recommendations" in content or "error" in content


@pytest.mark.asyncio
async def test_tools_analyze_query_indexes_dta(integration_settings: Settings) -> None:
    """analyze_query_indexes returns dict for given queries."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "analyze_query_indexes",
            {"queries": ["SELECT 1", "SELECT 2"], "max_index_size_mb": 100},
        )
    assert result.is_error is False
    content = _tool_content(result)
    assert content is not None
    assert isinstance(content, dict)
    assert "recommendations" in content or "error" in content


@pytest.mark.asyncio
@pytest.mark.parametrize("sort_by", ["total_time", "mean_time", "resources", "avg"])
async def test_tools_get_top_queries(integration_settings: Settings, sort_by: str) -> None:
    """get_top_queries returns at most limit rows, or the install hint when pg_stat_statements is missing."""
    mcp = create_server(integration_settings)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_top_queries",
            {"sort_by": sort_by, "limit": 5, "output": "json"},
            raise_on_error=False,
        )
    if result.is_error:
        assert "pg_stat_statements" in result.content[0].text
    else:
        assert len(_rows(result)) <= 5
```

В `tests/integration/test_write_mode.py` в `test_execute_sql_write_persists` блок от `assert insert.is_error is False` до `assert "alpha" in ...` заменить на:

```python
        assert insert.is_error is False
        assert "success" in insert.content[0].text.lower()

        select = await client.call_tool(
            "execute_sql",
            {"sql": "SELECT v FROM wm_write_test WHERE id = 1", "output": "json"},
        )
        assert select.is_error is False
        assert select.structured_content == {"rows": [{"v": "alpha"}], "row_count": 1}
```

В `tests/integration/test_top_queries_integration.py`:

После импорта `from postgres_fastmcp.domains.top_queries import ...` добавить:

```python
from postgres_fastmcp.shared.errors import PgStatStatementsNotInstalledError
```

В `test_get_top_queries_integration` блок от `total_result = await get_top_queries(...)` до `assert has_cross_join or ...` заменить на:

```python
        total_rows = await get_top_queries(db_service_full, sort_by="total_time", limit=10)
        mean_rows = await get_top_queries(db_service_full, sort_by="mean_time", limit=10)
        resource_rows = await get_top_queries(db_service_full, sort_by="resources", limit=2)

        assert 0 < len(total_rows) <= 10
        assert 0 < len(mean_rows) <= 10
        assert len(resource_rows) <= 2
        assert {"query", "calls", "rows"} <= set(total_rows[0])

        total_text = " ".join(row["query"] for row in total_rows)
        has_cross_join = "CROSS JOIN" in total_text
        has_value_gt_500 = "value > 500" in total_text
        has_count = "COUNT(*)" in total_text
        assert has_cross_join or has_value_gt_500 or has_count, "None of our test queries appeared in the results"
```

В `test_extension_not_available` docstring заменить на `"""When pg_stat_statements is not installed, a user-facing error carries installation instructions."""`, а три последние строки (вызов и два `assert`) — на:

```python
    with pytest.raises(PgStatStatementsNotInstalledError, match="CREATE EXTENSION pg_stat_statements"):
        await calc.get_top_queries_by_time()
```

В `tests/README.md` строки таблицы «Integration coverage» для пяти тулов заменить на:

```markdown
| `list_schemas` | output=json, default table |
| `execute_sql` | SELECT with rows, SELECT returning 0 rows (json and table) |
| `list_objects` | object_type: table, view, sequence, extension, 'Tables' |
| `get_object_details` | information_schema.tables view (json) |
```

```markdown
| `analyze_db_health` | health_type=all, health_type=['Connection'] |
```

```markdown
| `get_top_queries` | sort_by: total_time, mean_time, resources, avg; limit=5; output=json |
```

и в «Testing Conventions» пример хелпера `` `parse_schema_names` `` заменить на `` `_rows` (structured_content of output="json") ``.

- [ ] **Step 10: Интеграция собирается**

Run: `<env> uv run pytest tests/integration -q`
Expected: `78 skipped` (Docker недоступен; до задачи было `72 skipped`: `test_list_objects` и `test_get_top_queries` стали параметризованными). Если Docker есть — все PASS.

Run: `grep -rn "parse_schema_names\|_schema_name_from_obj" tests`
Expected: пусто.

- [ ] **Step 11: Линтеры и коммит**

Run: `uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format`
Expected: ноль ошибок.

```bash
git add src/postgres_fastmcp/tools/definitions.py src/postgres_fastmcp/tools/registry.py src/postgres_fastmcp/domains/querying.py src/postgres_fastmcp/domains/top_queries.py src/postgres_fastmcp/shared/errors.py tests/unit/tools/test_definitions.py tests/unit/tools/test_registry.py tests/unit/domains/test_querying.py tests/unit/domains/test_top_queries.py tests/unit/shared/test_errors.py tests/integration/test_tools_integration.py tests/integration/test_write_mode.py tests/integration/test_top_queries_integration.py tests/README.md
git commit -m "feat(tools): return row tools as a Markdown table by default and JSON on request"
```

---

### Task 5: Бюджет ответа: ResponseBudgetMiddleware, настройка и подключение

**Files:**
- Create: `src/postgres_fastmcp/app/middleware/__init__.py`, `src/postgres_fastmcp/app/middleware/response_budget.py`
- Modify: `src/postgres_fastmcp/shared/errors.py` (новый `ResponseTooLargeError` перед `ConnectionFailedError`)
- Modify: `src/postgres_fastmcp/app/config/server.py:84-86` (поле `response_max_tokens`)
- Modify: `src/postgres_fastmcp/app/config/__init__.py:12-13` (пример `config.json` в docstring)
- Modify: `src/postgres_fastmcp/app/server.py:10-13, 30, 53` (импорт, docstring, middleware первым)
- Test: `tests/unit/app/test_response_budget.py` (новый), `tests/unit/shared/test_errors.py`

**Interfaces:**
- Consumes: `create_server(settings, *, auth, extra_providers, extra_middleware)`; `_SAMPLES` в `tests/unit/shared/test_errors.py`; `rows_result` через тул `execute_sql` (Task 4).
- Produces:
  - `BYTES_PER_TOKEN = 3`, `estimate_tokens(result: ToolResult) -> int` = `ceil(sum(len(text.encode()) по TextContent) / 3)`; `structured_content` не учитывается.
  - `ResponseBudgetMiddleware(max_tokens: int)` с `on_call_tool`, бросает `ResponseTooLargeError(tokens, max_tokens)`.
  - `ResponseTooLargeError(tokens: int, max_tokens: int)`, атрибуты `.tokens`, `.max_tokens`, текст из спеки (раздел 3).
  - `ServerSettings.response_max_tokens: int = 20000` (`ge=1000`, env `MCP_RESPONSE_MAX_TOKENS`, у `ServerSettings` `env_prefix="MCP_"`).

- [ ] **Step 1: Написать тесты бюджета**

Создать `tests/unit/app/test_response_budget.py`:

```python
"""Тесты бюджета ответа: оценка токенов, middleware и его место в create_server."""

from unittest.mock import AsyncMock, MagicMock

import mcp.types as mt
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools import ToolResult
from mcp.types import TextContent
from pydantic import ValidationError

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.config.server import ServerSettings
from postgres_fastmcp.app.middleware.response_budget import (
    BYTES_PER_TOKEN,
    ResponseBudgetMiddleware,
    estimate_tokens,
)
from postgres_fastmcp.app.server import create_server
from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.shared.errors import ResponseTooLargeError


def _result(text: str, structured: dict | None = None) -> ToolResult:
    return ToolResult(content=[TextContent(type="text", text=text)], structured_content=structured)


def _context() -> MiddlewareContext[mt.CallToolRequestParams]:
    return MiddlewareContext(message=mt.CallToolRequestParams(name="execute_sql", arguments={}))


def test_estimate_tokens_rounds_bytes_up() -> None:
    assert BYTES_PER_TOKEN == 3
    assert estimate_tokens(_result("abcd")) == 2
    assert estimate_tokens(_result("abc")) == 1
    # Кириллица: 2 байта на символ в UTF-8
    assert estimate_tokens(_result("ёж")) == 2


def test_estimate_tokens_ignores_structured_content() -> None:
    structured = {"rows": [{"v": "x" * 10_000}], "row_count": 1}
    assert estimate_tokens(_result("abc", structured)) == 1


async def test_small_response_passes() -> None:
    result = _result("x" * 30)
    middleware = ResponseBudgetMiddleware(max_tokens=10)
    assert await middleware.on_call_tool(_context(), AsyncMock(return_value=result)) is result


async def test_large_response_raises_with_numbers() -> None:
    middleware = ResponseBudgetMiddleware(max_tokens=10)
    with pytest.raises(ResponseTooLargeError, match=r"~11 tokens, the limit is 10\. Refine the request") as exc_info:
        await middleware.on_call_tool(_context(), AsyncMock(return_value=_result("x" * 31)))
    assert exc_info.value.tokens == 11
    assert exc_info.value.max_tokens == 10


def test_response_max_tokens_default_and_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_RESPONSE_MAX_TOKENS", raising=False)
    assert ServerSettings().response_max_tokens == 20000
    monkeypatch.setenv("MCP_RESPONSE_MAX_TOKENS", "5000")
    assert ServerSettings().response_max_tokens == 5000
    with pytest.raises(ValidationError):
        ServerSettings(response_max_tokens=999)


def _server_with_rows(monkeypatch: pytest.MonkeyPatch, rows: list[RowResult], max_tokens: int):  # noqa: ANN202
    """create_server с подменённым DbAccessService: execute_sql получает заданные строки."""

    class FakeDb:
        def __init__(self, cfg: object) -> None:
            self.write_mode = False
            self.sql_driver = MagicMock()
            self.sql_driver.execute = AsyncMock(return_value=rows)

        async def close(self) -> None:
            return None

    monkeypatch.setattr("postgres_fastmcp.app.lifespan.DbAccessService", FakeDb)
    settings = Settings()
    settings.database = settings.database.model_copy(update={"access_mode": AccessMode.FULL})
    settings.server = settings.server.model_copy(update={"response_max_tokens": max_tokens})
    return create_server(settings)


async def test_create_server_rejects_large_execute_sql_result(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [RowResult(cells={"id": i, "name": f"row-{i:04d}-" + "x" * 40}) for i in range(200)]
    async with Client(_server_with_rows(monkeypatch, rows, max_tokens=1000)) as client:
        with pytest.raises(ToolError, match=r"Response is too large: ~\d+ tokens, the limit is 1000\. Refine the request"):
            await client.call_tool("execute_sql", {"sql": "SELECT id, name FROM t"})


async def test_create_server_passes_small_execute_sql_result(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [RowResult(cells={"id": 1, "name": "a"})]
    async with Client(_server_with_rows(monkeypatch, rows, max_tokens=1000)) as client:
        result = await client.call_tool("execute_sql", {"sql": "SELECT id, name FROM t"})
    assert result.content[0].text == "| id | name |\n| --- | --- |\n| 1 | a |\n\n1 rows."
```

В `tests/unit/shared/test_errors.py` в конец `_SAMPLES` добавить:

```python
    "ResponseTooLargeError": lambda: errors.ResponseTooLargeError(25000, 20000),
```

и в параметры `test_correctable_error_ends_with_hint` после строки с `PgStatStatementsNotInstalledError` добавить:

```python
        ("ResponseTooLargeError", "Refine the request: add WHERE or LIMIT"),
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `<env> uv run pytest tests/unit/app/test_response_budget.py tests/unit/shared/test_errors.py -q`
Expected: ошибка сбора `No module named 'postgres_fastmcp.app.middleware'`; в `test_errors.py` — `AttributeError: ... ResponseTooLargeError`.

- [ ] **Step 3: Ошибка ResponseTooLargeError**

В `src/postgres_fastmcp/shared/errors.py` перед классом `ConnectionFailedError` добавить:

```python
class ResponseTooLargeError(UserFacingError):
    """Ответ тула больше бюджета токенов: агенту нужно сузить запрос."""

    def __init__(self, tokens: int, max_tokens: int) -> None:
        """Инициализация с оценкой размера ответа и лимитом.

        Args:
            tokens: Оценка размера ответа в токенах.
            max_tokens: Лимит ответа в токенах.
        """
        message = (
            f"Response is too large: ~{tokens} tokens, the limit is {max_tokens}. Refine the request: "
            "add WHERE or LIMIT, select only the needed columns, aggregate (count, group by), "
            "or narrow the schema/object filter."
        )
        super().__init__(message)
        self.tokens = tokens
        self.max_tokens = max_tokens
```

- [ ] **Step 4: Middleware**

Создать `src/postgres_fastmcp/app/middleware/__init__.py`:

```python
"""Middleware сервера."""
```

Создать `src/postgres_fastmcp/app/middleware/response_budget.py`:

```python
"""Бюджет ответа тула в токенах: слишком большой ответ заменяется ошибкой «уточните запрос».

В отличие от обрезки, агент не получает оборванную таблицу или битый JSON,
а узнаёт, что запрос надо сузить.
"""

import math

import mcp.types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult
from mcp.types import TextContent

from postgres_fastmcp.shared.errors import ResponseTooLargeError


# Грубая оценка без токенайзера: JSON и латиница ~3.5-4 байта на токен, кириллица ~2-3.
# Делитель 3 слегка завышает оценку: лимит срабатывает раньше, а не позже.
BYTES_PER_TOKEN = 3


def estimate_tokens(result: ToolResult) -> int:
    """Оценить размер ответа тула в токенах по тексту, который читает модель.

    structured_content не учитывается: в режиме json его копия уже лежит в тексте.

    Args:
        result: Результат тула.

    Returns:
        Оценка числа токенов (округление вверх).
    """
    size = sum(len(block.text.encode()) for block in result.content if isinstance(block, TextContent))
    return math.ceil(size / BYTES_PER_TOKEN)


class ResponseBudgetMiddleware(Middleware):
    """Отклоняет ответы тулов больше лимита токенов (ServerSettings.response_max_tokens)."""

    def __init__(self, max_tokens: int) -> None:
        """Инициализировать middleware.

        Args:
            max_tokens: Предел ответа тула в токенах.
        """
        self._max_tokens = max_tokens

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Выполнить тул и проверить размер ответа.

        Args:
            context: Контекст вызова.
            call_next: Следующий обработчик цепочки.

        Returns:
            Результат тула, если он помещается в лимит.

        Raises:
            ResponseTooLargeError: Если ответ больше лимита токенов.
        """
        result = await call_next(context)
        tokens = estimate_tokens(result)
        if tokens > self._max_tokens:
            raise ResponseTooLargeError(tokens, self._max_tokens)
        return result
```

- [ ] **Step 5: Настройка response_max_tokens**

В `src/postgres_fastmcp/app/config/server.py` после поля `health_endpoint_enabled` добавить:

```python
    response_max_tokens: int = Field(
        default=20000,
        ge=1000,
        description="Предел ответа тула в токенах (MCP_RESPONSE_MAX_TOKENS); больший ответ заменяется ошибкой",
    )
```

В docstring `src/postgres_fastmcp/app/config/__init__.py` в примере `config.json` блок `server` закончить так:

```text
    "workers": 1,
    "health_endpoint_enabled": true,
    "response_max_tokens": 20000
  },
```

- [ ] **Step 6: Подключить middleware первым**

В `src/postgres_fastmcp/app/server.py` после импорта `build_lifespan` добавить:

```python
from postgres_fastmcp.app.middleware.response_budget import ResponseBudgetMiddleware
```

В docstring `create_server` строку про `extra_middleware` заменить на:

```text
        extra_middleware: Дополнительные middleware (встают после встроенных; бюджет ответа
            стоит первым и проверяет и их результат).
```

Перед `mcp.add_middleware(TimingMiddleware())` добавить:

```python
    # Первым = внешним: бюджет проверяет ровно то, что уходит клиенту, включая результат extra_middleware
    mcp.add_middleware(ResponseBudgetMiddleware(settings.server.response_max_tokens))
```

`LoggingMiddleware` внутри бюджета запишет вызов как успешный, даже если бюджет потом его отклонит; спека (раздел 3) это допускает.

- [ ] **Step 7: Тесты зелёные**

Run: `<env> uv run pytest tests/unit/app/test_response_budget.py -q`
Expected: `7 passed`.

Run: `<env> uv run pytest tests/unit -q`
Expected: `635 passed`.

- [ ] **Step 8: Линтеры и коммит**

Run: `uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format`
Expected: ноль ошибок.

```bash
git add src/postgres_fastmcp/app/middleware/__init__.py src/postgres_fastmcp/app/middleware/response_budget.py src/postgres_fastmcp/shared/errors.py src/postgres_fastmcp/app/config/server.py src/postgres_fastmcp/app/config/__init__.py src/postgres_fastmcp/app/server.py tests/unit/app/test_response_budget.py tests/unit/shared/test_errors.py
git commit -m "feat(server): replace tool responses above the token budget with a refine-the-request error"
```

---

### Task 6: Правила для авторов тулов и регрессия схемы

**Files:**
- Create: `src/postgres_fastmcp/tools/AGENTS.md`, `src/postgres_fastmcp/tools/CLAUDE.md`
- Modify: `AGENTS.md:947` (раздел «Tool Definitions»: ссылка на правила слоя тулов)
- Test: `tests/unit/tools/test_tool_schema.py` (новый)

**Interfaces:**
- Consumes: всё из Task 1–5; `create_server`, `FunctionTool.fn`, `Tool.to_mcp_tool()`.
- Produces: `_TOOLS_LIST_BUDGET_CHARS` в `tests/unit/tools/test_tool_schema.py` — потолок размера `tools/list` в режиме FULL.

- [ ] **Step 1: Написать тесты схемы с временным нулевым бюджетом**

Создать `tests/unit/tools/test_tool_schema.py` (константа бюджета пока `0`, её значение появится в Step 3):

```python
"""Регрессионные тесты схем MCP-тулов, как их видит клиент (tools/list)."""

import inspect
import json
import re
from typing import Annotated, get_args, get_origin

import pytest
from fastmcp.tools import FunctionTool, Tool
from pydantic.fields import FieldInfo

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.server import create_server
from postgres_fastmcp.domains.index_tuning.models import MAX_NUM_INDEX_TUNING_QUERIES
from postgres_fastmcp.shared.enums import AccessMode


# Бюджет на весь tools/list в режиме FULL: размер после изменений плюс 15 %.
# Пересчёт: команда в docs/superpowers/plans/2026-09-26-03-agent-output-budget.md, Task 6.
_TOOLS_LIST_BUDGET_CHARS = 0

_ROW_TOOLS = ("execute_sql", "list_objects", "get_object_details", "list_schemas", "get_top_queries")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")


async def _list_tools() -> list[Tool]:
    settings = Settings()
    settings.database = settings.database.model_copy(update={"access_mode": AccessMode.FULL})
    return list(await create_server(settings).list_tools())


def _wire(tool: Tool) -> dict:
    return tool.to_mcp_tool().model_dump(by_alias=True, exclude_none=True)


def _field_description(annotation: object, default: object) -> str | None:
    """Описание из Field(), заданного по умолчанию или в Annotated."""
    if isinstance(default, FieldInfo):
        return default.description
    if get_origin(annotation) is Annotated:
        for meta in get_args(annotation)[1:]:
            if isinstance(meta, FieldInfo) and meta.description:
                return meta.description
    return None


@pytest.fixture
async def tools() -> dict[str, Tool]:
    return {tool.name: tool for tool in await _list_tools()}


async def test_field_descriptions_reach_input_schema(tools: dict[str, Tool]) -> None:
    for tool in tools.values():
        assert isinstance(tool, FunctionTool), tool.name
        properties = tool.parameters.get("properties", {})
        for name, param in inspect.signature(tool.fn).parameters.items():
            expected = _field_description(param.annotation, param.default)
            if expected is not None:
                assert properties[name].get("description") == expected, f"{tool.name}.{name}"


async def test_queries_bounds_in_schema(tools: dict[str, Tool]) -> None:
    queries = tools["analyze_query_indexes"].parameters["properties"]["queries"]
    assert queries["minItems"] == 1
    assert queries["maxItems"] == MAX_NUM_INDEX_TUNING_QUERIES


async def test_health_type_accepts_string_or_list(tools: dict[str, Tool]) -> None:
    health_type = tools["analyze_db_health"].parameters["properties"]["health_type"]
    assert {branch["type"] for branch in health_type["anyOf"]} == {"array", "string"}


@pytest.mark.parametrize(
    ("tool", "param"),
    [
        ("list_objects", "object_type"),
        ("get_object_details", "object_type"),
        ("get_top_queries", "sort_by"),
        *((name, "output") for name in _ROW_TOOLS),
    ],
)
async def test_normalized_params_are_not_enums(tools: dict[str, Tool], tool: str, param: str) -> None:
    """Сервер принимает любой регистр и синонимы, поэтому схема не должна быть строже: без enum."""
    schema = tools[tool].parameters["properties"][param]
    assert schema["type"] == "string"
    assert "enum" not in schema


async def test_top_queries_limit_has_lower_bound_only(tools: dict[str, Tool]) -> None:
    """limit > 100 урезается сервером, поэтому maximum в схеме нет."""
    limit = tools["get_top_queries"].parameters["properties"]["limit"]
    assert limit["minimum"] == 1
    assert "maximum" not in limit


async def test_row_tools_do_not_wrap_output(tools: dict[str, Tool]) -> None:
    for name in _ROW_TOOLS:
        assert tools[name].output_schema is None, name


async def test_everything_the_agent_sees_is_english(tools: dict[str, Tool]) -> None:
    for tool in tools.values():
        assert not _CYRILLIC.search(json.dumps(_wire(tool), ensure_ascii=False)), tool.name
        assert isinstance(tool, FunctionTool)
        assert not _CYRILLIC.search(tool.fn.__doc__ or ""), f"{tool.name} docstring"


async def test_tools_list_fits_budget(tools: dict[str, Tool]) -> None:
    size = sum(len(json.dumps(_wire(tool))) for tool in tools.values())
    assert size <= _TOOLS_LIST_BUDGET_CHARS, size
```

- [ ] **Step 2: Запустить: падает только бюджет**

Run: `<env> uv run pytest tests/unit/tools/test_tool_schema.py -q`
Expected: `1 failed, 14 passed`; падает `test_tools_list_fits_budget` с `AssertionError: <size>`. Любой другой провал — дефект Task 3/4 (описание не дошло до схемы, `enum` в схеме, кириллица в `tools/list` или в docstring тула), его надо исправить в коде, а не в тесте.

- [ ] **Step 3: Измерить размер tools/list и зафиксировать порог**

Run:
```bash
<env> uv run python -c "import asyncio, json, math; from postgres_fastmcp.app.config import Settings; from postgres_fastmcp.app.server import create_server; from postgres_fastmcp.shared.enums import AccessMode; s = Settings(); s.database = s.database.model_copy(update={'access_mode': AccessMode.FULL}); tools = asyncio.run(create_server(s).list_tools()); size = sum(len(json.dumps(t.to_mcp_tool().model_dump(by_alias=True, exclude_none=True))) for t in tools); print(size, math.ceil(size * 1.15))" 2>/dev/null
```
Expected: два числа — размер и `ceil(size * 1.15)`. При тексте ровно как в этом плане: `9208 10590` (до плана размер был 8360). Если описания отличаются от плана, числа будут другими; брать вывод команды, а не эти значения.

В `tests/unit/tools/test_tool_schema.py` заменить `_TOOLS_LIST_BUDGET_CHARS = 0` на второе число из вывода, например:

```python
_TOOLS_LIST_BUDGET_CHARS = 10_590
```

Run: `<env> uv run pytest tests/unit -q`
Expected: `650 passed`.

- [ ] **Step 4: Правила для авторов тулов**

Создать `src/postgres_fastmcp/tools/AGENTS.md`:

```markdown
# Rules for the MCP tools layer (`src/postgres_fastmcp/tools/`)

These rules add to the root `AGENTS.md`; where they differ, this file wins for `tools/`.

## Language

Everything an agent or an external system sees is written in **English**:

- tool descriptions in `registry.py`;
- `Field(description=...)` of every tool parameter;
- docstrings of tool functions in `definitions.py` (FastMCP may show them to the client);
- error messages and log messages.

Russian is allowed only for comments and module/helper docstrings that never reach the agent.
`tests/unit/tools/test_tool_schema.py` fails on Cyrillic in `tools/list` and in tool docstrings;
`tests/unit/shared/test_errors.py` fails on Cyrillic in any `UserFacingError`.

## Parameters

- Declare constraints with `Annotated[Type, Field(...)]`: `ge`/`le`, `min_length`/`max_length`,
  `description`. Every user-facing parameter has an English `description` that lists the accepted
  forms and gives an example.
- Shared parameter types live in `params.py` (`ObjectTypeParam`, `HealthTypesParam`,
  `TopQueriesSortByParam`, `TopQueriesLimitParam`, `IndexQueriesParam`, `OutputParam`). Reuse them
  instead of redeclaring a parameter.
- Shared aliases are plain assignments (`X = Annotated[...]`), not PEP 695 `type X = ...`: with a
  `type` alias the function docstring overrides the `Field` description.
- Do not put `Field` on dependency-injected parameters (`ctx: Context = CurrentContext()`).

## Normalize input instead of rejecting it

- If the input has one obvious meaning (`'Tables'`, `'JSON'`, `'index,vacuum'`, `limit=500`),
  convert it to the canonical value in a `BeforeValidator` / `AfterValidator`.
- Reject only input that cannot be converted, with a `UserFacingError` that ends with a hint:
  `Did you mean '...'?` and the list of accepted values.
- The JSON Schema must not be stricter than what the server accepts, or the client rejects valid
  input before the call. Widen it with `Field(json_schema_extra=callable)` (see `_any_string`,
  `_string_or_list` in `params.py`). Do not use `WithJsonSchema`: it replaces the whole schema and
  drops `Field(description=...)`.

## Tools that return rows

- Take `output: OutputParam = "table"` and return `ToolResult` built by `rows_result()` or
  `sections_result()` from `rendering.py`.
- Register the tool with `"output_schema": None`; otherwise FastMCP wraps the answer into
  `{"result": ...}` and duplicates it.
- Never return Markdown and a JSON copy of the same data in one response.
- Do not cut rows or wrap SQL in `LIMIT`: `ResponseBudgetMiddleware` (`app/middleware/response_budget.py`)
  replaces an answer above `MCP_RESPONSE_MAX_TOKENS` with an error that asks the agent to refine the request.

## Errors

- Errors meant for the agent inherit `UserFacingError` (`shared/errors.py`); any other exception
  is hidden by `mask_error_details=True` and the agent sees only `Error calling tool`.
- A new `UserFacingError` subclass needs a sample in `tests/unit/shared/test_errors.py::_SAMPLES`.

## Schema budget

`tests/unit/tools/test_tool_schema.py::test_tools_list_fits_budget` caps the size of `tools/list`
in FULL mode. If a deliberate change grows it, re-measure and set the cap to the new size plus 15 %.
```

Создать `src/postgres_fastmcp/tools/CLAUDE.md` с одной строкой:

```text
@AGENTS.md
```

В корневом `AGENTS.md` в разделе `## Tool Definitions` перед строкой `All tools use` добавить абзац:

```markdown
Rules for the MCP tools layer (English-only agent-facing text, parameter types and normalization,
`output` and `ToolResult`, the response budget) live in
[`src/postgres_fastmcp/tools/AGENTS.md`](src/postgres_fastmcp/tools/AGENTS.md); they take precedence
over the generic examples below.
```

- [ ] **Step 5: Линтеры и коммит**

Run: `uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format`
Expected: ноль ошибок.

```bash
git add src/postgres_fastmcp/tools/AGENTS.md src/postgres_fastmcp/tools/CLAUDE.md AGENTS.md tests/unit/tools/test_tool_schema.py
git commit -m "docs(tools): add rules for tool authors and guard the tool schema with regression tests"
```

---

### Task 7: README, env.example и финальная проверка

**Files:**
- Modify: `README.md:92` (после абзаца об опциональных полях `database`), `README.md:100-114` (пример переменных), `README.md:288-290` (таблица инструментов), `README.md:293` (новый раздел после таблицы)
- Modify: `env.example:20` (после `MCP_SERVER_HEALTH_ENDPOINT_ENABLED`)

**Interfaces:**
- Consumes: поведение из Task 2–5.
- Produces: документация для пользователя; кода нет.

- [ ] **Step 1: README — настройка**

После абзаца «Подключение к БД задаётся полями ...» (конец раздела «#### 2. Конфигурационный файл») добавить:

```markdown
Опционально в `server`: `response_max_tokens` — предел ответа инструмента в токенах (по умолчанию `20000`, минимум `1000`). Ответ больше предела заменяется ошибкой с просьбой уточнить запрос.
```

В разделе «#### 3. Переменные окружения» в блок `bash` перед `uv run postgres-fastmcp` добавить строку:

```bash
export MCP_RESPONSE_MAX_TOKENS=20000
```

- [ ] **Step 2: README — таблица инструментов и формат ответа**

В таблице «### Доступные инструменты» строку `get_top_queries` заменить на:

```markdown
| `get_top_queries`      | Самые медленные или ресурсоёмкие запросы из `pg_stat_statements`, не больше `limit` (до 100) |
```

Сразу после таблицы (перед «### Ограничения по доступу») добавить раздел:

```markdown
### Формат ответа и бюджет

- `execute_sql`, `list_schemas`, `list_objects`, `get_object_details` и `get_top_queries` принимают `output`: `table` (по умолчанию) — Markdown-таблица, в которой колонки перечислены один раз, и строка `N rows.`; `json` — `{"rows": [...], "row_count": N}` в `structuredContent` и тот же JSON текстом. `get_object_details` в `json` отдаёт поля объекта и разделы (`columns`, `constraints`, `indexes`) одним объектом.
- Ответ инструмента больше `response_max_tokens` (переменная `MCP_RESPONSE_MAX_TOKENS`, по умолчанию 20000) заменяется ошибкой `Response is too large ... Refine the request`: агенту нужно добавить `WHERE`/`LIMIT`, выбрать меньше колонок или агрегировать. Размер оценивается как байты текста / 3.
- Ввод нормализуется: `object_type` понимает `Tables`, `VIEW`, `sequences`; `health_type` — список или строку через запятую в любом регистре; `sort_by` — синонимы `total`, `mean`, `avg`, `resource`; `limit` больше 100 урезается до 100 и действует для всех `sort_by`, включая `resources`. Неверное значение даёт ошибку с подсказкой `Did you mean ...?`.
```

- [ ] **Step 3: env.example**

В `env.example` после строки `MCP_SERVER_HEALTH_ENDPOINT_ENABLED=true` добавить:

```text

# Max tool response size in tokens (bytes of text / 3); a larger response is replaced by a
# "refine the request" error. Minimum 1000. No SERVER_ in the name: ServerSettings uses the MCP_ prefix.
MCP_RESPONSE_MAX_TOKENS=20000
```

- [ ] **Step 4: Полный локальный CI**

Run:
```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src/ && \
<env> uv run pytest tests/unit -q && \
<env> uv run pytest tests/integration -q
```
Expected: ruff и mypy без ошибок; юнит-тесты `650 passed`; интеграция `78 skipped` без ошибок сбора (или PASS при наличии Docker).

- [ ] **Step 5: Следов старых форм не осталось**

Run: `grep -rn "install_pg_stat_statements_message\|str(resource_queries)\|parse_schema_names\|Пожалуйста\|Неверный критерий" src tests README.md`
Expected: пусто.

Run: `grep -rn "from __future__ import annotations" src tests`
Expected: пусто.

- [ ] **Step 6: Коммит**

```bash
git add README.md env.example
git commit -m "docs(readme): describe the output parameter, the response budget and MCP_RESPONSE_MAX_TOKENS"
```

---

## Self-Review

**1. Покрытие спеки:**

| Раздел спеки | Задача |
| --- | --- |
| 3. `estimate_tokens`, `BYTES_PER_TOKEN`, `ResponseBudgetMiddleware`, `ResponseTooLargeError` и его текст | Task 5 |
| 3. `response_max_tokens` (20000, `ge=1000`, `MCP_RESPONSE_MAX_TOKENS`), middleware первым в `create_server` | Task 5 |
| 4. `rendering.py`: `OutputFormat`, `rows_result`, `sections_result`, экранирование, `None`, `0 rows.`, JSON без Markdown | Task 2 |
| 4. `output` у пяти тулов, `get_top_queries` строками для всех `sort_by`, `get_object_details` разделами, `output_schema=None` | Task 4 |
| 5. `params.py`: `object_type`, `health_type`, `sort_by`, `limit` (`TOP_QUERIES_MAX_LIMIT = 100`), `queries` (`min_length`/`max_length`), `output`; английские описания с примером | Task 3 |
| 5. Схема шире базового типа (`anyOf` для `health_type`, без `enum` для нормализуемых строк) | Task 3 (через `json_schema_extra`, см. «Проверено на установленных версиях») |
| 5. `limit` для `sort_by="resources"` | Task 3 |
| 6. Четыре ошибки на английском, подсказка в конце каждой исправимой ошибки | Task 1 (+ Task 4, 5 для новых ошибок) |
| 7. `tools/AGENTS.md`, `tools/CLAUDE.md`, ссылка из корневого `AGENTS.md` | Task 6 |
| 8. Юнит-тесты бюджета, middleware в `create_server`, рендера, параметров, схемы, бюджета `tools/list`, кириллицы | Task 1, 2, 3, 5, 6 |
| 8. Адаптация тестов тулов к `ToolResult`, интеграции на `output="json"` и `structured_content` | Task 4 |
| 9. Ломающие изменения: документация | Task 7 |

**2. Плейсхолдеры:** единственное значение, которое исполнитель вычисляет сам, — порог `_TOOLS_LIST_BUDGET_CHARS` (Task 6, Step 3): так требует спека, дана точная команда и ожидаемый результат для текста из плана.

**3. Согласованность типов:** `OutputFormat` (Task 2) → `OutputParam` (Task 3) → `output: OutputParam = "table"` (Task 4). `InvalidSortCriteriaError(sort_by)` (Task 1) → `_normalize_sort_by` (Task 3) и `get_top_queries` (Task 1, 4). `HealthTypesParam` — `tuple[HealthType, ...]`, тул передаёт в домен `",".join(...)` (Task 3). `get_top_resource_queries(limit=...)` (Task 3) сохраняется в Task 4. `ResponseTooLargeError(tokens, max_tokens)` (Task 5) совпадает с текстом из спеки. `_SAMPLES` (Task 1) пополняется в Task 4 (`PgStatStatementsNotInstalledError`) и Task 5 (`ResponseTooLargeError`).
