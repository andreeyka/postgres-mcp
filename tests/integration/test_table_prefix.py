# mypy: ignore-errors
"""Integration tests for table_prefix functionality."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from postgres_mcp.config import DatabaseConfig
from postgres_mcp.enums import AccessMode
from postgres_mcp.sql import SafeSqlDriver, SqlDriver
from postgres_mcp.tool import ToolManager


async def setup_test_tables(sql_driver: SqlDriver) -> None:
    """Create test tables with and without prefix."""
    # Create tables with prefix
    await sql_driver.execute_query(
        """
        CREATE TABLE IF NOT EXISTS app_users (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            email VARCHAR(255) UNIQUE NOT NULL
        )
        """
    )
    await sql_driver.execute_query(
        """
        CREATE TABLE IF NOT EXISTS app_orders (
            id SERIAL PRIMARY KEY,
            user_id INTEGER,
            amount DECIMAL(10,2) NOT NULL
        )
        """
    )

    # Create tables without prefix
    await sql_driver.execute_query(
        """
        CREATE TABLE IF NOT EXISTS other_users (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL
        )
        """
    )
    await sql_driver.execute_query(
        """
        CREATE TABLE IF NOT EXISTS test_users (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL
        )
        """
    )

    # Insert test data
    await sql_driver.execute_query(
        "INSERT INTO app_users (name, email) VALUES ('App User 1', 'app1@test.com') ON CONFLICT DO NOTHING"
    )
    await sql_driver.execute_query("INSERT INTO other_users (name) VALUES ('Other User 1') ON CONFLICT DO NOTHING")
    await sql_driver.execute_query("INSERT INTO test_users (name) VALUES ('Test User 1') ON CONFLICT DO NOTHING")


@pytest.mark.asyncio
async def test_table_prefix_allows_prefixed_tables(test_postgres_connection_string: tuple[str, str]) -> None:
    """Test that tables with prefix are accessible when table_prefix is set."""
    connection_string, _ = test_postgres_connection_string

    # First, setup tables using admin connection
    admin_config = DatabaseConfig(
        database_uri=SecretStr(connection_string),
        access_mode=AccessMode.ADMIN_RW,
    )
    admin_tool_manager = ToolManager(config=admin_config)
    await admin_tool_manager.__aenter__()
    try:
        await setup_test_tables(admin_tool_manager.sql_driver)
    finally:
        await admin_tool_manager.__aexit__(None, None, None)

    # Now test with user mode and table_prefix
    config = DatabaseConfig(
        database_uri=SecretStr(connection_string),
        access_mode=AccessMode.USER_RO,
        table_prefix="app_",
    )

    tool_manager = ToolManager(config=config)
    await tool_manager.__aenter__()

    try:
        sql_driver = tool_manager.sql_driver
        # Check that we have the correct driver type (validates API contract)
        assert isinstance(sql_driver, SafeSqlDriver)

        # Query to prefixed table should work and return actual data
        query = "SELECT * FROM app_users LIMIT 1"
        result = await sql_driver.execute_query(query)
        assert result is not None
        assert len(result) > 0
        # Verify we got actual data, not just empty result
        assert "name" in result[0].cells or "email" in result[0].cells

        # Test another prefixed table
        query2 = "SELECT COUNT(*) as cnt FROM app_orders"
        result2 = await sql_driver.execute_query(query2)
        assert result2 is not None
        assert len(result2) > 0

    finally:
        await tool_manager.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_table_prefix_blocks_non_prefixed_tables(test_postgres_connection_string: tuple[str, str]) -> None:
    """Test that tables without prefix are blocked when table_prefix is set."""
    connection_string, _ = test_postgres_connection_string

    # First, setup tables using admin connection
    admin_config = DatabaseConfig(
        database_uri=SecretStr(connection_string),
        access_mode=AccessMode.ADMIN_RW,
    )
    admin_tool_manager = ToolManager(config=admin_config)
    await admin_tool_manager.__aenter__()
    try:
        await setup_test_tables(admin_tool_manager.sql_driver)
    finally:
        await admin_tool_manager.__aexit__(None, None, None)

    # Now test with user mode and table_prefix
    config = DatabaseConfig(
        database_uri=SecretStr(connection_string),
        access_mode=AccessMode.USER_RO,
        table_prefix="app_",
    )

    tool_manager = ToolManager(config=config)
    await tool_manager.__aenter__()

    try:
        sql_driver = tool_manager.sql_driver
        # Check that we have the correct driver type (validates API contract)
        assert isinstance(sql_driver, SafeSqlDriver)

        # Query to non-prefixed table should be blocked
        # We only check that it raises ValueError, not the exact message
        query = "SELECT * FROM other_users LIMIT 1"
        with pytest.raises(ValueError):
            await sql_driver.execute_query(query)

        # Query to test_users (without prefix) should also be blocked
        query2 = "SELECT * FROM test_users LIMIT 1"
        with pytest.raises(ValueError):
            await sql_driver.execute_query(query2)

        # Verify that prefixed tables still work
        query3 = "SELECT * FROM app_users LIMIT 1"
        result = await sql_driver.execute_query(query3)
        assert result is not None

    finally:
        await tool_manager.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_table_prefix_is_case_insensitive(test_postgres_connection_string: tuple[str, str]) -> None:
    """Test that table prefix matching is case-insensitive."""
    connection_string, _ = test_postgres_connection_string

    # Setup tables
    admin_config = DatabaseConfig(
        database_uri=SecretStr(connection_string),
        access_mode=AccessMode.ADMIN_RW,
    )
    admin_tool_manager = ToolManager(config=admin_config)
    await admin_tool_manager.__aenter__()
    try:
        await setup_test_tables(admin_tool_manager.sql_driver)
        # Create table with uppercase prefix
        await admin_tool_manager.sql_driver.execute_query("CREATE TABLE IF NOT EXISTS APP_UPPER_TABLE (id INTEGER)")
    finally:
        await admin_tool_manager.__aexit__(None, None, None)

    # Test with lowercase prefix - should match uppercase table
    config = DatabaseConfig(
        database_uri=SecretStr(connection_string),
        access_mode=AccessMode.USER_RO,
        table_prefix="app_",
    )

    tool_manager = ToolManager(config=config)
    await tool_manager.__aenter__()

    try:
        sql_driver = tool_manager.sql_driver
        # Table with uppercase prefix should be accessible
        query = "SELECT * FROM APP_UPPER_TABLE LIMIT 1"
        result = await sql_driver.execute_query(query)
        assert result is not None

    finally:
        await tool_manager.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_table_prefix_blocks_system_schemas(test_postgres_connection_string: tuple[str, str]) -> None:
    """Test that system schemas are blocked in user mode with table_prefix."""
    connection_string, _ = test_postgres_connection_string

    # Create config with table_prefix
    config = DatabaseConfig(
        database_uri=SecretStr(connection_string),
        access_mode=AccessMode.USER_RO,
        table_prefix="app_",
    )

    tool_manager = ToolManager(config=config)
    await tool_manager.__aenter__()

    try:
        sql_driver = tool_manager.sql_driver
        # Check that we have the correct driver type (validates API contract)
        assert isinstance(sql_driver, SafeSqlDriver)

        # Query to pg_catalog should be blocked
        # We only check that it raises ValueError, not the exact message
        query = "SELECT * FROM pg_catalog.pg_class LIMIT 1"
        with pytest.raises(ValueError):
            await sql_driver.execute_query(query)

        # Query to information_schema should be allowed for metadata (needed for list_objects)
        # But we can't query arbitrary tables from it - only metadata queries are allowed
        # This is tested indirectly through list_objects working correctly

    finally:
        await tool_manager.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_list_objects_filters_by_prefix(test_postgres_connection_string: tuple[str, str]) -> None:
    """Test that list_objects only returns objects with prefix."""
    connection_string, _ = test_postgres_connection_string

    # First, setup tables using admin connection
    admin_config = DatabaseConfig(
        database_uri=SecretStr(connection_string),
        access_mode=AccessMode.ADMIN_RW,
    )
    admin_tool_manager = ToolManager(config=admin_config)
    await admin_tool_manager.__aenter__()
    try:
        await setup_test_tables(admin_tool_manager.sql_driver)
    finally:
        await admin_tool_manager.__aexit__(None, None, None)

    # Now test with user mode and table_prefix
    config = DatabaseConfig(
        database_uri=SecretStr(connection_string),
        access_mode=AccessMode.USER_RO,
        table_prefix="app_",
    )

    tool_manager = ToolManager(config=config)
    await tool_manager.__aenter__()

    try:
        # List tables - should only return prefixed tables
        tables = await tool_manager.list_objects(schema_name="public", object_type="table")
        assert isinstance(tables, list)

        # Extract table names
        table_names = [table["name"] for table in tables if isinstance(table, dict) and "name" in table]

        # Should contain prefixed tables (at least the ones we created)
        assert "app_users" in table_names, "app_users should be visible"
        assert "app_orders" in table_names, "app_orders should be visible"

        # Should NOT contain non-prefixed tables
        assert "other_users" not in table_names
        assert "test_users" not in table_names

        # Verify that all returned tables actually have the prefix
        for table_name in table_names:
            assert table_name.lower().startswith("app_"), f"Table {table_name} should have prefix 'app_'"

    finally:
        await tool_manager.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_table_prefix_ignored_in_admin_mode(test_postgres_connection_string: tuple[str, str]) -> None:
    """Test that table_prefix is ignored in admin modes."""
    connection_string, _ = test_postgres_connection_string

    # First, setup tables using admin connection
    admin_config = DatabaseConfig(
        database_uri=SecretStr(connection_string),
        access_mode=AccessMode.ADMIN_RW,
    )
    admin_tool_manager = ToolManager(config=admin_config)
    await admin_tool_manager.__aenter__()
    try:
        await setup_test_tables(admin_tool_manager.sql_driver)
    finally:
        await admin_tool_manager.__aexit__(None, None, None)

    # Create config with table_prefix but admin mode
    config = DatabaseConfig(
        database_uri=SecretStr(connection_string),
        access_mode=AccessMode.ADMIN_RO,
        table_prefix="app_",  # Should be ignored
    )

    tool_manager = ToolManager(config=config)
    await tool_manager.__aenter__()

    try:
        sql_driver = tool_manager.sql_driver
        # Check that we have the correct driver type (validates API contract)
        assert isinstance(sql_driver, SafeSqlDriver)

        # In admin mode, table_prefix should be ignored - we test behavior, not implementation
        # Query to non-prefixed table should work in admin mode (prefix is ignored)
        query = "SELECT * FROM test_users LIMIT 1"
        result = await sql_driver.execute_query(query)
        assert result is not None

        # Also test that prefixed tables work (prefix doesn't restrict in admin mode)
        query2 = "SELECT * FROM app_users LIMIT 1"
        result2 = await sql_driver.execute_query(query2)
        assert result2 is not None

    finally:
        await tool_manager.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_list_schemas_returns_only_public_in_user_mode(test_postgres_connection_string: tuple[str, str]) -> None:
    """Test that list_schemas returns only public schema in user mode."""
    connection_string, _ = test_postgres_connection_string

    # Create config with table_prefix
    config = DatabaseConfig(
        database_uri=SecretStr(connection_string),
        access_mode=AccessMode.USER_RO,
        table_prefix="app_",
    )

    tool_manager = ToolManager(config=config)
    await tool_manager.__aenter__()

    try:
        # List schemas - should only return public
        schemas = await tool_manager.list_schemas()
        assert isinstance(schemas, list)
        assert len(schemas) == 1
        assert schemas[0]["schema_name"] == "public"

        # Verify no system schemas are returned
        schema_names = [s["schema_name"] for s in schemas if isinstance(s, dict) and "schema_name" in s]
        assert "pg_catalog" not in schema_names
        assert "information_schema" not in schema_names

    finally:
        await tool_manager.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_table_prefix_with_different_prefixes(test_postgres_connection_string: tuple[str, str]) -> None:
    """Test that different prefixes work correctly."""
    connection_string, _ = test_postgres_connection_string

    # Setup tables with different prefixes
    admin_config = DatabaseConfig(
        database_uri=SecretStr(connection_string),
        access_mode=AccessMode.ADMIN_RW,
    )
    admin_tool_manager = ToolManager(config=admin_config)
    await admin_tool_manager.__aenter__()
    try:
        await admin_tool_manager.sql_driver.execute_query("CREATE TABLE IF NOT EXISTS user_data (id INTEGER)")
        await admin_tool_manager.sql_driver.execute_query("CREATE TABLE IF NOT EXISTS user_settings (id INTEGER)")
        await admin_tool_manager.sql_driver.execute_query("CREATE TABLE IF NOT EXISTS admin_logs (id INTEGER)")
    finally:
        await admin_tool_manager.__aexit__(None, None, None)

    # Test with "user_" prefix
    config = DatabaseConfig(
        database_uri=SecretStr(connection_string),
        access_mode=AccessMode.USER_RO,
        table_prefix="user_",
    )

    tool_manager = ToolManager(config=config)
    await tool_manager.__aenter__()

    try:
        sql_driver = tool_manager.sql_driver

        # Tables with "user_" prefix should be accessible
        query1 = "SELECT * FROM user_data LIMIT 1"
        result1 = await sql_driver.execute_query(query1)
        assert result1 is not None

        query2 = "SELECT * FROM user_settings LIMIT 1"
        result2 = await sql_driver.execute_query(query2)
        assert result2 is not None

        # Table with "admin_" prefix should be blocked
        query3 = "SELECT * FROM admin_logs LIMIT 1"
        with pytest.raises(ValueError):
            await sql_driver.execute_query(query3)

    finally:
        await tool_manager.__aexit__(None, None, None)
