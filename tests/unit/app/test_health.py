"""GET /health: 200 при ответе БД, 503 с замаскированным паролем, без аутентификации."""

import asyncio
import logging
import time
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


def _secret_forms(secret: str) -> set[str]:
    return {secret, quote_plus(secret), quote(secret, safe="")}


async def _get_health(settings: Settings) -> tuple[int, dict[str, str]]:
    async with asgi_server(create_server(settings)) as running, running.http_client() as http:
        response = await http.get(_HEALTH_URL)
    return response.status_code, response.json() if response.status_code != 404 else {}


async def test_health_is_ok_when_the_database_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ping(_self: PostgresProvider) -> None:
        return None

    monkeypatch.setattr(PostgresProvider, "ping", ping)
    assert await _get_health(_settings()) == (200, {"status": "ok"})


async def test_health_masks_the_password_in_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ping(_self: PostgresProvider) -> None:
        msg = f"could not connect to postgresql://u:{_SECRET}@db/d (password={_SECRET})"
        raise RuntimeError(msg)

    monkeypatch.setattr(PostgresProvider, "ping", ping)
    status, body = await _get_health(_settings())
    assert status == 503
    assert body["status"] == "degraded"
    assert "could not connect" in body["error"]
    assert _SECRET not in body["error"]


async def test_health_masks_a_password_from_the_uri_in_every_form(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Пароль из URI со спецсимволами: ни сырая, ни %-кодированная форма не попадают в ответ и лог."""
    settings = _uri_settings()
    uri = settings.database.database_uri

    async def ping(_self: PostgresProvider) -> None:
        msg = f"failed: {uri} raw={_URI_SECRET} quoted={quote(_URI_SECRET, safe='')}"
        raise RuntimeError(msg)

    monkeypatch.setattr(PostgresProvider, "ping", ping)
    with caplog.at_level(logging.DEBUG):
        status, body = await _get_health(settings)
    assert status == 503
    assert body["error"].startswith("failed: ")
    for form in _secret_forms(_URI_SECRET):
        assert form not in body["error"]
        assert form not in caplog.text
    assert "Health check failed" in caplog.text


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


@pytest.mark.timeout(10)
async def test_health_reports_an_unreachable_database_without_the_password() -> None:
    """Настоящий ping на закрытый порт: ошибка psycopg за доли секунды, пароля в ответе нет."""
    started = time.monotonic()
    status, body = await _get_health(_settings())
    assert time.monotonic() - started < server_module.HEALTH_TIMEOUT_SECONDS
    assert status == 503
    assert "127.0.0.1" in body["error"]
    assert _SECRET not in body["error"]


@pytest.mark.timeout(10)
async def test_health_on_an_unreachable_database_hides_a_uri_password(caplog: pytest.LogCaptureFixture) -> None:
    """Настоящий ping с паролем из URI: ни ответ, ни лог (включая логи psycopg) не содержат пароль."""
    with caplog.at_level(logging.DEBUG):
        status, body = await _get_health(_uri_settings())
    assert status == 503
    assert set(body) == {"status", "error"}
    for form in _secret_forms(_URI_SECRET):
        assert form not in body["error"]
        assert form not in caplog.text


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
