# FastMCP 4 Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перевести проект на FastMCP 4.x, убрать `method='llm'` (он построен на удалённом `ctx.sample()`), перевести аннотации тулов на snake_case и выполнить правило `AGENTS.md` про `from __future__ import annotations`.

**Architecture:** Шаг 1 из пяти по спеке `docs/superpowers/specs/2026-09-26-auth-fastmcp4-hardening-design.md` (раздел 9). Поведение сервера для пользователя не меняется, кроме исчезновения параметра `method` у двух тулов. Слои и файлы остаются на местах; `PostgresProvider` и auth появятся в следующих планах.

**Tech Stack:** Python 3.12+, uv, FastMCP 4.0.10, pydantic 2.13, pytest + pytest-asyncio (asyncio_mode=auto), ruff, mypy strict.

## Global Constraints

- Зависимость: `fastmcp>=4.0.10,<5` (спека, раздел 5).
- Все команды Python только через `uv run ...` (AGENTS.md).
- Нет `from __future__ import annotations` нигде в `src/` (AGENTS.md).
- Docstrings и комментарии на русском, сообщения ошибок, логи и коммиты на английском (AGENTS.md).
- Перед каждым коммитом: `uv run ruff check --fix --show-fixes`, `uv run mypy src/`, `uv run ruff format` — все три с нулём ошибок (AGENTS.md).
- Юнит-тесты требуют env: `MCP_DATABASE_HOST=localhost MCP_DATABASE_PORT=5432 MCP_DATABASE_USER=u MCP_DATABASE_PASSWORD=p MCP_DATABASE_NAME=d` (см. `.github/workflows/ci.yml`). Ниже это обозначено как `<env>`; экспортируйте один раз в начале сессии.
- Формат коммитов как в истории репозитория: `type(scope): message` в повелительном наклонении, английский.
- Ветка работы: `claude/fastmcp4-upgrade` от `main`.

---

### Task 1: Пин FastMCP 4 и проверка, что всё проходит без изменений

**Files:**
- Modify: `pyproject.toml:14` (строка `"fastmcp>=3.4.4,<4",`)
- Modify: `uv.lock` (перегенерируется)

**Interfaces:**
- Consumes: ничего.
- Produces: окружение с `fastmcp==4.0.x`, на котором работают все последующие задачи.

- [ ] **Step 1: Создать ветку**

```bash
git checkout main && git pull && git checkout -b claude/fastmcp4-upgrade
```

- [ ] **Step 2: Поменять пин в pyproject.toml**

Найти в секции `dependencies` строку:

```toml
    "fastmcp>=3.4.4,<4",
```

Заменить на:

```toml
    "fastmcp>=4.0.10,<5",
```

- [ ] **Step 3: Перегенерировать lock и синхронизировать окружение**

Run: `uv lock && uv sync --all-extras`
Expected: в выводе `uv lock` строка вида `Updated fastmcp v3.4.4 -> v4.0.10` (или новее 4.0.x), без ошибок разрешения.

Проверка: `uv run python -c "import fastmcp; print(fastmcp.__version__)"`
Expected: `4.0.10` или старше в ветке 4.x.

- [ ] **Step 4: Прогнать юнит-тесты на новой версии**

Run: `<env> uv run pytest tests/unit -q`
Expected: `301 passed` (то же число, что на 3.4.4). Возможны `DeprecationWarning` про camelCase, это нормально до Task 2.

- [ ] **Step 5: Линтеры**

Run: `uv run ruff check . && uv run mypy src/ && uv run ruff format --check .`
Expected: `All checks passed!`, `Success: no issues found`, без изменений формата.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build(deps): upgrade fastmcp to 4.x"
```

---

### Task 2: Аннотации тулов в snake_case и жёсткая проверка camelCase в CI

**Files:**
- Modify: `src/postgres_fastmcp/tools/registry.py:46-66` (пресеты аннотаций)
- Modify: `.github/workflows/ci.yml:14-19` (блок `env`)
- Test: `tests/unit/tools/test_registry.py`

**Interfaces:**
- Consumes: `register_tools(mcp: FastMCP, settings: Settings)` из `tools/registry.py`.
- Produces: `Tool.annotations` со snake_case-полями (`read_only_hint`, `destructive_hint`, `idempotent_hint`, `open_world_hint`); CI падает на любом camelCase-чтении.

- [ ] **Step 1: Написать падающий тест**

В `tests/unit/tools/test_registry.py` заменить строку импорта реестра на:

```python
from postgres_fastmcp.tools.registry import (
    DESTRUCTIVE,
    READ_ONLY_IDEMPOTENT,
    READ_ONLY_NON_IDEMPOTENT,
    register_tools,
)
```

и добавить в конец файла:

```python
def test_annotation_presets_use_snake_case_keys() -> None:
    """Пресеты аннотаций задают snake_case-поля SDK v2, а не camelCase-алиасы v1."""
    expected = {"read_only_hint", "destructive_hint", "idempotent_hint", "open_world_hint"}
    for preset in (READ_ONLY_IDEMPOTENT, READ_ONLY_NON_IDEMPOTENT, DESTRUCTIVE):
        assert set(preset) == expected


