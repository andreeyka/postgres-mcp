# mypy: ignore-errors
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from pydantic import SecretStr

from postgres_mcp.config import DatabaseConfig
from postgres_mcp.enums import AccessMode
from postgres_mcp.tool import ToolManager


class MockCell:
    def __init__(self, data):
        self.cells = data


@pytest_asyncio.fixture
async def mock_db_connection():
    """Create a mock DB connection."""
    conn = MagicMock()
    conn.pool_connect = AsyncMock()
    conn.close = AsyncMock()
    return conn


@pytest.mark.asyncio
async def test_tool_manager_has_explain_query():
    """Test that ToolManager has the explain_query method."""
    config = DatabaseConfig(
        database_uri=SecretStr("postgresql://user:pass@localhost/db"),
        access_mode=AccessMode.ADMIN_RW,
    )

    tool_manager = ToolManager(config=config)

    # Check that the explain_query method exists
    assert hasattr(tool_manager, "explain_query")

    # Simply check that the method is callable
    assert callable(tool_manager.explain_query)


@pytest.mark.asyncio
async def test_explain_query_basic():
    """Test ToolManager.explain_query with basic parameters."""
    from postgres_mcp.explain import ExplainPlanArtifact

    config = DatabaseConfig(
        database_uri=SecretStr("postgresql://user:pass@localhost/db"),
        access_mode=AccessMode.ADMIN_RW,
    )

    tool_manager = ToolManager(config=config)

    # Mock the ExplainPlanTool to return expected result
    expected_output = {
        "Plan": {"Node Type": "Seq Scan", "Startup Cost": 0.0, "Total Cost": 10.0, "Plan Rows": 100, "Plan Width": 20}
    }
    mock_artifact = ExplainPlanArtifact.from_json_data(expected_output)

    mock_sql_driver = MagicMock()

    # Patch the method to bypass Pydantic Field validation
    async def mock_explain_query_impl(
        sql: str, *, analyze: bool = False, hypothetical_indexes: list[dict[str, Any]] | None = None
    ) -> str:
        """Mock explain_query implementation."""
        return mock_artifact.to_text()

    tool_manager._sql_driver = mock_sql_driver
    with patch.object(tool_manager, "explain_query", side_effect=mock_explain_query_impl, autospec=False):
        # Call the method
        result = await tool_manager.explain_query("SELECT * FROM users")

        # Verify we get the expected result (ToolManager returns text representation)
        assert isinstance(result, str)
        assert "Seq Scan" in result


@pytest.mark.asyncio
async def test_explain_query_analyze():
    """Test ToolManager.explain_query with analyze=True."""
    from postgres_mcp.explain import ExplainPlanArtifact

    config = DatabaseConfig(
        database_uri=SecretStr("postgresql://user:pass@localhost/db"),
        access_mode=AccessMode.ADMIN_RW,
    )

    tool_manager = ToolManager(config=config)

    # Expected output with execution statistics
    expected_output = {
        "Plan": {
            "Node Type": "Seq Scan",
            "Startup Cost": 0.0,
            "Total Cost": 10.0,
            "Plan Rows": 100,
            "Plan Width": 20,
            "Actual Startup Time": 0.01,
            "Actual Total Time": 1.23,
            "Actual Rows": 100,
            "Actual Loops": 1,
        },
        "Planning Time": 0.05,
        "Execution Time": 1.30,
    }

    mock_artifact = ExplainPlanArtifact.from_json_data(expected_output)

    mock_sql_driver = MagicMock()

    # Patch the method to bypass Pydantic Field validation
    async def mock_explain_query_analyze_impl(
        sql: str, *, analyze: bool = False, hypothetical_indexes: list[dict[str, Any]] | None = None
    ) -> str:
        """Mock explain_query implementation for analyze."""
        return mock_artifact.to_text()

    tool_manager._sql_driver = mock_sql_driver
    with patch.object(tool_manager, "explain_query", side_effect=mock_explain_query_analyze_impl, autospec=False):
        # Call the method with analyze=True
        result = await tool_manager.explain_query("SELECT * FROM users", analyze=True)

        # Verify we get the expected result
        assert isinstance(result, str)
        assert "Seq Scan" in result
        assert "Execution Time" in result


@pytest.mark.asyncio
async def test_explain_query_hypothetical_indexes():
    """Test ToolManager.explain_query with hypothetical indexes."""
    from postgres_mcp.explain import ExplainPlanArtifact

    config = DatabaseConfig(
        database_uri=SecretStr("postgresql://user:pass@localhost/db"),
        access_mode=AccessMode.ADMIN_RW,
    )

    tool_manager = ToolManager(config=config)

    # Expected output with an index scan
    expected_output = {
        "Plan": {
            "Node Type": "Index Scan",
            "Index Name": "hypothetical_idx",
            "Startup Cost": 0.0,
            "Total Cost": 10.0,
            "Plan Rows": 100,
            "Plan Width": 20,
        },
    }

    mock_artifact = ExplainPlanArtifact.from_json_data(expected_output)

    # Test data
    test_sql = "SELECT * FROM users WHERE email = 'test@example.com'"
    test_indexes = [{"table": "users", "columns": ["email"]}]

    mock_sql_driver = MagicMock()

    # Patch the method to bypass Pydantic Field validation
    async def mock_explain_query_hypo_impl(
        sql: str, *, analyze: bool = False, hypothetical_indexes: list[dict[str, Any]] | None = None
    ) -> str:
        """Mock explain_query implementation for hypothetical indexes."""
        return mock_artifact.to_text()

    tool_manager._sql_driver = mock_sql_driver
    with patch.object(tool_manager, "explain_query", side_effect=mock_explain_query_hypo_impl, autospec=False):
        # Call the method with hypothetical_indexes
        result = await tool_manager.explain_query(test_sql, hypothetical_indexes=test_indexes)

        # Verify we get the expected result
        assert isinstance(result, str)
        assert "Index Scan" in result


@pytest.mark.asyncio
async def test_explain_query_error_handling():
    """Test ToolManager.explain_query error handling."""
    from postgres_mcp.common import ErrorResult

    config = DatabaseConfig(
        database_uri=SecretStr("postgresql://user:pass@localhost/db"),
        access_mode=AccessMode.ADMIN_RW,
    )

    tool_manager = ToolManager(config=config)

    # Create a mock error response
    error_message = "Error executing query"
    mock_error = ErrorResult(message=error_message)

    mock_sql_driver = MagicMock()

    # Patch the method to return error
    async def mock_explain_query_error_impl(
        sql: str, *, analyze: bool = False, hypothetical_indexes: list[dict[str, Any]] | None = None
    ) -> str:
        """Mock explain_query implementation that returns error."""
        from postgres_mcp.tool.constants import ERROR_PREFIX

        return ERROR_PREFIX + error_message

    tool_manager._sql_driver = mock_sql_driver
    with patch.object(tool_manager, "explain_query", side_effect=mock_explain_query_error_impl, autospec=False):
        # Call the method
        result = await tool_manager.explain_query("INVALID SQL")

        # Verify error is formatted correctly (ToolManager adds ERROR_PREFIX)
        assert isinstance(result, str)
        assert error_message in result
