# mypy: ignore-errors
"""Unit tests for main entry point."""

from io import StringIO
from unittest.mock import patch

import pytest
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
