# PostgresProvider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Шаг 3 из §9 спеки: тулы переезжают в `PostgresProvider(LocalProvider)`, домены получают доступ к БД через `DbAccessPort` с правами конкретного запроса, `create_server` собирается поверх провайдера, `app/lifespan.py` и `app/context.py` удаляются, публичное API пакета — по §6. Поведение для пользователя не меняется (кроме нейтрального описания `execute_sql`, которое спека требует явно).

**Architecture:** План по спеке `docs/superpowers/specs/2026-09-26-auth-fastmcp4-hardening-design.md` (§3.1–3.4, провайдерная часть §3.7, §3.8, §6, unit-тесты §8) с учётом кода после PR #8 (`docs/superpowers/specs/2026-09-26-agent-output-budget-design.md`). Новые модули в корне пакета: `access.py` (`EffectiveAccess`, `AccessPolicy`, `AccessResolver`, резолвер «всегда потолок», `bounded_resolver`, `full_access_check`) и `provider.py` (`PostgresProvider`). `DbAccessService` выдаёт `DbAccess` через `view(EffectiveAccess)` с кэшем исполнителей по правам; `ToolSet` — девять тулов как методы с `get_db: Callable[[], DbAccessPort]`; `registry.register_tools(provider, toolset, *, ceiling, full_tool_auth)`. Шов для шага 4: провайдер на каждый вызов берёт `get_access_token()` и прогоняет его через резолвер; шаг 4 меняет только тело резолвера (`resolve_access`) и добавляет `AuthSettings`.

**Tech Stack:** Python 3.12+, uv, FastMCP 4.0.10, pydantic 2.13, pytest + pytest-asyncio (asyncio_mode=auto), ruff (select=ALL), mypy strict.

## Global Constraints

- Язык: всё, что видит агент или внешняя система (описания тулов, `Field(description=...)`, docstring методов-тулов, сообщения ошибок, логи, коммиты), пишется на английском. По-русски только комментарии и docstring, которые видит лишь разработчик.
- Нигде нет `from __future__ import annotations`.
- Перед каждым коммитом: `uv run ruff check --fix --show-fixes`, `uv run mypy src/`, `uv run ruff format` — все три с нулём ошибок; плюс `uv run ruff format <изменённые файлы тестов>` по путям: `tests` исключены из ruff в `pyproject.toml`, но явно переданный путь форматируется.
- `<env>` ниже означает `MCP_DATABASE_HOST=localhost MCP_DATABASE_PORT=5432 MCP_DATABASE_USER=u MCP_DATABASE_PASSWORD=p MCP_DATABASE_NAME=d FASTMCP_MCP_CAMELCASE_COMPAT=false`. Юнит-тесты: `<env> uv run pytest tests/unit -q`. Базовая линия до плана: `690 passed`.
- Docker локально недоступен: `<env> uv run pytest tests/integration -q` только собирает и пропускает тесты (до плана `78 skipped`). Интеграционные тесты всё равно адаптируются к новому API в той задаче, которая ломает старое; каждая такая правка проверяется статически: сбор без ошибок, `uv run ruff check <файлы> --select F,ARG --no-fix` и `grep` на старые имена.
- Ломающие изменения разрешены, легаси не сохраняется (память проекта, спека §2).
- Субагенты никогда не запускают `git config`.
- Работа идёт в текущей ветке `claude/postgres-provider`. Отдельных веток, push и PR в плане нет; последняя задача — полная локальная проверка CI.
- Формат коммитов: `type(scope): message`, повелительное наклонение, английский.
- Сохраняется поведение PR #8: видимость тулов в basic/full, таймауты тулов из `safe_sql_timeout` (`registry._tool_timeout`), аннотации `execute_sql` по потолку (BASIC+write — `read_only_hint=False`), `output`/`ToolResult`, `ResponseBudgetMiddleware` первым в `create_server`, тесты бюджета, кириллицы (`_SAMPLES`) и схемы (`_TOOLS_LIST_BUDGET_CHARS = 10_774`).
- Вне объёма (шаги 4–5): `AuthSettings`, `build_auth_provider`, `resolve_access` с claim-правилами, предупреждения на старте, HTTP-тесты auth, `/health` и `PostgresProvider.ping()`, `endpoint`, `workers`, переименование скрипта, версия `0.1.0`, `env.example`, `docker/config.json`.

## Проверено на установленных версиях (FastMCP 4.0.10, pydantic 2.13, mypy strict)

Справка для исполнителя; каждое утверждение проверено экспериментом или чтением кода `.venv/lib/python3.12/site-packages/fastmcp` до написания плана. Весь план прогнан в копии репозитория (`git worktree`) против `.venv` проекта: числа тестов ниже — реальные.

- `fastmcp.server.providers.LocalProvider(on_duplicate="error")` — единственный параметр конструктора. `add_tool(Tool)` кладёт тул в провайдер; `disable(tags={"full"})` на провайдере добавляет Visibility-трансформ только этому провайдеру: тул пропадает из `list_tools`, вызов даёт `ToolError: Unknown tool: 'list_schemas'`.
- `Provider.lifespan()` — `@asynccontextmanager`-метод без аргументов. FastMCP входит в lifespan каждого провайдера из `self.providers` внутри своего lifespan (`server/mixins/lifespan.py:203`). In-memory `Client(server)` входит и выходит на каждую сессию; повторный вход работает. `DbConnPool.close()` обнуляет пул, следующий запрос открывает его заново, поэтому `DbAccessService`, созданный в `__init__` провайдера, переживает несколько lifespan.
- `FastMCP(providers=[...])` и `add_provider(provider, namespace="analytics")`: имена тулов становятся `analytics_<name>`; таймауты, `disable(tags=...)` провайдера и `tool.auth` сохраняются под namespace.
- `on_duplicate="error"` у `FastMCP` действует только на собственный `LocalProvider` сервера. Одинаковые имена из разных провайдеров дают WARNING `Duplicate list_tools component` и выигрывает первый провайдер (локальный провайдер сервера, затем провайдеры по порядку). Поэтому фраза README «коллизии приведут к ошибке на старте» неверна и до, и после плана; в Task 6 она исправлена.
- `Tool.from_function(..., auth: AuthCheck | list[AuthCheck] | None)` существует. Проверки запускаются в `FastMCP.list_tools`/`_get_tool` (`server/server.py:885-925`), если `tool.auth is not None`; отказ убирает тул из списка, вызов даёт `Unknown tool`. В stdio проверки **не вызываются вовсе** (`_get_auth_context()` возвращает `skip_auth=True`), по HTTP без auth и в in-memory `Client` вызываются с `ctx.token = None`. Проверка срабатывает и на сервере без `auth`.
- `AuthCheck`, `AuthContext(token, component)`, `AccessToken`, `AuthProvider` импортируются из `fastmcp.server.auth`; `Provider` — из `fastmcp.server.providers`; `Middleware` — из `fastmcp.server.middleware`.
- `fastmcp.server.dependencies.get_access_token()` вне запроса и без auth возвращает `None`, исключений нет. `get_context()` там же отдаёт `Context` текущего запроса (используется только во временном мосте Task 3).
- Связанный метод как `fn` в `Tool.from_function`: `self` не попадает в схему, `inspect.signature(tool.fn)` без `self`, `tool.fn.__doc__` — docstring метода. Проверки `test_tool_schema.py` (описания `Field`, кириллица в docstring) работают без изменений.
- `FastMCP(middleware=[...])` сохраняет порядок списка; конструктор сам дописывает `DereferenceRefsMiddleware` **в конец** (раньше, при `add_middleware` после конструктора, он стоял первым). У него только `on_list_tools`/`on_list_resource_templates`, поэтому для вызовов `ResponseBudgetMiddleware` остаётся внешним.
- mypy strict: frozen dataclass **не** удовлетворяет `Protocol` с обычными атрибутами (`Protocol member P.x expected settable variable, got read-only attribute`). Поэтому члены `DbAccessPort` объявлены как `@property`; `DbAccess(frozen=True, slots=True)` им удовлетворяет.
- `MagicMock(spec=DbAccess)` (slots-dataclass): присваивание полей работает, обращение к несуществующему атрибуту (`.config`) даёт `AttributeError`.
- Размер `tools/list` в режиме FULL: 9368 символов до плана и после Task 1–4; 9710 после нейтрального описания `execute_sql` (Task 5). Порог `_TOOLS_LIST_BUDGET_CHARS = 10_774` не меняется.
- CLI в stdio (`python -m postgres_fastmcp.app.main --transport stdio`, режим через `MCP_DATABASE_ACCESS_MODE`) после плана отдаёт те же списки тулов basic/full, что и до него (проверено через `fastmcp.Client(StdioTransport(...))` на `b56b951` и на результате плана). `StdioTransport` без `env=` не передаёт дочернему процессу переменные `MCP_DATABASE_*`.

## Решения там, где спека открыта

- **`full_access_check` — в шаге 3, а не в шаге 4.** §6 требует экспортировать его из пакета, а обновление публичного API входит в шаг 3. С резолвером «всегда потолок» проверка нейтральна: при потолке FULL она пропускает всё, при потолке BASIC full-тулы и так скрыты `disable(tags)`. Зато регистрация full-тулов с `auth=` получает окончательную форму уже сейчас, и шаг 4 меняет только резолвер. Тесты с claim токена (§8) остаются шагу 4; здесь — токен `None` и пользовательский резолвер.
- **`resolve_access` — в шаге 4.** В шаге 3 `build_resolver(ceiling, policy)` возвращает функцию «всегда потолок». Функция с правилами claim появится вместе с их тестами, без заглушки с неиспользуемыми аргументами.
- **`AccessPolicy(enforced=True)` без резолвера отклоняется** (`ValueError` в `build_resolver`). Иначе библиотечный пользователь с auth-провайдером получил бы полный потолок молча. Шаг 4 убирает эту ветку, когда появятся правила.
- **Пользовательский `access_resolver` ограничивается потолком** (`bounded_resolver`). Спека требует «токен не расширяет права выше потолка» только для `resolve_access`; для своего резолвера это не сказано, но исполнитель строится по результату, и FULL+write от резолвера при потолке basic/read-only выдал бы `SqlExecutor` без ограничений.
- **`DatabaseHealthAnalyzer` принимает `DbAccessPort`**, как перечислено в §3.3, а не голый драйвер.
- **`registry` типизирует потолок через `DatabaseConfigPort`** из `domains/db_access.py`, а не `Settings`/`DatabaseConfig`: так `tools/` перестаёт импортировать `app/`. `PostgresProvider` принимает `DatabaseConfig`, как в спеке; это единственный импорт «вверх» (`provider.py` → `app/config/database.py`, чистая модель данных), он записан в `AGENTS.md`.
- **Имена интеграционных фикстур** `db_service_full`, `db_service_user_prefix`, `db_service_with_hypopg` меняются на `db_full`, `db_user_prefix`, `db_with_hypopg`: они отдают уже не сервис, а `DbAccess`.
- **`ping()` не добавляется**: единственный потребитель — `/health` (шаг 5).

## Отступления от спеки

| Где в спеке | Что в спеке | Что в плане | Почему |
| --- | --- | --- | --- |
| §3.3, спека :144-149 | `DbAccessPort` с обычными атрибутами | Члены протокола — `@property` | mypy strict не принимает frozen dataclass для протокола с изменяемыми атрибутами (проверено) |
| §3.7 п.3, спека :291-293 | `middleware=[TimingMiddleware(), LoggingMiddleware(), *extra]` | `[ResponseBudgetMiddleware(...), TimingMiddleware(), LoggingMiddleware(), *extra]` | PR #8 (`app/server.py:57` до плана) ставит бюджет первым, внешним; спека бюджета §3 это требует |
| §3.7 п.1–2, спека :289-290 | `build_auth_provider(settings.auth)`, `access_policy=settings.auth.access_policy` | `auth` передаётся как есть, `access_policy` не передаётся | `Settings.auth` появляется в шаге 4 |
| §3.8, спека :312-315 | Нейтральный текст описывает только read-only режим | Нейтральный текст покрывает все режимы: read-only, basic+write (DML в `public`, DDL кроме `CREATE EXTENSION hypopg / pg_stat_statements`), full+write | После спеки коммит `b81cce5` сделал описание точным для basic+write (`tools/registry.py:393-410` до плана); текст спеки потерял бы эту информацию. Стрелки `->` вместо `→`, как в остальных описаниях |
| §3.2, спека :99 | `PostgresProvider.ping()` | Не добавлен | Нужен только `/health`, это шаг 5 |
| §3.4, спека :186 | `resolve_access(...)` | Нет в шаге 3; есть `build_resolver` и `bounded_resolver` | См. «Решения»: правила claim — шаг 4 |
| §5, спека :353-357 | README «Использование как библиотека» и `AGENTS.md` переписываются в шаге 5 | Переписываются в шаге 3 (Task 6), остальное README/AGENTS — в шаге 5 | Шаг 3 убирает экспорты `LocalProvider`/`Middleware` и `app/lifespan.py`: без правки README-пример и `AGENTS.md` врут сразу после мержа |
| §3.2, спека :110-111 | Видимость — `self.disable(tags={"full"})` провайдера | Так и сделано | Побочный эффект: раньше `mcp.disable(tags={"full"})` на сервере прятал и `full`-тулы из `extra_providers`; теперь фильтр действует только на тулы провайдера |

## Вопрос к пользователю до исполнения

**Бюджет ответа для библиотечных пользователей.** `ResponseBudgetMiddleware` — middleware сервера; `PostgresProvider`, добавленный в чужой `FastMCP`, его не получает. План сохраняет текущее поведение (бюджет ставит только `create_server`) и документирует в README ручное подключение: `mcp.add_middleware(ResponseBudgetMiddleware(20000))` с импортом из `postgres_fastmcp.app.middleware.response_budget`. Варианты:

1. Оставить как в плане (документация). Ничего не меняется в коде, но легко забыть.
2. Экспортировать `ResponseBudgetMiddleware` из корня пакета (расширение списка §6).
3. Бюджет на уровне провайдера: `PostgresProvider(..., response_max_tokens=20000)` оборачивает свои тулы (Transform или подкласс `FunctionTool` с проверкой в `run`), серверный middleware остаётся для `extra_providers`. Своих тулов провайдер тогда защищает сам, но проверка идёт до внешних middleware и двоится на `create_server`.

Если выбран вариант 2 или 3, его лучше оформить отдельной задачей после Task 6.

## Файлы

| Файл | Ответственность |
| --- | --- |
| `src/postgres_fastmcp/access.py` (новый) | `EffectiveAccess`, `AccessPolicy`, `AccessResolver`, `build_resolver`, `bounded_resolver`, `full_access_check` |
| `src/postgres_fastmcp/domains/db_access.py` | `DatabaseConfigPort`, `DbAccessPort`, `DbAccess`, `DbAccessService.view()/close()` с кэшем исполнителей |
| `src/postgres_fastmcp/domains/{querying,top_queries}.py`, `domains/catalog/{service,tables,sequences,extensions}.py`, `domains/explain/service.py`, `domains/index_tuning/service.py`, `domains/health/database_health.py` | Типизация против `DbAccessPort`; `sequences` читает `db.table_prefix` |
| `src/postgres_fastmcp/tools/definitions.py` | `ToolSet` — девять тулов как методы |
| `src/postgres_fastmcp/tools/registry.py` | `register_tools(provider, toolset, *, ceiling, full_tool_auth)`, нейтральное описание `execute_sql` |
| `src/postgres_fastmcp/provider.py` (новый) | `PostgresProvider` |
| `src/postgres_fastmcp/app/server.py` | `create_server` поверх провайдера |
| `src/postgres_fastmcp/app/lifespan.py`, `app/context.py` | Удаляются (Task 4) |
| `src/postgres_fastmcp/__init__.py` | Публичное API §6 |
| `README.md`, `AGENTS.md`, `src/postgres_fastmcp/tools/AGENTS.md` | Библиотечное использование, слои, правила авторов тулов |
| `tests/unit/...`, `tests/integration/...` | См. задачи |

Порядок задач: `access.py` (Task 1) нужен `DbAccessService.view` (Task 2); `ToolSet` и новый `register_tools` (Task 3) работают поверх временного моста через lifespan, который Task 4 заменяет провайдером и удаляет. Так после каждого коммита все тесты зелёные. Нейтральное описание `execute_sql` (Task 5) — отдельно: это единственное видимое агенту изменение, ревьюер может отклонить его независимо от рефакторинга.

---
### Task 1: `access.py` — потолок прав, политика и резолвер «всегда потолок»

**Files:**
- Create: `src/postgres_fastmcp/access.py`
- Test: `tests/unit/test_access.py` (новый)

