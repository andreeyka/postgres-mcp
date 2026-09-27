"""Стартовые логи: эффективный потолок прав и auth, адрес HTTP, предупреждения о старых env."""

import logging
from unittest.mock import patch

import pytest
from fastmcp import FastMCP

from postgres_fastmcp.app.config import LEGACY_ENV_NAMES, Settings, warn_about_legacy_env
from postgres_fastmcp.app.config.auth import AuthSettings
from postgres_fastmcp.app.main import app
from postgres_fastmcp.app.server import create_server


_SECRET = "tok-secret-value"


def _messages(caplog: pytest.LogCaptureFixture, logger: str, level: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == logger and r.levelname == level]


def test_create_server_logs_the_ceiling_and_auth(caplog: pytest.LogCaptureFixture) -> None:
    settings = Settings(
        database={"access_mode": "full", "write_mode": True, "table_prefix": "app_"},
        auth=AuthSettings(mode="static", tokens={_SECRET: {"client_id": "a"}}),
    )
    with caplog.at_level(logging.INFO):
        create_server(settings)
    [line] = [m for m in _messages(caplog, "postgres_fastmcp.app.server", "INFO") if "Database ceiling" in m]
    assert line == "Database ceiling: access_mode=full, write_mode=True, table_prefix=app_; auth=StaticTokenVerifier"
    assert _SECRET not in caplog.text


def test_create_server_logs_auth_none(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        create_server(Settings())
    [line] = [m for m in _messages(caplog, "postgres_fastmcp.app.server", "INFO") if "Database ceiling" in m]
    assert line == "Database ceiling: access_mode=basic, write_mode=False, table_prefix=None; auth=none"


def _run(tokens: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FastMCP, "run", lambda self, **kw: None)
    # configure_logging мокается: root_logger.handlers.clear() снял бы обработчик caplog
    with patch("postgres_fastmcp.app.main.configure_logging"):
        app(tokens=tokens, result_action="return_value")


def test_http_logs_the_listen_address(
    monkeypatch: pytest.MonkeyPatch, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MCP_SERVER_ENDPOINT", "/db")
    with caplog.at_level(logging.INFO):
        _run(["--transport", "http", "--host", "127.0.0.1", "--port", "9123"], monkeypatch)
    assert "Serving MCP over HTTP on 127.0.0.1:9123/db" in _messages(caplog, "postgres_fastmcp.app.main", "INFO")


def test_stdio_logs_no_listen_address_and_writes_nothing_to_stdout(
    monkeypatch: pytest.MonkeyPatch, tmp_path, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    with caplog.at_level(logging.INFO):
        _run(["--transport", "stdio"], monkeypatch)
    assert not any("Serving MCP" in m for m in caplog.messages)
    assert any("Database ceiling" in m for m in caplog.messages)
    assert capsys.readouterr().out == ""


def test_legacy_env_names_warn_once_each_without_values(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    for name in LEGACY_ENV_NAMES:
        monkeypatch.setenv(name, f"value-of-{name}")
    with caplog.at_level(logging.WARNING):
        warn_about_legacy_env()
    messages = _messages(caplog, "postgres_fastmcp.app.config", "WARNING")
    assert len(messages) == len(LEGACY_ENV_NAMES) == 11
    assert "Environment variable MCP_HOST is no longer read; use MCP_SERVER_HOST" in messages
    assert "Environment variable MCP_SERVER_NAME is no longer read; use MCP_FASTMCP_SERVER_NAME" in messages
    assert any(m.startswith("Environment variable MCP_WORKERS is no longer read;") and "removed" in m for m in messages)
    assert "value-of-" not in caplog.text


def test_no_legacy_warning_without_old_names(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    for name in LEGACY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    with caplog.at_level(logging.WARNING):
        warn_about_legacy_env()
    assert _messages(caplog, "postgres_fastmcp.app.config", "WARNING") == []


def test_cli_warns_about_legacy_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MCP_PORT", "9999")
    with caplog.at_level(logging.WARNING):
        _run(["--transport", "stdio"], monkeypatch)
    [message] = _messages(caplog, "postgres_fastmcp.app.config", "WARNING")
    assert message == "Environment variable MCP_PORT is no longer read; use MCP_SERVER_PORT"
