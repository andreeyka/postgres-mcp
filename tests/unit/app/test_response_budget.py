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
from postgres_fastmcp.app.middleware.response_budget import BYTES_PER_TOKEN, ResponseBudgetMiddleware, estimate_tokens
from postgres_fastmcp.app.server import create_server
from postgres_fastmcp.postgres.models import RowResult, StatementResult
from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.shared.errors import ResponseTooLargeAfterWriteError, ResponseTooLargeError


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
    monkeypatch.delenv("MCP_SERVER_RESPONSE_MAX_TOKENS", raising=False)
    assert ServerSettings().response_max_tokens == 20000
    monkeypatch.setenv("MCP_SERVER_RESPONSE_MAX_TOKENS", "5000")
    assert ServerSettings().response_max_tokens == 5000
    with pytest.raises(ValidationError):
        ServerSettings(response_max_tokens=999)


def _server_with_rows(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[RowResult],
    max_tokens: int,
    *,
    access_mode: AccessMode = AccessMode.FULL,
    write_mode: bool = False,
):
    """create_server с подменённым DbAccessService: execute_sql получает заданные строки."""

    class FakeDb:
        def __init__(self, cfg: object) -> None:
            self.write_mode = write_mode
            self.sql_driver = MagicMock()
            self.sql_driver.execute = AsyncMock(return_value=rows)
            self.sql_driver.execute_statement = AsyncMock(
                return_value=StatementResult(rows=rows, status=f"SELECT {len(rows)}", affected_rows=len(rows))
            )
            self.catalog_driver = MagicMock()

        def view(self, access: object) -> "FakeDb":
            return self

        async def close(self) -> None:
            return None

    monkeypatch.setattr("postgres_fastmcp.provider.DbAccessService", FakeDb)
    settings = Settings()
    settings.database = settings.database.model_copy(update={"access_mode": access_mode, "write_mode": write_mode})
    settings.server = settings.server.model_copy(update={"response_max_tokens": max_tokens})
    return create_server(settings)


async def test_create_server_rejects_large_execute_sql_result(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [RowResult(cells={"id": i, "name": f"row-{i:04d}-" + "x" * 40}) for i in range(200)]
    async with Client(_server_with_rows(monkeypatch, rows, max_tokens=1000)) as client:
        with pytest.raises(
            ToolError, match=r"Response is too large: ~\d+ tokens, the limit is 1000\. Refine the request"
        ):
            await client.call_tool("execute_sql", {"sql": "SELECT id, name FROM t"})


async def test_create_server_passes_small_execute_sql_result(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [RowResult(cells={"id": 1, "name": "a"})]
    async with Client(_server_with_rows(monkeypatch, rows, max_tokens=1000)) as client:
        result = await client.call_tool("execute_sql", {"sql": "SELECT id, name FROM t"})
    assert result.content[0].text == "| id | name |\n| --- | --- |\n| 1 | a |\n\n1 rows."


def _large_rows() -> list[RowResult]:
    return [RowResult(cells={"id": i, "name": f"row-{i:04d}-" + "x" * 40}) for i in range(200)]


@pytest.mark.parametrize("access_mode", [AccessMode.BASIC, AccessMode.FULL])
async def test_read_only_server_asks_to_refine(monkeypatch: pytest.MonkeyPatch, access_mode: AccessMode) -> None:
    """Без write_mode execute_sql ничего не записал: обычная ошибка «уточните запрос»."""
    server = _server_with_rows(monkeypatch, _large_rows(), max_tokens=1000, access_mode=access_mode)
    async with Client(server) as client:
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("execute_sql", {"sql": "SELECT id, name FROM t"})
    assert "Refine the request" in str(exc_info.value)
    assert "do not re-run" not in str(exc_info.value)


@pytest.mark.parametrize("access_mode", [AccessMode.BASIC, AccessMode.FULL])
async def test_write_server_warns_not_to_rerun(monkeypatch: pytest.MonkeyPatch, access_mode: AccessMode) -> None:
    """С write_mode изменения уже закоммичены: агент не должен повторять запрос."""
    server = _server_with_rows(monkeypatch, _large_rows(), max_tokens=1000, access_mode=access_mode, write_mode=True)
    async with Client(server) as client:
        with pytest.raises(
            ToolError,
            match=r"Response is too large: ~\d+ tokens, the limit is 1000\. If the statement modified data, "
            r"its changes are already applied — do not re-run it;",
        ):
            await client.call_tool("execute_sql", {"sql": "UPDATE t SET name = name RETURNING id, name"})


async def test_unresolved_tool_falls_back_to_refine_error() -> None:
    """Без fastmcp_context тул не найти: обычная ошибка, а не «не повторяйте»."""
    middleware = ResponseBudgetMiddleware(max_tokens=10)
    with pytest.raises(ResponseTooLargeError) as exc_info:
        await middleware.on_call_tool(_context(), AsyncMock(return_value=_result("x" * 31)))
    assert not isinstance(exc_info.value, ResponseTooLargeAfterWriteError)
