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
    FULL = "full"  # Полная роль: все схемы, все инструменты (9), расширенные привилегии


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


class ToolName(StrEnum):
    """Доступные имена инструментов."""

    LIST_SCHEMAS = "list_schemas"
    LIST_OBJECTS = "list_objects"
    GET_OBJECT_DETAILS = "get_object_details"
    EXPLAIN_QUERY = "explain_query"
    EXECUTE_SQL = "execute_sql"
    ANALYZE_WORKLOAD_INDEXES = "analyze_workload_indexes"
    ANALYZE_QUERY_INDEXES = "analyze_query_indexes"
    ANALYZE_DB_HEALTH = "analyze_db_health"
    GET_TOP_QUERIES = "get_top_queries"

    @classmethod
    def available_tools(cls) -> list["ToolName"]:
        """Получить список всех доступных инструментов, которые можно включить/выключить."""
        return [
            cls.LIST_SCHEMAS,
            cls.LIST_OBJECTS,
            cls.GET_OBJECT_DETAILS,
            cls.EXPLAIN_QUERY,
            cls.EXECUTE_SQL,
            cls.ANALYZE_WORKLOAD_INDEXES,
            cls.ANALYZE_QUERY_INDEXES,
            cls.ANALYZE_DB_HEALTH,
            cls.GET_TOP_QUERIES,
        ]

    @classmethod
    def basic_tools(cls) -> list["ToolName"]:
        """Получить список базовых инструментов (доступны для ролей USER и FULL)."""
        return [
            cls.LIST_OBJECTS,
            cls.GET_OBJECT_DETAILS,
            cls.EXPLAIN_QUERY,
            cls.EXECUTE_SQL,
        ]

    @classmethod
    def admin_tools(cls) -> list["ToolName"]:
        """Получить список инструментов администратора, доступных только для FULL роли."""
        return [
            cls.LIST_SCHEMAS,
            cls.ANALYZE_WORKLOAD_INDEXES,
            cls.ANALYZE_QUERY_INDEXES,
            cls.ANALYZE_DB_HEALTH,
            cls.GET_TOP_QUERIES,
        ]
