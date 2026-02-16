# mypy: ignore-errors
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from postgres_mcp.enums import AccessMode
from postgres_mcp.sql import SafeSqlDriver, SqlDriver


@pytest.mark.asyncio
async def test_force_readonly_enforcement():
    """
    Test that force_readonly is properly enforced based on access mode:
    - In ADMIN_RO mode: SafeSqlDriver always uses read_only=True (ignores force_readonly parameter)
    - In ADMIN_RW mode: SqlDriver respects the force_readonly parameter (default False)
    """
    from pydantic import SecretStr

    from postgres_mcp.config import DatabaseConfig
    from postgres_mcp.sql.sql_driver import DbConnPool
    from postgres_mcp.tool import ToolManager

    # Create mock for connection pool
    mock_conn_pool = MagicMock(spec=DbConnPool)
    mock_conn_pool._is_valid = True
    mock_conn_pool.connection_url = "postgresql://user:pass@localhost/db"

    # Create a mock for the base SqlDriver._execute_with_connection
    mock_execute = AsyncMock()
    mock_execute.return_value = [SqlDriver.RowResult(cells={"test": "value"})]

    # Create mock pool and connection for pool_connect
    mock_pool = MagicMock()
    mock_connection = AsyncMock()
    mock_connection.set_autocommit = AsyncMock()
    mock_pool_connection_cm = AsyncMock()
    mock_pool_connection_cm.__aenter__ = AsyncMock(return_value=mock_connection)
    mock_pool_connection_cm.__aexit__ = AsyncMock(return_value=None)
    mock_pool.connection = MagicMock(return_value=mock_pool_connection_cm)
    mock_conn_pool.pool_connect = AsyncMock(return_value=mock_pool)

    # Test ADMIN_RW mode (unrestricted - uses SqlDriver)
    config_rw = DatabaseConfig(
        database_uri=SecretStr("postgresql://user:pass@localhost/db"),
        access_mode=AccessMode.ADMIN_RW,
    )

    tool_manager = ToolManager(config=config_rw)
    tool_manager.db_connection = mock_conn_pool
    with patch.object(SqlDriver, "_execute_with_connection", mock_execute):
        driver = tool_manager.sql_driver
        assert isinstance(driver, SqlDriver)
        assert not isinstance(driver, SafeSqlDriver)

        # Test default behavior (should be False)
        mock_execute.reset_mock()
        await driver.execute_query("SELECT 1")
        assert mock_execute.call_count == 1
        # Check that force_readonly is False by default
        assert mock_execute.call_args[1]["force_readonly"] is False

        # Test explicit True
        mock_execute.reset_mock()
        await driver.execute_query("SELECT 1", force_readonly=True)
        assert mock_execute.call_count == 1
        # Check that force_readonly=True is respected
        assert mock_execute.call_args[1]["force_readonly"] is True

        # Test explicit False
        mock_execute.reset_mock()
        await driver.execute_query("SELECT 1", force_readonly=False)
        assert mock_execute.call_count == 1
        # Check that force_readonly=False is respected
        assert mock_execute.call_args[1]["force_readonly"] is False

    # Test ADMIN_RO mode (restricted - uses SafeSqlDriver with read_only=True)
    config_ro = DatabaseConfig(
        database_uri=SecretStr("postgresql://user:pass@localhost/db"),
        access_mode=AccessMode.ADMIN_RO,
    )

    # Create new mock pool for this test
    mock_pool_ro = MagicMock()
    mock_connection_ro = AsyncMock()
    mock_connection_ro.set_autocommit = AsyncMock()
    mock_pool_connection_cm_ro = AsyncMock()
    mock_pool_connection_cm_ro.__aenter__ = AsyncMock(return_value=mock_connection_ro)
    mock_pool_connection_cm_ro.__aexit__ = AsyncMock(return_value=None)
    mock_pool_ro.connection = MagicMock(return_value=mock_pool_connection_cm_ro)
    mock_conn_pool_ro = MagicMock(spec=DbConnPool)
    mock_conn_pool_ro._is_valid = True
    mock_conn_pool_ro.connection_url = "postgresql://user:pass@localhost/db"
    mock_conn_pool_ro.pool_connect = AsyncMock(return_value=mock_pool_ro)

    tool_manager = ToolManager(config=config_ro)
    tool_manager.db_connection = mock_conn_pool_ro
    with patch.object(SqlDriver, "_execute_with_connection", mock_execute):
        driver = tool_manager.sql_driver
        # Check that we have the correct driver type and it has read_only attribute
        assert isinstance(driver, SafeSqlDriver)
        assert driver.read_only is True

        # Test default behavior - SafeSqlDriver ignores force_readonly parameter
        mock_execute.reset_mock()
        await driver.execute_query("SELECT 1")
        assert mock_execute.call_count == 1
        # SafeSqlDriver always uses self.read_only, which is True
        assert mock_execute.call_args[1]["force_readonly"] is True

        # Test explicit False (should still be True because SafeSqlDriver ignores the parameter)
        mock_execute.reset_mock()
        await driver.execute_query("SELECT 1", force_readonly=False)
        assert mock_execute.call_count == 1
        # Check that force_readonly is True despite passing False
        # (SafeSqlDriver uses self.read_only instead)
        assert mock_execute.call_args[1]["force_readonly"] is True

        # Test explicit True
        mock_execute.reset_mock()
        await driver.execute_query("SELECT 1", force_readonly=True)
        assert mock_execute.call_count == 1
        # Check that force_readonly remains True
        assert mock_execute.call_args[1]["force_readonly"] is True