async def test_registered_tools_expose_snake_case_annotations() -> None:
    """Аннотации тулов заданы snake_case-полями SDK v2 и доходят до зарегистрированного Tool."""
    mcp = FastMCP(name="test")
    register_tools(mcp, _build_settings(AccessMode.FULL))

    list_objects = await mcp.get_tool("list_objects")
    execute_sql = await mcp.get_tool("execute_sql")

    assert list_objects is not None and list_objects.annotations is not None
    assert list_objects.annotations.read_only_hint is True
    assert list_objects.annotations.destructive_hint is False
    assert list_objects.annotations.idempotent_hint is True
    assert list_objects.annotations.open_world_hint is True

    assert execute_sql is not None and execute_sql.annotations is not None
    assert execute_sql.annotations.read_only_hint is True  # FULL без write_mode: read-only
    assert execute_sql.annotations.idempotent_hint is False
```

- [ ] **Step 2: Запустить тесты и убедиться, что первый падает**

Run: `FASTMCP_MCP_CAMELCASE_COMPAT=false <env> uv run pytest tests/unit/tools/test_registry.py -q`
Expected: `test_annotation_presets_use_snake_case_keys` FAIL с `AssertionError` (ключи пресетов сейчас `readOnlyHint` и т.д.). `test_registered_tools_expose_snake_case_annotations` уже PASS: pydantic принимает camelCase-алиасы в конструкторе даже при выключенном мосте, поэтому он служит регрессионной защитой, а не красным тестом.

- [ ] **Step 3: Перевести пресеты на snake_case**

В `src/postgres_fastmcp/tools/registry.py` заменить блок пресетов целиком:

```python
# Annotation presets for Tool.from_function(annotations=...)
# See https://gofastmcp.com/servers/tools — ToolAnnotations fields (snake_case since MCP SDK v2).
READ_ONLY_IDEMPOTENT: dict[str, bool] = {
    "read_only_hint": True,
    "destructive_hint": False,
    "idempotent_hint": True,
    "open_world_hint": True,
}
READ_ONLY_NON_IDEMPOTENT: dict[str, bool] = {
    "read_only_hint": True,
    "destructive_hint": False,
    "idempotent_hint": False,
    "open_world_hint": True,
}
DESTRUCTIVE: dict[str, bool] = {
    "read_only_hint": False,
    "destructive_hint": True,
    "idempotent_hint": False,
    "open_world_hint": True,
}
```

Функция `_ann(title, preset)` остаётся как есть: `ToolAnnotations(title=title, **preset)`.

- [ ] **Step 4: Запустить тесты с выключенным мостом**

Run: `FASTMCP_MCP_CAMELCASE_COMPAT=false <env> uv run pytest tests/unit -q`
Expected: `303 passed`, ни одного `FastMCPDeprecationWarning` про camelCase в выводе.

- [ ] **Step 5: Включить проверку в CI**

В `.github/workflows/ci.yml` в блок верхнего уровня `env:` добавить строку после `MCP_DATABASE_NAME: testdb`:

```yaml
  # Camel-case field access shims are removed in a future FastMCP release; fail early.
  FASTMCP_MCP_CAMELCASE_COMPAT: "false"
