"""Types for MCP server configuration."""

from enum import StrEnum
from typing import Literal


# Mount mode types
MountMode = Literal["tool", "endpoint"]


class AccessMode(StrEnum):
    """SQL access level for the server."""

    RESTRICTED = "restricted"  # Read-only access (SELECT only)
    UNRESTRICTED = "unrestricted"  # Read-write access (DML: INSERT/UPDATE/DELETE), or full access (DDL) for full role


class UserRole(StrEnum):
    """User role that determines schema access and available tools."""

    USER = "user"  # Basic role: only public schema, basic tools (4)
    FULL = "full"  # Full role: all schemas, all tools (9), extended privileges


class TransportConfig(StrEnum):
    """Transport types for configuration."""

    HTTP = "http"
    STDIO = "stdio"


class TransportHttpApp(StrEnum):
    """HTTP transport types for FastMCP http_app."""

    HTTP = "http"
    STREAMABLE_HTTP = "streamable-http"


class ToolTag(StrEnum):
    """Tags for tool filtering (basic vs full)."""

    BASIC = "basic"
    FULL = "full"


class ToolName(StrEnum):
    """Available tool names."""

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
        """Get list of all available tools that can be enabled/disabled."""
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
        """Get list of basic tools (available for both USER and FULL roles)."""
        return [
            cls.LIST_OBJECTS,
            cls.GET_OBJECT_DETAILS,
            cls.EXPLAIN_QUERY,
            cls.EXECUTE_SQL,
        ]

    @classmethod
    def admin_tools(cls) -> list["ToolName"]:
        """Get list of admin tools that are only available for FULL role."""
        return [
            cls.LIST_SCHEMAS,
            cls.ANALYZE_WORKLOAD_INDEXES,
            cls.ANALYZE_QUERY_INDEXES,
            cls.ANALYZE_DB_HEALTH,
            cls.GET_TOP_QUERIES,
        ]
