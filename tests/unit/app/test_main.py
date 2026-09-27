# mypy: ignore-errors
"""Unit tests for main entry point."""

from io import StringIO
from unittest.mock import patch

import pytest
from fastmcp import FastMCP
from fastmcp.server.auth.oidc_proxy import OIDCProxy
from rich.console import Console

from postgres_fastmcp.app.main import app


class TestMainVersion:
    """Tests for --version flag."""

    def test_version_prints_and_exits(self) -> None:
        buf = StringIO()
        with pytest.raises(SystemExit) as exc_info:
            app(tokens=["--version"], console=Console(file=buf))
        assert exc_info.value.code == 0
        out = buf.getvalue()
        assert "postgres-fastmcp" in out
        assert "version" in out.lower() or "0." in out


class TestMainTransportStdio:
    """Tests for main with transport=stdio (mocked to avoid starting server)."""

    def test_stdio_calls_configure_logging_with_disable(self) -> None:
        with patch("postgres_fastmcp.app.main.create_server") as mock_compose:
            with patch("postgres_fastmcp.app.main.configure_logging") as mock_configure:
                with patch("postgres_fastmcp.app.main.build_settings_from_cli") as mock_build:
                    mock_build.return_value = type(
                        "Settings",
                        (),
                        {
                            "server": type(
                                "Server",
                                (),
                                {"transport": "stdio", "host": "127.0.0.1", "port": 8000},
                            )(),
                        },
                    )()
                    mock_mcp = type("MCP", (), {"run": lambda self, **kw: None})()
                    mock_compose.return_value = mock_mcp
                    with pytest.raises(SystemExit):
                        app(tokens=["--transport", "stdio"])
                    # First call: configure_logging(level="INFO", ...), second: configure_logging(disable=True)
                    calls = mock_configure.call_args_list
                    disable_calls = [c for c in calls if c[1].get("disable") is True]
                    assert len(disable_calls) == 1


def test_stdio_builds_the_server_before_disabling_logs() -> None:
    """Предупреждения auth пишутся в create_server: логи в stdio отключаются только после него.

    build_auth=False здесь идёт по реальному транспорту запуска (actual_transport), а не по
    settings.server.transport из конфига — иначе settings.server.transport="stdio" с последующим
    mcp.run(transport="http") обслуживал бы HTTP вовсе без аутентификации.
    """
    calls: list[str] = []
    build_auth_seen: list[bool] = []
    settings = type(
        "Settings", (), {"server": type("Server", (), {"transport": "stdio", "host": "127.0.0.1", "port": 8000})()}
    )()
    mcp = type("MCP", (), {"run": lambda self, **kw: None})()

    def fake_create_server(_settings, *, build_auth=True, **_kwargs):
        calls.append("create_server")
        build_auth_seen.append(build_auth)
        return mcp

    def fake_configure_logging(**kwargs):
        calls.append("disable_logging" if kwargs.get("disable") else "configure_logging")

    with (
        patch("postgres_fastmcp.app.main.build_settings_from_cli", return_value=settings),
        patch("postgres_fastmcp.app.main.create_server", side_effect=fake_create_server),
        patch("postgres_fastmcp.app.main.configure_logging", side_effect=fake_configure_logging),
    ):
        app(tokens=["--transport", "stdio"], result_action="return_value")
    assert calls == ["configure_logging", "create_server", "disable_logging"]
    assert build_auth_seen == [False]


def test_http_passes_build_auth_true_to_create_server() -> None:
    settings = type(
        "Settings",
        (),
        {"server": type("Server", (), {"transport": "http", "host": "127.0.0.1", "port": 8000, "endpoint": "/mcp"})()},
    )()
    mcp = type("MCP", (), {"run": lambda self, **kw: None})()

    with (
        patch("postgres_fastmcp.app.main.build_settings_from_cli", return_value=settings),
        patch("postgres_fastmcp.app.main.create_server", return_value=mcp) as mock_create_server,
        patch("postgres_fastmcp.app.main.configure_logging"),
    ):
        app(tokens=["--transport", "http"], result_action="return_value")
    assert mock_create_server.call_args.kwargs["build_auth"] is True


def test_stdio_cli_skips_oidc_discovery_and_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Сквозь настоящий main(): --transport stdio с auth.mode=oidc не должен трогать сеть за
    недоступным IdP (create_server не мокается), но должен предупредить, что провайдер не построен.
    configure_logging мокается, иначе root_logger.handlers.clear() снял бы и обработчик caplog.
    """

    def discovery(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("OIDC discovery must not run for the stdio CLI")

    monkeypatch.setattr(OIDCProxy, "get_oidc_configuration", discovery)
    monkeypatch.setattr(FastMCP, "run", lambda self, **kw: None)
    monkeypatch.setenv("MCP_AUTH_MODE", "oidc")
    monkeypatch.setenv("MCP_AUTH_OIDC_CONFIG_URL", "https://sso.example.com/.well-known/openid-configuration")
    monkeypatch.setenv("MCP_AUTH_OIDC_CLIENT_ID", "postgres-mcp")
    monkeypatch.setenv("MCP_AUTH_OIDC_CLIENT_SECRET", "secret")
    monkeypatch.setenv("MCP_AUTH_BASE_URL", "https://mcp.example.com")
    with patch("postgres_fastmcp.app.main.configure_logging"):
        app(tokens=["--transport", "stdio"], result_action="return_value")
    messages = [
        r.getMessage() for r in caplog.records if r.name == "postgres_fastmcp.app.server" and r.levelname == "WARNING"
    ]
    assert any("build_auth=False" in m and "mode=oidc" in m for m in messages)


def test_pool_max_size_default_is_10() -> None:
    from pydantic import SecretStr

    from postgres_fastmcp.app.config.database import DatabaseConfig

    cfg = DatabaseConfig(
        host="localhost",
        port=5432,
        user="u",
        password=SecretStr("p"),
        name="db",
    )
    assert cfg.pool_max_size == 10


@pytest.mark.parametrize("value", [0, -1])
def test_safe_sql_timeout_must_be_positive(value: int) -> None:
    """A non-positive statement_timeout would disable or break the guard: rejected at config load."""
    from pydantic import SecretStr, ValidationError

    from postgres_fastmcp.app.config.database import DatabaseConfig

    with pytest.raises(ValidationError, match="safe_sql_timeout"):
        DatabaseConfig(
            host="localhost",
            port=5432,
            user="u",
            password=SecretStr("p"),
            name="db",
            safe_sql_timeout=value,
        )
