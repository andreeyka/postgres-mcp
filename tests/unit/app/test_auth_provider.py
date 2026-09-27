"""Тесты build_auth_provider: тип и параметры провайдера FastMCP по режиму, без сети."""

from pathlib import Path

import fastmcp
import pytest
from fastmcp.server.auth.oidc_proxy import OIDCConfiguration, OIDCProxy
from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair, StaticTokenVerifier
from pydantic import AnyHttpUrl

from postgres_fastmcp.app.auth import build_auth_provider
from postgres_fastmcp.app.config.auth import AuthSettings


def test_none_mode_gives_no_provider() -> None:
    assert build_auth_provider(AuthSettings(mode="none")) is None


async def test_static_mode_spreads_claims_into_the_token() -> None:
    auth = AuthSettings(
        mode="static",
        required_scopes=["mcp"],
        tokens={
            "tok-alice": {
                "client_id": "alice",
                "scopes": ["mcp", "pg:write"],
                "claims": {"groups": ["dba"], "client_id": "ignored"},
            }
        },
    )
    provider = build_auth_provider(auth)
    assert isinstance(provider, StaticTokenVerifier)
    assert provider.required_scopes == ["mcp"]
    token = await provider.verify_token("tok-alice")
    assert token is not None
    assert token.client_id == "alice"
    assert token.scopes == ["mcp", "pg:write"]
    assert token.claims["groups"] == ["dba"]
    assert await provider.verify_token("unknown") is None


async def test_static_mode_without_required_scopes_accepts_any_listed_token() -> None:
    provider = build_auth_provider(AuthSettings(mode="static", tokens={"t": {"client_id": "c"}}))
    assert isinstance(provider, StaticTokenVerifier)
    assert provider.required_scopes == []
    assert await provider.verify_token("t") is not None


async def test_jwt_mode_with_public_key_verifies_tokens() -> None:
    keys = RSAKeyPair.generate()
    auth = AuthSettings(
        mode="jwt",
        jwt_public_key=keys.public_key,
        jwt_issuer="https://sso.example.com",
        jwt_audience="postgres-mcp",
        required_scopes=["mcp"],
    )
    provider = build_auth_provider(auth)
    assert isinstance(provider, JWTVerifier)
    assert provider.issuer == "https://sso.example.com"
    assert provider.audience == "postgres-mcp"
    assert provider.algorithm == "RS256"
    good = keys.create_token(
        issuer="https://sso.example.com",
        audience="postgres-mcp",
        scopes=["mcp", "pg:write"],
        additional_claims={"groups": ["dba"]},
    )
    token = await provider.verify_token(good)
    assert token is not None
    assert token.scopes == ["mcp", "pg:write"]
    assert token.claims["groups"] == ["dba"]
    wrong_audience = keys.create_token(issuer="https://sso.example.com", audience="other", scopes=["mcp"])
    assert await provider.verify_token(wrong_audience) is None


def test_jwt_mode_with_jwks_uri() -> None:
    auth = AuthSettings(
        mode="jwt",
        jwt_jwks_uri="https://sso.example.com/certs",
        jwt_issuer="https://sso.example.com",
        jwt_audience=["postgres-mcp", "other"],
        jwt_algorithm="RS512",
    )
    provider = build_auth_provider(auth)
    assert isinstance(provider, JWTVerifier)
    assert provider.jwks_uri == "https://sso.example.com/certs"
    assert provider.public_key is None
    assert provider.audience == ["postgres-mcp", "other"]
    assert provider.algorithm == "RS512"


def test_oidc_mode_builds_the_proxy_without_network(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """OIDCProxy читает config_url в конструкторе: discovery подменён, хранилище клиентов — во временной папке."""
    seen: list[str] = []

    def discovery(self: OIDCProxy, config_url: AnyHttpUrl, strict: bool | None, timeout_seconds: int | None):
        seen.append(str(config_url))
        return OIDCConfiguration(
            strict=False,
            issuer="https://sso.example.com",
            authorization_endpoint="https://sso.example.com/authorize",
            token_endpoint="https://sso.example.com/token",
            jwks_uri="https://sso.example.com/certs",
        )

    monkeypatch.setattr(OIDCProxy, "get_oidc_configuration", discovery)
    monkeypatch.setattr(fastmcp.settings, "home", tmp_path)
    auth = AuthSettings(
        mode="oidc",
        oidc_config_url="https://sso.example.com/.well-known/openid-configuration",
        oidc_client_id="postgres-mcp",
        oidc_client_secret="oidc-client-secret",
        oidc_audience="postgres-mcp",
        base_url="https://mcp.example.com",
        required_scopes=["openid"],
    )
    provider = build_auth_provider(auth)
    assert isinstance(provider, OIDCProxy)
    assert seen == ["https://sso.example.com/.well-known/openid-configuration"]
    assert str(provider.base_url) == "https://mcp.example.com/"
    assert provider.required_scopes == ["openid"]
    assert provider._upstream_client_id == "postgres-mcp"
    assert provider._upstream_client_secret.get_secret_value() == "oidc-client-secret"
    verifier = provider._token_validator
    assert isinstance(verifier, JWTVerifier)
    assert verifier.audience == "postgres-mcp"
    assert verifier.jwks_uri == "https://sso.example.com/certs"
