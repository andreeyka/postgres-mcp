"""Неизвестные ключи секций auth/server/fastmcp и AccessPolicy — ошибка, а не молчаливый default."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from postgres_fastmcp.app.config import Settings, build_settings_from_cli


_TOKEN_STRING = "tok-SUPER-SECRET-123"
_JWT_KEY = "jwt-KEY-SECRET-456"

_UNKNOWN_KEY_CASES = [
    ({"auth": {"mdoe": "static", "tokens": {_TOKEN_STRING: {"client_id": "a"}}}}, "mdoe"),
    ({"auth": {"acess_policy": {"enforced": True}, "jwt_public_key": _JWT_KEY}}, "acess_policy"),
    ({"auth": {"access_policy": {"enforcd": True}, "jwt_public_key": _JWT_KEY}}, "enforcd"),
    ({"server": {"workers": 4}}, "workers"),
    ({"server": {"hots": "0.0.0.0"}}, "hots"),
    ({"fastmcp": {"return_errors_as_strings": True}}, "return_errors_as_strings"),
]


@pytest.mark.parametrize(("sections", "key"), _UNKNOWN_KEY_CASES)
def test_settings_rejects_unknown_block_keys(sections: dict, key: str) -> None:
    """Опечатка в auth/server/fastmcp не должна молча давать значение по умолчанию (mode=none, enforced=False)."""
    with pytest.raises(ValidationError, match=key) as exc_info:
        Settings(**sections)
    for secret in (_TOKEN_STRING, _JWT_KEY):
        assert secret not in str(exc_info.value)
        assert secret not in repr(exc_info.value)


@pytest.mark.parametrize(("sections", "key"), _UNKNOWN_KEY_CASES)
def test_config_json_rejects_unknown_block_keys(tmp_path: Path, sections: dict, key: str) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(sections), encoding="utf-8")
    with pytest.raises(ValidationError, match=key) as exc_info:
        build_settings_from_cli(config_path=config_path)
    for secret in (_TOKEN_STRING, _JWT_KEY):
        assert secret not in str(exc_info.value)
        assert secret not in repr(exc_info.value)


def test_access_policy_env_typo_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """MCP_AUTH_ACCESS_POLICY__ENFORCD не должен молча оставлять enforced=False."""
    monkeypatch.setenv("MCP_AUTH_ACCESS_POLICY__ENFORCD", "true")
    with pytest.raises(ValidationError, match="enforcd"):
        Settings()


def test_valid_blocks_still_load(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server": {"host": "0.0.0.0", "port": 9000, "health_endpoint_enabled": False},
                "fastmcp": {"server_name": "X"},
                "auth": {
                    "mode": "static",
                    "access_policy": {"enforced": True, "claim": "groups"},
                    "tokens": {_TOKEN_STRING: {"client_id": "a"}},
                },
            }
        ),
        encoding="utf-8",
    )
    settings = build_settings_from_cli(config_path=config_path)
    assert (settings.server.host, settings.server.port) == ("0.0.0.0", 9000)
    assert settings.fastmcp.server_name == "X"
    assert settings.auth.access_policy.enforced is True
    assert _TOKEN_STRING in settings.auth.tokens


def test_block_dicts_still_merge_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Словарь секции дополняется env этого блока: проверка ключей не отключает env."""
    monkeypatch.setenv("MCP_SERVER_PORT", "9100")
    monkeypatch.setenv("MCP_FASTMCP_SERVER_NAME", "from-env")
    monkeypatch.setenv("MCP_AUTH_ACCESS_POLICY__CLAIM", "groups")
    settings = Settings(server={"host": "0.0.0.0"}, fastmcp={}, auth={"mode": "none"})
    assert (settings.server.host, settings.server.port) == ("0.0.0.0", 9100)
    assert settings.fastmcp.server_name == "from-env"
    assert settings.auth.access_policy.claim == "groups"
