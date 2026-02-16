# mypy: ignore-errors
"""Unit tests for main entry point."""

from unittest.mock import patch

from postgres_fastmcp.main import main


class TestMainVersion:
    """Tests for --version flag."""

    def test_version_prints_and_exits(self) -> None:
        import click.testing

        runner = click.testing.CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "postgres-fastmcp" in result.output
        assert "version" in result.output.lower() or "0." in result.output

    def test_version_flag_calls_configure_logging(self) -> None:
        import click.testing

        with patch("postgres_fastmcp.main.configure_logging") as mock_configure:
            runner = click.testing.CliRunner()
            runner.invoke(main, ["--version"])
            mock_configure.assert_called_once()


class TestMainTransportStdio:
    """Tests for main with transport=stdio (mocked to avoid starting server)."""

    def test_stdio_calls_configure_logging_with_disable(self) -> None:
        import click.testing

        with patch("postgres_fastmcp.main.compose_mcp") as mock_compose:
            with patch("postgres_fastmcp.main.configure_logging") as mock_configure:
                with patch("postgres_fastmcp.main.build_settings_from_cli") as mock_build:
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
                    runner = click.testing.CliRunner()
                    runner.invoke(main, ["--transport", "stdio"])
                    # First call: configure_logging(level="INFO", ...), second: configure_logging(disable=True)
                    calls = mock_configure.call_args_list
                    disable_calls = [c for c in calls if c[1].get("disable") is True]
                    assert len(disable_calls) == 1
