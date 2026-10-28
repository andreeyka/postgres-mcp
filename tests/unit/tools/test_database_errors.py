"""Ошибки PostgreSQL видны клиенту при включённом маскировании FastMCP."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import Client, FastMCP
from fastmcp.server.providers import LocalProvider
from psycopg import Error, OperationalError
from psycopg.errors import DivisionByZero, InvalidPassword, UndefinedColumn
from psycopg.pq import DiagnosticField

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.domains.db_access import DbAccessPort
from postgres_fastmcp.shared.errors import QueryCancelledError, QueryTimeoutError
from postgres_fastmcp.tools.definitions import ToolSet
from postgres_fastmcp.tools.registry import register_tools


async def _call(error: Exception, tool: str = "execute_sql") -> str:
    db = MagicMock(spec=DbAccessPort)
    db.sql_driver = AsyncMock()
    db.sql_driver.execute_statement.side_effect = error
    db.sql_driver.execute.side_effect = error
    provider = LocalProvider()
    register_tools(provider, ToolSet(get_db=lambda: db), ceiling=Settings().database)
    server = FastMCP("errors", mask_error_details=True, providers=[provider])
    async with Client(server) as client:
        result = await client.call_tool(tool, {"sql": "SELECT 1"}, raise_on_error=False)
    assert result.is_error
    return result.content[0].text


@pytest.mark.parametrize("tool", ["execute_sql", "explain_query"])
async def test_primary_message_and_sqlstate_survive_masking(tool: str) -> None:
    error = UndefinedColumn(
        "unsafe driver exception: password=driver-secret",
        info={
            DiagnosticField.MESSAGE_PRIMARY: b'column "missing" does not exist',
            DiagnosticField.MESSAGE_DETAIL: b"password=detail-secret",
            DiagnosticField.CONTEXT: b"postgresql://user:context-secret@private/db",
            DiagnosticField.INTERNAL_QUERY: b"SELECT 'query-secret'",
        },
    )
    assert await _call(error, tool) == 'PostgreSQL error [42703]: column "missing" does not exist'


@pytest.mark.parametrize(
    "message",
    [
        'invalid value "postgresql://user:uri-secret@private:5432/db?token=query-secret"',
        "invalid value 'host=private user=alice password=dsn-secret dbname=internal'",
        "invalid value \"password='a secret with spaces' sslpassword=ssl-secret\"",
        "invalid value 'token=token-secret api_key=api-secret'",
        "invalid value 'password=dsn-secret,comma-tail;semicolon-tail'",
        r"invalid value 'password=dsn-secret\ escaped-tail'",
        'invalid value "postgresql://user:uri-secret\'apostrophe-tail@private/db"',
    ],
)
async def test_sensitive_values_in_primary_message_are_redacted(message: str) -> None:
    text = await _call(UndefinedColumn("unused", info={DiagnosticField.MESSAGE_PRIMARY: message.encode()}))
    assert "42703" in text
    for secret in [
        "private",
        "alice",
        "internal",
        "uri-secret",
        "query-secret",
        "dsn-secret",
        "a secret with spaces",
        "ssl-secret",
        "token-secret",
        "api-secret",
        "comma-tail",
        "semicolon-tail",
        "escaped-tail",
        "apostrophe-tail",
    ]:
        assert secret not in text


@pytest.mark.parametrize("error", [Error("password=secret"), OperationalError("postgres://user:secret@private/db")])
async def test_missing_diagnostics_never_fall_back_to_driver_exception(error: Error) -> None:
    assert await _call(error) == "Database operation failed. PostgreSQL diagnostics are unavailable."


async def test_sqlstate_without_primary_has_safe_fallback() -> None:
    assert await _call(DivisionByZero("driver-secret")) == "PostgreSQL error [22012]: Database operation failed."


async def test_authentication_error_never_exposes_password_equal_to_username() -> None:
    error = InvalidPassword(
        "driver-secret",
        info={DiagnosticField.MESSAGE_PRIMARY: b'password authentication failed for user "same-secret"'},
    )
    assert await _call(error) == "PostgreSQL error [28P01]: Database connection failed."


@pytest.mark.parametrize("error", [QueryCancelledError(), QueryTimeoutError(5)])
async def test_existing_user_facing_errors_are_preserved(error: Exception) -> None:
    assert await _call(error) == str(error)


async def test_unexpected_application_error_remains_masked() -> None:
    assert await _call(RuntimeError("internal-secret")) == "Error calling tool 'execute_sql'"
