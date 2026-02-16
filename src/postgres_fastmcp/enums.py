"""Типы для конфигурации сервера MCP."""

from enum import StrEnum
from typing import Literal


# Mount mode types
MountMode = Literal["tool", "endpoint"]


class AccessMode(StrEnum):
    """Уровень доступа SQL для сервера."""

    RESTRICTED = "restricted"  # Только чтение (SELECT)
    UNRESTRICTED = "unrestricted"  # Чтение-запись (DML: INSERT/UPDATE/DELETE) или полный доступ (DDL) для полной роли


class UserRole(StrEnum):
    """Роль пользователя, определяющая доступ к схемам и доступные инструменты."""

    USER = "user"  # Базовая роль: только схема public, базовые инструменты (4)
    ADMIN = "admin"  # Роль администратора: все схемы, все инструменты (9), расширенные привилегии


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


class TransportHttpApp(StrEnum):
    """Типы HTTP транспорта для FastMCP http_app."""

    HTTP = "http"
    STREAMABLE_HTTP = "streamable-http"


class ToolTag(StrEnum):
    """Теги для фильтрации инструментов (базовые и полные)."""

    BASIC = "basic"
    FULL = "full"
