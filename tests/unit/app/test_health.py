"""GET /health: 200 при ответе БД, 503 с замаскированным паролем, без аутентификации."""

import asyncio
import logging
import struct
import time
from collections.abc import AsyncIterator
from urllib.parse import quote, quote_plus

import pytest
from fastmcp.utilities.tests import asgi_server
from pydantic import SecretStr

from postgres_fastmcp.app import server as server_module
from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.config.auth import AuthSettings
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.app.server import create_server
from postgres_fastmcp.provider import PostgresProvider


_SECRET = "S3CRETPW"
_HEALTH_URL = "http://127.0.0.1/health"
# Пароль со спецсимволами: в URI он лежит в %-кодированной форме, в конфиге — как есть
_URI_SECRET = "Pa ss/w@rd+S3CRET"


def _settings(**server: object) -> Settings:
    database = DatabaseConfig(host="127.0.0.1", port=1, user="u", password=SecretStr(_SECRET), name="d")
    return Settings(database=database, server=server)


def _uri_settings() -> Settings:
    uri = f"postgresql://u:{quote(_URI_SECRET, safe='')}@127.0.0.1:1/d"
    return Settings(database=DatabaseConfig.from_uri(uri))


_UNAVAILABLE = {"status": "degraded", "error": "database unavailable"}
_REDACTED = "database connection failed (details redacted)"


def _secret_forms(secret: str) -> set[str]:
    return {secret, quote_plus(secret), quote(secret, safe="")}


def _messages(caplog: pytest.LogCaptureFixture) -> str:
    """Текст всех записей лога без имён логгеров (в имени пакета есть 'postgres')."""
    return "\n".join(record.getMessage() for record in caplog.records)


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]


def _pg_error(message: str) -> bytes:
    """ErrorResponse протокола Postgres: FATAL 28P01 с заданным текстом."""
    fields = [("S", "FATAL"), ("V", "FATAL"), ("C", "28P01"), ("M", message)]
    body = b"".join(key.encode() + value.encode() + b"\0" for key, value in fields) + b"\0"
    return b"E" + struct.pack("!I", len(body) + 4) + body


