# mypy: ignore-errors
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

import postgres_mcp.top_queries.top_queries_calc as top_queries_module
from postgres_mcp.sql import SqlDriver
from postgres_mcp.sql.extension_utils import ExtensionStatus
from postgres_mcp.top_queries import TopQueriesCalc


class MockSqlRowResult:
    def __init__(self, cells):
        self.cells = cells


# Fixtures for different PostgreSQL versions
@pytest.fixture
def mock_pg12_driver():
    """Create a mock for SqlDriver that simulates PostgreSQL 12."""
    driver = MagicMock(spec=SqlDriver)

    # Set up the version mock directly on the mock driver
    with patch.object(top_queries_module, "get_postgres_version", autospec=True) as mock_version:
        mock_version.return_value = 12

        # Create async mock for execute_query
        mock_execute = AsyncMock()

        # Configure the mock to return different results based on the query
        async def side_effect(query, *args, **kwargs):
            if "pg_stat_statements" in query:
                # Return data in PG 12 format with total_time and mean_time columns
                return [
                    MockSqlRowResult(
                        cells={
                            "query": "SELECT * FROM users",
                            "calls": 100,
                            "total_time": 1000.0,
                            "mean_time": 10.0,
                            "rows": 1000,
                        }
                    ),
                    MockSqlRowResult(
                        cells={
                            "query": "SELECT * FROM orders",
                            "calls": 50,
                            "total_time": 750.0,
                            "mean_time": 15.0,
                            "rows": 500,
                        }
                    ),
                    MockSqlRowResult(
                        cells={
                            "query": "SELECT * FROM products",
                            "calls": 200,
                            "total_time": 500.0,
                            "mean_time": 2.5,
                            "rows": 2000,
                        }
                    ),
                ]
            return None

        mock_execute.side_effect = side_effect
        driver.execute_query = mock_execute

        yield driver


@pytest.fixture
def mock_pg13_driver():
    """Create a mock for SqlDriver that simulates PostgreSQL 13."""
    driver = MagicMock(spec=SqlDriver)

    # Set up the version mock directly on the mock driver
    with patch.object(top_queries_module, "get_postgres_version", autospec=True) as mock_version:
        mock_version.return_value = 13

        # Create async mock for execute_query
        mock_execute = AsyncMock()

        # Configure the mock to return different results based on the query
        async def side_effect(query, *args, **kwargs):
            if "pg_stat_statements" in query:
                # Return data in PG 13+ format with total_exec_time and mean_exec_time columns
                return [
                    MockSqlRowResult(
                        cells={
                            "query": "SELECT * FROM users",
                            "calls": 100,
                            "total_exec_time": 1000.0,
                            "mean_exec_time": 10.0,
                            "rows": 1000,
                        }
                    ),
                    MockSqlRowResult(
                        cells={
                            "query": "SELECT * FROM orders",
                            "calls": 50,
                            "total_exec_time": 750.0,
                            "mean_exec_time": 15.0,
                            "rows": 500,
                        }
                    ),
                    MockSqlRowResult(
                        cells={
                            "query": "SELECT * FROM products",
                            "calls": 200,
                            "total_exec_time": 500.0,
                            "mean_exec_time": 2.5,
                            "rows": 2000,
                        }
                    ),
                ]
            return None

        mock_execute.side_effect = side_effect
        driver.execute_query = mock_execute

        yield driver


# Patch check_extension to return different extension statuses
@pytest.fixture
def mock_extension_installed():
    """Mock check_extension to report extension is installed."""
    with patch.object(top_queries_module, "check_extension", autospec=True) as mock_check:
        mock_check.return_value = ExtensionStatus(
            is_installed=True,
            is_available=True,
            name="pg_stat_statements",
            message="Extension is installed",
            default_version="1.0",
        )
        yield mock_check


@pytest.fixture
def mock_extension_not_installed():
    """Mock check_extension to report extension is not installed."""
    with patch.object(top_queries_module, "check_extension", autospec=True) as mock_check:
        mock_check.return_value = ExtensionStatus(
            is_installed=False,
            is_available=True,
            name="pg_stat_statements",
            message="Extension not installed",
            default_version=None,
        )
        yield mock_check


@pytest_asyncio.fixture
async def real_db_driver(test_postgres_connection_string):
    """Create a real database driver for testing."""
    connection_string, version = test_postgres_connection_string
    driver = SqlDriver(engine_url=connection_string)

    # Verify connection
    result = await driver.execute_query("SELECT 1")
    assert result is not None

    # Create pg_stat_statements extension if needed
    try:
        await driver.execute_query("CREATE EXTENSION IF NOT EXISTS pg_stat_statements", force_readonly=False)
    except Exception as e:
        pytest.skip(f"pg_stat_statements extension is not available: {e}")

    yield driver

    # Cleanup
    if hasattr(driver, "conn") and driver.conn is not None:
        await driver.conn.close()


