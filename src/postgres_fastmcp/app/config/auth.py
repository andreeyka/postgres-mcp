"""Конфигурация аутентификации: режим, параметры провайдера и политика прав по claim."""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, SecretStr, TypeAdapter, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from postgres_fastmcp.access import AccessPolicy
from postgres_fastmcp.shared.enums import AuthMode


class StaticToken(BaseModel):
    """Один статический токен: клиент, скоупы и произвольные claims."""

    client_id: str
    scopes: list[str] = Field(default_factory=list)
    claims: dict[str, Any] = Field(default_factory=dict)


_TOKENS: TypeAdapter[dict[str, StaticToken]] = TypeAdapter(dict[str, StaticToken])


def _validate_tokens(value: object, source: str) -> dict[str, StaticToken]:
    """Разобрать словарь токенов; строка токена (секрет) не попадает в текст ошибки.

    У ошибки pydantic первый элемент ``loc`` — ключ словаря, то есть сам токен.
    """
    try:
        return _TOKENS.validate_python(value)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'][1:]) or 'entry'}: {err['msg']}" for err in exc.errors()
        )
        msg = f"{source} must map each token to {{client_id, scopes?, claims?}} ({details}); token values are hidden"
        raise ValueError(msg) from None


class AuthSettings(BaseSettings):
    """Аутентификация HTTP-транспорта: env ``MCP_AUTH_*`` (вложенные поля через ``__``), секция ``auth``.

    Поля чужого режима игнорируются. Строки токенов и секреты не попадают ни в repr,
    ни в текст ошибок валидации (``hide_input_in_errors``).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="MCP_AUTH_",
        env_nested_delimiter="__",
        extra="ignore",
        hide_input_in_errors=True,
    )

    mode: AuthMode = Field(default=AuthMode.NONE, description="none | static | jwt | oidc")
    required_scopes: list[str] = Field(default_factory=list, description="Скоупы, без которых токен не принимается")
    access_policy: AccessPolicy = Field(default_factory=AccessPolicy, description="Сужение прав по claim токена")

    tokens: dict[str, StaticToken] = Field(default_factory=dict, repr=False, description="static: токен -> описание")
    tokens_file: Path | None = Field(default=None, description="static: JSON-файл той же формы, что tokens")

    jwt_jwks_uri: str | None = Field(default=None, description="jwt: URL JWKS (или jwt_public_key)")
    jwt_public_key: SecretStr | None = Field(default=None, description="jwt: PEM ключа или общий секрет для HS*")
    jwt_issuer: str | None = Field(default=None, description="jwt: ожидаемый iss")
    jwt_audience: str | list[str] | None = Field(default=None, description="jwt: ожидаемый aud")
    jwt_algorithm: str | None = Field(default=None, description="jwt: алгоритм подписи; по умолчанию решает FastMCP")

    oidc_config_url: str | None = Field(default=None, description="oidc: URL .well-known/openid-configuration")
    oidc_client_id: str | None = Field(default=None, description="oidc: client_id сервера в IdP")
    oidc_client_secret: SecretStr | None = Field(default=None, description="oidc: client_secret сервера в IdP")
    oidc_audience: str | None = Field(default=None, description="oidc: ожидаемый aud токена IdP")
    base_url: str | None = Field(default=None, description="oidc: публичный адрес сервера для callback")

    @field_validator("tokens", mode="before")
    @classmethod
    def _check_tokens(cls, value: object) -> dict[str, StaticToken]:
        return _validate_tokens(value, "tokens")

    @model_validator(mode="after")
    def _check_mode(self) -> "AuthSettings":
        if self.mode == AuthMode.STATIC:
            self.tokens = {**self._read_tokens_file(), **self.tokens}
            if not self.tokens:
                msg = "auth.mode=static requires tokens or tokens_file with at least one token"
                raise ValueError(msg)
        elif self.mode == AuthMode.JWT:
            if bool(self.jwt_jwks_uri) == bool(self.jwt_public_key):
                msg = "auth.mode=jwt requires exactly one of jwt_jwks_uri and jwt_public_key"
                raise ValueError(msg)
            self._require("jwt_issuer", "jwt_audience")
        elif self.mode == AuthMode.OIDC:
            self._require("oidc_config_url", "oidc_client_id", "oidc_client_secret", "base_url")
        return self

    def _require(self, *names: str) -> None:
        missing = [name for name in names if not getattr(self, name)]
        if missing:
            msg = f"auth.mode={self.mode} requires {', '.join(missing)}"
            raise ValueError(msg)

    def _read_tokens_file(self) -> dict[str, StaticToken]:
        """Токены из tokens_file (k8s secret); при совпадении строки токена побеждает ``tokens``."""
        if self.tokens_file is None:
            return {}
        try:
            raw = json.loads(self.tokens_file.read_text(encoding="utf-8"))
        except OSError as exc:
            msg = f"tokens_file {self.tokens_file} cannot be read: {exc.strerror}"
            raise ValueError(msg) from None
        except json.JSONDecodeError as exc:
            msg = f"tokens_file {self.tokens_file} is not valid JSON (line {exc.lineno}, column {exc.colno})"
            raise ValueError(msg) from None
        return _validate_tokens(raw, f"tokens_file {self.tokens_file}")
