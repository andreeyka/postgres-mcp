"""Common helpers for MCP tool registration."""

from postgres_fastmcp.config.database import DatabaseConfig
from postgres_fastmcp.enums import AccessMode, UserRole
from postgres_fastmcp.tools.descriptions import (
    DESC_ANALYZE_DB_HEALTH,
    DESC_ANALYZE_QUERY_INDEXES,
    DESC_ANALYZE_WORKLOAD_INDEXES,
    DESC_EXECUTE_SQL_RESTRICTED,
    DESC_EXECUTE_SQL_UNRESTRICTED,
    DESC_EXPLAIN_QUERY,
    DESC_GET_OBJECT_DETAILS_FULL,
    DESC_GET_OBJECT_DETAILS_USER,
    DESC_GET_TOP_QUERIES,
    DESC_LIST_OBJECTS_FULL,
    DESC_LIST_OBJECTS_USER,
    DESC_LIST_SCHEMAS,
)


class ToolDescriptions:
    """Описания инструментов в зависимости от настроек (role, access_mode). Свойства — по одному на инструмент."""

    def __init__(self, config: DatabaseConfig) -> None:
        self._role = config.role
        self._access_mode = config.access_mode

    @property
    def list_schemas(self) -> str:
        """Описание инструмента list_schemas."""
        return DESC_LIST_SCHEMAS

    @property
    def list_objects(self) -> str:
        """Описание инструмента list_objects (зависит от role)."""
        return DESC_LIST_OBJECTS_USER if self._role == UserRole.USER else DESC_LIST_OBJECTS_FULL

    @property
    def get_object_details(self) -> str:
        """Описание инструмента get_object_details (зависит от role)."""
        return DESC_GET_OBJECT_DETAILS_USER if self._role == UserRole.USER else DESC_GET_OBJECT_DETAILS_FULL

    @property
    def explain_query(self) -> str:
        """Описание инструмента explain_query."""
        return DESC_EXPLAIN_QUERY

    @property
    def execute_sql(self) -> str:
        """Описание инструмента execute_sql (зависит от role и access_mode)."""
        if self._role == UserRole.FULL and self._access_mode == AccessMode.UNRESTRICTED:
            return DESC_EXECUTE_SQL_UNRESTRICTED
        return DESC_EXECUTE_SQL_RESTRICTED

    @property
    def analyze_workload_indexes(self) -> str:
        """Описание инструмента analyze_workload_indexes."""
        return DESC_ANALYZE_WORKLOAD_INDEXES

    @property
    def analyze_query_indexes(self) -> str:
        """Описание инструмента analyze_query_indexes."""
        return DESC_ANALYZE_QUERY_INDEXES

    @property
    def analyze_db_health(self) -> str:
        """Описание инструмента analyze_db_health."""
        return DESC_ANALYZE_DB_HEALTH

    @property
    def get_top_queries(self) -> str:
        """Описание инструмента get_top_queries."""
        return DESC_GET_TOP_QUERIES
