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
from postgres_fastmcp.shared.enums import AccessMode


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
