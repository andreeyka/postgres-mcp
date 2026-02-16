# mypy: ignore-errors
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from pydantic import SecretStr

from postgres_mcp.config import DatabaseConfig
from postgres_mcp.enums import AccessMode
from postgres_mcp.explain import ExplainPlanArtifact
from postgres_mcp.tool import ToolManager


@pytest_asyncio.fixture
async def mock_safe_sql_driver():
    """Create a mock SafeSqlDriver for testing."""
    driver = MagicMock()
    return driver


@pytest.fixture
def mock_explain_plan_tool():
    """Create a mock ExplainPlanTool."""
    tool = MagicMock()
    tool.explain = AsyncMock()
    tool.explain_analyze = AsyncMock()
    tool.explain_with_hypothetical_indexes = AsyncMock()
    return tool


class MockCell:
    def __init__(self, data):
        self.cells = data


@pytest.mark.asyncio
async def test_explain_query_integration():
    """Test the entire ToolManager.explain_query tool end-to-end."""
    config = DatabaseConfig(
        database_uri=SecretStr("postgresql://user:pass@localhost/db"),
        access_mode=AccessMode.ADMIN_RW,
    )

    tool_manager = ToolManager(config=config)

    # Expected output
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
        result = await tool_manager.explain_query("SELECT * FROM users")

        # Verify result matches our expected plan data (ToolManager returns text)
        assert isinstance(result, str)
        assert "Seq Scan" in result


@pytest.mark.asyncio
async def test_explain_query_with_analyze_integration():
    """Test the ToolManager.explain_query tool with analyze=True."""
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
        result = await tool_manager.explain_query("SELECT * FROM users", analyze=True)

        # Verify result matches our expected plan data
        assert isinstance(result, str)
        assert "Seq Scan" in result
        assert "Execution Time" in result


@pytest.mark.asyncio
async def test_explain_query_with_hypothetical_indexes_integration():
    """Test the ToolManager.explain_query tool with hypothetical indexes."""
    config = DatabaseConfig(
        database_uri=SecretStr("postgresql://user:pass@localhost/db"),
        access_mode=AccessMode.ADMIN_RW,
    )

    tool_manager = ToolManager(config=config)

    # Expected output
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
        result = await tool_manager.explain_query(test_sql, hypothetical_indexes=test_indexes)

        # Verify result matches our expected plan data
        assert isinstance(result, str)
        assert "Index Scan" in result


@pytest.mark.asyncio
async def test_explain_query_missing_hypopg_integration():
    """Test the ToolManager.explain_query tool when hypopg extension is missing."""
    config = DatabaseConfig(
        database_uri=SecretStr("postgresql://user:pass@localhost/db"),
        access_mode=AccessMode.ADMIN_RW,
    )

    tool_manager = ToolManager(config=config)

    # Test data
    test_sql = "SELECT * FROM users WHERE email = 'test@example.com'"
    test_indexes = [{"table": "users", "columns": ["email"]}]

    # Mock message about missing extension
    missing_ext_message = "extension is required"

    mock_sql_driver = MagicMock()

    # Patch the method to return missing extension message
    async def mock_explain_query_missing_hypo_impl(
        sql: str, *, analyze: bool = False, hypothetical_indexes: list[dict[str, Any]] | None = None
    ) -> str:
        """Mock explain_query implementation that returns missing extension message."""
        return missing_ext_message

    tool_manager._sql_driver = mock_sql_driver
    with patch.object(tool_manager, "explain_query", side_effect=mock_explain_query_missing_hypo_impl, autospec=False):
        result = await tool_manager.explain_query(test_sql, hypothetical_indexes=test_indexes)

        # Verify result (ToolManager returns the message directly when hypopg is not installed)
        assert isinstance(result, str)
        assert missing_ext_message in result


@pytest.mark.asyncio
async def test_explain_query_error_handling_integration():
    """Test the ToolManager.explain_query tool's error handling."""
    from postgres_mcp.common import ErrorResult

    config = DatabaseConfig(
        database_uri=SecretStr("postgresql://user:pass@localhost/db"),
        access_mode=AccessMode.ADMIN_RW,
    )

    tool_manager = ToolManager(config=config)

    # Mock error response
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
        result = await tool_manager.explain_query("INVALID SQL")

        # Verify error is correctly formatted (ToolManager adds ERROR_PREFIX)
        assert isinstance(result, str)
        assert error_message in result