```

- [ ] **Step 6: Линтеры и коммит**

Run: `uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format`
Expected: ноль ошибок.

```bash
git add src/postgres_fastmcp/tools/registry.py tests/unit/tools/test_registry.py .github/workflows/ci.yml
git commit -m "refactor(tools): use snake_case tool annotations and disable camelCase shims in CI"
```

---

### Task 3: Убрать `from __future__ import annotations`

**Files:**
- Modify: `src/postgres_fastmcp/tools/registry.py:1-33`
- Modify: `src/postgres_fastmcp/app/server.py:1-20`
- Modify: `src/postgres_fastmcp/app/context.py:1-20`

**Interfaces:**
- Consumes: ничего нового.
- Produces: три модуля без `__future__`; сигнатуры `register_tools`, `create_server`, `get_db`, `LifespanContext` не меняются.

- [ ] **Step 1: Написать падающую проверку**

Это правило стиля, а не поведение: проверкой служит grep. Убедиться, что сейчас он находит три файла:

Run: `grep -rl "from __future__ import annotations" src/`
Expected:
```
src/postgres_fastmcp/tools/registry.py
src/postgres_fastmcp/app/server.py
src/postgres_fastmcp/app/context.py
```

- [ ] **Step 2: registry.py — обычные импорты вместо TYPE_CHECKING**

Заменить шапку `src/postgres_fastmcp/tools/registry.py` (от docstring до блока `try: _VERSION`) на:

```python
"""Программная регистрация тулов в FastMCP через Tool.from_function + add_tool.

Описания строятся в момент регистрации с учётом Settings (access_mode, write_mode),
поэтому модуль `tools/definitions` может импортироваться без инициализации конфига.
"""

from importlib.metadata import (
    PackageNotFoundError,
    version as _pkg_version,
)
from typing import Any

from fastmcp import FastMCP
from fastmcp.tools import Tool
from mcp.types import ToolAnnotations

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.shared.enums import AccessMode, ToolTag
from postgres_fastmcp.tools.definitions import (
    HEALTH_TYPE_VALUES,
    analyze_db_health,
    analyze_query_indexes,
    analyze_workload_indexes,
    execute_sql,
    explain_query,
    get_object_details,
    get_top_queries,
    list_objects,
    list_schemas,
)
```

Блок `if TYPE_CHECKING:` удалить целиком.

- [ ] **Step 3: server.py — обычные импорты**

Заменить шапку `src/postgres_fastmcp/app/server.py` (до `def create_server`) на:

```python
"""Фабрика MCP-сервера: create_server(settings, *, auth, extra_providers, extra_middleware) -> FastMCP."""

from collections.abc import Sequence
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.middleware.logging import LoggingMiddleware
from fastmcp.server.middleware.timing import TimingMiddleware

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.lifespan import build_lifespan
from postgres_fastmcp.shared.enums import AccessMode, ToolTag
from postgres_fastmcp.tools.registry import register_tools
```

Блок `if TYPE_CHECKING:` удалить.

- [ ] **Step 4: context.py — обычные импорты**

Заменить шапку `src/postgres_fastmcp/app/context.py` (до `class LifespanContext`) на:

```python
"""Типизированный контракт lifespan-контекста сервера и аксессор для тулов.

Единая точка, описывающая форму ``ctx.lifespan_context``: его наполняет
``lifespan.build_lifespan`` (продюсер), а читают тулы через ``get_db`` (потребитель).
Это убирает дублирование нетипизированного доступа ``ctx.lifespan_context["db"]``.
"""

from typing import TypedDict, cast

