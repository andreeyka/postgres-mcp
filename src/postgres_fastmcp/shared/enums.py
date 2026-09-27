"""Типы для конфигурации сервера MCP."""

from enum import StrEnum
from typing import Literal


# Mount mode types
MountMode = Literal["tool", "endpoint"]

# Tool parameter types
ObjectType = Literal["table", "view", "sequence", "extension"]
TopQueriesSortBy = Literal["total_time", "mean_time", "resources"]


class AccessMode(StrEnum):
    """Уровень доступа: область схем и набор инструментов."""

    BASIC = "basic"  # Только схема public, базовые инструменты (4)
    FULL = "full"  # Все схемы, все инструменты (9), расширенные привилегии


class AuthMode(StrEnum):
    """Режим аутентификации HTTP-транспорта."""

    NONE = "none"  # Без аутентификации
    STATIC = "static"  # Фиксированные токены из конфига (StaticTokenVerifier)
    JWT = "jwt"  # JWT от внешнего IdP (JWTVerifier)
    OIDC = "oidc"  # OAuth-прокси к OIDC-провайдеру (OIDCProxy)


class SslMode(StrEnum):
    """Режим SSL для подключения к PostgreSQL (libpq)."""

    DISABLE = "disable"
    ALLOW = "allow"
    PREFER = "prefer"
    REQUIRE = "require"
    VERIFY_CA = "verify-ca"
    VERIFY_FULL = "verify-full"


class TransportConfig(StrEnum):
    """Типы транспорта для конфигурации."""

    HTTP = "http"
    STDIO = "stdio"


class ToolTag(StrEnum):
    """Теги для фильтрации инструментов (базовые и полные)."""

    BASIC = "basic"
    FULL = "full"