async def _reject_password(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Фейковый Postgres: отказывает в SSL/GSS и отвечает на startup ошибкой аутентификации с именем пользователя."""
    try:
        while True:
            length, code = struct.unpack("!II", await reader.readexactly(8))
            rest = await reader.readexactly(length - 8)
            if code in {80877103, 80877104}:  # SSLRequest, GSSENCRequest
                writer.write(b"N")
                await writer.drain()
                continue
            params = rest.split(b"\0")
            user = dict(zip(params[0::2], params[1::2], strict=False)).get(b"user", b"").decode()
            writer.write(_pg_error(f'password authentication failed for user "{user}"'))
            await writer.drain()
            return
    except (asyncio.IncompleteReadError, ConnectionError):
        return
    finally:
        writer.close()


@pytest.fixture
async def fake_postgres_port() -> AsyncIterator[int]:
    server = await asyncio.start_server(_reject_password, "127.0.0.1", 0)
    async with server:
        yield server.sockets[0].getsockname()[1]


async def _get_health(settings: Settings) -> tuple[int, dict[str, str]]:
    async with asgi_server(create_server(settings)) as running, running.http_client() as http:
        response = await http.get(_HEALTH_URL)
    return response.status_code, response.json() if response.status_code != 404 else {}


async def test_health_is_ok_when_the_database_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ping(_self: PostgresProvider) -> None:
        return None

    monkeypatch.setattr(PostgresProvider, "ping", ping)
    assert await _get_health(_settings()) == (200, {"status": "ok"})


async def test_health_hides_the_error_text_and_logs_it_masked(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Текст ошибки не уходит анониму; оператор видит его в WARNING с маской строки подключения."""

    async def ping(_self: PostgresProvider) -> None:
        msg = "could not connect to postgresql://u:hunter2@db/d; password=hunter2 host=db"
        raise RuntimeError(msg)

    monkeypatch.setattr(PostgresProvider, "ping", ping)
    with caplog.at_level(logging.DEBUG, logger="postgres_fastmcp"):
        status, body = await _get_health(_settings())
    assert (status, body) == (503, _UNAVAILABLE)
    assert _warnings(caplog) == [
        "Health check failed: could not connect to postgresql://u:****@db/d; password=**** host=db"
    ]
    assert "hunter2" not in _messages(caplog)


async def test_health_redacts_the_log_when_a_password_form_survives_masking(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Пароль из URI, оставшийся в тексте после маски (сырой или %-кодированный), не вклеивается в лог."""
    settings = _uri_settings()
    uri = settings.database.database_uri

    async def ping(_self: PostgresProvider) -> None:
        msg = f"failed: {uri} raw={_URI_SECRET} quoted={quote(_URI_SECRET, safe='')}"
        raise RuntimeError(msg)

    monkeypatch.setattr(PostgresProvider, "ping", ping)
    with caplog.at_level(logging.DEBUG, logger="postgres_fastmcp"):
        status, body = await _get_health(settings)
    assert (status, body) == (503, _UNAVAILABLE)
    assert _warnings(caplog) == [f"Health check failed: {_REDACTED}"]
    for form in _secret_forms(_URI_SECRET):
        assert form not in _messages(caplog)


async def test_health_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ping(_self: PostgresProvider) -> None:
        await asyncio.sleep(10)

    monkeypatch.setattr(PostgresProvider, "ping", ping)
    monkeypatch.setattr(server_module, "HEALTH_TIMEOUT_SECONDS", 0.05)
    started = time.monotonic()
    status, body = await _get_health(_settings())
    assert time.monotonic() - started < 2
    assert status == 503
    assert body == {"status": "degraded", "error": "database did not answer within 0.05 s"}


async def test_health_timeout_body_names_the_default_timeout(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Таймаут по умолчанию (2 s) даёт ровно этот текст; ожидание не тратится — ping сразу бросает TimeoutError."""

    async def ping(_self: PostgresProvider) -> None:
        raise TimeoutError

    monkeypatch.setattr(PostgresProvider, "ping", ping)
    with caplog.at_level(logging.WARNING, logger="postgres_fastmcp"):
        status, body = await _get_health(_settings())
    assert (status, body) == (503, {"status": "degraded", "error": "database did not answer within 2 s"})
    assert _warnings(caplog) == ["Health check failed: database did not answer within 2 s"]


@pytest.mark.timeout(10)
async def test_health_reports_an_unreachable_database_without_details(caplog: pytest.LogCaptureFixture) -> None:
    """Настоящий ping на закрытый порт: ошибка psycopg за доли секунды, наружу — только общий текст."""
    started = time.monotonic()
    with caplog.at_level(logging.DEBUG, logger="postgres_fastmcp"):
        status, body = await _get_health(_settings())
    assert time.monotonic() - started < server_module.HEALTH_TIMEOUT_SECONDS
    assert (status, body) == (503, _UNAVAILABLE)
    [warning] = _warnings(caplog)
    assert "127.0.0.1" in warning
    assert "Connection refused" in warning
    assert _SECRET not in _messages(caplog)


@pytest.mark.timeout(10)
async def test_health_on_an_unreachable_database_hides_a_uri_password(caplog: pytest.LogCaptureFixture) -> None:
    """Настоящий ping с паролем из URI: ни ответ, ни лог (включая DEBUG-логи psycopg) не содержат пароль."""
    with caplog.at_level(logging.DEBUG):
        status, body = await _get_health(_uri_settings())
    assert (status, body) == (503, _UNAVAILABLE)
    for form in _secret_forms(_URI_SECRET):
        assert form not in _messages(caplog)


@pytest.mark.timeout(10)
async def test_health_is_no_oracle_for_a_password_equal_to_the_user(
    fake_postgres_port: int, caplog: pytest.LogCaptureFixture
) -> None:
    """postgres/postgres: libpq называет пользователя в ошибке; маска '****' на его месте выдала бы пароль."""
    database = DatabaseConfig(
        host="127.0.0.1", port=fake_postgres_port, user="postgres", password=SecretStr("postgres"), name="d"
    )
    with caplog.at_level(logging.DEBUG, logger="postgres_fastmcp"):
        status, body = await _get_health(Settings(database=database))
    assert (status, body) == (503, _UNAVAILABLE)
    assert _warnings(caplog) == [f"Health check failed: {_REDACTED}"]
    messages = _messages(caplog)
    assert "postgres" not in messages
    assert 'user "****"' not in messages


@pytest.mark.timeout(10)
async def test_health_logs_the_auth_error_for_the_operator(
    fake_postgres_port: int, caplog: pytest.LogCaptureFixture
) -> None:
    """Пароль не совпадает с другими полями: оператор видит настоящую причину в WARNING, аноним — нет."""
    database = DatabaseConfig(
        host="127.0.0.1", port=fake_postgres_port, user="alice", password=SecretStr(_SECRET), name="salesdb"
    )
    with caplog.at_level(logging.DEBUG, logger="postgres_fastmcp"):
        status, body = await _get_health(Settings(database=database))
    assert (status, body) == (503, _UNAVAILABLE)
    [warning] = _warnings(caplog)
    assert 'password authentication failed for user "alice"' in warning
    assert _SECRET not in _messages(caplog)


async def test_concurrent_health_checks_share_one_ping(monkeypatch: pytest.MonkeyPatch) -> None:
    """Single-flight: одновременные GET ждут одну пробу, а не открывают по соединению каждый."""
    calls = 0

    async def ping(_self: PostgresProvider) -> None:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)

    monkeypatch.setattr(PostgresProvider, "ping", ping)
    async with asgi_server(create_server(_settings())) as running, running.http_client() as http:
        responses = await asyncio.gather(*(http.get(_HEALTH_URL) for _ in range(20)))
    assert calls == 1
    assert {(response.status_code, response.text) for response in responses} == {(200, '{"status":"ok"}')}


async def test_health_result_is_cached_until_the_ttl_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    """Результат живёт HEALTH_CACHE_SECONDS по монотонным часам модуля; потом проба повторяется."""
    calls = 0
    now = 1000.0

    async def ping(_self: PostgresProvider) -> None:
        nonlocal calls
        calls += 1
        if calls > 1:
            msg = "gone"
            raise RuntimeError(msg)

    monkeypatch.setattr(PostgresProvider, "ping", ping)
    monkeypatch.setattr(server_module, "monotonic", lambda: now)
    async with asgi_server(create_server(_settings())) as running, running.http_client() as http:
        first = await http.get(_HEALTH_URL)
        now += server_module.HEALTH_CACHE_SECONDS / 2
        cached = await http.get(_HEALTH_URL)
        now += server_module.HEALTH_CACHE_SECONDS
        refreshed = await http.get(_HEALTH_URL)
    assert calls == 2
    assert (first.status_code, cached.status_code, refreshed.status_code) == (200, 200, 503)
    assert refreshed.json() == _UNAVAILABLE


async def test_health_cache_is_per_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Кэш живёт в замыкании маршрута: второй сервер делает свою пробу."""
    calls = 0

    async def ping(_self: PostgresProvider) -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(PostgresProvider, "ping", ping)
    monkeypatch.setattr(server_module, "monotonic", lambda: 1000.0)
    await _get_health(_settings())
    await _get_health(_settings())
    assert calls == 2


async def test_health_needs_no_token_while_mcp_does(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ping(_self: PostgresProvider) -> None:
        return None

    monkeypatch.setattr(PostgresProvider, "ping", ping)
    settings = _settings()
    settings.auth = AuthSettings(mode="static", tokens={"tok-secret": {"client_id": "c"}})
    async with asgi_server(create_server(settings)) as running, running.http_client() as http:
        health = await http.get(_HEALTH_URL)
        mcp = await http.post(running.url, json={}, headers={"Accept": "application/json, text/event-stream"})
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert mcp.status_code == 401


async def test_health_can_be_disabled() -> None:
    status, _ = await _get_health(_settings(health_endpoint_enabled=False))
    assert status == 404


async def test_health_follows_the_http_app_path() -> None:
    """/health живёт в корне, а не под server.endpoint."""
    server = create_server(_settings(endpoint="/api/mcp"))
    paths = {getattr(route, "path", None) for route in server.http_app(path="/api/mcp").routes}
    assert {"/api/mcp", "/health"} <= paths
