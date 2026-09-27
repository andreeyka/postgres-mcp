"""Тесты Settings: секреты не должны попадать в текст ошибок валидации вложенных блоков.

DatabaseConfig, ServerSettings и FastMCPSettings собираются через default_factory:
ошибка их собственного model_validator несёт `input_value` с их полями, и
`Settings.hide_input_in_errors` эту вложенную ошибку не прикрывает — нужен тот же
флаг на самой вложенной модели.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from postgres_fastmcp.app.config import Settings, build_settings_from_cli
from postgres_fastmcp.app.config.database import DatabaseConfig, DatabaseSettings
from postgres_fastmcp.app.config.fastmcp import FastMCPSettings
from postgres_fastmcp.app.config.server import ServerSettings
from postgres_fastmcp.shared.enums import AccessMode, TransportConfig


_SECRET_PASSWORD = "S3CRETPW"


@pytest.fixture(autouse=True)
def _clean_database_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тестовое окружение задаёт полный набор MCP_DATABASE_*: тест сам решает, что оставить."""
    monkeypatch.delenv("MCP_DATABASE_NAME", raising=False)


def test_database_config_error_hides_the_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings() с неполным DatabaseConfig не должен печатать пароль в тексте ошибки."""
    monkeypatch.setenv("MCP_DATABASE_HOST", "localhost")
    monkeypatch.setenv("MCP_DATABASE_USER", "u")
    monkeypatch.setenv("MCP_DATABASE_PASSWORD", _SECRET_PASSWORD)
    with pytest.raises(ValidationError) as exc_info:
        Settings()
    assert _SECRET_PASSWORD not in str(exc_info.value)
    assert _SECRET_PASSWORD not in repr(exc_info.value)


@pytest.mark.parametrize(
    ("name", "value"),
    [("AUTH", "basic"), ("DATABASE", '{"host": "evil"}'), ("SERVER", '{"port": 1}'), ("FASTMCP", "x")],
)
def test_unprefixed_block_env_is_ignored(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    """Переменная с именем блока без префикса MCP_ не должна подменять блок целиком или ронять старт."""
    monkeypatch.setenv("MCP_DATABASE_NAME", "d")
    monkeypatch.setenv(name, value)
    settings = Settings()
    assert settings.database.host == "localhost"
    assert settings.server.port == 8000


def test_nested_blocks_still_read_their_own_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Каждый блок читает свой префикс; словарь (config.json) дополняется из env."""
    monkeypatch.setenv("MCP_DATABASE_NAME", "d")
    monkeypatch.setenv("MCP_DATABASE_ACCESS_MODE", "full")
    monkeypatch.setenv("MCP_AUTH_ACCESS_POLICY__ENFORCED", "true")
    settings = Settings(database={"table_prefix": "app_"})
    assert settings.database.access_mode == "full"
    assert settings.database.table_prefix == "app_"
    assert settings.auth.access_policy.enforced is True


_CONNECTION = {"host": "h", "user": "u", "password": "p", "name": "n"}


def test_database_config_ignores_env_and_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Библиотечный DatabaseConfig берёт только переданное: ни env, ни .env не поднимают потолок прав."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("MCP_DATABASE_WRITE_MODE=true\n", encoding="utf-8")
    monkeypatch.setenv("MCP_DATABASE_ACCESS_MODE", "full")
    config = DatabaseConfig(**_CONNECTION)
    assert config.access_mode == AccessMode.BASIC
    assert config.write_mode is False
    assert DatabaseConfig.from_uri("postgresql://u:p@h/n").access_mode == AccessMode.BASIC


def test_database_settings_reads_env_and_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("MCP_DATABASE_WRITE_MODE=true\n", encoding="utf-8")
    monkeypatch.setenv("MCP_DATABASE_NAME", "d")
    monkeypatch.setenv("MCP_DATABASE_ACCESS_MODE", "full")
    config = DatabaseSettings()
    assert config.access_mode == AccessMode.FULL
    assert config.write_mode is True
    assert isinstance(config, DatabaseConfig)


def test_database_config_rejects_unknown_fields() -> None:
    """Опечатка или устаревшее поле (role) в коде библиотеки — ошибка, а не молчаливый пропуск."""
    with pytest.raises(ValidationError, match="role") as exc_info:
        DatabaseConfig(**{**_CONNECTION, "password": _SECRET_PASSWORD}, role="admin")
    assert _SECRET_PASSWORD not in str(exc_info.value)
    assert _SECRET_PASSWORD not in repr(exc_info.value)


def test_settings_keeps_a_passed_database_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_DATABASE_NAME", "d")
    monkeypatch.setenv("MCP_DATABASE_ACCESS_MODE", "full")
    config = DatabaseConfig(**_CONNECTION)
    settings = Settings(database=config)
    assert settings.database is config
    assert settings.database.access_mode == AccessMode.BASIC


