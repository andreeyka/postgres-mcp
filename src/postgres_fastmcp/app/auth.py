"""Фабрика auth-провайдера FastMCP по AuthSettings."""

from typing import Any

from fastmcp.server.auth import AuthProvider
from fastmcp.server.auth.oidc_proxy import OIDCProxy
from fastmcp.server.auth.providers.jwt import JWTVerifier, StaticTokenVerifier

from postgres_fastmcp.app.config.auth import AuthSettings, StaticToken
from postgres_fastmcp.shared.enums import AuthMode


def _static_token_data(token: StaticToken) -> dict[str, Any]:
    # StaticTokenVerifier кладёт весь словарь в AccessToken.claims: claims разворачиваются рядом
    # с client_id и scopes, а client_id/scopes из описания токена важнее одноимённых claims
    return {**token.claims, "client_id": token.client_id, "scopes": token.scopes}


def _required[T](value: T | None, name: str, mode: AuthMode) -> T:
    # AuthSettings уже проверил поля; повтор сужает тип для mypy (OIDCProxy не принимает None)
    if value is None:
        msg = f"auth.mode={mode} requires {name}"
        raise ValueError(msg)
    return value


def build_auth_provider(auth: AuthSettings) -> AuthProvider | None:
    """Собрать auth-провайдер FastMCP для режима из настроек.

    Args:
        auth: Настройки аутентификации.

    Returns:
        None для mode=none, иначе StaticTokenVerifier, JWTVerifier или OIDCProxy.

    Raises:
        ValueError: Если обязательное поле режима не задано.
    """
    required_scopes = auth.required_scopes or None
    if auth.mode == AuthMode.STATIC:
        return StaticTokenVerifier(
            tokens={token: _static_token_data(entry) for token, entry in auth.tokens.items()},
            required_scopes=required_scopes,
        )
    if auth.mode == AuthMode.JWT:
        return JWTVerifier(
            jwks_uri=auth.jwt_jwks_uri,
            public_key=auth.jwt_public_key.get_secret_value() if auth.jwt_public_key else None,
            issuer=auth.jwt_issuer,
            audience=auth.jwt_audience,
            algorithm=auth.jwt_algorithm,
            required_scopes=required_scopes,
        )
    if auth.mode == AuthMode.OIDC:
        # OIDCProxy на старте читает oidc_config_url по сети; сбой останавливает запуск
        return OIDCProxy(
            config_url=_required(auth.oidc_config_url, "oidc_config_url", auth.mode),
            client_id=_required(auth.oidc_client_id, "oidc_client_id", auth.mode),
            client_secret=_required(auth.oidc_client_secret, "oidc_client_secret", auth.mode).get_secret_value(),
            audience=auth.oidc_audience,
            base_url=_required(auth.base_url, "base_url", auth.mode),
            required_scopes=required_scopes,
        )
    return None