**Interfaces:**
- Consumes: `AccessMode` из `postgres_fastmcp.shared.enums`; `AccessToken`, `AuthCheck`, `AuthContext` из `fastmcp.server.auth`.
- Produces:
  - `EffectiveAccess(access_mode: AccessMode, write_mode: bool)` — `@dataclass(frozen=True, slots=True)`, хешируемый (ключ кэша в Task 2).
  - `AccessPolicy(BaseModel)`: `enforced: bool = False`, `claim: str = "scope"`, `write_values: list[str] = ["pg:write"]`, `full_values: list[str] = ["pg:full"]`.
  - `AccessResolver = Callable[[AccessToken | None], EffectiveAccess]`.
  - `build_resolver(ceiling: EffectiveAccess, policy: AccessPolicy) -> AccessResolver` — всегда `ceiling`; `ValueError` с текстом `AccessPolicy.enforced=True is not supported yet ...` при `policy.enforced`.
  - `bounded_resolver(resolver: AccessResolver, ceiling: EffectiveAccess) -> AccessResolver` — результат не выше потолка (FULL только если оба FULL, запись только если обе разрешают).
  - `full_access_check(resolver: AccessResolver) -> AuthCheck` — `True`, если `resolver(ctx.token).access_mode == AccessMode.FULL`.

- [ ] **Step 1: Написать тест**

Создать `tests/unit/test_access.py`:

```python
"""Тесты access.py: потолок прав, политика, резолвер и проверка full-тулов."""

import dataclasses
from unittest.mock import MagicMock

import pytest
from fastmcp.server.auth import AccessToken, AuthContext

from postgres_fastmcp.access import (
    AccessPolicy,
    EffectiveAccess,
    bounded_resolver,
    build_resolver,
    full_access_check,
)
from postgres_fastmcp.shared.enums import AccessMode


_ALL_ACCESS = [
    EffectiveAccess(AccessMode.BASIC, write_mode=False),
    EffectiveAccess(AccessMode.BASIC, write_mode=True),
    EffectiveAccess(AccessMode.FULL, write_mode=False),
    EffectiveAccess(AccessMode.FULL, write_mode=True),
]


def _token(scopes: list[str]) -> AccessToken:
    return AccessToken(token="t", client_id="c", scopes=scopes)


def test_effective_access_is_frozen_and_hashable() -> None:
    access = EffectiveAccess(AccessMode.FULL, write_mode=True)
    assert {access: 1}[EffectiveAccess(AccessMode.FULL, write_mode=True)] == 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        access.write_mode = False  # type: ignore[misc]


def test_access_policy_defaults() -> None:
    policy = AccessPolicy()
    assert policy.enforced is False
    assert policy.claim == "scope"
    assert policy.write_values == ["pg:write"]
    assert policy.full_values == ["pg:full"]


@pytest.mark.parametrize("ceiling", _ALL_ACCESS)
@pytest.mark.parametrize("token", [None, _token([]), _token(["pg:write", "pg:full"])])
def test_default_resolver_returns_ceiling(ceiling: EffectiveAccess, token: AccessToken | None) -> None:
    """Без auth-шага сужения нет: любой токен (или его отсутствие) даёт серверный потолок."""
    assert build_resolver(ceiling, AccessPolicy())(token) == ceiling


def test_enforced_policy_is_rejected_until_claims_are_supported() -> None:
    """enforced=True без реализации claim-правил молча дал бы полный потолок: отказываем явно."""
    ceiling = EffectiveAccess(AccessMode.FULL, write_mode=True)
    with pytest.raises(ValueError, match="AccessPolicy.enforced"):
        build_resolver(ceiling, AccessPolicy(enforced=True))


@pytest.mark.parametrize("ceiling", _ALL_ACCESS)
@pytest.mark.parametrize("wanted", _ALL_ACCESS)
def test_bounded_resolver_never_exceeds_ceiling(ceiling: EffectiveAccess, wanted: EffectiveAccess) -> None:
    resolved = bounded_resolver(lambda _token: wanted, ceiling)(None)
    full = ceiling.access_mode == AccessMode.FULL and wanted.access_mode == AccessMode.FULL
    assert resolved.access_mode == (AccessMode.FULL if full else AccessMode.BASIC)
    assert resolved.write_mode == (ceiling.write_mode and wanted.write_mode)


def test_bounded_resolver_passes_token_through() -> None:
    seen: list[AccessToken | None] = []
    ceiling = EffectiveAccess(AccessMode.FULL, write_mode=False)
    token = _token(["x"])

    def resolver(t: AccessToken | None) -> EffectiveAccess:
        seen.append(t)
        return ceiling

    bounded_resolver(resolver, ceiling)(token)
    assert seen == [token]


@pytest.mark.parametrize(
    ("ceiling", "allowed"),
    [
        (EffectiveAccess(AccessMode.BASIC, write_mode=True), False),
        (EffectiveAccess(AccessMode.FULL, write_mode=False), True),
    ],
)
def test_full_access_check_follows_resolver(ceiling: EffectiveAccess, *, allowed: bool) -> None:
    """Проверка full-тула пропускает, только если эффективный режим FULL; токен None — потолок."""
    check = full_access_check(build_resolver(ceiling, AccessPolicy()))
    assert check(AuthContext(token=None, component=MagicMock())) is allowed
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `<env> uv run pytest tests/unit/test_access.py -q`
Expected: ошибка сбора `ModuleNotFoundError: No module named 'postgres_fastmcp.access'`.

- [ ] **Step 3: Реализовать `access.py`**

Создать `src/postgres_fastmcp/access.py`:

```python
"""Права одного запроса: серверный потолок, политика по claim токена и резолвер.

Потолок (``EffectiveAccess`` из ``DatabaseConfig``) задаёт максимум прав сервера.
Резолвер переводит токен запроса в эффективные права не выше потолка. Пока
auth-шаг не реализован, резолвер по умолчанию всегда возвращает потолок, а
``AccessPolicy(enforced=True)`` отклоняется, чтобы не выдать права молча.
"""

from collections.abc import Callable
from dataclasses import dataclass

from fastmcp.server.auth import AccessToken, AuthCheck, AuthContext
from pydantic import BaseModel, Field

from postgres_fastmcp.shared.enums import AccessMode


ERROR_POLICY_NOT_SUPPORTED = (
    "AccessPolicy.enforced=True is not supported yet: token claims do not narrow access in this version. "
    "Remove enforced=True or pass a custom access_resolver."
)


@dataclass(frozen=True, slots=True)
class EffectiveAccess:
    """Эффективные права одного запроса (или серверный потолок)."""

    access_mode: AccessMode
    write_mode: bool


class AccessPolicy(BaseModel):
    """Политика сужения прав по claim токена (правила применяются на auth-шаге)."""

    enforced: bool = False
    claim: str = "scope"
    write_values: list[str] = Field(default_factory=lambda: ["pg:write"])
    full_values: list[str] = Field(default_factory=lambda: ["pg:full"])


AccessResolver = Callable[[AccessToken | None], EffectiveAccess]


def build_resolver(ceiling: EffectiveAccess, policy: AccessPolicy) -> AccessResolver:
    """Резолвер по политике: сейчас всегда возвращает потолок.

    Args:
        ceiling: Серверный потолок прав.
        policy: Политика сужения по claim.

    Returns:
        Резолвер токен -> права.

    Raises:
        ValueError: Если policy.enforced=True (claim-правила ещё не реализованы).
    """
    if policy.enforced:
        raise ValueError(ERROR_POLICY_NOT_SUPPORTED)

    def resolve(_token: AccessToken | None) -> EffectiveAccess:
        return ceiling

    return resolve


def bounded_resolver(resolver: AccessResolver, ceiling: EffectiveAccess) -> AccessResolver:
    """Обернуть резолвер так, чтобы результат никогда не превышал потолок.

    Нужен для пользовательского ``access_resolver``: он может вернуть FULL или
    запись при серверном basic/read-only, а исполнитель строится по результату.
    """

    def resolve(token: AccessToken | None) -> EffectiveAccess:
        wanted = resolver(token)
        full = ceiling.access_mode == AccessMode.FULL and wanted.access_mode == AccessMode.FULL
        return EffectiveAccess(
            access_mode=AccessMode.FULL if full else AccessMode.BASIC,
            write_mode=ceiling.write_mode and wanted.write_mode,
        )

    return resolve


def full_access_check(resolver: AccessResolver) -> AuthCheck:
    """AuthCheck для full-тулов: тул виден и вызываем, только если эффективный режим FULL.

    Args:
        resolver: Резолвер токен -> права (токен None в stdio и без auth).

    Returns:
        Проверка для ``Tool.from_function(auth=...)``.
    """

    def check(ctx: AuthContext) -> bool:
        return resolver(ctx.token).access_mode == AccessMode.FULL

    return check
```

- [ ] **Step 4: Запустить тесты**

Run: `<env> uv run pytest tests/unit/test_access.py -q`
Expected: `34 passed`.

Run: `<env> uv run pytest tests/unit -q`
Expected: `724 passed`.

- [ ] **Step 5: Линтеры и коммит**

Run: `uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff format tests/unit/test_access.py`
Expected: `All checks passed!`, `Success: no issues found in 77 source files`, форматирование без изменений.

```bash
git add src/postgres_fastmcp/access.py tests/unit/test_access.py
git commit -m "feat(access): add effective access, access policy and a ceiling resolver"
```

---

### Task 2: `DbAccessPort` и `DbAccessService.view()`; домены на порту

**Files:**
- Modify: `src/postgres_fastmcp/domains/db_access.py` (переписать целиком)
- Modify: `src/postgres_fastmcp/domains/querying.py:5,12`, `domains/top_queries.py:8,191`, `domains/catalog/service.py:3,17`, `domains/catalog/tables.py:6,20`, `domains/catalog/sequences.py:5,14,45-46`, `domains/catalog/extensions.py:5,13`, `domains/explain/service.py:5,19`, `domains/index_tuning/service.py:5,15`, `domains/health/database_health.py:15,39-45`
- Modify (временный мост до Task 4): `src/postgres_fastmcp/app/lifespan.py:28-29`, `src/postgres_fastmcp/app/context.py:13,19,23-25`, `src/postgres_fastmcp/tools/definitions.py:123`
- Test: `tests/unit/domains/test_db_access.py` (новый), `tests/unit/conftest.py`, `tests/unit/domains/test_catalog_service.py`, `tests/unit/domains/health/test_database_health.py:34`, `tests/unit/tools/test_definitions.py:242,254`, `tests/unit/app/test_lifespan.py`, `tests/unit/app/test_response_budget.py:87`
- Test (интеграция, статическая проверка): `tests/integration/conftest.py`, `tests/integration/dta/conftest.py`, `tests/integration/dta/test_dta_calc_integration.py`, `tests/integration/test_table_prefix.py`, `tests/integration/test_top_queries_integration.py`, `tests/integration/test_sql_hardening.py`

**Interfaces:**
- Consumes: `EffectiveAccess` (Task 1).
- Produces:
  - `DbAccessPort(Protocol)` с `@property`: `sql_driver: SqlDriverPort`, `access_mode: AccessMode`, `write_mode: bool`, `table_prefix: str | None`, `connection_id: str`.
  - `DbAccess` — `@dataclass(frozen=True, slots=True)` с теми же полями.
  - `DbAccessService(config: DatabaseConfigPort)`: `view(access: EffectiveAccess) -> DbAccess`, `async close() -> None`; приватные `_pool: DbConnPool`, `_executors: dict[EffectiveAccess, SqlDriverPort]`. Свойств `sql_driver`, `access_mode`, `write_mode`, `table_prefix`, `db_connection`, `config` у сервиса больше нет.
  - `DatabaseHealthAnalyzer(db: DbAccessPort)` (было `sql_driver`).
  - Все доменные сервисы и функции принимают `DbAccessPort`.
  - Временно (до Task 4): lifespan кладёт в `ctx.lifespan_context["db"]` уже `DbAccess` с правами потолка; `app.context.get_db(ctx) -> DbAccessPort`.
  - Интеграционные фикстуры: `db_full`, `db_user_prefix`, `db_with_hypopg` отдают `DbAccess`.

- [ ] **Step 1: Написать тест `view()`**

Создать `tests/unit/domains/test_db_access.py`:

```python
"""Тесты DbAccessService.view: исполнитель по правам запроса, кэш и один пул."""

from unittest.mock import AsyncMock

import pytest

from postgres_fastmcp.access import EffectiveAccess
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.domains.db_access import DbAccess, DbAccessService
from postgres_fastmcp.postgres.driver import SqlExecutor
from postgres_fastmcp.postgres.security.driver import SafeSqlExecutor
from postgres_fastmcp.shared.enums import AccessMode


_ALL_ACCESS = [
    EffectiveAccess(AccessMode.BASIC, write_mode=False),
    EffectiveAccess(AccessMode.BASIC, write_mode=True),
    EffectiveAccess(AccessMode.FULL, write_mode=False),
    EffectiveAccess(AccessMode.FULL, write_mode=True),
]


def _service(**overrides: object) -> DbAccessService:
    config = DatabaseConfig(host="h", user="u", password="p", name="d", **overrides)
    return DbAccessService(config)


def test_view_carries_request_access_and_config_fields() -> None:
    service = _service(table_prefix="app_")
    view = service.view(EffectiveAccess(AccessMode.BASIC, write_mode=True))
    assert isinstance(view, DbAccess)
    assert view.access_mode == AccessMode.BASIC
    assert view.write_mode is True
    assert view.table_prefix == "app_"
    assert view.connection_id.startswith("postgresql://u:p@h:5432/d")


def test_only_full_write_gets_the_unrestricted_executor() -> None:
    service = _service()
    for access in _ALL_ACCESS:
        driver = service.view(access).sql_driver
        unrestricted = access == EffectiveAccess(AccessMode.FULL, write_mode=True)
        assert isinstance(driver, SqlExecutor if unrestricted else SafeSqlExecutor), access


@pytest.mark.parametrize(
    ("access", "schema", "read_only", "prefix", "explain_analyze"),
    [
        (EffectiveAccess(AccessMode.BASIC, write_mode=False), "public", True, "app_", False),
        (EffectiveAccess(AccessMode.BASIC, write_mode=True), "public", False, "app_", False),
        (EffectiveAccess(AccessMode.FULL, write_mode=False), None, True, None, True),
    ],
)
def test_safe_executor_is_built_from_access_not_config(
    access: EffectiveAccess, schema: str | None, *, read_only: bool, prefix: str | None, explain_analyze: bool
) -> None:
    """Конфиг сервиса — FULL+write, но исполнитель собирается по правам запроса."""
    service = _service(access_mode=AccessMode.FULL, write_mode=True, table_prefix="app_", safe_sql_timeout=7)
    driver = service.view(access).sql_driver
    assert isinstance(driver, SafeSqlExecutor)
    assert driver._config.allowed_schema == schema
    assert driver._config.read_only is read_only
    assert driver._config.table_prefix == prefix
    assert driver._config.timeout == 7
    assert driver._config.query_tag == "postgres_fastmcp"
    assert driver._validator.allow_explain_analyze is explain_analyze


def test_executors_are_cached_per_access_and_share_one_pool() -> None:
    service = _service()
    first = {access: service.view(access).sql_driver for access in _ALL_ACCESS}
    again = {access: service.view(access).sql_driver for access in _ALL_ACCESS}
    assert first == again
    assert len(service._executors) == 4
    pools = {
        id(d._delegate.conn if isinstance(d, SafeSqlExecutor) else d.conn)  # type: ignore[attr-defined]
        for d in first.values()
    }
    assert pools == {id(service._pool)}


async def test_close_closes_the_pool() -> None:
    service = _service()
    service._pool.close = AsyncMock()  # type: ignore[method-assign]
    await service.close()
    service._pool.close.assert_awaited_once_with()


def test_service_has_no_sql_driver() -> None:
    """Исполнитель выдаётся только через view(access): у сервиса нет «общего» sql_driver."""
    assert not hasattr(_service(), "sql_driver")
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `<env> uv run pytest tests/unit/domains/test_db_access.py -q`
Expected: ошибка сбора `ImportError: cannot import name 'DbAccess' from 'postgres_fastmcp.domains.db_access'`.

- [ ] **Step 3: Переписать `domains/db_access.py`**

Заменить `src/postgres_fastmcp/domains/db_access.py` целиком (логи теперь на английском по правилу языка; проверка пустого URL ушла — `DatabaseConfig` не создаётся без полного подключения):

