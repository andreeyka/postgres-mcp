"""Тесты AuthSettings: режимы, валидация, env и config.json, секреты вне ошибок."""

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from postgres_fastmcp.access import AccessPolicy
from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.config.auth import AuthSettings, StaticToken
from postgres_fastmcp.shared.enums import AuthMode


_SECRET = "s3cr3t-token-value"


@pytest.fixture(autouse=True)
def _clean_auth_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Никаких MCP_AUTH_* из окружения разработчика и никакого .env из текущей директории."""
    for key in list(os.environ):
        if key.startswith("MCP_AUTH_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)


def test_default_mode_is_none_with_default_policy() -> None:
    auth = AuthSettings()
    assert auth.mode == AuthMode.NONE
    assert auth.required_scopes == []
    assert auth.access_policy == AccessPolicy()
    assert auth.tokens == {}


def test_settings_has_auth_block() -> None:
    assert Settings().auth.mode == AuthMode.NONE


def test_static_tokens_inline() -> None:
    auth = AuthSettings(
        mode="static",
        tokens={_SECRET: {"client_id": "alice", "scopes": ["pg:write"], "claims": {"groups": ["dba"]}}},
    )
    assert auth.tokens == {_SECRET: StaticToken(client_id="alice", scopes=["pg:write"], claims={"groups": ["dba"]})}


def test_static_without_tokens_is_rejected() -> None:
    with pytest.raises(ValidationError, match="auth.mode=static requires tokens or tokens_file"):
        AuthSettings(mode="static")


def test_static_tokens_file_is_merged_and_inline_wins(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps({"from-file": {"client_id": "bob"}, _SECRET: {"client_id": "file"}}), encoding="utf-8")
    auth = AuthSettings(mode="static", tokens_file=path, tokens={_SECRET: {"client_id": "inline"}})
    assert {token: entry.client_id for token, entry in auth.tokens.items()} == {"from-file": "bob", _SECRET: "inline"}


def test_static_tokens_file_alone_is_enough(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps({_SECRET: {"client_id": "alice"}}), encoding="utf-8")
    assert AuthSettings(mode="static", tokens_file=path).tokens[_SECRET].client_id == "alice"


@pytest.mark.parametrize(
    ("content", "error"),
    [
        (None, "cannot be read"),
        ("{not json", "is not valid JSON"),
        (json.dumps({_SECRET: {"scopes": []}}), r"client_id: Field required"),
    ],
)
def test_bad_tokens_file_is_rejected_without_token_values(tmp_path: Path, content: str | None, error: str) -> None:
    path = tmp_path / "tokens.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    with pytest.raises(ValidationError, match=error) as exc_info:
        AuthSettings(mode="static", tokens_file=path)
    assert _SECRET not in str(exc_info.value)


def test_tokens_file_is_ignored_outside_static(tmp_path: Path) -> None:
    """Поля чужого режима игнорируются: даже битый путь не мешает mode=none."""
    assert AuthSettings(tokens_file=tmp_path / "missing.json").mode == AuthMode.NONE


def test_bad_inline_token_hides_the_token_string() -> None:
    with pytest.raises(ValidationError, match=r"tokens must map each token.*client_id: Field required") as exc_info:
        AuthSettings(mode="static", tokens={_SECRET: {"scopes": ["x"]}})
    assert _SECRET not in str(exc_info.value)


def test_tokens_are_not_in_repr() -> None:
    auth = AuthSettings(mode="static", tokens={_SECRET: {"client_id": "alice"}})
    assert _SECRET not in repr(auth)


def test_tokens_from_env_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_AUTH_MODE", "static")
    monkeypatch.setenv("MCP_AUTH_TOKENS", json.dumps({_SECRET: {"client_id": "alice", "claims": {"groups": ["dba"]}}}))
    monkeypatch.setenv("MCP_AUTH_REQUIRED_SCOPES", '["mcp"]')
    auth = AuthSettings()
    assert auth.tokens[_SECRET].claims == {"groups": ["dba"]}
    assert auth.required_scopes == ["mcp"]


def test_access_policy_from_nested_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_AUTH_ACCESS_POLICY__ENFORCED", "true")
    monkeypatch.setenv("MCP_AUTH_ACCESS_POLICY__CLAIM", "groups")
    monkeypatch.setenv("MCP_AUTH_ACCESS_POLICY__WRITE_VALUES", '["dba","backend-writers"]')
    monkeypatch.setenv("MCP_AUTH_ACCESS_POLICY__FULL_VALUES", '["dba"]')
    assert AuthSettings().access_policy == AccessPolicy(
        enforced=True, claim="groups", write_values=["dba", "backend-writers"], full_values=["dba"]
    )


def test_jwt_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_AUTH_MODE", "jwt")
    monkeypatch.setenv("MCP_AUTH_JWT_JWKS_URI", "https://sso.example.com/certs")
    monkeypatch.setenv("MCP_AUTH_JWT_ISSUER", "https://sso.example.com")
    monkeypatch.setenv("MCP_AUTH_JWT_AUDIENCE", "postgres-mcp")
    auth = AuthSettings()
    assert auth.jwt_audience == "postgres-mcp"
    monkeypatch.setenv("MCP_AUTH_JWT_AUDIENCE", '["a","b"]')
    assert AuthSettings().jwt_audience == ["a", "b"]


_JWT = {"jwt_issuer": "https://sso.example.com", "jwt_audience": "postgres-mcp"}


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        (_JWT, "exactly one of jwt_jwks_uri and jwt_public_key"),
        ({**_JWT, "jwt_jwks_uri": "https://x/certs", "jwt_public_key": "PEM"}, "exactly one of"),
        ({"jwt_jwks_uri": "https://x/certs", "jwt_audience": "a"}, "requires jwt_issuer"),
        ({"jwt_jwks_uri": "https://x/certs", "jwt_issuer": "i"}, "requires jwt_audience"),
    ],
)
def test_invalid_jwt_settings(fields: dict[str, str], error: str) -> None:
    with pytest.raises(ValidationError, match=error):
        AuthSettings(mode="jwt", **fields)


@pytest.mark.parametrize("key", ["jwt_jwks_uri", "jwt_public_key"])
def test_valid_jwt_settings(key: str) -> None:
    assert AuthSettings(mode="jwt", **_JWT, **{key: "value"}).mode == AuthMode.JWT


_OIDC = {
    "oidc_config_url": "https://sso.example.com/.well-known/openid-configuration",
    "oidc_client_id": "postgres-mcp",
    "oidc_client_secret": "oidc-client-secret",
    "base_url": "https://mcp.example.com",
}


def test_valid_oidc_settings() -> None:
    auth = AuthSettings(mode="oidc", **_OIDC)
    assert auth.oidc_client_secret is not None
    assert auth.oidc_client_secret.get_secret_value() == "oidc-client-secret"
    assert "oidc-client-secret" not in repr(auth)


@pytest.mark.parametrize("missing", sorted(_OIDC))
def test_oidc_requires_four_fields(missing: str) -> None:
    fields = {key: value for key, value in _OIDC.items() if key != missing}
    with pytest.raises(ValidationError, match=f"auth.mode=oidc requires {missing}"):
        AuthSettings(mode="oidc", **fields)


def test_unknown_mode_is_rejected() -> None:
    with pytest.raises(ValidationError, match="mode"):
        AuthSettings(mode="basic")


def test_settings_from_config_json_section() -> None:
    settings = Settings(
        auth={
            "mode": "static",
            "access_policy": {"enforced": True, "claim": "groups", "full_values": ["dba"], "write_values": ["dba"]},
            "tokens": {_SECRET: {"client_id": "alice", "claims": {"groups": ["dba"]}}},
        }
    )
    assert settings.auth.mode == AuthMode.STATIC
    assert settings.auth.access_policy.claim == "groups"
    assert settings.auth.tokens[_SECRET].claims == {"groups": ["dba"]}


def test_settings_error_hides_secrets_of_the_auth_block() -> None:
    """Ошибка вложенного блока не печатает его ввод: client_secret и токены остаются скрыты."""
    with pytest.raises(ValidationError, match="auth.mode=oidc requires base_url") as exc_info:
        Settings(auth={**{k: v for k, v in _OIDC.items() if k != "base_url"}, "mode": "oidc"})
    assert "oidc-client-secret" not in str(exc_info.value)


def test_malformed_inline_tokens_are_ignored_in_none_mode() -> None:
    """Форма tokens проверяется только в static: в none поле чужого режима игнорируется."""
    auth = AuthSettings(tokens={_SECRET: {"scopes": ["x"]}})
    assert auth.mode == AuthMode.NONE


def test_malformed_inline_tokens_are_ignored_in_jwt_mode() -> None:
    auth = AuthSettings(mode="jwt", tokens={_SECRET: {"scopes": ["x"]}}, **_JWT, jwt_jwks_uri="https://x/certs")
    assert auth.mode == AuthMode.JWT


def test_malformed_env_tokens_are_ignored_outside_static(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKENS", json.dumps({_SECRET: {"scopes": ["x"]}}))
    assert AuthSettings().mode == AuthMode.NONE


def test_malformed_tokens_still_fail_in_static_mode_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_AUTH_MODE", "static")
    monkeypatch.setenv("MCP_AUTH_TOKENS", json.dumps({_SECRET: {"scopes": ["x"]}}))
    with pytest.raises(ValidationError, match=r"tokens must map each token.*client_id: Field required") as exc_info:
        AuthSettings()
    assert _SECRET not in str(exc_info.value)


def test_tokens_are_excluded_from_model_dump() -> None:
    auth = AuthSettings(mode="static", tokens={_SECRET: {"client_id": "alice"}})
    dump = auth.model_dump()
    assert "tokens" not in dump
    assert _SECRET not in json.dumps(dump, default=str)
    assert _SECRET not in auth.model_dump_json()


def test_tokens_file_bad_encoding_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    path.write_bytes(b"\xff\xfe\x00\x01")
    with pytest.raises(ValidationError, match="cannot be read") as exc_info:
        AuthSettings(mode="static", tokens_file=path)
    assert _SECRET not in str(exc_info.value)


def test_blank_token_key_is_rejected() -> None:
    with pytest.raises(ValidationError, match="tokens must not contain a blank token string") as exc_info:
        AuthSettings(mode="static", tokens={"   ": {"client_id": "alice"}})
    assert _SECRET not in str(exc_info.value)


def test_blank_token_key_in_tokens_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps({"": {"client_id": "alice"}}), encoding="utf-8")
    with pytest.raises(ValidationError, match="tokens_file .* must not contain a blank token string"):
        AuthSettings(mode="static", tokens_file=path)