from fastmcp.server.context import Context

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.domains.db_access import DbAccessService
```

Блок `if TYPE_CHECKING:` удалить. Тело `LifespanContext` и `get_db` не трогать.

- [ ] **Step 5: Проверить, что grep пуст, импорты не циклические, тесты зелёные**

Run: `grep -rl "from __future__ import annotations" src/ ; echo "exit=$?"`
Expected: пустой вывод и `exit=1`.

Run: `uv run python -c "import postgres_fastmcp.app.server, postgres_fastmcp.app.context, postgres_fastmcp.tools.registry; print('ok')"`
Expected: `ok` (нет `ImportError` из-за циклов).

Run: `<env> uv run pytest tests/unit -q`
Expected: `303 passed`.

- [ ] **Step 6: Линтеры и коммит**

Run: `uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format`
Expected: ноль ошибок.

```bash
git add src/postgres_fastmcp/tools/registry.py src/postgres_fastmcp/app/server.py src/postgres_fastmcp/app/context.py
git commit -m "style: drop from __future__ annotations per AGENTS.md"
```

---

### Task 4: Убрать `llm`-метод: сервис, тулы, реестр, enum, ошибки

**Files:**
- Delete: `src/postgres_fastmcp/domains/index_tuning/llm_opt.py`
- Delete: `tests/unit/domains/index_tuning/test_llm_opt.py`
- Modify: `src/postgres_fastmcp/domains/index_tuning/service.py` (переписывается целиком)
- Modify: `src/postgres_fastmcp/tools/definitions.py:17-21, 113-160`
- Modify: `src/postgres_fastmcp/tools/registry.py:168-196` (описания двух тулов)
- Modify: `src/postgres_fastmcp/shared/enums.py:13` (`AnalysisMethod`)
- Modify: `src/postgres_fastmcp/shared/errors.py:298-310` (класс `ContextRequiredError`)
- Modify: `tests/unit/domains/test_index_service.py` (переписывается целиком)
- Modify: `tests/unit/tools/test_definitions.py:153-180`
- Modify: `tests/unit/domains/index_tuning/test_pareto_objective.py:1-9, 31-36`
- Modify: `tests/unit/shared/test_errors.py:28`

**Interfaces:**
- Consumes: `DatabaseTuningAdvisor(sql_driver, connection_id=...)`, `TextPresentation(sql_driver, index_tuning)` — без изменений.
- Produces: `IndexAnalysisService.analyze_workload_indexes(*, max_index_size_mb: int = 10000) -> dict[str, Any]`, `IndexAnalysisService.analyze_query_indexes(*, queries: list[str], max_index_size_mb: int = 10000) -> dict[str, Any]`; тулы `analyze_query_indexes(queries, max_index_size_mb, ctx)` и `analyze_workload_indexes(max_index_size_mb, ctx)` без `method`; `shared.enums` без `AnalysisMethod`; `shared.errors` без `ContextRequiredError`.

Задача одна, потому что после удаления `llm_opt.py` mypy и тесты зелёные только когда сервис, тулы и enum изменены вместе.

- [ ] **Step 1: Переписать тесты сервиса под новую сигнатуру**

Заменить содержимое `tests/unit/domains/test_index_service.py` целиком:

```python
# mypy: ignore-errors
"""Unit tests for IndexAnalysisService (DTA only)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from postgres_fastmcp.domains.index_tuning.models import MAX_NUM_INDEX_TUNING_QUERIES
from postgres_fastmcp.domains.index_tuning.service import IndexAnalysisService
from postgres_fastmcp.shared.errors import EmptyQueriesError, QueriesLimitError


class TestIndexAnalysisServiceAnalyzeWorkloadIndexes:
    """Tests for IndexAnalysisService.analyze_workload_indexes."""

    @patch("postgres_fastmcp.domains.index_tuning.service.TextPresentation")
    @patch("postgres_fastmcp.domains.index_tuning.service.DatabaseTuningAdvisor")
    async def test_returns_recommendations_dict(
        self,
        mock_dta_cls: MagicMock,  # noqa: ARG002 (required for patch order)
        mock_presentation_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """analyze_workload_indexes returns the presentation result."""
        mock_presentation = MagicMock()
        mock_presentation.analyze_workload = AsyncMock(return_value={"recommendations": []})
        mock_presentation_cls.return_value = mock_presentation

        service = IndexAnalysisService(db=mock_db_access)
        result = await service.analyze_workload_indexes(max_index_size_mb=1000)

        assert result == {"recommendations": []}
        mock_presentation.analyze_workload.assert_awaited_once_with(max_index_size_mb=1000)

    @patch("postgres_fastmcp.domains.index_tuning.service.TextPresentation")
    @patch("postgres_fastmcp.domains.index_tuning.service.DatabaseTuningAdvisor")
    async def test_builds_dta_with_connection_id(
        self,
        mock_dta_cls: MagicMock,
        mock_presentation_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """The advisor is built on the service's driver and connection id."""
        mock_presentation_cls.return_value.analyze_workload = AsyncMock(return_value={})

        await IndexAnalysisService(db=mock_db_access).analyze_workload_indexes()

        mock_dta_cls.assert_called_once_with(mock_db_access.sql_driver, connection_id=mock_db_access.connection_id)


