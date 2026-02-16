# mypy: ignore-errors
"""Unit tests for ToolDescriptions (tools/common.py)."""

from postgres_fastmcp.tools.common import ToolDescriptions
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


class TestToolDescriptions:
    """Tests for ToolDescriptions role and access_mode branching."""

    def test_list_schemas_same_for_all_roles(
        self,
        tool_descriptions_full: ToolDescriptions,
        tool_descriptions_user: ToolDescriptions,
    ) -> None:
        """list_schemas description does not depend on role."""
        assert tool_descriptions_full.list_schemas == DESC_LIST_SCHEMAS
        assert tool_descriptions_user.list_schemas == DESC_LIST_SCHEMAS

    def test_list_objects_user_returns_user_description(
        self,
        tool_descriptions_user: ToolDescriptions,
    ) -> None:
        """User role gets list_objects description for public schema only."""
        assert tool_descriptions_user.list_objects == DESC_LIST_OBJECTS_USER

    def test_list_objects_full_returns_full_description(
        self,
        tool_descriptions_full: ToolDescriptions,
    ) -> None:
        """Full role gets list_objects description for any schema."""
        assert tool_descriptions_full.list_objects == DESC_LIST_OBJECTS_FULL

    def test_get_object_details_user_returns_user_description(
        self,
        tool_descriptions_user: ToolDescriptions,
    ) -> None:
        """User role gets get_object_details description for public schema only."""
        assert tool_descriptions_user.get_object_details == DESC_GET_OBJECT_DETAILS_USER

    def test_get_object_details_full_returns_full_description(
        self,
        tool_descriptions_full: ToolDescriptions,
    ) -> None:
        """Full role gets get_object_details description for any schema."""
        assert tool_descriptions_full.get_object_details == DESC_GET_OBJECT_DETAILS_FULL

    def test_explain_query_same_for_all(
        self,
        tool_descriptions_full: ToolDescriptions,
        tool_descriptions_user: ToolDescriptions,
    ) -> None:
        """explain_query description does not depend on role."""
        assert tool_descriptions_full.explain_query == DESC_EXPLAIN_QUERY
        assert tool_descriptions_user.explain_query == DESC_EXPLAIN_QUERY

    def test_execute_sql_restricted_for_user(
        self,
        tool_descriptions_user: ToolDescriptions,
    ) -> None:
        """User role gets restricted execute_sql description."""
        assert tool_descriptions_user.execute_sql == DESC_EXECUTE_SQL_RESTRICTED

    def test_execute_sql_restricted_for_full_restricted(
        self,
        tool_descriptions_full: ToolDescriptions,
    ) -> None:
        """Full role with restricted access_mode gets restricted execute_sql description."""
        assert tool_descriptions_full.execute_sql == DESC_EXECUTE_SQL_RESTRICTED

    def test_execute_sql_unrestricted_for_full_unrestricted(
        self,
        tool_descriptions_unrestricted: ToolDescriptions,
    ) -> None:
        """Full role with unrestricted access_mode gets unrestricted execute_sql description."""
        assert tool_descriptions_unrestricted.execute_sql == DESC_EXECUTE_SQL_UNRESTRICTED

    def test_analyze_workload_indexes_same_for_all(
        self,
        tool_descriptions_full: ToolDescriptions,
    ) -> None:
        """analyze_workload_indexes description does not depend on role."""
        assert tool_descriptions_full.analyze_workload_indexes == DESC_ANALYZE_WORKLOAD_INDEXES

    def test_analyze_query_indexes_same_for_all(
        self,
        tool_descriptions_full: ToolDescriptions,
    ) -> None:
        """analyze_query_indexes description does not depend on role."""
        assert tool_descriptions_full.analyze_query_indexes == DESC_ANALYZE_QUERY_INDEXES

    def test_analyze_db_health_same_for_all(
        self,
        tool_descriptions_full: ToolDescriptions,
    ) -> None:
        """analyze_db_health description does not depend on role."""
        assert tool_descriptions_full.analyze_db_health == DESC_ANALYZE_DB_HEALTH

    def test_get_top_queries_same_for_all(
        self,
        tool_descriptions_full: ToolDescriptions,
    ) -> None:
        """get_top_queries description does not depend on role."""
        assert tool_descriptions_full.get_top_queries == DESC_GET_TOP_QUERIES