@pytest_asyncio.fixture
async def setup_top_queries_test_data(real_db_driver):
    """Set up test data for top queries tests."""
    driver = real_db_driver

    # Create test tables
    await driver.execute_query(
        """
        DROP TABLE IF EXISTS test_users;
        DROP TABLE IF EXISTS test_orders;
        DROP TABLE IF EXISTS test_products;
        
        CREATE TABLE test_users (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100),
            email VARCHAR(100)
        );
        
        CREATE TABLE test_orders (
            id SERIAL PRIMARY KEY,
            user_id INTEGER,
            amount DECIMAL(10,2)
        );
        
        CREATE TABLE test_products (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100),
            price DECIMAL(10,2)
        );
        """,
        force_readonly=False,
    )

    # Insert test data
    await driver.execute_query(
        """
        INSERT INTO test_users (name, email)
        SELECT 'User ' || i, 'user' || i || '@example.com'
        FROM generate_series(1, 1000) i;
        
        INSERT INTO test_orders (user_id, amount)
        SELECT (i % 1000) + 1, (i % 1000)::decimal
        FROM generate_series(1, 5000) i;
        
        INSERT INTO test_products (name, price)
        SELECT 'Product ' || i, (i % 100)::decimal
        FROM generate_series(1, 2000) i;
        """,
        force_readonly=False,
    )

    # Reset pg_stat_statements to ensure clean data
    await driver.execute_query("SELECT pg_stat_statements_reset()", force_readonly=False)

    # Execute queries multiple times to populate pg_stat_statements
    # Query 1: Simple select (should be fast, many calls)
    for _ in range(100):
        await driver.execute_query("SELECT * FROM test_users")

    # Query 2: More complex query (should be slower, fewer calls)
    for _ in range(50):
        await driver.execute_query("SELECT * FROM test_orders")

    # Query 3: Very slow query (should be slowest, many calls)
    for _ in range(200):
        await driver.execute_query("SELECT * FROM test_products")

    yield

    # Cleanup
    await driver.execute_query("DROP TABLE IF EXISTS test_products", force_readonly=False)
    await driver.execute_query("DROP TABLE IF EXISTS test_orders", force_readonly=False)
    await driver.execute_query("DROP TABLE IF EXISTS test_users", force_readonly=False)


@pytest.mark.asyncio
async def test_top_queries_total_sort(real_db_driver, setup_top_queries_test_data):
    """Test top queries calculation sorted by total execution time with real database."""
    calc = TopQueriesCalc(sql_driver=real_db_driver)

    # Get top queries sorted by total time
    result = await calc.get_top_queries_by_time(limit=3, sort_by="total")

    # Check that the result contains the expected information
    assert "Top 3 slowest queries by total execution time" in result
    # Should contain at least one of our test queries
    assert "test_users" in result or "test_orders" in result or "test_products" in result


@pytest.mark.asyncio
async def test_top_queries_mean_sort(real_db_driver, setup_top_queries_test_data):
    """Test top queries calculation sorted by mean execution time with real database."""
    calc = TopQueriesCalc(sql_driver=real_db_driver)

    # Get top queries sorted by mean time
    result = await calc.get_top_queries_by_time(limit=3, sort_by="mean")

    # Check that the result contains the expected information
    assert "Top 3 slowest queries by mean execution time per call" in result
    # Should contain at least one of our test queries
    assert "test_users" in result or "test_orders" in result or "test_products" in result


@pytest.mark.asyncio
async def test_extension_not_installed(mock_pg13_driver, mock_extension_not_installed):
    """Test behavior when pg_stat_statements extension is not installed."""
    # Create the TopQueriesCalc instance with the mock driver
    calc = TopQueriesCalc(sql_driver=mock_pg13_driver)

    # Try to get top queries when extension is not installed
    result = await calc.get_top_queries_by_time(limit=3)

    # Check that the result contains the installation instructions
    assert "extension is required to report" in result
    assert "CREATE EXTENSION" in result

    # Verify that execute_query was not called (since extension is not installed)
    mock_pg13_driver.execute_query.assert_not_called()


@pytest.mark.asyncio
async def test_error_handling(mock_pg13_driver, mock_extension_installed):
    """Test error handling in the TopQueriesCalc class."""
    # Configure execute_query to raise an exception
    mock_pg13_driver.execute_query.side_effect = Exception("Database error")

    # Create the TopQueriesCalc instance with the mock driver
    calc = TopQueriesCalc(sql_driver=mock_pg13_driver)

    # Try to get top queries
    result = await calc.get_top_queries_by_time(limit=3)

    # Check that the error is properly reported
    assert "Error getting slow queries: Database error" in result