class TestIndexAnalysisServiceAnalyzeQueryIndexes:
    """Tests for IndexAnalysisService.analyze_query_indexes."""

    @patch("postgres_fastmcp.domains.index_tuning.service.TextPresentation")
    @patch("postgres_fastmcp.domains.index_tuning.service.DatabaseTuningAdvisor")
    async def test_returns_recommendations_dict(
        self,
        mock_dta_cls: MagicMock,  # noqa: ARG002 (required for patch order)
        mock_presentation_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """analyze_query_indexes with valid queries returns the presentation result."""
        mock_presentation = MagicMock()
        mock_presentation.analyze_queries = AsyncMock(return_value={"recommendations": []})
        mock_presentation_cls.return_value = mock_presentation

        service = IndexAnalysisService(db=mock_db_access)
        result = await service.analyze_query_indexes(queries=["SELECT 1"], max_index_size_mb=10)

        assert result == {"recommendations": []}
        mock_presentation.analyze_queries.assert_awaited_once_with(queries=["SELECT 1"], max_index_size_mb=10)

    async def test_empty_queries_raises(self, mock_db_access: MagicMock) -> None:
        """analyze_query_indexes(queries=[]) raises EmptyQueriesError."""
        service = IndexAnalysisService(db=mock_db_access)
        with pytest.raises(EmptyQueriesError):
            await service.analyze_query_indexes(queries=[])

    async def test_over_limit_raises(self, mock_db_access: MagicMock) -> None:
        """More than MAX_NUM_INDEX_TUNING_QUERIES queries raises QueriesLimitError."""
        service = IndexAnalysisService(db=mock_db_access)
        too_many = ["SELECT 1"] * (MAX_NUM_INDEX_TUNING_QUERIES + 1)
        with pytest.raises(QueriesLimitError) as exc_info:
            await service.analyze_query_indexes(queries=too_many)
        assert exc_info.value.limit == MAX_NUM_INDEX_TUNING_QUERIES
```

- [ ] **Step 2: Переписать два теста тулов**

В `tests/unit/tools/test_definitions.py` заменить тесты `test_analyze_workload_indexes_default` и `test_analyze_query_indexes_default` на:

```python
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

- [ ] **Step 3: Убрать llm-тест из test_pareto_objective.py и ContextRequiredError из test_errors.py**

В `tests/unit/domains/index_tuning/test_pareto_objective.py` удалить строку импорта:

```python
from postgres_fastmcp.domains.index_tuning.llm_opt import LLMOptimizerTool
```

и удалить целиком метод `test_llm_score_delegates_to_shared_objective` (последний метод класса). Остальные три теста не трогать.

В `tests/unit/shared/test_errors.py` удалить строку `        ("ContextRequiredError", ()),` из списка `parametrize` (классы там берутся через `getattr`, импорт править не нужно).

- [ ] **Step 4: Удалить llm-файлы и запустить тесты, убедиться, что падают ожидаемо**

```bash
git rm src/postgres_fastmcp/domains/index_tuning/llm_opt.py tests/unit/domains/index_tuning/test_llm_opt.py
```

Run: `<env> uv run pytest tests/unit/domains tests/unit/tools -q 2>&1 | tail -5`
Expected: ошибка сбора `ModuleNotFoundError: No module named 'postgres_fastmcp.domains.index_tuning.llm_opt'` из `service.py`. Это ожидаемо.

- [ ] **Step 5: Переписать service.py**

Заменить содержимое `src/postgres_fastmcp/domains/index_tuning/service.py` целиком:

```python
"""Сервис анализа индексов (фасад модуля index_tuning): только DTA."""

from typing import Any

from postgres_fastmcp.domains.db_access import DbAccessService
from postgres_fastmcp.domains.index_tuning.dta_calc import DatabaseTuningAdvisor
from postgres_fastmcp.domains.index_tuning.models import MAX_NUM_INDEX_TUNING_QUERIES
from postgres_fastmcp.domains.index_tuning.presentation import TextPresentation
from postgres_fastmcp.shared.errors import EmptyQueriesError, QueriesLimitError


class IndexAnalysisService:
    """Сервис для анализа нагрузки и индексов запросов."""

    def __init__(self, db: DbAccessService) -> None:
        """Инициализация сервиса с подключением к базе данных."""
        self.db = db

    async def analyze_workload_indexes(self, *, max_index_size_mb: int = 10000) -> dict[str, Any]:
        """Проанализировать часто выполняемые запросы и рекомендовать оптимальные индексы.

        Args:
            max_index_size_mb: Максимальный размер индексов в МБ (по умолчанию 10000).

        Returns:
            Словарь с результатами анализа и рекомендациями.
        """
        return await self._presentation().analyze_workload(max_index_size_mb=max_index_size_mb)

    async def analyze_query_indexes(self, *, queries: list[str], max_index_size_mb: int = 10000) -> dict[str, Any]:
        """Проанализировать список SQL запросов и рекомендовать оптимальные индексы.

        Args:
            queries: Список SQL запросов для анализа.
            max_index_size_mb: Максимальный размер индексов в МБ (по умолчанию 10000).

        Returns:
            Словарь с результатами анализа и рекомендациями.

        Raises:
            EmptyQueriesError: Если список запросов пуст.
            QueriesLimitError: Если запросов больше MAX_NUM_INDEX_TUNING_QUERIES.
        """
        if len(queries) == 0:
            raise EmptyQueriesError()
        if len(queries) > MAX_NUM_INDEX_TUNING_QUERIES:
            raise QueriesLimitError(MAX_NUM_INDEX_TUNING_QUERIES)
        return await self._presentation().analyze_queries(queries=queries, max_index_size_mb=max_index_size_mb)

    def _presentation(self) -> TextPresentation:
        """Собрать DTA-советник и презентацию поверх драйвера сервиса."""
        sql_driver = self.db.sql_driver
        advisor = DatabaseTuningAdvisor(sql_driver, connection_id=self.db.connection_id)
        return TextPresentation(sql_driver, advisor)
```

- [ ] **Step 6: Переписать две функции в definitions.py**

В `src/postgres_fastmcp/tools/definitions.py` изменить импорт enum:

```python
from postgres_fastmcp.shared.enums import ObjectType, TopQueriesSortBy
```

и заменить функции `analyze_query_indexes` и `analyze_workload_indexes` на:

```python
async def analyze_query_indexes(
    queries: Annotated[
        list[str],
        Field(description=f"SQL queries to analyze (up to {MAX_NUM_INDEX_TUNING_QUERIES})."),
    ],
    max_index_size_mb: Annotated[
        int,
        Field(default=10000, ge=1, description="Max recommended index size (MB)."),
    ] = 10000,
    ctx: Context = CurrentContext(),
) -> dict[str, Any]:
    """Рекомендовать индексы под список запросов (cost-based DTA, требует hypopg)."""
    service = IndexAnalysisService(db=get_db(ctx))
    return await service.analyze_query_indexes(queries=queries, max_index_size_mb=max_index_size_mb)


async def analyze_workload_indexes(
    max_index_size_mb: Annotated[
        int,
        Field(default=10000, ge=1, description="Max recommended index size (MB)."),
    ] = 10000,
    ctx: Context = CurrentContext(),
) -> dict[str, Any]:
    """Рекомендовать индексы по агрегированной нагрузке БД (cost-based DTA, требует hypopg)."""
    service = IndexAnalysisService(db=get_db(ctx))
    return await service.analyze_workload_indexes(max_index_size_mb=max_index_size_mb)
```

- [ ] **Step 7: Обновить описания в registry.py**

В `src/postgres_fastmcp/tools/registry.py` внутри `_full_specs()` заменить `description` у `analyze_query_indexes` на:

```python
            "description": (
                "Recommend optimal indexes for a given list of SQL queries using cost-based analysis "
                "with hypothetical indexes (the hypopg extension is required). "
                "Use analyze_workload_indexes instead if you want to optimize aggregate workload."
            ),
```

и у `analyze_workload_indexes` на:

```python
            "description": (
                "Recommend indexes based on the actual workload captured in pg_stat_statements, "
                "using cost-based analysis with hypothetical indexes (hypopg required). "
                "Use periodically to find missing indexes. "
                "Use analyze_query_indexes instead if you want to optimize specific queries."
            ),
```

- [ ] **Step 8: Удалить AnalysisMethod и ContextRequiredError**

В `src/postgres_fastmcp/shared/enums.py` удалить строку:

```python
AnalysisMethod = Literal["dta", "llm"]
```

В `src/postgres_fastmcp/shared/errors.py` удалить целиком класс `ContextRequiredError` (начинается на строке ~298 с `class ContextRequiredError(UserFacingError):` и заканчивается перед следующим `class`).

Run: `grep -rn "AnalysisMethod\|ContextRequiredError\|llm_opt\|LLMOptimizer" src tests`
Expected: пусто.

- [ ] **Step 9: Полный прогон юнит-тестов**

Run: `FASTMCP_MCP_CAMELCASE_COMPAT=false <env> uv run pytest tests/unit -q`
Expected: все PASS; число тестов меньше 303 ровно на удалённые llm-тесты (`test_llm_opt.py` целиком, один тест в `test_pareto_objective.py`, один параметр в `test_errors.py`, пять llm-тестов в `test_index_service.py`).

- [ ] **Step 10: Линтеры и коммит**

Run: `uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format`
Expected: ноль ошибок.

```bash
git add -A src/postgres_fastmcp/domains/index_tuning src/postgres_fastmcp/tools src/postgres_fastmcp/shared tests/unit
git commit -m "refactor(index_tuning): drop LLM optimizer and the method parameter, keep DTA only"
```

---

### Task 5: Интеграционные тесты и документация без `method`

**Files:**
- Modify: `tests/integration/test_tools_integration.py:11-12, 272-300`
- Modify: `tests/integration/dta/test_dta_calc_integration.py:153-157`
- Modify: `tests/README.md:47-48`
- Modify: `AGENTS.md:1003`

**Interfaces:**
- Consumes: тулы и сервис из Task 4.
- Produces: интеграционный набор, который проходит в CI на FastMCP 4.

- [ ] **Step 1: Убрать `"method": "dta"` из вызовов тулов**

В `tests/integration/test_tools_integration.py`:
- в docstring модуля строки `- analyze_workload_indexes (method=dta)` и `- analyze_query_indexes (method=dta)` заменить на `- analyze_workload_indexes` и `- analyze_query_indexes`;
- в `test_tools_analyze_workload_indexes_dta` аргументы вызова заменить на `{"max_index_size_mb": 100}`;
- в `test_tools_analyze_query_indexes_dta` — на `{"queries": ["SELECT 1", "SELECT 2"], "max_index_size_mb": 100}`;
- в docstrings этих двух тестов убрать «with method=dta».

- [ ] **Step 2: Убрать `method="dta"` из dta-интеграции**

В `tests/integration/dta/test_dta_calc_integration.py` вызов сервиса привести к виду:

```python
        result = await service.analyze_query_indexes(
            queries=["SELECT * FROM service_test WHERE a = 1", "SELECT * FROM service_test WHERE b = 'x100'"],
            max_index_size_mb=50,
        )
```

- [ ] **Step 3: Документация**

В `tests/README.md` строки таблицы заменить на:

```markdown
| `analyze_workload_indexes` | max_index_size_mb |
| `analyze_query_indexes` | queries list |
```

В `AGENTS.md` удалить строку `    - `llm_opt.py` — `LLMOptimizerTool`: LLM-driven search via MCP context sampling`.

- [ ] **Step 4: Прогнать интеграцию локально, если есть Docker**

Run: `<env> uv run pytest tests/integration/test_tools_integration.py -q -k "analyze" --timeout=180`
Expected: PASS (или `skipped` с текстом про Docker, если Docker недоступен — тогда проверку выполнит CI).

Run: `grep -rn "method" tests/integration | grep -i "dta\|llm"`
Expected: пусто.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_tools_integration.py tests/integration/dta/test_dta_calc_integration.py tests/README.md AGENTS.md
git commit -m "test(integration): drop method argument from index analysis tools"
```

---

### Task 6: Финальная проверка и PR

**Files:** нет изменений кода.

- [ ] **Step 1: Полный локальный CI**

Run:
```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src/ && \
FASTMCP_MCP_CAMELCASE_COMPAT=false <env> uv run pytest tests/unit -q
```
Expected: все четыре команды с нулём ошибок, тесты зелёные.

- [ ] **Step 2: Убедиться, что следов llm и camelCase не осталось**

Run: `grep -rn "llm\|LLMOptimizer\|readOnlyHint\|destructiveHint\|idempotentHint\|openWorldHint\|ctx.sample" src tests`
Expected: пусто.

- [ ] **Step 3: Push и PR**

```bash
git push -u origin claude/fastmcp4-upgrade
gh pr create --title "Upgrade to FastMCP 4, drop LLM index method" --body "Step 1 of docs/superpowers/specs/2026-09-26-auth-fastmcp4-hardening-design.md: fastmcp>=4.0.10, snake_case tool annotations, no __future__ annotations, LLM optimizer removed (ctx.sample is gone in FastMCP 4)."
```

Expected: CI зелёный на всех трёх джобах.
