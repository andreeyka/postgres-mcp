# mypy: ignore-errors
"""CLI -> Settings: каждый явно заданный флаг действует, незаданный не затирает config.json и env."""

import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from rich.console import Console

from postgres_fastmcp.app.config import build_settings_from_cli
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.app.config.server import ServerSettings
from postgres_fastmcp.app.main import app
from postgres_fastmcp.shared.enums import AccessMode, TransportConfig


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    """config.json с непустыми server и database: CLI должен дополнять секции, а не заменять."""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "server": {"host": "0.0.0.0", "port": 9200},
                "database": {"table_prefix": "app_", "access_mode": "full"},
            }
        ),
        encoding="utf-8",
    )
    return path


def _run_cli(tokens: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[object, dict]:
    """Прогнать настоящий main() без сервера: вернуть Settings и аргументы mcp.run."""
    monkeypatch.chdir(tmp_path)
    seen: dict = {}

    def fake_create_server(settings, **_kwargs):
        seen["settings"] = settings
        return type("MCP", (), {"run": lambda self, **kw: seen.update(run=kw)})()

    with (
        patch("postgres_fastmcp.app.main.create_server", side_effect=fake_create_server),
        patch("postgres_fastmcp.app.main.configure_logging"),
    ):
        app(tokens=tokens, result_action="return_value")
    return seen["settings"], seen["run"]


@pytest.mark.parametrize(
    ("tokens", "field", "expected"),
    [
        (["--port", "9001"], ("server", "port"), 9001),
        (["--host", "0.0.0.0"], ("server", "host"), "0.0.0.0"),
        (["--transport", "stdio"], ("server", "transport"), TransportConfig.STDIO),
        (["--access-mode", "full"], ("database", "access_mode"), AccessMode.FULL),
        (["--write-mode"], ("database", "write_mode"), True),
    ],
)
def test_each_flag_applies_without_database_uri(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tokens: list[str], field: tuple[str, str], expected: object
) -> None:
    settings, _ = _run_cli(tokens, monkeypatch, tmp_path)
    section, name = field
    assert getattr(getattr(settings, section), name) == expected


def test_port_and_host_reach_mcp_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _, run = _run_cli(["--host", "0.0.0.0", "--port", "9001"], monkeypatch, tmp_path)
    assert run["host"] == "0.0.0.0"
    assert run["port"] == 9001


def test_no_write_mode_overrides_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MCP_DATABASE_WRITE_MODE", "true")
    settings, _ = _run_cli(["--no-write-mode"], monkeypatch, tmp_path)
    assert settings.database.write_mode is False


def test_unset_flags_keep_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Значения по умолчанию CLI не затирают env: без флагов access_mode из MCP_DATABASE_ACCESS_MODE."""
    monkeypatch.setenv("MCP_DATABASE_ACCESS_MODE", "full")
    settings, _ = _run_cli(["--database-uri", "postgresql://a:b@db:5433/app"], monkeypatch, tmp_path)
    assert settings.database.access_mode == AccessMode.FULL
    assert (settings.database.host, settings.database.port, settings.database.name) == ("db", 5433, "app")


def test_workers_option_is_gone() -> None:
    """create_server подменён: пока --workers существовал, этот вызов поднимал бы настоящий сервер."""
    buf = StringIO()
    with (
        patch("postgres_fastmcp.app.main.create_server") as create_server,
        patch("postgres_fastmcp.app.main.configure_logging"),
        pytest.raises(SystemExit),
    ):
        app(tokens=["--workers", "2"], console=Console(file=buf), error_console=Console(file=buf))
    create_server.assert_not_called()
    assert "Unknown option: --workers" in buf.getvalue()


def test_cli_override_merges_into_config_sections(config_path: Path) -> None:
    """--transport не выбрасывает host/port из config.json; --database-uri не выбрасывает table_prefix."""
    settings = build_settings_from_cli(
        transport="stdio", database_uri="postgresql://a:b@db/app", config_path=config_path
    )
    assert (settings.server.host, settings.server.port) == ("0.0.0.0", 9200)
    assert settings.server.transport == TransportConfig.STDIO
    assert settings.database.table_prefix == "app_"
    assert settings.database.access_mode == AccessMode.FULL
    assert settings.database.host == "db"


def test_explicit_flag_beats_config_file(config_path: Path) -> None:
    settings = build_settings_from_cli(port=9001, access_mode=AccessMode.BASIC, config_path=config_path)
    assert settings.server.port == 9001
    assert settings.server.host == "0.0.0.0"
    assert settings.database.access_mode == AccessMode.BASIC


def test_database_uri_password_stays_secret(config_path: Path) -> None:
    settings = build_settings_from_cli(database_uri="postgresql://a:S3CRETPW@db/app", config_path=config_path)
    assert settings.database.password is not None
    assert settings.database.password.get_secret_value() == "S3CRETPW"
    assert "S3CRETPW" not in repr(settings)


def test_database_uri_without_password_takes_it_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Пароль можно не передавать в командной строке (виден в ps): его дополняет MCP_DATABASE_PASSWORD."""
    monkeypatch.setenv("MCP_DATABASE_PASSWORD", "from-env")
    settings, _ = _run_cli(["--database-uri", "postgresql://a@db/app"], monkeypatch, tmp_path)
    assert settings.database.user == "a"
    assert settings.database.password.get_secret_value() == "from-env"


def test_precedence_uri_over_config_json_over_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Поля URI > config.json > env, а явный флаг CLI > всего: --database-uri не выбрасывает секцию database."""
    monkeypatch.setenv("MCP_DATABASE_TABLE_PREFIX", "env_")
    monkeypatch.setenv("MCP_DATABASE_SAFE_SQL_TIMEOUT", "77")
    monkeypatch.setenv("MCP_DATABASE_ACCESS_MODE", "full")
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "database": {
                    "host": "cfg-host",
                    "port": 6000,
                    "user": "cfg-user",
                    "password": "cfg-pw",
                    "name": "cfg_db",
                    "table_prefix": "cfg_",
                    "access_mode": "basic",
                }
            }
        ),
        encoding="utf-8",
    )
    settings = build_settings_from_cli(
        database_uri="postgresql://uri-user@uri-host/uri_db", access_mode=AccessMode.FULL, config_path=path
    )
    database = settings.database
    # URI задаёт то, что в нём есть (порт не указан — 5432), и перекрывает config.json
    assert (database.host, database.port, database.user, database.name) == ("uri-host", 5432, "uri-user", "uri_db")
    # пароля в URI нет — он из config.json, а не из env
    assert database.password.get_secret_value() == "cfg-pw"
    # config.json перекрывает env, env дополняет отсутствующее в config.json
    assert database.table_prefix == "cfg_"
    assert database.safe_sql_timeout == 77
    # явный флаг перекрывает config.json
    assert database.access_mode == AccessMode.FULL


def test_from_uri_accepts_a_connection_field_override() -> None:
    """Переопределение поля, которое есть в URI, не даёт TypeError о повторном аргументе."""
    config = DatabaseConfig.from_uri("postgresql://u:p@h:5433/n?sslmode=require", host="other", sslmode="disable")
    assert (config.host, config.port, config.sslmode) == ("other", 5433, "disable")


@pytest.mark.parametrize(
    ("configured", "expected"), [("mcp", "/mcp"), ("/api/mcp", "/api/mcp"), (" api/v1 ", "/api/v1")]
)
def test_endpoint_gets_a_leading_slash(configured: str, expected: str) -> None:
    """Starlette требует путь с '/': значение 'mcp' из старых конфигов не должно ронять HTTP-старт."""
    assert ServerSettings(endpoint=configured).endpoint == expected


@pytest.mark.parametrize("configured", ["health", "/health", "/health/", " health/ ", "//health"])
def test_endpoint_cannot_shadow_the_health_route(configured: str) -> None:
    """MCP endpoint на /health перекрыл бы проверку состояния (или она — MCP): такой конфиг — ошибка."""
    with pytest.raises(ValidationError, match=r"server\.endpoint must not be /health"):
        ServerSettings(endpoint=configured)


@pytest.mark.parametrize("configured", ["/healthz", "/api/health", "/health/mcp"])
def test_endpoint_may_contain_health_elsewhere(configured: str) -> None:
    assert ServerSettings(endpoint=configured).endpoint == configured


def test_http_run_uses_the_configured_endpoint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"server": {"endpoint": "api/mcp"}}), encoding="utf-8")
    _, run = _run_cli(["--transport", "http"], monkeypatch, tmp_path)
    assert run["path"] == "/api/mcp"


def test_stdio_run_takes_no_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _, run = _run_cli(["--transport", "stdio"], monkeypatch, tmp_path)
    assert run == {"transport": "stdio"}


def test_docker_config_loads() -> None:
    """docker/config.json — рабочий пример: неизвестный ключ верхнего уровня (например _comment) ронял бы старт."""
    path = Path(__file__).resolve().parents[3] / "docker" / "config.json"
    settings = build_settings_from_cli(config_path=path)
    assert (settings.server.host, settings.server.endpoint) == ("0.0.0.0", "/mcp")
    assert (settings.database.host, settings.database.table_prefix) == ("postgres", "app_")