```python
"""Доступ к базе данных: один пул на сервис и исполнитель под права конкретного запроса."""

from dataclasses import dataclass
from typing import Protocol

from postgres_fastmcp.access import EffectiveAccess
from postgres_fastmcp.postgres.connection import DbConnPool
from postgres_fastmcp.postgres.driver import SqlExecutor
from postgres_fastmcp.postgres.ports import SqlDriverPort
from postgres_fastmcp.postgres.security.driver import SafeSqlConfig, SafeSqlExecutor
from postgres_fastmcp.postgres.security.query_validator import QueryValidator
from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.shared.logger import get_logger


logger = get_logger(__name__)

DEFAULT_QUERY_TAG = "postgres_fastmcp"


class DatabaseConfigPort(Protocol):
    """Поля конфигурации БД, нужные сервису доступа.

    Структурно реализуется ``app.config.database.DatabaseConfig`` — сам домен
    при этом не зависит от слоя приложения.
    """

    pool_min_size: int
    pool_max_size: int
    write_mode: bool
    access_mode: AccessMode
    safe_sql_timeout: int
    table_prefix: str | None
    query_tag: str | None

    @property
    def database_uri(self) -> str | None:
        """Строка подключения к БД (None, если не задана)."""
        ...


class DbAccessPort(Protocol):
    """То, что домены получают на один запрос: исполнитель и права этого запроса."""

    @property
    def sql_driver(self) -> SqlDriverPort:
        """Исполнитель SQL под права запроса."""
        ...

    @property
    def access_mode(self) -> AccessMode:
        """Эффективный уровень доступа запроса."""
        ...

    @property
    def write_mode(self) -> bool:
        """Разрешена ли запись в этом запросе."""
        ...

    @property
    def table_prefix(self) -> str | None:
        """Префикс имён таблиц из конфигурации (действует в BASIC)."""
        ...

    @property
    def connection_id(self) -> str:
        """Устойчивый идентификатор соединения (ключ кэшей версий и расширений)."""
        ...


@dataclass(frozen=True, slots=True)
class DbAccess:
    """Реализация DbAccessPort для одного запроса."""

    sql_driver: SqlDriverPort
    access_mode: AccessMode
    write_mode: bool
    table_prefix: str | None
    connection_id: str


class DbAccessService:
    """Пул подключений и исполнители, закэшированные по эффективным правам (не больше четырёх)."""

    def __init__(self, config: DatabaseConfigPort) -> None:
        """Инициализация с конфигурацией базы данных; пул открывается лениво при первом запросе.

        Args:
            config: Конфигурация базы данных.
        """
        self._config = config
        self._pool = DbConnPool(
            connection_url=config.database_uri,
            min_size=config.pool_min_size,
            max_size=config.pool_max_size,
        )
        self._executors: dict[EffectiveAccess, SqlDriverPort] = {}

    def view(self, access: EffectiveAccess) -> DbAccess:
        """Доступ к БД для одного запроса с заданными правами.

        Args:
            access: Эффективные права запроса (не выше серверного потолка).

        Returns:
            DbAccess с исполнителем под эти права.
        """
        return DbAccess(
            sql_driver=self._executor(access),
            access_mode=access.access_mode,
            write_mode=access.write_mode,
            table_prefix=self._config.table_prefix,
            connection_id=self._pool.connection_url or "",
        )

    async def close(self) -> None:
        """Закрыть пул подключений. Вызывать при завершении жизненного цикла сервиса."""
        logger.debug("Closing the database connection pool")
        try:
            await self._pool.close()
        except Exception as e:
            logger.error("Failed to close the database connection pool: %s", e)

    def _executor(self, access: EffectiveAccess) -> SqlDriverPort:
        """Исполнитель под права: создаётся при первом обращении и переиспользуется."""
        cached = self._executors.get(access)
        if cached is not None:
            return cached

        base = SqlExecutor(conn=self._pool)
        executor: SqlDriverPort
        if access.access_mode == AccessMode.FULL and access.write_mode:
            logger.debug("Using unrestricted SqlExecutor (access_mode=full, write_mode=True)")
            executor = base
        else:
            basic = access.access_mode == AccessMode.BASIC
            safe_config = SafeSqlConfig(
                timeout=self._config.safe_sql_timeout,
                allowed_schema="public" if basic else None,
                read_only=not access.write_mode,
                query_tag=self._config.query_tag or DEFAULT_QUERY_TAG,
                table_prefix=self._config.table_prefix if basic else None,
            )
            validator = QueryValidator(
                allowed_schema=safe_config.allowed_schema,
                table_prefix=safe_config.table_prefix,
                read_only=safe_config.read_only,
                allow_explain_analyze=not basic,
            )
            logger.debug(
                "Using SafeSqlExecutor (access_mode=%s, write_mode=%s, allowed_schema=%s, "
                "read_only=%s, allow_explain_analyze=%s, timeout=%ss, table_prefix=%s)",
                access.access_mode,
                access.write_mode,
                safe_config.allowed_schema,
                safe_config.read_only,
                not basic,
                safe_config.timeout,
                safe_config.table_prefix,
            )
            executor = SafeSqlExecutor(delegate=base, validator=validator, config=safe_config)
        self._executors[access] = executor
        return executor
```

Run: `<env> uv run pytest tests/unit/domains/test_db_access.py -q`
Expected: `8 passed`.

- [ ] **Step 4: Домены на `DbAccessPort`**

Run:
```bash
sed -i 's/\bDbAccessService\b/DbAccessPort/g' \
  src/postgres_fastmcp/domains/querying.py src/postgres_fastmcp/domains/top_queries.py \
  src/postgres_fastmcp/domains/catalog/service.py src/postgres_fastmcp/domains/catalog/tables.py \
  src/postgres_fastmcp/domains/catalog/sequences.py src/postgres_fastmcp/domains/catalog/extensions.py \
  src/postgres_fastmcp/domains/explain/service.py src/postgres_fastmcp/domains/index_tuning/service.py
sed -i 's/self\.db\.config\.table_prefix/self.db.table_prefix/g' src/postgres_fastmcp/domains/catalog/sequences.py
```

Результат в `domains/catalog/sequences.py:45-46`:

```python
        if self.db.access_mode == AccessMode.BASIC and self.db.table_prefix:
            prefix = self.db.table_prefix.lower()
```

В `src/postgres_fastmcp/domains/health/database_health.py` удалить импорт `from postgres_fastmcp.postgres.ports import QueryExecutorPort`, добавить первым в блоке импортов проекта `from postgres_fastmcp.domains.db_access import DbAccessPort` и заменить конструктор `DatabaseHealthAnalyzer`:

```python
    def __init__(self, db: DbAccessPort) -> None:
        """Инициализация инструмента проверки состояния базы данных.

        Args:
            db: Доступ к БД для текущего запроса.
        """
        self.sql_driver = db.sql_driver
```

- [ ] **Step 5: Временный мост в lifespan и тулах**

В `src/postgres_fastmcp/app/lifespan.py` добавить импорт `from postgres_fastmcp.access import EffectiveAccess` (перед `from postgres_fastmcp.app.config import Settings`), в docstring `build_lifespan` заменить `` ``{"db": ..., "settings": ...}`` `` на `` ``{"db": <доступ с правами потолка>, "settings": ...}`` ``, а тело `lifespan` начать так:

```python
        db = DbAccessService(settings.database)
        ceiling = EffectiveAccess(settings.database.access_mode, write_mode=settings.database.write_mode)
        context: LifespanContext = {"db": db.view(ceiling), "settings": settings}
```

В `src/postgres_fastmcp/app/context.py` заменить импорт `DbAccessService` на `DbAccessPort`, поле `db: DbAccessService` на `db: DbAccessPort` и функцию:

```python
def get_db(ctx: Context) -> DbAccessPort:
    """Вернуть доступ к БД из lifespan-контекста (единый типизированный доступ для всех тулов)."""
    return cast("DbAccessPort", ctx.lifespan_context["db"])
```

В `src/postgres_fastmcp/tools/definitions.py:123`:

```python
    health_tool = DatabaseHealthAnalyzer(get_db(ctx))
```

- [ ] **Step 6: Юнит-тесты на новый порт**

`tests/unit/conftest.py` заменить целиком:

```python
# mypy: ignore-errors
"""Shared fixtures for unit tests: mock executor and a per-request DbAccess."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.domains.db_access import DbAccess


@pytest.fixture
def mock_executor() -> AsyncMock:
    """Mock QueryExecutorPort (sql_driver) used by all services."""
    executor = AsyncMock()
    executor.execute = AsyncMock(return_value=[])
    executor.render = MagicMock(side_effect=lambda q, p: q)
    return executor


@pytest.fixture
def mock_db_access(mock_executor: AsyncMock) -> MagicMock:
    """Mock DbAccess (DbAccessPort) with preconfigured sql_driver for service tests."""
    db = MagicMock(spec=DbAccess)
    db.sql_driver = mock_executor
    db.connection_id = "test://localhost:5432/testdb"
    db.access_mode = AccessMode.FULL
    db.write_mode = False
    db.table_prefix = None
    return db
```

В `tests/unit/domains/test_catalog_service.py` сразу после `test_list_objects_sequence_returns_list_with_name_and_data_type` (класс `TestCatalogServiceListObjects`) добавить регрессию на `sequences.py:45-46` (со старым `self.db.config.table_prefix` она падает `AttributeError`: у `DbAccess` нет `config`):

```python
    async def test_list_objects_sequence_basic_filters_by_table_prefix(
        self,
        mock_db_access: MagicMock,
        mock_executor: MagicMock,
    ) -> None:
        """BASIC: sequences are filtered by the table_prefix of the request's DbAccess."""
        mock_db_access.access_mode = AccessMode.BASIC
        mock_db_access.table_prefix = "app_"
        mock_executor.execute.return_value = [
            RowResult(cells={"sequence_schema": "public", "sequence_name": "app_seq", "data_type": "bigint"}),
            RowResult(cells={"sequence_schema": "public", "sequence_name": "other_seq", "data_type": "bigint"}),
        ]
        service = CatalogService(db=mock_db_access)
        result = await service.list_objects(schema_name="public", object_type="sequence")
        assert [row["name"] for row in result] == ["app_seq"]
```

В `tests/unit/domains/health/test_database_health.py` заменить `tool = dh.DatabaseHealthAnalyzer(sql_driver=mock_db_access.sql_driver)` на `tool = dh.DatabaseHealthAnalyzer(mock_db_access)`.

В `tests/unit/tools/test_definitions.py` (две строки) заменить `lambda sql_driver: fake_tool` на `lambda db: fake_tool`.

`tests/unit/app/test_lifespan.py` заменить целиком (файл удаляется в Task 4, до тех пор проверяет мост):

```python
"""Тесты для фабрики lifespan: создание и закрытие DbAccessService."""

import pytest

from postgres_fastmcp.access import EffectiveAccess
from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.lifespan import build_lifespan


@pytest.mark.asyncio
async def test_lifespan_yields_db_and_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lifespan кладёт доступ с правами потолка и Settings в context, на выходе закрывает пул."""
    closed = {"count": 0}

    class FakeDb:
        def __init__(self, cfg: object) -> None:
            self.cfg = cfg
            self.access: object = None

        def view(self, access: object) -> "FakeDb":
            self.access = access
            return self

        async def close(self) -> None:
            closed["count"] += 1

    monkeypatch.setattr("postgres_fastmcp.app.lifespan.DbAccessService", FakeDb)

    settings = Settings()
    lifespan_cm = build_lifespan(settings)
    async with lifespan_cm(server=None) as ctx:
        assert ctx["settings"] is settings
        assert isinstance(ctx["db"], FakeDb)
        assert ctx["db"].cfg is settings.database
        assert ctx["db"].access == EffectiveAccess(
            settings.database.access_mode, write_mode=settings.database.write_mode
        )

    assert closed["count"] == 1


@pytest.mark.asyncio
async def test_lifespan_closes_db_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """При исключении внутри async-with пул должен быть закрыт через finally."""
    closed = {"count": 0}

    class FakeDb:
        def __init__(self, cfg: object) -> None: ...

        def view(self, access: object) -> "FakeDb":
            return self

        async def close(self) -> None:
            closed["count"] += 1

    monkeypatch.setattr("postgres_fastmcp.app.lifespan.DbAccessService", FakeDb)

    settings = Settings()
    lifespan_cm = build_lifespan(settings)
    with pytest.raises(ValueError, match="boom"):
        async with lifespan_cm(server=None):
            raise ValueError("boom")
    assert closed["count"] == 1
```

В `tests/unit/app/test_response_budget.py` в классе `FakeDb` внутри `_server_with_rows` перед `async def close` добавить:

```python
        def view(self, access: object) -> "FakeDb":
            return self
```

Run: `<env> uv run pytest tests/unit -q`
Expected: `733 passed`.

- [ ] **Step 7: Интеграционные тесты на `DbAccess`**

`tests/integration/conftest.py` заменить целиком:

```python
# mypy: ignore-errors
"""Fixtures for integration tests: settings and per-request DbAccess views from Docker PostgreSQL."""

from collections.abc import AsyncGenerator

import pytest

from postgres_fastmcp.access import EffectiveAccess
from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.domains.db_access import DbAccess, DbAccessService
from postgres_fastmcp.shared.enums import AccessMode


@pytest.fixture
def integration_settings(
    test_postgres_connection_string: tuple[str, str],
) -> Settings:
    """Settings with database pointing to the test PostgreSQL (access_mode=full, write_mode=True)."""
    connection_string, _ = test_postgres_connection_string
    database = DatabaseConfig.from_uri(
        connection_string,
        access_mode=AccessMode.FULL,
        write_mode=True,
    )
    return Settings(database=database)


@pytest.fixture
async def db_full(
    integration_settings: Settings,
) -> AsyncGenerator[DbAccess, None]:
    """DbAccess with access_mode=full and write_mode=True for DDL and setup."""
    service = DbAccessService(integration_settings.database)
    try:
        yield service.view(EffectiveAccess(AccessMode.FULL, write_mode=True))
    finally:
        await service.close()


@pytest.fixture
async def db_user_prefix(
    test_postgres_connection_string: tuple[str, str],
) -> AsyncGenerator[DbAccess, None]:
    """DbAccess with access_mode=basic and table_prefix=app_ for table_prefix tests."""
    connection_string, _ = test_postgres_connection_string
    config = DatabaseConfig.from_uri(
        connection_string,
        access_mode=AccessMode.BASIC,
        write_mode=False,
        table_prefix="app_",
    )
    service = DbAccessService(config)
    try:
        yield service.view(EffectiveAccess(AccessMode.BASIC, write_mode=False))
    finally:
        await service.close()
```

Переименовать фикстуры и типы в остальных файлах:

```bash
sed -i -e 's/db_service_with_hypopg/db_with_hypopg/g' -e 's/db_service_full/db_full/g' \
  -e 's/db_service_user_prefix/db_user_prefix/g' -e 's/\bDbAccessService\b/DbAccess/g' \
  tests/integration/dta/conftest.py tests/integration/dta/test_dta_calc_integration.py \
  tests/integration/test_table_prefix.py tests/integration/test_top_queries_integration.py
```

`tests/integration/test_table_prefix.py` после `sed` ещё создаёт сервисы сам. Импорты в начале файла:

```python
import pytest

from postgres_fastmcp.access import EffectiveAccess
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.domains.catalog.service import CatalogService
from postgres_fastmcp.domains.db_access import DbAccess, DbAccessService
from postgres_fastmcp.postgres.security.driver import SafeSqlExecutor
from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.shared.errors import SchemaNotAllowedError, TablePrefixAccessError
```

и тело `test_table_prefix_with_different_prefixes` (последний тест файла) целиком:

```python
@pytest.mark.asyncio
async def test_table_prefix_with_different_prefixes(
    test_postgres_connection_string: tuple[str, str],
) -> None:
    """Different table_prefix values work correctly."""
    connection_string, _ = test_postgres_connection_string
    full_config = DatabaseConfig.from_uri(
        connection_string,
        access_mode=AccessMode.FULL,
        write_mode=True,
    )
    user_config = DatabaseConfig.from_uri(
        connection_string,
        access_mode=AccessMode.BASIC,
        write_mode=False,
        table_prefix="user_",
    )

    full_svc = DbAccessService(full_config)
    try:
        full_sql = full_svc.view(EffectiveAccess(AccessMode.FULL, write_mode=True)).sql_driver
        await full_sql.execute(
            "CREATE TABLE IF NOT EXISTS user_data (id INTEGER)",
            readonly=False,
        )
        await full_sql.execute(
            "CREATE TABLE IF NOT EXISTS user_settings (id INTEGER)",
            readonly=False,
        )
        await full_sql.execute(
            "CREATE TABLE IF NOT EXISTS admin_logs (id INTEGER)",
            readonly=False,
        )
    finally:
        await full_svc.close()

    user_svc = DbAccessService(user_config)
    try:
        sql_driver = user_svc.view(EffectiveAccess(AccessMode.BASIC, write_mode=False)).sql_driver

        result1 = await sql_driver.execute("SELECT * FROM user_data LIMIT 1", readonly=True)
        assert result1 is not None

        result2 = await sql_driver.execute("SELECT * FROM user_settings LIMIT 1", readonly=True)
        assert result2 is not None

        with pytest.raises(TablePrefixAccessError):
            await sql_driver.execute("SELECT * FROM admin_logs LIMIT 1", readonly=True)
    finally:
        await user_svc.close()
```

В `tests/integration/test_sql_hardening.py` добавить `from postgres_fastmcp.access import EffectiveAccess` перед `from postgres_fastmcp.app.config import Settings`, а в `test_statement_timeout_cancels_long_query` заменить блок после `service = DbAccessService(config)`:

```python
    service = DbAccessService(config)
    sql_driver = service.view(EffectiveAccess(AccessMode.FULL, write_mode=False)).sql_driver
    try:
        with pytest.raises(QueryTimeoutError):
            await sql_driver.execute("SELECT count(*) FROM generate_series(1, 10000000000)")
        # Пул после statement_timeout остаётся валидным: следующий запрос идёт без пересоздания.
        assert service._pool.is_valid is True
        rows = await sql_driver.execute("SELECT 1 AS one")
        assert rows is not None and rows[0].cells["one"] == 1
    finally:
        await service.close()
```

Статическая проверка:

Run: `<env> uv run pytest tests/integration -q`
Expected: `78 skipped`, без ошибок сбора.

Run: `uv run ruff check tests/integration --select F,ARG --no-fix`
Expected: `All checks passed!`

