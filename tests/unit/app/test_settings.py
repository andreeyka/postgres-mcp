"""Тесты Settings: секреты не должны попадать в текст ошибок валидации вложенных блоков.

DatabaseConfig, ServerSettings и FastMCPSettings собираются через default_factory:
ошибка их собственного model_validator несёт `input_value` с их полями, и
`Settings.hide_input_in_errors` эту вложенную ошибку не прикрывает — нужен тот же
флаг на самой вложенной модели.
"""

import pytest
from pydantic import ValidationError

from postgres_fastmcp.app.config import Settings


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