def test_settings_database_dict_error_hides_the_password() -> None:
    """Неполный словарь database (config.json) даёт ошибку без пароля в тексте."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(database={"host": "h", "password": _SECRET_PASSWORD})
    assert _SECRET_PASSWORD not in str(exc_info.value)
    assert _SECRET_PASSWORD not in repr(exc_info.value)


def test_config_json_database_rejects_unknown_key_without_leaking_password(tmp_path: Path) -> None:
    """Устаревший ключ role в config.json — ошибка с именем ключа, но без значений и пароля."""
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"database": {**_CONNECTION, "password": _SECRET_PASSWORD, "role": "admin"}}),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="role") as exc_info:
        build_settings_from_cli(config_path=config_path)
    assert _SECRET_PASSWORD not in str(exc_info.value)
    assert _SECRET_PASSWORD not in repr(exc_info.value)
    assert "admin" not in str(exc_info.value)


def test_settings_database_dict_rejects_a_typo() -> None:
    """Опечатка table_prefx не должна молча снимать ограничение по префиксу в basic."""
    with pytest.raises(ValidationError, match="table_prefx"):
        Settings(database={"table_prefx": "app_"})


def test_settings_database_dict_with_known_keys_still_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_DATABASE_NAME", "d")
    settings = Settings(database={"table_prefix": "app_", "access_mode": "full"})
    assert settings.database.table_prefix == "app_"
    assert settings.database.access_mode == AccessMode.FULL
    assert settings.database.host == "localhost"


def test_cli_database_uri_still_reads_database_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--database-uri задаёт только подключение; остальное (префикс, таймаут, sslmode, права) — из env.

    Незаданные --access-mode / --write-mode ничего не переопределяют: права берутся из env.
    """
    monkeypatch.setenv("MCP_DATABASE_TABLE_PREFIX", "app_")
    monkeypatch.setenv("MCP_DATABASE_SAFE_SQL_TIMEOUT", "99")
    monkeypatch.setenv("MCP_DATABASE_SSLMODE", "require")
    monkeypatch.setenv("MCP_DATABASE_ACCESS_MODE", "full")
    monkeypatch.setenv("MCP_DATABASE_WRITE_MODE", "true")
    settings = build_settings_from_cli(
        database_uri="postgresql://cli:pw@cli-host:6543/cli_db", config_path=tmp_path / "missing.json"
    )
    database = settings.database
    assert (database.host, database.port, database.user, database.name) == ("cli-host", 6543, "cli", "cli_db")
    assert database.table_prefix == "app_"
    assert database.safe_sql_timeout == 99
    assert database.sslmode == "require"
    assert database.access_mode == AccessMode.FULL
    assert database.write_mode is True


def test_cli_database_uri_sslmode_beats_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MCP_DATABASE_SSLMODE", "require")
    settings = build_settings_from_cli(
        database_uri="postgresql://u:p@h/n?sslmode=disable", config_path=tmp_path / "missing.json"
    )
    assert settings.database.sslmode == "disable"


_TRICKY_CREDENTIALS = [
    ("user name", "pa ss"),
    ("a+b", "p+w"),
    ("me@corp", "p@ss:w/rd"),
    ("u%41", "100%"),
    ("пользователь", "пароль€"),
    ("u", "a b+c@d/e%f:g?h#i&j=k"),
]


@pytest.mark.parametrize(("user", "password"), _TRICKY_CREDENTIALS)
def test_database_uri_round_trips_credentials(user: str, password: str) -> None:
    """database_uri -> from_uri возвращает те же user и password, что были в конфиге."""
    config = DatabaseConfig(host="h", user=user, password=password, name="d")
    restored = DatabaseConfig.from_uri(config.database_uri)
    assert (restored.user, restored.password.get_secret_value()) == (user, password)


@pytest.mark.parametrize(("user", "password"), _TRICKY_CREDENTIALS)
def test_libpq_reads_the_raw_credentials_from_database_uri(user: str, password: str) -> None:
    """Libpq не декодирует '+' как пробел: креды кодируются percent-encoding, а не quote_plus."""
    from psycopg.conninfo import conninfo_to_dict

    params = conninfo_to_dict(DatabaseConfig(host="h", user=user, password=password, name="d").database_uri)
    assert (params["user"], params["password"]) == (user, password)


def test_server_block_reads_the_mcp_server_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Префикс блока = MCP_<СЕКЦИЯ>_, как у MCP_DATABASE_ и MCP_AUTH_; старые MCP_PORT и т.п. не читаются."""
    monkeypatch.setenv("MCP_SERVER_PORT", "9100")
    monkeypatch.setenv("MCP_SERVER_TRANSPORT", "stdio")
    monkeypatch.setenv("MCP_SERVER_RESPONSE_MAX_TOKENS", "5000")
    monkeypatch.setenv("MCP_HOST", "0.0.0.0")
    server = ServerSettings()
    assert (server.port, server.transport, server.response_max_tokens) == (9100, TransportConfig.STDIO, 5000)
    assert server.host == "127.0.0.1"


def test_fastmcp_block_reads_the_mcp_fastmcp_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_FASTMCP_SERVER_NAME", "orders-db")
    monkeypatch.setenv("MCP_SERVER_NAME", "ignored")
    assert FastMCPSettings().server_name == "orders-db"


def test_dead_fields_are_gone() -> None:
    assert "workers" not in ServerSettings.model_fields
    assert {"return_errors_as_strings", "error_traceback_in_strings"}.isdisjoint(FastMCPSettings.model_fields)


def test_default_instructions_are_english() -> None:
    """Инструкции сервера видит агент: только английский текст."""
    assert FastMCPSettings().instructions.isascii()


def test_uri_with_unknown_sslmode_fails() -> None:
    """Опечатка sslmode=requre не должна молча давать libpq-умолчание prefer."""
    with pytest.raises(ValueError, match="requre") as exc_info:
        DatabaseConfig.from_uri("postgresql://u:pw@h/d?sslmode=requre")
    assert "verify-full" in str(exc_info.value)


def test_cli_database_uri_with_unknown_sslmode_fails(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requre"):
        build_settings_from_cli(database_uri="postgresql://u:pw@h/d?sslmode=requre", config_path=tmp_path / "x.json")