Run: `grep -rn "db_service\|\.db_connection\|svc\.sql_driver\|service\.sql_driver" tests/integration`
Expected: пусто.

- [ ] **Step 8: Линтеры и коммит**

Run:
```bash
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && \
uv run ruff format tests/unit/domains/test_db_access.py tests/unit/conftest.py tests/unit/app/test_lifespan.py \
  tests/unit/app/test_response_budget.py tests/unit/domains/health/test_database_health.py \
  tests/unit/domains/test_catalog_service.py tests/unit/tools/test_definitions.py tests/integration
```
Expected: ruff исправляет порядок импортов в `domains/health/database_health.py` (`I001`), если импорт `DbAccessPort` вставлен не по алфавиту; mypy `Success: no issues found in 77 source files`; `ruff format` переформатирует `tests/unit/domains/health/test_database_health.py` (старый `with ... \` превращается в скобочный `with (...)`) — это ожидаемо.

Run: `<env> uv run pytest tests/unit -q`
Expected: `733 passed`.

```bash
git add src/postgres_fastmcp/domains src/postgres_fastmcp/app/lifespan.py src/postgres_fastmcp/app/context.py \
  src/postgres_fastmcp/tools/definitions.py tests/unit tests/integration
git commit -m "refactor(db_access): give domains a per-request DbAccess view built from EffectiveAccess"
```

---

### Task 3: `ToolSet` и регистрация в `LocalProvider`

**Files:**
- Modify: `src/postgres_fastmcp/tools/definitions.py` (переписать целиком)
- Modify: `src/postgres_fastmcp/tools/registry.py` (переписать целиком)
- Modify (временный мост до Task 4): `src/postgres_fastmcp/app/context.py`, `src/postgres_fastmcp/app/server.py`
- Test: `tests/unit/tools/conftest.py`, `tests/unit/tools/test_definitions.py`, `tests/unit/tools/test_registry.py`, `tests/unit/tools/test_tool_descriptions.py`, `tests/unit/tools/test_params.py`

**Interfaces:**
- Consumes: `DbAccessPort`, `DatabaseConfigPort` (Task 2).
- Produces:
  - `ToolSet(get_db: Callable[[], DbAccessPort])` с async-методами `execute_sql`, `explain_query`, `list_objects`, `get_object_details`, `list_schemas`, `analyze_db_health`, `get_top_queries`, `analyze_query_indexes`, `analyze_workload_indexes`. Параметры, docstring и возвращаемые типы — как у прежних функций, без `ctx`.
  - `register_tools(provider: LocalProvider, toolset: ToolSet, *, ceiling: DatabaseConfigPort, full_tool_auth: AuthCheck | None = None) -> None` — регистрирует все 9 тулов (видимость не трогает); `full_tool_auth` уходит в `auth=` пяти `full`-тулов.
  - Пресеты `READ_ONLY_IDEMPOTENT`, `READ_ONLY_NON_IDEMPOTENT`, `WRITE_NON_DESTRUCTIVE`, `DESTRUCTIVE` — без изменений.
  - Временно (до Task 4): `app.context.get_db() -> DbAccessPort` без аргументов (через `get_context()`); `create_server` регистрирует тулы в своём `LocalProvider` и передаёт его в `providers`.

- [ ] **Step 1: Фикстуры и тесты тулов**

`tests/unit/tools/conftest.py` заменить целиком:

```python
"""Общие фикстуры для тестов тулов."""

from unittest import mock

import pytest

from postgres_fastmcp.tools.definitions import ToolSet


@pytest.fixture
def db_mock() -> mock.AsyncMock:
    """Готовый AsyncMock на роль DbAccessPort."""
    return mock.AsyncMock()


@pytest.fixture
def toolset(db_mock: mock.AsyncMock) -> ToolSet:
    """ToolSet, который на каждый вызов тула отдаёт db_mock."""
    return ToolSet(get_db=lambda: db_mock)
```

Перевести `tests/unit/tools/test_definitions.py` на методы `ToolSet` скриптом (временный файл, как требует `AGENTS.md`):

```bash
cat > /tmp/convert_test_definitions.py <<'PY'
import re
from pathlib import Path

path = Path("tests/unit/tools/test_definitions.py")
text = path.read_text()
text = text.replace(
    '"""Тесты всех MCP-тулов: тонкие функции из tools.definitions."""',
    '"""Тесты всех MCP-тулов: методы ToolSet из tools.definitions."""',
)
text = text.replace("make_ctx", "toolset")
tools = (
    "execute_sql|explain_query|list_objects|get_object_details|list_schemas|"
    "analyze_db_health|get_top_queries|analyze_query_indexes|analyze_workload_indexes"
)
text = re.sub(rf"defs\.({tools})\(", r"toolset.\1(", text)
text = text.replace("(ctx=toolset(db_mock))", "()")
text = text.replace(", ctx=toolset(db_mock)", "")
text = text.replace("        ctx=toolset(db_mock),\n", "")
path.write_text(text)
PY
uv run python /tmp/convert_test_definitions.py && rm /tmp/convert_test_definitions.py
grep -n "ctx" tests/unit/tools/test_definitions.py
```
Expected: `grep` ничего не находит. `monkeypatch.setattr(defs, "querying", ...)` и прочие подмены модуля `defs` остаются: `ToolSet` живёт в том же модуле.

В конец `tests/unit/tools/test_definitions.py` добавить:

```python
@pytest.mark.asyncio
async def test_toolset_resolves_db_on_every_call(monkeypatch) -> None:
    """ToolSet не кэширует доступ к БД: права запроса берутся заново на каждый вызов тула."""
    fake_querying = mock.AsyncMock()
    fake_querying.execute_sql.return_value = []
    monkeypatch.setattr(defs, "querying", fake_querying)
    first, second = object(), object()
    toolset = defs.ToolSet(get_db=iter([first, second]).__next__)

    await toolset.execute_sql(sql="SELECT 1")
    await toolset.execute_sql(sql="SELECT 2")

    assert [call.args[0] for call in fake_querying.execute_sql.await_args_list] == [first, second]
```

`tests/unit/tools/test_registry.py` заменить целиком (добавлены три теста на `full_tool_auth`; `test_register_tools_unique_names_total_nine` теперь требует ровно 9):

```python
"""Тесты для tools/registry.py: регистрация 9 тулов ToolSet в LocalProvider."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import Client, FastMCP
from fastmcp.server.auth import AuthContext
from fastmcp.server.providers import LocalProvider

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.shared.enums import AccessMode, ToolTag
from postgres_fastmcp.tools.definitions import ToolSet
from postgres_fastmcp.tools.registry import (
    DESTRUCTIVE,
    READ_ONLY_IDEMPOTENT,
    READ_ONLY_NON_IDEMPOTENT,
    WRITE_NON_DESTRUCTIVE,
    register_tools,
)


def _database(access_mode: AccessMode, *, write_mode: bool = False, safe_sql_timeout: int = 30) -> DatabaseConfig:
    return Settings().database.model_copy(
        update={"access_mode": access_mode, "write_mode": write_mode, "safe_sql_timeout": safe_sql_timeout}
    )


def _provider(database: DatabaseConfig, db: object | None = None, **kwargs: object) -> LocalProvider:
    """LocalProvider с зарегистрированными тулами; get_db отдаёт db (или MagicMock)."""
    provider = LocalProvider()
    register_tools(provider, ToolSet(get_db=lambda: db or MagicMock()), ceiling=database, **kwargs)
    return provider


def _registered_tool_names(provider: LocalProvider) -> set[str]:
    return {t.name for t in asyncio.run(provider.list_tools())}


def test_register_tools_basic_contains_basic_four() -> None:
    names = _registered_tool_names(_provider(_database(AccessMode.BASIC)))
    assert {"execute_sql", "list_objects", "get_object_details", "explain_query"} <= names


def test_register_tools_full_includes_all_nine() -> None:
    names = _registered_tool_names(_provider(_database(AccessMode.FULL, write_mode=True)))
    assert {
        "execute_sql",
        "list_objects",
        "get_object_details",
        "explain_query",
        "list_schemas",
        "analyze_db_health",
        "get_top_queries",
        "analyze_query_indexes",
        "analyze_workload_indexes",
    } <= names


def test_register_tools_unique_names_total_nine() -> None:
    """Регистрация не зависит от режима: все 9 тулов есть всегда, видимость настраивает провайдер."""
    names = _registered_tool_names(_provider(_database(AccessMode.BASIC)))
    assert len(names) == 9


def test_annotation_presets_use_snake_case_keys() -> None:
    """Пресеты аннотаций задают snake_case-поля SDK v2, а не camelCase-алиасы v1."""
    expected = {"read_only_hint", "destructive_hint", "idempotent_hint", "open_world_hint"}
    for preset in (READ_ONLY_IDEMPOTENT, READ_ONLY_NON_IDEMPOTENT, WRITE_NON_DESTRUCTIVE, DESTRUCTIVE):
        assert set(preset) == expected


async def test_registered_tools_expose_snake_case_annotations() -> None:
    """Аннотации тулов заданы snake_case-полями SDK v2 и доходят до зарегистрированного Tool."""
    provider = _provider(_database(AccessMode.FULL))

    list_objects = await provider.get_tool("list_objects")
    execute_sql = await provider.get_tool("execute_sql")

    assert list_objects is not None and list_objects.annotations is not None
    assert list_objects.annotations.read_only_hint is True
    assert list_objects.annotations.destructive_hint is False
    assert list_objects.annotations.idempotent_hint is True
    assert list_objects.annotations.open_world_hint is True

    assert execute_sql is not None and execute_sql.annotations is not None
    assert execute_sql.annotations.read_only_hint is True  # FULL без write_mode: read-only
    assert execute_sql.annotations.idempotent_hint is False


@pytest.mark.parametrize(
    ("access_mode", "write_mode", "read_only", "destructive"),
    [
        (AccessMode.BASIC, False, True, False),
        (AccessMode.FULL, False, True, False),
        (AccessMode.BASIC, True, False, False),
        (AccessMode.FULL, True, False, True),
    ],
)
async def test_execute_sql_annotations_follow_write_mode(
    access_mode: AccessMode, *, write_mode: bool, read_only: bool, destructive: bool
) -> None:
    """С write_mode execute_sql пишет в любом режиме: read_only_hint=False; destructive только FULL+write."""
    execute_sql = await _provider(_database(access_mode, write_mode=write_mode)).get_tool("execute_sql")

    assert execute_sql is not None and execute_sql.annotations is not None
    assert execute_sql.annotations.read_only_hint is read_only
    assert execute_sql.annotations.destructive_hint is destructive
    assert execute_sql.annotations.idempotent_hint is False
    assert execute_sql.annotations.open_world_hint is True


# Таймауты тулов до выравнивания со statement_timeout: ни один не должен стать короче.
_PRE_CHANGE_TIMEOUTS: dict[str, float] = {
    "execute_sql": 30.0,
    "list_objects": 30.0,
    "get_object_details": 30.0,
    "explain_query": 30.0,
    "list_schemas": 30.0,
    "analyze_db_health": 60.0,
    "get_top_queries": 30.0,
    "analyze_query_indexes": 60.0,
    "analyze_workload_indexes": 60.0,
}


def _registered_timeouts(database: DatabaseConfig) -> dict[str, float | None]:
    return {t.name: t.timeout for t in asyncio.run(_provider(database).list_tools())}


@pytest.mark.parametrize(
    ("access_mode", "write_mode"),
    [(AccessMode.FULL, False), (AccessMode.BASIC, False), (AccessMode.BASIC, True)],
    ids=["full-ro", "basic-ro", "basic-rw"],
)
def test_tool_timeouts_outlast_statement_timeout(access_mode: AccessMode, *, write_mode: bool) -> None:
    """При SafeSqlExecutor таймаут тула длиннее statement_timeout + клиентской страховки."""
    timeouts = _registered_timeouts(_database(access_mode, write_mode=write_mode, safe_sql_timeout=30))
    assert set(timeouts) == set(_PRE_CHANGE_TIMEOUTS)
    for name, timeout in timeouts.items():
        assert timeout is not None
        assert timeout > 30 + 5, name
        assert timeout >= _PRE_CHANGE_TIMEOUTS[name], name


def test_tool_timeouts_follow_larger_safe_sql_timeout() -> None:
    """Таймаут тула выводится из safe_sql_timeout, а не только из констант."""
    timeouts = _registered_timeouts(_database(AccessMode.FULL, write_mode=False, safe_sql_timeout=120))
    for name, timeout in timeouts.items():
        assert timeout is not None
        assert timeout > 120 + 5, name


def test_unrestricted_mode_keeps_existing_tool_timeouts() -> None:
    """FULL + write_mode идёт мимо SafeSqlExecutor: таймауты тулов остаются прежними."""
    timeouts = _registered_timeouts(_database(AccessMode.FULL, write_mode=True, safe_sql_timeout=120))
    assert timeouts == _PRE_CHANGE_TIMEOUTS


_ROW_TOOLS = ("execute_sql", "list_objects", "get_object_details", "list_schemas", "get_top_queries")


async def test_row_tools_have_no_output_schema() -> None:
    """Тулы со строками отдают ToolResult сами: FastMCP не должен заворачивать ответ в {'result': ...}."""
    provider = _provider(_database(AccessMode.FULL))
    for name in _ROW_TOOLS:
        tool = await provider.get_tool(name)
        assert tool is not None
        assert tool.output_schema is None, name
        assert "output" in tool.parameters["properties"], name


async def test_execute_sql_output_over_mcp() -> None:
    """По MCP: 'table' — только Markdown, 'JSON' (любой регистр) — JSON-текст и structured_content."""

    db = MagicMock()
    db.write_mode = False
    db.sql_driver.execute = AsyncMock(return_value=[RowResult(cells={"n": 1})])
    mcp = FastMCP(name="test", providers=[_provider(_database(AccessMode.FULL), db)])
    async with Client(mcp) as client:
        table = await client.call_tool("execute_sql", {"sql": "SELECT 1 AS n"})
        as_json = await client.call_tool("execute_sql", {"sql": "SELECT 1 AS n", "output": "JSON"})

    assert [block.text for block in table.content] == ["| n |\n| --- |\n| 1 |\n\n1 rows."]
    assert table.structured_content is None
    assert [block.text for block in as_json.content] == ['{"rows": [{"n": 1}], "row_count": 1}']
    assert as_json.structured_content == {"rows": [{"n": 1}], "row_count": 1}


_FULL_TOOLS = (
    "list_schemas",
    "analyze_db_health",
    "get_top_queries",
    "analyze_query_indexes",
    "analyze_workload_indexes",
)


async def test_full_tool_auth_is_attached_to_full_tools_only() -> None:
    """Проверка прав навешивается только на full-тулы; basic-тулы без auth."""

    def check(ctx: AuthContext) -> bool:
        return True

    provider = _provider(_database(AccessMode.FULL), full_tool_auth=check)
    for tool in await provider.list_tools():
        if ToolTag.FULL.value in tool.tags:
            assert tool.name in _FULL_TOOLS
            assert tool.auth is check, tool.name
        else:
            assert tool.auth is None, tool.name


async def test_without_full_tool_auth_no_tool_has_auth() -> None:
    assert all(tool.auth is None for tool in await _provider(_database(AccessMode.FULL)).list_tools())


async def test_denied_full_tool_auth_hides_full_tools() -> None:
    """Отказ проверки убирает full-тул из списка и делает вызов Unknown tool."""
    provider = _provider(_database(AccessMode.FULL), full_tool_auth=lambda ctx: False)
    async with Client(FastMCP(name="test", providers=[provider])) as client:
        names = {t.name for t in await client.list_tools()}
        denied = await client.call_tool("list_schemas", {}, raise_on_error=False)
    assert names == {"execute_sql", "list_objects", "get_object_details", "explain_query"}
    assert denied.is_error is True
    assert "Unknown tool" in denied.content[0].text
```

`tests/unit/tools/test_tool_descriptions.py` заменить целиком (помощник строит `LocalProvider`; проверки прежние):

```python
"""Тесты описаний тулов в реестре."""

import asyncio
from unittest.mock import MagicMock

from fastmcp.server.providers import LocalProvider

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.tools.definitions import ToolSet
from postgres_fastmcp.tools.registry import register_tools


def _descriptions(*, access_mode: AccessMode, write_mode: bool = False) -> dict[str, str]:
    database = Settings().database.model_copy(update={"access_mode": access_mode, "write_mode": write_mode})
    provider = LocalProvider()
    register_tools(provider, ToolSet(get_db=MagicMock), ceiling=database)
    return {t.name: (t.description or "") for t in asyncio.run(provider.list_tools())}


def test_execute_sql_description_restricted_in_basic_mode() -> None:
    desc = _descriptions(access_mode=AccessMode.BASIC)["execute_sql"]
    assert "read-only" in desc.lower()


def test_execute_sql_description_allows_dml_in_basic_write_mode() -> None:
    """BASIC + write_mode: DML разрешён и коммитится, DDL отклоняется — описание не называет тул read-only."""
    desc = _descriptions(access_mode=AccessMode.BASIC, write_mode=True)["execute_sql"]
    assert "read-only" not in desc.lower()
    assert "INSERT, UPDATE and DELETE" in desc
    assert "DDL is rejected (except CREATE EXTENSION hypopg / pg_stat_statements)" in desc
    assert "public schema" in desc


def test_execute_sql_description_restricted_in_full_without_write_mode() -> None:
    desc = _descriptions(access_mode=AccessMode.FULL, write_mode=False)["execute_sql"]
    assert "read-only" in desc.lower()


def test_execute_sql_description_unrestricted_when_full_and_write_mode() -> None:
    desc = _descriptions(access_mode=AccessMode.FULL, write_mode=True)["execute_sql"]
    lower = desc.lower()
    assert "any sql" in lower or "ddl" in lower


def test_list_objects_description_mentions_public_in_basic() -> None:
    desc = _descriptions(access_mode=AccessMode.BASIC)["list_objects"]
    assert "public" in desc.lower()


def test_list_objects_description_in_full_mentions_schema() -> None:
    desc = _descriptions(access_mode=AccessMode.FULL)["list_objects"]
    assert "specified schema" in desc.lower()


def test_get_object_details_description_mentions_public_in_basic() -> None:
    desc = _descriptions(access_mode=AccessMode.BASIC)["get_object_details"]
    assert "public" in desc.lower()


def test_explain_query_description_present() -> None:
    desc = _descriptions(access_mode=AccessMode.BASIC)["explain_query"]
    assert "execution plan" in desc.lower()


def test_annotation_presets_have_expected_keys() -> None:
    from postgres_fastmcp.tools.registry import DESTRUCTIVE, READ_ONLY_IDEMPOTENT, READ_ONLY_NON_IDEMPOTENT

    required = {"read_only_hint", "destructive_hint", "idempotent_hint", "open_world_hint"}
    for preset in (READ_ONLY_IDEMPOTENT, READ_ONLY_NON_IDEMPOTENT, DESTRUCTIVE):
        assert set(preset.keys()) == required


def test_destructive_preset_marks_writes() -> None:
    from postgres_fastmcp.tools.registry import DESTRUCTIVE

    assert DESTRUCTIVE["read_only_hint"] is False
    assert DESTRUCTIVE["destructive_hint"] is True
    assert DESTRUCTIVE["idempotent_hint"] is False
```

В `tests/unit/tools/test_params.py` добавить импорты `from unittest.mock import MagicMock`, `from fastmcp.server.providers import LocalProvider`, `from postgres_fastmcp.tools.definitions import ToolSet` и заменить тело `test_rejected_input_reaches_client_with_hint` до `async with`:

```python
    """Ошибка нормализации проходит mask_error_details: агент видит текст с подсказкой, а не 'Error calling tool'."""
    provider = LocalProvider()
    database = Settings().database.model_copy(update={"access_mode": AccessMode.FULL})
    register_tools(provider, ToolSet(get_db=MagicMock), ceiling=database)
    mcp = FastMCP(name="t", mask_error_details=True, providers=[provider])
    async with Client(mcp) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error is True
    assert hint in result.content[0].text
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `<env> uv run pytest tests/unit/tools -q`
Expected: `ImportError while loading conftest ... cannot import name 'ToolSet' from 'postgres_fastmcp.tools.definitions'`.

- [ ] **Step 3: `ToolSet`**

`src/postgres_fastmcp/tools/definitions.py` заменить целиком:

```python
"""Все MCP-тулы сервера: методы ToolSet, тонкие обёртки над доменными сервисами.

ToolSet получает ``get_db`` — функцию, которая на каждый вызов тула отдаёт
доступ к БД с правами текущего запроса (DbAccessPort). Контекст FastMCP тулам
не нужен. Описания и аннотации задаются при регистрации в ``tools.registry``.
Docstring методов-тулов на английском: FastMCP показывает их агенту.
Тулы, которые возвращают строки, принимают ``output`` и отдают ToolResult
через ``tools.rendering``.
"""

from collections.abc import Callable
from typing import Annotated, Any

from fastmcp.tools import ToolResult
from pydantic import Field

from postgres_fastmcp.domains import querying, top_queries
from postgres_fastmcp.domains.catalog.service import CatalogService
from postgres_fastmcp.domains.db_access import DbAccessPort
from postgres_fastmcp.domains.explain.service import ExplainService
from postgres_fastmcp.domains.health.database_health import DatabaseHealthAnalyzer, HealthType
from postgres_fastmcp.domains.index_tuning.service import IndexAnalysisService
from postgres_fastmcp.domains.querying import SUCCESS_NO_ROWS
from postgres_fastmcp.shared.errors import ObjectNotFoundError
from postgres_fastmcp.tools.params import (
    HealthTypesParam,
    IndexQueriesParam,
    ObjectTypeParam,
    OutputParam,
    TopQueriesLimitParam,
    TopQueriesSortByParam,
)
from postgres_fastmcp.tools.rendering import rows_result, sections_result


# Поля заголовка, которые get_object_details добавляет сам, без данных каталога.
_TOOL_HEADER_KEYS = frozenset({"schema", "name", "type"})


class ToolSet:
    """Девять тулов как методы; доступ к БД на каждый вызов берётся из get_db."""

    def __init__(self, get_db: Callable[[], DbAccessPort]) -> None:
        """Инициализировать набор тулов.

        Args:
            get_db: Функция, возвращающая доступ к БД с правами текущего запроса.
        """
        self._get_db = get_db

    async def execute_sql(
        self,
        sql: Annotated[
            str,
            Field(description="SQL statement to execute, e.g. 'SELECT id, name FROM users WHERE active LIMIT 50'."),
        ],
        output: OutputParam = "table",
    ) -> ToolResult:
        """Execute a SQL statement and return the result rows."""
        rows = await querying.execute_sql(self._get_db(), sql)
        if rows is None:
            return rows_result([], output, title=SUCCESS_NO_ROWS)
        return rows_result(rows, output)

    async def explain_query(
        self,
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
    ) -> str:
        """Show the execution plan of a SQL query: plain, analyze or with hypothetical indexes."""
        service = ExplainService(db=self._get_db())
        return await service.explain(sql, analyze=analyze, hypothetical_indexes=hypothetical_indexes)

    async def list_objects(
        self,
        schema_name: Annotated[str, Field(description="Schema name to inspect.")],
        object_type: ObjectTypeParam = "table",
        output: OutputParam = "table",
    ) -> ToolResult:
        """List objects of the given kind in a schema."""
        service = CatalogService(db=self._get_db())
        return rows_result(await service.list_objects(schema_name=schema_name, object_type=object_type), output)

    async def get_object_details(
        self,
        schema_name: Annotated[str, Field(description="Schema name.")],
        object_name: Annotated[str, Field(description="Object name.")],
        object_type: ObjectTypeParam = "table",
        output: OutputParam = "table",
    ) -> ToolResult:
        """Show object details: columns, constraints, indexes and other metadata."""
        service = CatalogService(db=self._get_db())
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
        # Каталог не бросает на отсутствующий объект: таблица приходит с пустыми разделами,
        # последовательность и расширение — пустым словарём. Кроме полей самого тула ничего нет — объекта нет.
        if header.keys() <= _TOOL_HEADER_KEYS and not any(sections.values()):
            raise ObjectNotFoundError(header["schema"], object_name, object_type)
        return sections_result(sections, output, header=header)

    async def list_schemas(self, output: OutputParam = "table") -> ToolResult:
        """List database schemas."""
        service = CatalogService(db=self._get_db())
        return rows_result(await service.list_schemas(), output)

    async def analyze_db_health(self, health_type: HealthTypesParam = (HealthType.ALL,)) -> str:
        """Run database health checks and return a text report."""
        health_tool = DatabaseHealthAnalyzer(self._get_db())
        return await health_tool.health(health_type=",".join(health_type))

    async def get_top_queries(
        self,
        sort_by: TopQueriesSortByParam = "resources",
        limit: TopQueriesLimitParam = 10,
        output: OutputParam = "table",
    ) -> ToolResult:
        """Report top queries from pg_stat_statements by the chosen criteria."""
        rows = await top_queries.get_top_queries(self._get_db(), sort_by=sort_by, limit=limit)
        return rows_result(rows, output)

    async def analyze_query_indexes(
        self,
        queries: IndexQueriesParam,
        max_index_size_mb: Annotated[
            int,
            Field(default=10000, ge=1, description="Max recommended index size (MB)."),
        ] = 10000,
    ) -> dict[str, Any]:
        """Recommend indexes for a list of queries (cost-based DTA, requires hypopg)."""
        service = IndexAnalysisService(db=self._get_db())
        return await service.analyze_query_indexes(queries=queries, max_index_size_mb=max_index_size_mb)

    async def analyze_workload_indexes(
        self,
        max_index_size_mb: Annotated[
            int,
            Field(default=10000, ge=1, description="Max recommended index size (MB)."),
        ] = 10000,
    ) -> dict[str, Any]:
        """Recommend indexes for the aggregated database workload (cost-based DTA, requires hypopg)."""
        service = IndexAnalysisService(db=self._get_db())
        return await service.analyze_workload_indexes(max_index_size_mb=max_index_size_mb)
```

- [ ] **Step 4: `register_tools` на `LocalProvider`**

`src/postgres_fastmcp/tools/registry.py` заменить целиком (описания и таймауты прежние; `_execute_sql_desc` меняется в Task 5):

```python
"""Программная регистрация тулов ToolSet в LocalProvider через Tool.from_function + add_tool.

Описания, аннотации и таймауты строятся в момент регистрации по серверному потолку
(access_mode, write_mode, safe_sql_timeout из конфигурации БД).
"""

from importlib.metadata import (
    PackageNotFoundError,
    version as _pkg_version,
)
from typing import Any

from fastmcp.server.auth import AuthCheck
from fastmcp.server.providers import LocalProvider
from fastmcp.tools import Tool
from mcp.types import ToolAnnotations

from postgres_fastmcp.domains.db_access import DatabaseConfigPort
from postgres_fastmcp.postgres.security.driver import CLIENT_TIMEOUT_GRACE_SECONDS
from postgres_fastmcp.shared.enums import AccessMode, ToolTag
from postgres_fastmcp.tools.definitions import ToolSet
from postgres_fastmcp.tools.params import HEALTH_TYPE_VALUES


try:
    _VERSION = _pkg_version("postgres-fastmcp")
except PackageNotFoundError:
    _VERSION = "0.0.0"

_META: dict[str, Any] = {"version": _VERSION}

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
# BASIC + write_mode: DML в public коммитится, но DDL отклоняется — запись без разрушения схемы.
WRITE_NON_DESTRUCTIVE: dict[str, bool] = {
    "read_only_hint": False,
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


# Запас поверх statement_timeout + клиентской страховки SafeSqlExecutor: первым должен
# срабатывать Postgres (QueryTimeoutError), а не таймаут тула в FastMCP.
_TOOL_TIMEOUT_MARGIN = 5.0


def _ann(title: str, preset: dict[str, bool]) -> ToolAnnotations:
    return ToolAnnotations(title=title, **preset)


def register_tools(
    provider: LocalProvider,
    toolset: ToolSet,
    *,
    ceiling: DatabaseConfigPort,
    full_tool_auth: AuthCheck | None = None,
) -> None:
    """Зарегистрировать все 9 тулов в провайдере.

    Видимость basic/full по тегам настраивает владелец провайдера (``disable(tags=...)``).

    Args:
        provider: Провайдер, в который добавляются тулы.
        toolset: Методы-тулы с доступом к БД.
        ceiling: Серверный потолок: от него зависят описания, аннотации и таймауты.
        full_tool_auth: Проверка прав на full-тулах (None — без проверки).
    """
    for spec in _all_tool_specs(toolset, ceiling, full_tool_auth):
        provider.add_tool(Tool.from_function(**spec))


def _all_tool_specs(
    toolset: ToolSet, ceiling: DatabaseConfigPort, full_tool_auth: AuthCheck | None
) -> list[dict[str, Any]]:
    specs = _basic_specs(toolset, ceiling) + _full_specs(toolset, full_tool_auth)
    for spec in specs:
        spec["timeout"] = _tool_timeout(spec["timeout"], ceiling)
    return specs


def _tool_timeout(base: float, ceiling: DatabaseConfigPort) -> float:
    """Таймаут тула: не короче базового и строго длиннее statement_timeout + страховки.

    В режиме FULL + write_mode SafeSqlExecutor не используется и statement_timeout нет,
    поэтому базовое значение остаётся как есть.
    """
    if ceiling.access_mode == AccessMode.FULL and ceiling.write_mode:
        return base
    derived = ceiling.safe_sql_timeout + CLIENT_TIMEOUT_GRACE_SECONDS + _TOOL_TIMEOUT_MARGIN
    return max(base, derived)


def _basic_specs(toolset: ToolSet, ceiling: DatabaseConfigPort) -> list[dict[str, Any]]:
    unrestricted = ceiling.access_mode == AccessMode.FULL and ceiling.write_mode
    if unrestricted:
        execute_preset = DESTRUCTIVE
    elif ceiling.write_mode:
        execute_preset = WRITE_NON_DESTRUCTIVE
    else:
        execute_preset = READ_ONLY_NON_IDEMPOTENT
    return [
        {
            "fn": toolset.execute_sql,
            "name": "execute_sql",
            "output_schema": None,
            "description": _execute_sql_desc(unrestricted=unrestricted, write_mode=ceiling.write_mode),
            "tags": {ToolTag.BASIC.value},
            "annotations": _ann("Execute SQL", execute_preset),
            "timeout": 30.0,
            "meta": _META,
        },
        {
            "fn": toolset.list_objects,
            "name": "list_objects",
            "output_schema": None,
            "description": _list_objects_desc(ceiling.access_mode),
            "tags": {ToolTag.BASIC.value},
            "annotations": _ann("List Objects", READ_ONLY_IDEMPOTENT),
            "timeout": 30.0,
            "meta": _META,
        },
        {
            "fn": toolset.get_object_details,
            "name": "get_object_details",
            "output_schema": None,
            "description": _get_object_details_desc(ceiling.access_mode),
            "tags": {ToolTag.BASIC.value},
            "annotations": _ann("Get Object Details", READ_ONLY_IDEMPOTENT),
            "timeout": 30.0,
            "meta": _META,
        },
        {
            "fn": toolset.explain_query,
            "name": "explain_query",
            "description": _explain_query_desc(),
            "tags": {ToolTag.BASIC.value},
            "annotations": _ann("Explain Query", READ_ONLY_IDEMPOTENT),
            "timeout": 30.0,
            "meta": _META,
        },
    ]


def _full_specs(toolset: ToolSet, full_tool_auth: AuthCheck | None) -> list[dict[str, Any]]:
    return [
        {
            "fn": toolset.list_schemas,
            "name": "list_schemas",
            "output_schema": None,
            "description": (
                "Lists all schemas in the PostgreSQL database. Use this first to discover "
                "available namespaces before listing objects in a specific schema."
            ),
            "tags": {ToolTag.FULL.value},
            "annotations": _ann("List Schemas", READ_ONLY_IDEMPOTENT),
            "timeout": 30.0,
            "meta": _META,
            "auth": full_tool_auth,
        },
        {
            "fn": toolset.analyze_db_health,
            "name": "analyze_db_health",
            "description": (
                "Comprehensive PostgreSQL health audit across multiple independent dimensions: "
                f"{HEALTH_TYPE_VALUES}. Returns a structured report with actionable findings "
                "and recommendations. Use periodically to detect issues early. "
                "Pass 'all' for the full audit or a comma-separated subset such as 'index,connection'."
            ),
            "tags": {ToolTag.FULL.value},
            "annotations": _ann("Analyze DB Health", READ_ONLY_IDEMPOTENT),
            "timeout": 60.0,
            "meta": _META,
            "auth": full_tool_auth,
        },
        {
            "fn": toolset.get_top_queries,
            "name": "get_top_queries",
            "output_schema": None,
            "description": (
                "Report the slowest or most resource-intensive queries from pg_stat_statements. "
                "The pg_stat_statements extension must be enabled. Workflow: get_top_queries -> "
                "explain_query -> analyze_query_indexes."
            ),
            "tags": {ToolTag.FULL.value},
            "annotations": _ann("Get Top Queries", READ_ONLY_NON_IDEMPOTENT),
            "timeout": 30.0,
            "meta": _META,
            "auth": full_tool_auth,
        },
        {
            "fn": toolset.analyze_query_indexes,
            "name": "analyze_query_indexes",
            "description": (
                "Recommend optimal indexes for a given list of SQL queries using cost-based analysis "
                "with hypothetical indexes (the hypopg extension is required). "
                "Use analyze_workload_indexes instead if you want to optimize aggregate workload."
            ),
            "tags": {ToolTag.FULL.value},
            "annotations": _ann("Analyze Query Indexes", READ_ONLY_IDEMPOTENT),
            "timeout": 60.0,
            "meta": _META,
            "auth": full_tool_auth,
        },
        {
            "fn": toolset.analyze_workload_indexes,
            "name": "analyze_workload_indexes",
            "description": (
                "Recommend indexes based on the actual workload captured in pg_stat_statements, "
                "using cost-based analysis with hypothetical indexes (hypopg required). "
                "Use periodically to find missing indexes. "
                "Use analyze_query_indexes instead if you want to optimize specific queries."
            ),
            "tags": {ToolTag.FULL.value},
            "annotations": _ann("Analyze Workload Indexes", READ_ONLY_NON_IDEMPOTENT),
            "timeout": 60.0,
            "meta": _META,
            "auth": full_tool_auth,
        },
    ]


def _execute_sql_desc(*, unrestricted: bool, write_mode: bool) -> str:
    if unrestricted:
        return (
            "Execute ANY SQL statement (DDL, DML, DCL). Server is in FULL access with write_mode=True. "
            "Use with caution; prefer explain_query first for non-trivial SELECTs. "
            "Workflow: 1) list_objects, 2) get_object_details, 3) execute_sql."
        )
    if write_mode:
        return (
            "Execute a SQL statement in the public schema. SELECT, EXPLAIN and SHOW are allowed, "
            "and so are INSERT, UPDATE and DELETE (changes are committed); DDL is rejected "
            "(except CREATE EXTENSION hypopg / pg_stat_statements). "
            "Workflow: 1) list_objects, 2) get_object_details, 3) execute_sql."
        )
    return (
        "Execute a read-only SELECT query. DDL/DML/DCL statements are blocked. "
        "Workflow: 1) list_objects, 2) get_object_details, 3) execute_sql."
    )


def _list_objects_desc(access_mode: AccessMode) -> str:
    if access_mode == AccessMode.BASIC:
        return (
            "List objects (tables/views/sequences/extensions) in the 'public' schema. "
            "BASIC mode restricts access to 'public'. After listing, use get_object_details "
            "to examine structure before writing SQL."
        )
    return (
        "List objects (tables/views/sequences/extensions) in a specified schema. "
        "Use list_schemas to discover available schemas first."
    )


def _get_object_details_desc(access_mode: AccessMode) -> str:
    if access_mode == AccessMode.BASIC:
        return (
            "Show details (columns, constraints, indexes, metadata) for an object in the 'public' schema. "
            "Use this before writing SQL so you know the exact structure of the target object."
        )
    return (
        "Show details (columns, constraints, indexes, metadata) for a database object. "
        "Workflow: list_schemas -> list_objects -> get_object_details -> execute_sql."
    )


def _explain_query_desc() -> str:
    return (
        "Explain the execution plan of a SQL query. analyze=True actually runs the query to collect "
        "real statistics; use cautiously on large tables. hypothetical_indexes simulates index impact "
        "via hypopg without creating them. Workflow: explain_query -> add indexes / rewrite query -> execute_sql."
    )
```

- [ ] **Step 5: Временный мост в `create_server`**

`src/postgres_fastmcp/app/context.py` заменить целиком (файл удаляется в Task 4):

```python
"""Типизированный контракт lifespan-контекста сервера и аксессор для тулов.

Единая точка, описывающая форму ``ctx.lifespan_context``: его наполняет
``lifespan.build_lifespan`` (продюсер), а читает ToolSet через ``get_db`` (потребитель).
Это убирает дублирование нетипизированного доступа ``ctx.lifespan_context["db"]``.
"""

from typing import TypedDict, cast

from fastmcp.server.dependencies import get_context

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.domains.db_access import DbAccessPort


class LifespanContext(TypedDict):
    """Форма словаря, который lifespan кладёт в ``ctx.lifespan_context``."""

    db: DbAccessPort
    settings: Settings


def get_db() -> DbAccessPort:
    """Вернуть доступ к БД из lifespan-контекста текущего запроса (для ToolSet)."""
    return cast("DbAccessPort", get_context().lifespan_context["db"])
```

`src/postgres_fastmcp/app/server.py` заменить целиком (файл переписывается в Task 4):

```python
"""Фабрика MCP-сервера: create_server(settings, *, auth, extra_providers, extra_middleware) -> FastMCP."""

from collections.abc import Sequence
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.middleware.logging import LoggingMiddleware
from fastmcp.server.middleware.timing import TimingMiddleware
from fastmcp.server.providers import LocalProvider

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.context import get_db
from postgres_fastmcp.app.lifespan import build_lifespan
from postgres_fastmcp.app.middleware.response_budget import ResponseBudgetMiddleware
from postgres_fastmcp.shared.enums import AccessMode, ToolTag
from postgres_fastmcp.tools.definitions import ToolSet
from postgres_fastmcp.tools.registry import register_tools


def create_server(
    settings: Settings,
    *,
    auth: Any = None,  # noqa: ANN401
    extra_providers: Sequence[Any] = (),
    extra_middleware: Sequence[Any] = (),
) -> FastMCP:
    """Собрать FastMCP-сервер: lifespan + middleware + регистрация тулов + visibility.

    Args:
        settings: Конфигурация (database, server, fastmcp блоки).
        auth: Опциональный auth-provider FastMCP (Bearer/JWT/custom).
            По умолчанию без авторизации.
        extra_providers: Дополнительные FastMCP-провайдеры от потребителя библиотеки.
        extra_middleware: Дополнительные middleware (встают после встроенных; бюджет ответа
            стоит первым и проверяет и их результат).

    Returns:
        Готовый FastMCP, на котором можно сразу вызывать `.run(...)`.
    """
    lifespan_cm = build_lifespan(settings)
    tools = LocalProvider(on_duplicate="error")
    register_tools(tools, ToolSet(get_db=get_db), ceiling=settings.database)

    fastmcp_kwargs: dict[str, Any] = {
        "name": settings.fastmcp.server_name,
        "lifespan": lifespan_cm,
        "mask_error_details": True,
        "on_duplicate": "error",
    }
    instructions = getattr(settings.fastmcp, "instructions", None)
    if instructions:
        fastmcp_kwargs["instructions"] = instructions
    if auth is not None:
        fastmcp_kwargs["auth"] = auth
    fastmcp_kwargs["providers"] = [tools, *extra_providers]

    mcp = FastMCP(**fastmcp_kwargs)

    # Первым = внешним: бюджет проверяет ровно то, что уходит клиенту, включая результат extra_middleware
    mcp.add_middleware(ResponseBudgetMiddleware(settings.server.response_max_tokens))
    mcp.add_middleware(TimingMiddleware())
    mcp.add_middleware(LoggingMiddleware())
    for m in extra_middleware:
        mcp.add_middleware(m)

    if settings.database.access_mode == AccessMode.BASIC:
        mcp.disable(tags={ToolTag.FULL.value})

    return mcp
```

- [ ] **Step 6: Запустить тесты**

Run: `<env> uv run pytest tests/unit/tools -q`
Expected: все проходят.

Run: `<env> uv run pytest tests/unit -q`
Expected: `737 passed`.

Run: `<env> uv run pytest tests/integration -q`
Expected: `78 skipped`.

- [ ] **Step 7: Линтеры и коммит**

Run:
```bash
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && \
uv run ruff format tests/unit/tools/conftest.py tests/unit/tools/test_definitions.py tests/unit/tools/test_registry.py \
  tests/unit/tools/test_tool_descriptions.py tests/unit/tools/test_params.py
```
Expected: ruff и mypy без ошибок (`77 source files`).

```bash
git add src/postgres_fastmcp/tools src/postgres_fastmcp/app/context.py src/postgres_fastmcp/app/server.py tests/unit/tools
git commit -m "refactor(tools): turn tool functions into ToolSet methods registered on a LocalProvider"
```

---

### Task 4: `PostgresProvider` и `create_server` поверх него; удаление lifespan/context

**Files:**
- Create: `src/postgres_fastmcp/provider.py`
- Modify: `src/postgres_fastmcp/app/server.py` (переписать целиком), `src/postgres_fastmcp/app/__init__.py:1`
- Delete: `src/postgres_fastmcp/app/lifespan.py`, `src/postgres_fastmcp/app/context.py`, `tests/unit/app/test_lifespan.py`
- Test: `tests/unit/test_provider.py` (новый), `tests/unit/app/test_server.py`, `tests/unit/app/test_response_budget.py:93`
- Test (интеграция, статическая проверка): `tests/integration/test_provider_integration.py` (новый)

**Interfaces:**
- Consumes: `EffectiveAccess`, `AccessPolicy`, `AccessResolver`, `build_resolver`, `bounded_resolver`, `full_access_check` (Task 1); `DbAccessService`, `DbAccessPort` (Task 2); `ToolSet`, `register_tools` (Task 3).
- Produces:
  - `PostgresProvider(database: DatabaseConfig, *, access_policy: AccessPolicy | None = None, access_resolver: AccessResolver | None = None)` — наследник `LocalProvider(on_duplicate="error")`; в BASIC вызывает `self.disable(tags={"full"})`; `full`-тулы получают `auth=full_access_check(resolver)`; `lifespan()` закрывает пул. `access_resolver` приоритетнее `access_policy`; результат любого резолвера ограничен потолком.
  - `create_server(settings: Settings, *, auth: AuthProvider | None = None, access_resolver: AccessResolver | None = None, extra_providers: Sequence[Provider] = (), extra_middleware: Sequence[Middleware] = ()) -> FastMCP`.
  - Подмена сервиса в тестах: `monkeypatch.setattr("postgres_fastmcp.provider.DbAccessService", Fake)`; у подмены есть `view(access)` и `async close()`.

- [ ] **Step 1: Тесты провайдера**

Создать `tests/unit/test_provider.py`:

```python
"""Тесты PostgresProvider: видимость тулов, права запроса, lifespan и namespace."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import Client, FastMCP
from fastmcp.server.auth import AccessToken

from postgres_fastmcp.access import AccessPolicy, EffectiveAccess
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.domains.db_access import DbAccess
from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.provider import PostgresProvider
from postgres_fastmcp.shared.enums import AccessMode, ToolTag


_BASIC_TOOLS = {"execute_sql", "list_objects", "get_object_details", "explain_query"}
_FULL_TOOLS = {
    "list_schemas",
    "analyze_db_health",
    "get_top_queries",
    "analyze_query_indexes",
    "analyze_workload_indexes",
}


def _database(access_mode: AccessMode = AccessMode.FULL, *, write_mode: bool = False) -> DatabaseConfig:
    return DatabaseConfig(host="h", user="u", password="p", name="d", access_mode=access_mode, write_mode=write_mode)


class FakeService:
    """Подмена DbAccessService: запоминает запрошенные права и число закрытий."""

    instances: list["FakeService"] = []

    def __init__(self, config: object) -> None:
        self.config = config
        self.views: list[EffectiveAccess] = []
        self.closed = 0
        self.sql_driver = MagicMock()
        self.sql_driver.execute = AsyncMock(return_value=[RowResult(cells={"n": 1})])
        FakeService.instances.append(self)

    def view(self, access: EffectiveAccess) -> DbAccess:
        self.views.append(access)
        return DbAccess(
            sql_driver=self.sql_driver,
            access_mode=access.access_mode,
            write_mode=access.write_mode,
            table_prefix=None,
            connection_id="fake",
        )

    async def close(self) -> None:
        self.closed += 1


@pytest.fixture
def fake_service(monkeypatch: pytest.MonkeyPatch) -> type[FakeService]:
    FakeService.instances = []
    monkeypatch.setattr("postgres_fastmcp.provider.DbAccessService", FakeService)
    return FakeService


async def _tool_names(server: FastMCP) -> set[str]:
    async with Client(server) as client:
        return {tool.name for tool in await client.list_tools()}


async def test_basic_ceiling_lists_basic_tools_only() -> None:
    server = FastMCP("t", providers=[PostgresProvider(_database(AccessMode.BASIC))])
    assert await _tool_names(server) == _BASIC_TOOLS
    async with Client(server) as client:
        result = await client.call_tool("list_schemas", {}, raise_on_error=False)
    assert result.is_error is True
    assert "Unknown tool" in result.content[0].text


async def test_full_ceiling_lists_all_nine_tools() -> None:
    server = FastMCP("t", providers=[PostgresProvider(_database(AccessMode.FULL))])
    assert await _tool_names(server) == _BASIC_TOOLS | _FULL_TOOLS


async def test_full_tools_carry_access_check() -> None:
    provider = PostgresProvider(_database(AccessMode.FULL))
    for tool in await provider.list_tools():
        assert (tool.auth is not None) is (ToolTag.FULL.value in tool.tags), tool.name


async def test_lifespan_closes_the_service(fake_service: type[FakeService]) -> None:
    server = FastMCP("t", providers=[PostgresProvider(_database())])
    async with Client(server) as client:
        await client.call_tool("execute_sql", {"sql": "SELECT 1 AS n"})
        assert fake_service.instances[0].closed == 0
    assert fake_service.instances[0].closed == 1


async def test_tool_call_gets_access_from_resolver_and_token(fake_service: type[FakeService]) -> None:
    """get_db: токен текущего запроса (None без auth) -> резолвер -> view(права)."""
    tokens: list[AccessToken | None] = []
    narrowed = EffectiveAccess(AccessMode.FULL, write_mode=False)

    def resolver(token: AccessToken | None) -> EffectiveAccess:
        tokens.append(token)
        return narrowed

    provider = PostgresProvider(_database(AccessMode.FULL, write_mode=True), access_resolver=resolver)
    async with Client(FastMCP("t", providers=[provider])) as client:
        await client.call_tool("execute_sql", {"sql": "SELECT 1 AS n"})
    assert tokens
    assert set(tokens) == {None}
    assert fake_service.instances[0].views == [narrowed]


async def test_default_resolver_uses_the_ceiling(fake_service: type[FakeService]) -> None:
    provider = PostgresProvider(_database(AccessMode.BASIC, write_mode=True))
    async with Client(FastMCP("t", providers=[provider])) as client:
        await client.call_tool("execute_sql", {"sql": "SELECT 1 AS n"})
    assert fake_service.instances[0].views == [EffectiveAccess(AccessMode.BASIC, write_mode=True)]


async def test_custom_resolver_cannot_exceed_the_ceiling(fake_service: type[FakeService]) -> None:
    provider = PostgresProvider(
        _database(AccessMode.BASIC, write_mode=False),
        access_resolver=lambda _token: EffectiveAccess(AccessMode.FULL, write_mode=True),
    )
    async with Client(FastMCP("t", providers=[provider])) as client:
        await client.call_tool("execute_sql", {"sql": "SELECT 1 AS n"})
    assert fake_service.instances[0].views == [EffectiveAccess(AccessMode.BASIC, write_mode=False)]


async def test_resolver_narrowing_to_basic_hides_full_tools() -> None:
    """При потолке FULL резолвер, вернувший BASIC, скрывает full-тулы через проверку прав."""
    provider = PostgresProvider(
        _database(AccessMode.FULL),
        access_resolver=lambda _token: EffectiveAccess(AccessMode.BASIC, write_mode=False),
    )
    assert await _tool_names(FastMCP("t", providers=[provider])) == _BASIC_TOOLS


def test_enforced_policy_without_resolver_is_rejected() -> None:
    with pytest.raises(ValueError, match="AccessPolicy.enforced"):
        PostgresProvider(_database(), access_policy=AccessPolicy(enforced=True))


def test_access_resolver_takes_priority_over_policy() -> None:
    PostgresProvider(
        _database(),
        access_policy=AccessPolicy(enforced=True),
        access_resolver=lambda _token: EffectiveAccess(AccessMode.BASIC, write_mode=False),
    )


async def test_two_databases_in_one_host_via_namespace() -> None:
    host = FastMCP("host")
    host.add_provider(PostgresProvider(_database(AccessMode.BASIC)))
    host.add_provider(PostgresProvider(_database(AccessMode.FULL)), namespace="analytics")
    names = await _tool_names(host)
    assert names == _BASIC_TOOLS | {f"analytics_{name}" for name in _BASIC_TOOLS | _FULL_TOOLS}
```

- [ ] **Step 2: Тесты `create_server`**

В `tests/unit/app/test_server.py` заменить блок импортов на:

```python
import pytest
from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.middleware.logging import LoggingMiddleware
from fastmcp.server.middleware.timing import TimingMiddleware
from fastmcp.server.providers import LocalProvider

from postgres_fastmcp.access import EffectiveAccess
from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.middleware.response_budget import ResponseBudgetMiddleware
from postgres_fastmcp.app.server import create_server
from postgres_fastmcp.provider import PostgresProvider
from postgres_fastmcp.shared.enums import AccessMode
```

и добавить в конец файла:

```python
def test_create_server_builds_on_postgres_provider() -> None:
    server = create_server(_settings())
    assert sum(isinstance(p, PostgresProvider) for p in server.providers) == 1


def test_create_server_keeps_budget_outermost_and_extra_middleware_after_builtin() -> None:
    class M(Middleware):
        pass

    extra = M()
    server = create_server(_settings(), extra_middleware=[extra])
    assert [type(m) for m in server.middleware[:4]] == [
        ResponseBudgetMiddleware,
        TimingMiddleware,
        LoggingMiddleware,
        M,
    ]


async def test_create_server_adds_extra_providers() -> None:
    extra = LocalProvider()

    def ping() -> str:
        return "pong"

    extra.add_tool(ping)
    server = create_server(_settings(), extra_providers=[extra])
    names = {t.name for t in await server.list_tools()}
    assert {"ping", "execute_sql"} <= names


async def test_create_server_passes_access_resolver_to_provider() -> None:
    """Резолвер, сужающий FULL до BASIC, прячет full-тулы сервера."""
    server = create_server(
        _settings(access_mode=AccessMode.FULL),
        access_resolver=lambda _token: EffectiveAccess(AccessMode.BASIC, write_mode=False),
    )
    names = {t.name for t in await server.list_tools()}
    assert names == {"execute_sql", "list_objects", "get_object_details", "explain_query"}
```

В `tests/unit/app/test_response_budget.py` заменить путь подмены:

```bash
sed -i 's/postgres_fastmcp.app.lifespan.DbAccessService/postgres_fastmcp.provider.DbAccessService/' tests/unit/app/test_response_budget.py
```

- [ ] **Step 3: Запустить и убедиться, что падает**

Run: `<env> uv run pytest tests/unit/test_provider.py tests/unit/app/test_server.py -q`
Expected: ошибки сбора `ModuleNotFoundError: No module named 'postgres_fastmcp.provider'`.

- [ ] **Step 4: `PostgresProvider`**

Создать `src/postgres_fastmcp/provider.py`:

```python
"""PostgresProvider: источник девяти тулов одной базы для любого FastMCP-сервера.

Провайдер владеет DbAccessService (пул открывается лениво при первом запросе,
закрывается в ``lifespan``) и на каждый вызов тула считает права запроса:
токен текущего запроса -> резолвер -> ``DbAccessService.view(права)``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastmcp.server.dependencies import get_access_token
from fastmcp.server.providers import LocalProvider

from postgres_fastmcp.access import (
    AccessPolicy,
    AccessResolver,
    EffectiveAccess,
    bounded_resolver,
    build_resolver,
    full_access_check,
)
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.domains.db_access import DbAccessPort, DbAccessService
from postgres_fastmcp.shared.enums import AccessMode, ToolTag
from postgres_fastmcp.tools.definitions import ToolSet
from postgres_fastmcp.tools.registry import register_tools


class PostgresProvider(LocalProvider):
    """Тулы одной базы PostgreSQL с правами не выше потолка из ``database``."""

    def __init__(
        self,
        database: DatabaseConfig,
        *,
        access_policy: AccessPolicy | None = None,
        access_resolver: AccessResolver | None = None,
    ) -> None:
        """Создать провайдер и зарегистрировать тулы.

        Args:
            database: Подключение и серверный потолок прав (access_mode, write_mode).
            access_policy: Политика сужения прав по claim токена (без resolver).
            access_resolver: Свой резолвер токен -> права; приоритетнее access_policy.
                Результат всегда ограничивается потолком.

        Raises:
            ValueError: Если access_policy.enforced=True, а access_resolver не задан.
        """
        super().__init__(on_duplicate="error")
        ceiling = EffectiveAccess(database.access_mode, write_mode=database.write_mode)
        if access_resolver is None:
            access_resolver = build_resolver(ceiling, access_policy or AccessPolicy())
        self._resolve = bounded_resolver(access_resolver, ceiling)
        self._db = DbAccessService(database)
        register_tools(
            self,
            ToolSet(get_db=self._current_db),
            ceiling=database,
            full_tool_auth=full_access_check(self._resolve),
        )
        if database.access_mode == AccessMode.BASIC:
            self.disable(tags={ToolTag.FULL.value})

    def _current_db(self) -> DbAccessPort:
        """Доступ к БД с правами текущего запроса (токен None в stdio и без auth)."""
        return self._db.view(self._resolve(get_access_token()))

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """Закрыть пул подключений при остановке сервера."""
        try:
            yield
        finally:
            await self._db.close()
```

- [ ] **Step 5: `create_server` поверх провайдера, удалить lifespan и context**

`src/postgres_fastmcp/app/server.py` заменить целиком:

```python
"""Фабрика MCP-сервера: FastMCP поверх PostgresProvider с middleware сервера."""

from collections.abc import Sequence

from fastmcp import FastMCP
from fastmcp.server.auth import AuthProvider
from fastmcp.server.middleware import Middleware
from fastmcp.server.middleware.logging import LoggingMiddleware
from fastmcp.server.middleware.timing import TimingMiddleware
from fastmcp.server.providers import Provider

from postgres_fastmcp.access import AccessResolver
from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.middleware.response_budget import ResponseBudgetMiddleware
from postgres_fastmcp.provider import PostgresProvider


def create_server(
    settings: Settings,
    *,
    auth: AuthProvider | None = None,
    access_resolver: AccessResolver | None = None,
    extra_providers: Sequence[Provider] = (),
    extra_middleware: Sequence[Middleware] = (),
) -> FastMCP:
    """Собрать FastMCP-сервер: PostgresProvider + middleware.

    Args:
        settings: Конфигурация (database, server, fastmcp блоки).
        auth: Опциональный auth-провайдер FastMCP. По умолчанию без авторизации.
        access_resolver: Свой резолвер токен -> права для PostgresProvider
            (результат ограничивается потолком из settings.database).
        extra_providers: Дополнительные FastMCP-провайдеры от потребителя библиотеки.
        extra_middleware: Дополнительные middleware (встают после встроенных; бюджет ответа
            стоит первым и проверяет и их результат).

    Returns:
        Готовый FastMCP, на котором можно сразу вызывать `.run(...)`.
    """
    provider = PostgresProvider(settings.database, access_resolver=access_resolver)
    return FastMCP(
        name=settings.fastmcp.server_name,
        instructions=settings.fastmcp.instructions or None,
        auth=auth,
        providers=[provider, *extra_providers],
        # Первым = внешним: бюджет проверяет ровно то, что уходит клиенту, включая результат extra_middleware
        middleware=[
            ResponseBudgetMiddleware(settings.server.response_max_tokens),
            TimingMiddleware(),
            LoggingMiddleware(),
            *extra_middleware,
        ],
        mask_error_details=True,
        on_duplicate="error",
    )
```

Run:
```bash
git rm src/postgres_fastmcp/app/lifespan.py src/postgres_fastmcp/app/context.py tests/unit/app/test_lifespan.py
sed -i 's/"""Composition root: конфигурация, сервер, lifespan и точка входа."""/"""Composition root: конфигурация, сборка сервера и точка входа."""/' src/postgres_fastmcp/app/__init__.py
grep -rn "app.lifespan\|app.context\|get_db(ctx\|CurrentContext\|make_ctx\|FakeCtx" src tests
```
Expected: `grep` ничего не находит.

- [ ] **Step 6: Запустить тесты**

Run: `<env> uv run pytest tests/unit/test_provider.py tests/unit/app -q`
Expected: все проходят (`test_provider.py` — 11 тестов).

Run: `<env> uv run pytest tests/unit -q`
Expected: `750 passed`.

- [ ] **Step 7: Интеграционный тест библиотечного сценария**

Создать `tests/integration/test_provider_integration.py` (два провайдера на одной тестовой БД, один под namespace; прогонит CI с Docker):

```python
# mypy: ignore-errors
"""Integration test for library usage: two PostgresProvider instances in one host FastMCP server."""

import pytest
from fastmcp import Client, FastMCP

from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.provider import PostgresProvider
from postgres_fastmcp.shared.enums import AccessMode


@pytest.mark.asyncio
async def test_host_server_with_basic_and_namespaced_full_provider(
    test_postgres_connection_string: tuple[str, str],
) -> None:
    """Basic provider without namespace and a full provider under 'analytics' share one host server."""
    connection_string, _ = test_postgres_connection_string
    host = FastMCP("host")
    host.add_provider(PostgresProvider(DatabaseConfig.from_uri(connection_string, access_mode=AccessMode.BASIC)))
    host.add_provider(
        PostgresProvider(DatabaseConfig.from_uri(connection_string, access_mode=AccessMode.FULL)),
        namespace="analytics",
    )
    async with Client(host) as client:
        names = {tool.name for tool in await client.list_tools()}
        basic = await client.call_tool("execute_sql", {"sql": "SELECT 1 AS n", "output": "json"})
        schemas = await client.call_tool("analytics_list_schemas", {"output": "json"})
        blocked = await client.call_tool("list_schemas", {}, raise_on_error=False)

    assert "execute_sql" in names
    assert "analytics_list_schemas" in names
    assert "list_schemas" not in names
    assert basic.structured_content == {"rows": [{"n": 1}], "row_count": 1}
    assert "public" in {row["schema_name"] for row in schemas.structured_content["rows"]}
    assert blocked.is_error is True
```

Run: `<env> uv run pytest tests/integration -q`
Expected: `80 skipped` (новый тест × два образа Postgres), без ошибок сбора.

Run: `uv run ruff check tests/integration --select F,ARG --no-fix`
Expected: `All checks passed!`

- [ ] **Step 8: Линтеры и коммит**

Run:
```bash
uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && \
uv run ruff format tests/unit/test_provider.py tests/unit/app/test_server.py tests/unit/app/test_response_budget.py \
  tests/integration/test_provider_integration.py
```
Expected: ruff и mypy без ошибок (`Success: no issues found in 76 source files`).

```bash
git add src/postgres_fastmcp/provider.py src/postgres_fastmcp/app tests/unit tests/integration/test_provider_integration.py
git commit -m "feat(provider): serve the tools through PostgresProvider and build create_server on it"
```

---

### Task 5: Нейтральное описание `execute_sql`

**Files:**
- Modify: `src/postgres_fastmcp/tools/registry.py` (`_basic_specs`, `_execute_sql_desc`)
- Test: `tests/unit/tools/test_tool_descriptions.py` (четыре теста `execute_sql` заменяются двумя)

**Interfaces:**
- Consumes: `register_tools` (Task 3).
- Produces: `_execute_sql_desc() -> str` без аргументов; одинаковое описание `execute_sql` при любом потолке. Аннотации `execute_sql` по-прежнему по потолку: `DESTRUCTIVE` при FULL+write, `WRITE_NON_DESTRUCTIVE` при basic+write, `READ_ONLY_NON_IDEMPOTENT` без записи.

Зачем: с шага 4 права запроса могут быть уже потолка (сужение по токену), а описание строится один раз при регистрации. Описание «Execute ANY SQL statement» для потолка FULL+write обманет агента с read-only токеном.

- [ ] **Step 1: Переписать тесты описания**

В `tests/unit/tools/test_tool_descriptions.py` удалить четыре функции `test_execute_sql_description_restricted_in_basic_mode`, `test_execute_sql_description_allows_dml_in_basic_write_mode`, `test_execute_sql_description_restricted_in_full_without_write_mode`, `test_execute_sql_description_unrestricted_when_full_and_write_mode` и вставить на их место:

```python
_MODES = [
    (AccessMode.BASIC, False),
    (AccessMode.BASIC, True),
    (AccessMode.FULL, False),
    (AccessMode.FULL, True),
]


def test_execute_sql_description_is_the_same_in_every_mode() -> None:
    """Описание execute_sql не зависит от потолка: права конкретного запроса могут быть уже потолка."""
    descriptions = {_descriptions(access_mode=mode, write_mode=write)["execute_sql"] for mode, write in _MODES}
    assert len(descriptions) == 1


def test_execute_sql_description_covers_every_mode() -> None:
    desc = _descriptions(access_mode=AccessMode.BASIC)["execute_sql"]
    assert "read-only mode only SELECT, EXPLAIN and SHOW are accepted" in desc
    assert "INSERT, UPDATE and DELETE on the public schema" in desc
    assert "DDL is rejected except CREATE EXTENSION hypopg / pg_stat_statements" in desc
    assert "full write access any statement runs" in desc
    assert "explicit error" in desc
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `<env> uv run pytest tests/unit/tools/test_tool_descriptions.py -q`
Expected: `2 failed, 6 passed`.

- [ ] **Step 3: Нейтральное описание**

В `src/postgres_fastmcp/tools/registry.py` в начале `_basic_specs` заменить

```python
    unrestricted = ceiling.access_mode == AccessMode.FULL and ceiling.write_mode
    if unrestricted:
        execute_preset = DESTRUCTIVE
```

на

```python
    # Аннотации execute_sql отражают потолок: клиент решает, спрашивать ли подтверждение.
    if ceiling.access_mode == AccessMode.FULL and ceiling.write_mode:
        execute_preset = DESTRUCTIVE
```

в спеке `execute_sql` строку описания заменить на `"description": _execute_sql_desc(),`, а функцию `_execute_sql_desc` целиком на:

```python
def _execute_sql_desc() -> str:
    # Описание не зависит от потолка: права запроса могут быть уже серверных (сужение по токену).
    return (
        "Execute a SQL statement. The server enforces the access policy of the current request: "
        "in read-only mode only SELECT, EXPLAIN and SHOW are accepted; with basic write access "
        "INSERT, UPDATE and DELETE on the public schema are also accepted and committed, and DDL is "
        "rejected except CREATE EXTENSION hypopg / pg_stat_statements; with full write access any "
        "statement runs. A rejected statement returns an explicit error. "
        "Workflow: list_objects -> get_object_details -> execute_sql."
    )
```

- [ ] **Step 4: Запустить тесты и проверить бюджет схемы**

Run: `<env> uv run pytest tests/unit/tools -q`
Expected: все проходят, в том числе `test_tools_list_fits_budget` и `test_everything_the_agent_sees_is_english`.

Run:
```bash
<env> uv run python -c "import asyncio, json, math; from postgres_fastmcp.app.config import Settings; from postgres_fastmcp.app.server import create_server; from postgres_fastmcp.shared.enums import AccessMode; s = Settings(); s.database = s.database.model_copy(update={'access_mode': AccessMode.FULL}); tools = asyncio.run(create_server(s).list_tools()); size = sum(len(json.dumps(t.to_mcp_tool().model_dump(by_alias=True, exclude_none=True))) for t in tools); print(size, math.ceil(size * 1.15))" 2>/dev/null
```
Expected: `9710 11167` (до задачи `9368 10774`). Размер помещается в `_TOOLS_LIST_BUDGET_CHARS = 10_774`, порог не трогать.

Run: `<env> uv run pytest tests/unit -q`
Expected: `748 passed`.

- [ ] **Step 5: Линтеры и коммит**

Run: `uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff format tests/unit/tools/test_tool_descriptions.py`
Expected: без ошибок.

```bash
git add src/postgres_fastmcp/tools/registry.py tests/unit/tools/test_tool_descriptions.py
git commit -m "feat(tools): describe execute_sql the same way in every access mode"
```

---

### Task 6: Публичное API §6 и документация библиотечного сценария

**Files:**
- Modify: `src/postgres_fastmcp/__init__.py` (переписать целиком)
- Test: `tests/unit/test_public_api.py` (переписать целиком)
- Modify: `README.md` — раздел «### Управление жизненным циклом» (`README.md:262-268`) и раздел «## Использование как библиотека» целиком (`README.md:428-461`)
- Modify: `AGENTS.md` — разделы «## Context Access — `CurrentContext()`» (удаляется), «## Tool Definitions», «## Layered Architecture» (`AGENTS.md:932-1020`)
- Modify: `src/postgres_fastmcp/tools/AGENTS.md` — разделы «## Language» и «## Parameters»

**Interfaces:**
- Consumes: всё из Task 1–5.
- Produces: `postgres_fastmcp.__all__ == ["AccessPolicy", "AccessResolver", "DatabaseConfig", "EffectiveAccess", "PostgresProvider", "Settings", "__version__", "create_server", "full_access_check"]`; `Middleware`, `LocalProvider`, `FileSystemProvider` из пакета больше не экспортируются.

- [ ] **Step 1: Тест публичного API**

`tests/unit/test_public_api.py` заменить целиком:

```python
"""Тесты публичного API пакета postgres_fastmcp."""

import postgres_fastmcp as pkg
from postgres_fastmcp.access import AccessPolicy, EffectiveAccess, full_access_check
from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.app.server import create_server
from postgres_fastmcp.provider import PostgresProvider


_EXPECTED = {
    "AccessPolicy",
    "AccessResolver",
    "DatabaseConfig",
    "EffectiveAccess",
    "PostgresProvider",
    "Settings",
    "__version__",
    "create_server",
    "full_access_check",
}


def test_public_api_exports_exactly_the_spec_list() -> None:
    assert set(pkg.__all__) == _EXPECTED
    missing = {name for name in _EXPECTED if not hasattr(pkg, name)}
    assert not missing, f"Missing exports: {missing}"


def test_fastmcp_reexports_are_gone() -> None:
    """Middleware, LocalProvider, FileSystemProvider импортируются из fastmcp, а не из пакета."""
    for name in ("Middleware", "LocalProvider", "FileSystemProvider"):
        assert not hasattr(pkg, name), name


def test_exports_are_the_implementation_objects() -> None:
    assert pkg.PostgresProvider is PostgresProvider
    assert pkg.DatabaseConfig is DatabaseConfig
    assert pkg.AccessPolicy is AccessPolicy
    assert pkg.EffectiveAccess is EffectiveAccess
    assert pkg.full_access_check is full_access_check
    assert pkg.Settings is Settings
    assert pkg.create_server is create_server
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `<env> uv run pytest tests/unit/test_public_api.py -q`
Expected: `3 failed` (`__all__` другой, `hasattr(pkg, 'Middleware')` истинно, `module 'postgres_fastmcp' has no attribute 'PostgresProvider'`).

- [ ] **Step 3: `__init__.py`**

`src/postgres_fastmcp/__init__.py` заменить целиком:

```python
"""postgres-fastmcp: PostgreSQL Tuning and Analysis MCP server, also usable as a library.

Public API:
    PostgresProvider — источник тулов одной базы для своего FastMCP (``add_provider``, ``namespace``).
    DatabaseConfig — подключение и серверный потолок прав (access_mode, write_mode).
    AccessPolicy, EffectiveAccess, AccessResolver, full_access_check — права одного запроса.
    Settings, create_server — готовый сервер с конфигурацией из env/.env/config.json.

Middleware, LocalProvider и прочие классы FastMCP импортируются из ``fastmcp``.
"""

from importlib.metadata import (
    PackageNotFoundError,
    version as _pkg_version,
)


try:
    __version__ = _pkg_version("postgres-fastmcp")
except PackageNotFoundError:
    __version__ = "0.0.0"

from postgres_fastmcp.access import AccessPolicy, AccessResolver, EffectiveAccess, full_access_check
from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.app.server import create_server
from postgres_fastmcp.provider import PostgresProvider


__all__ = [
    "AccessPolicy",
    "AccessResolver",
    "DatabaseConfig",
    "EffectiveAccess",
    "PostgresProvider",
    "Settings",
    "__version__",
    "create_server",
    "full_access_check",
]
```

Run: `<env> uv run pytest tests/unit/test_public_api.py -q`
Expected: `3 passed`.

Run: `for m in provider access domains.db_access tools.registry app.server app.main; do uv run python -c "import postgres_fastmcp.$m" && echo "ok $m"; done`
Expected: шесть строк `ok ...` — циклических импортов нет.

Run: `<env> uv run pytest tests/unit -q`
Expected: `748 passed`.

- [ ] **Step 4: Коммит API**

Run: `uv run ruff check --fix --show-fixes && uv run mypy src/ && uv run ruff format && uv run ruff format tests/unit/test_public_api.py`
Expected: без ошибок.

```bash
git add src/postgres_fastmcp/__init__.py tests/unit/test_public_api.py
git commit -m "feat(api): export PostgresProvider, DatabaseConfig and access types from the package root"
```

- [ ] **Step 5: README**

В `README.md` в разделе «### Управление жизненным циклом» заменить абзац и список до «- Обработка сигналов» на:

```markdown
Пулом соединений владеет `PostgresProvider`, его жизненный цикл привязан к lifespan сервера FastMCP:

- Пул открывается при первом запросе к БД
- Соединения закрываются при остановке сервера
- Обработка сигналов (SIGINT, SIGTERM)
```

Раздел от «## Использование как библиотека» до «## Разработка» (не включая) заменить на:

````markdown
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
    Settings,
    create_server,
)
```

`Middleware`, `LocalProvider` и прочие классы FastMCP импортируйте из `fastmcp`.

### Свой сервер: `PostgresProvider`

```python
from fastmcp import FastMCP
from postgres_fastmcp import DatabaseConfig, PostgresProvider

mcp = FastMCP("my-app")
mcp.add_provider(PostgresProvider(DatabaseConfig(host="db", port=5432, user="u", password="p", name="orders")))
mcp.add_provider(
    PostgresProvider(DatabaseConfig(host="db", user="u", password="p", name="analytics", access_mode="full")),
    namespace="analytics",  # инструменты analytics_execute_sql, analytics_list_schemas, ...
)
mcp.run(transport="http", host="0.0.0.0", port=8000)
```

- Провайдер владеет пулом соединений: пул открывается при первом запросе и закрывается при остановке сервера.
- Видимость инструментов задаёт `access_mode` из `DatabaseConfig`: в `basic` доступны четыре инструмента, в `full` — девять.
- `access_resolver` — функция `AccessToken | None -> EffectiveAccess`, считающая права запроса по токену; результат никогда не превышает потолок из `DatabaseConfig`. Без резолвера права запроса равны потолку. Сужение прав по claim через `AccessPolicy(enforced=True)` пока не поддерживается: провайдер отклоняет такую политику при создании.
- Бюджет ответа подключает только `create_server`. На своём сервере добавьте его сами первым middleware: `mcp.add_middleware(ResponseBudgetMiddleware(20000))`, импорт — `from postgres_fastmcp.app.middleware.response_budget import ResponseBudgetMiddleware`.

### Готовый сервер: `create_server`

```python
from postgres_fastmcp import Settings, create_server

server = create_server(
    Settings(),
    auth=None,  # любой AuthProvider из fastmcp.server.auth
    access_resolver=None,  # свой резолвер прав для PostgresProvider
    extra_providers=[],  # свои источники tools/resources/prompts
    extra_middleware=[],  # добавляются после встроенных middleware
)
server.run(transport="http", host="0.0.0.0", port=8000)
```

Замечания:
- `auth=None` означает отсутствие аутентификации. Это безопасно для транспорта `stdio` или доверенного localhost. Для сетевого HTTP-развёртывания подключите `AuthProvider` из `fastmcp.server.auth` или собственный.
- Имена инструментов из `extra_providers` не должны совпадать со встроенными (`execute_sql`, `list_objects`, `get_object_details`, `explain_query`, `list_schemas`, `analyze_db_health`, `get_top_queries`, `analyze_query_indexes`, `analyze_workload_indexes`): при совпадении FastMCP пишет предупреждение и оставляет встроенный инструмент. Чтобы развести имена, подключите свой провайдер через `server.add_provider(provider, namespace="...")`.
- Middleware выполняются в порядке: бюджет ответа, timing, logging, затем `extra_middleware`. Бюджет стоит первым и проверяет в том числе результат ваших middleware.
````

Проверить пример из README (переменные `MCP_DATABASE_*` сняты, чтобы работали только явные поля):

```bash
env -u MCP_DATABASE_HOST -u MCP_DATABASE_USER -u MCP_DATABASE_PASSWORD -u MCP_DATABASE_NAME uv run python - <<'PY'
import asyncio
from fastmcp import FastMCP
from postgres_fastmcp import DatabaseConfig, PostgresProvider

mcp = FastMCP("my-app")
mcp.add_provider(PostgresProvider(DatabaseConfig(host="db", port=5432, user="u", password="p", name="orders")))
mcp.add_provider(
    PostgresProvider(DatabaseConfig(host="db", user="u", password="p", name="analytics", access_mode="full")),
    namespace="analytics",
)
print(sorted(t.name for t in asyncio.run(mcp.list_tools())))
PY
```
Expected: 13 имён — `analytics_*` девяти тулов и четыре basic-тула без префикса.

- [ ] **Step 6: `AGENTS.md`**

В корневом `AGENTS.md` удалить раздел «## Context Access — `CurrentContext()`» целиком (тулы больше не принимают `Context`) и заменить раздел «## Tool Definitions» (до «## Server Startup») на:

```markdown
## Tool Definitions

Rules for the MCP tools layer (English-only agent-facing text, parameter types and normalization,
`output` and `ToolResult`, the response budget, adding a tool) live in
[`src/postgres_fastmcp/tools/AGENTS.md`](src/postgres_fastmcp/tools/AGENTS.md).

In short: tools are async methods of `ToolSet` (`tools/definitions.py`); they take no FastMCP
`Context` and get database access through `self._get_db()`, which returns a `DbAccessPort` with the
current request's access. `tools/registry.py` registers them on a `LocalProvider` with
`Tool.from_function` (description, tags, annotations, timeout, `auth` for `full` tools).
`PostgresProvider` (`provider.py`) owns the registration, the connection pool and tool visibility.
```

В разделе «## Layered Architecture» заменить три первых пункта (App, Presentation, Domains — строку Domains до вложенного списка `index_tuning`) на:

```markdown
- **App / composition root** (`app/`): config, server assembly (`app/server.py::create_server`), middleware, entry point (`app/main.py`)
- **Provider** (`provider.py`): `PostgresProvider(LocalProvider)` — owns `DbAccessService` (pool closed in the provider `lifespan`), resolves the request's access and registers the tools
- **Access** (`access.py`): `EffectiveAccess`, `AccessPolicy`, `AccessResolver`, `full_access_check`; depends only on `shared/` and `fastmcp.server.auth`
- **Presentation** (`tools/`): `ToolSet` with the tool methods (`tools/definitions.py`) and registration with descriptions/annotations (`tools/registry.py`)
- **Domains** (`domains/`): one package or module per feature — `catalog`, `querying`, `explain`, `health`, `index_tuning`, `top_queries`, plus `db_access` (pool, executors per `EffectiveAccess`, `DbAccessPort`). Domain services take a `DbAccessPort`, never `DbAccessService`
```

и первый пункт «Dependency rules» на два:

```markdown
- Direction: `app -> provider -> tools -> domains -> postgres -> shared`; `access.py` is imported by `provider`, `app` and `domains/db_access`. The one upward import: `provider.py` takes the `DatabaseConfig` model from `app/config/database.py` (plain data, no app logic).
- No upward imports (`postgres/` must not import from `domains/`, `domains/` must not import from `tools/`, `provider` or `app/`).
```

- [ ] **Step 7: `tools/AGENTS.md`**

В `src/postgres_fastmcp/tools/AGENTS.md` в «## Language» заменить пункт про docstring на:

```markdown
- docstrings of `ToolSet` methods in `definitions.py` (FastMCP shows them to the client);
```

В «## Parameters» удалить последний пункт (`Do not put Field on dependency-injected parameters ...`) и сразу после раздела добавить:

```markdown
## Tool methods and registration

- A tool is an async method of `ToolSet` in `definitions.py`. It takes no FastMCP `Context`: it gets
  database access with `self._get_db()`, which returns a `DbAccessPort` carrying the current
  request's access (executor, `access_mode`, `write_mode`). Call it once per tool call and pass the
  result to the domain; never store it on `self`.
- Register the tool in `registry.py` (`_basic_specs` or `_full_specs`) with `"fn": toolset.<method>`,
  an explicit `name`, a description, `tags`, `annotations`, `timeout` and `meta`. A `full` tool also
  gets `"auth": full_tool_auth`: `PostgresProvider` passes `full_access_check(...)` there, so the
  tool is listed and callable only when the request's effective access is `full`.
- Descriptions follow the server ceiling (`access_mode`). The exception is `execute_sql`: which
  statements it accepts depends on the request's access, so it has one description for all modes;
  its annotations still follow the ceiling.
```

Run: `grep -rn "lifespan.py\|app/context.py\|CurrentContext\|LocalProvider,\|FileSystemProvider" README.md AGENTS.md src/postgres_fastmcp/tools/AGENTS.md`
Expected: пусто.

- [ ] **Step 8: Коммит документации**

```bash
git add README.md AGENTS.md src/postgres_fastmcp/tools/AGENTS.md
git commit -m "docs: describe PostgresProvider, the access seam and ToolSet for library users and tool authors"
```

---

### Task 7: Полная локальная проверка CI

**Files:** нет изменений.

**Interfaces:**
- Consumes: результат Task 1–6.
- Produces: подтверждение, что ветка готова к ревью.

- [ ] **Step 1: Полный локальный CI**

Run:
```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src/ && \
<env> uv run pytest tests/unit -q && \
<env> uv run pytest tests/integration -q
```
Expected: `All checks passed!`, `76 files already formatted`, `Success: no issues found in 76 source files`, юнит-тесты `748 passed`, интеграция `80 skipped` без ошибок сбора (или PASS при наличии Docker).

- [ ] **Step 2: Следов старого API не осталось**

Run: `grep -rn "app.lifespan\|app.context\|get_db(ctx\|CurrentContext\|make_ctx\|FakeCtx\|db_service_" src tests`
Expected: пусто.

Run: `grep -rln "DbAccessService" src`
Expected: только `src/postgres_fastmcp/domains/db_access.py` и `src/postgres_fastmcp/provider.py`.

Run: `grep -rn "from __future__ import annotations" src tests`
Expected: пусто.

- [ ] **Step 3: CLI в stdio отдаёт те же тулы**

Run:
```bash
<env> uv run python - <<'PY' 2>/dev/null
import asyncio, os, sys
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

async def names(mode: str) -> list[str]:
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "postgres_fastmcp.app.main", "--transport", "stdio"],
        env={**os.environ, "MCP_DATABASE_ACCESS_MODE": mode},
    )
    async with Client(transport) as client:
        return sorted(t.name for t in await client.list_tools())

for mode in ("basic", "full"):
    print(mode, asyncio.run(names(mode)))
PY
```
Expected:
```text
basic ['execute_sql', 'explain_query', 'get_object_details', 'list_objects']
full ['analyze_db_health', 'analyze_query_indexes', 'analyze_workload_indexes', 'execute_sql', 'explain_query', 'get_object_details', 'get_top_queries', 'list_objects', 'list_schemas']
```
Тот же вывод даёт ветка до плана (проверено на `b56b951`). Режим задаётся через окружение: `StdioTransport` без `env` не передаёт дочернему процессу `MCP_DATABASE_*`, а CLI-флаг `--access-mode` без `--database-uri` игнорируется (`build_settings_from_cli`, существующее поведение, вне объёма). Вызовы тулов здесь не проверяются: без БД пул ждёт подключения до таймаута.

- [ ] **Step 4: Итог**

Run: `git log --oneline -7`
Expected: семь коммитов плана поверх `b56b951`: `feat(access)`, `refactor(db_access)`, `refactor(tools)`, `feat(provider)`, `feat(tools)`, `feat(api)`, `docs`.

---

## Self-Review

**1. Покрытие спеки (шаг 3 из §9):**

| Раздел спеки | Задача |
| --- | --- |
| §3.1 слои: `provider.py`, `access.py`, удаление `app/lifespan.py` и `app/context.py`, направление зависимостей | Task 1, 4; описание слоёв — Task 6 |
| §3.2 `PostgresProvider(LocalProvider)`: владеет `DbAccessService`, ленивый пул, `lifespan()` закрывает; `access_resolver` приоритетнее `access_policy`; без них — потолок; `register_tools(self, toolset, ceiling=database)`; `get_db` = токен → права → `view()`; `disable(tags={"full"})` в basic; `full`-тулы с `auth=full_access_check(resolver)`; тулы без `Context`; несколько баз через namespace | Task 3 (`ToolSet`, `register_tools`), Task 4 (провайдер, тесты namespace) |
| §3.2 `ping()` | Не входит: нужен только `/health` (шаг 5) |
| §3.3 `DbAccessPort`, `DbAccess`, `view()`, кэш `dict[EffectiveAccess, SqlDriverPort]` (≤ 4), `SqlExecutor` только для FULL+write, `SafeSqlConfig` из `access`, удаление `sql_driver` у сервиса, домены на `DbAccessPort` (включая `DatabaseHealthAnalyzer`) | Task 2 |
| §3.4 `EffectiveAccess`, `AccessPolicy`, `AccessResolver`, `build_resolver`, `full_access_check`; правило «не выше потолка» | Task 1 (+ `bounded_resolver`); `resolve_access` с правилами claim — шаг 4 |
| §3.7 `create_server(settings, *, auth, access_resolver, extra_providers, extra_middleware)` поверх провайдера, `providers=[provider, *extra]`, `mask_error_details=True`, `on_duplicate="error"` | Task 4 (бюджет первым — см. «Отступления»); `build_auth_provider`, `/health`, предупреждения — шаги 4–5 |
| §3.8 аннотации по потолку, описания по потолку, нейтральное описание `execute_sql` | Task 3 (перенос без изменений), Task 5 |
| §6 публичное API | Task 6 |
| §8 unit: `DbAccessService.view` (кэш, один пул, `close()`), `PostgresProvider` (тулы basic/full без auth, `full_access_check` с токеном `None`) | Task 2, Task 1, Task 4; проверки claim — шаг 4 |
| §8 интеграция: «существующие тесты адаптируются к `PostgresProvider`» | Task 2 (фикстуры и тесты на `DbAccess`), Task 4 (`create_server` уже на провайдере + новый библиотечный сценарий) |
| §10 ломающие изменения API (экспорты, `DbAccessService.sql_driver` → `view`, удаление lifespan/context, `create_server(access_resolver=...)`) | Task 2, 4, 6; README — Task 6 |

**2. Плейсхолдеры:** нет. Весь код дан целиком или точной правкой; все числа (34, 8, 11 тестов; 724/733/737/750/748 unit; 78/80 skipped; 9368/9710 символов `tools/list`) получены прогоном плана в копии репозитория против `.venv` проекта.

**3. Согласованность типов:** `EffectiveAccess(access_mode, write_mode)` (Task 1) — ключ `DbAccessService._executors` и аргумент `view()` (Task 2), результат резолвера в `PostgresProvider._current_db` (Task 4). `AccessResolver` (Task 1) — тип `access_resolver` у `PostgresProvider` и `create_server` (Task 4). `DatabaseConfigPort` (Task 2) — тип `ceiling` в `register_tools` (Task 3); `DatabaseConfig` удовлетворяет ему структурно (проверено mypy). `ToolSet(get_db: Callable[[], DbAccessPort])` (Task 3) получает `PostgresProvider._current_db` (Task 4). `full_tool_auth: AuthCheck | None` (Task 3) получает `full_access_check(self._resolve)` (Task 4). Временный мост Task 2–3 (`app.context.get_db`, lifespan с `view(ceiling)`) удаляется в Task 4 вместе с тестом `test_lifespan.py`; `grep` в Task 4 Step 5 и Task 7 Step 2 это проверяет.
