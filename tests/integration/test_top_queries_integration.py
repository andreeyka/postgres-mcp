# mypy: ignore-errors
"""Integration tests for top queries (postgres_fastmcp: DbAccess, top_queries)."""

import logging

import pytest

from postgres_fastmcp.domains.db_access import DbAccess
from postgres_fastmcp.domains.top_queries import PG_STAT_STATEMENTS, TopQueriesCalc, get_top_queries
from postgres_fastmcp.shared.errors import PgStatStatementsNotInstalledError


logger = logging.getLogger(__name__)


async def setup_test_data(db: DbAccess) -> None:
    """Ensure pg_stat_statements is available, create test table and run sample queries."""
    sql = db.sql_driver

    rows = await sql.execute(
        "SELECT 1 FROM pg_available_extensions WHERE name = 'pg_stat_statements'",
        readonly=True,
    )
    if not rows or len(rows) == 0:
        pytest.skip("pg_stat_statements extension not available")

    try:
        await sql.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements", readonly=False)
    except Exception as e:
        logger.warning("Could not create pg_stat_statements: %s", e)
        pytest.skip("pg_stat_statements extension not available or cannot be created")

    await sql.execute(
        "DROP TABLE IF EXISTS test_items; CREATE TABLE test_items (id SERIAL PRIMARY KEY, name TEXT NOT NULL, value INTEGER NOT NULL)",
        readonly=False,
    )
    await sql.execute(
        """
        INSERT INTO test_items (name, value)
        SELECT 'Item ' || i, (random() * 1000)::INTEGER
        FROM generate_series(1, 1000) i
        """,
        readonly=False,
    )
    await sql.execute("SELECT pg_stat_statements_reset()", readonly=False)

    for _ in range(10):
        await sql.execute("SELECT COUNT(*) FROM test_items", readonly=True)
    for _ in range(5):
        await sql.execute(
            "SELECT name, value FROM test_items WHERE value > 500 ORDER BY value DESC",
            readonly=True,
        )
    for _ in range(10):
        await sql.execute(
            "SELECT t1.name, t2.name FROM test_items t1 CROSS JOIN test_items t2 WHERE t1.value > t2.value LIMIT 100",
            readonly=True,
        )


async def cleanup_test_data(db: DbAccess) -> None:
    """Drop test table and reset pg_stat_statements."""
    try:
        await db.sql_driver.execute("DROP TABLE IF EXISTS test_items", readonly=False)
        await db.sql_driver.execute("SELECT pg_stat_statements_reset()", readonly=False)
    except Exception as e:
        logger.warning("Cleanup error: %s", e)


@pytest.mark.asyncio
async def test_get_top_queries_integration(db_full: DbAccess) -> None:
    """Integration test for get_top_queries with real database and pg_stat_statements."""
    try:
        await setup_test_data(db_full)

        pg_stats = await db_full.sql_driver.execute(
            "SELECT query FROM pg_stat_statements WHERE query LIKE '%CROSS JOIN%' LIMIT 1",
            readonly=True,
        )
        if not pg_stats or len(pg_stats) == 0:
            pytest.skip("pg_stat_statements did not capture the CROSS JOIN query")

        total_rows = await get_top_queries(db_full, sort_by="total_time", limit=10)
        mean_rows = await get_top_queries(db_full, sort_by="mean_time", limit=10)
        resource_rows = await get_top_queries(db_full, sort_by="resources", limit=2)

        assert 0 < len(total_rows) <= 10
        assert 0 < len(mean_rows) <= 10
        assert len(resource_rows) <= 2
        assert {"query", "calls", "rows"} <= set(total_rows[0])

        total_text = " ".join(row["query"] for row in total_rows)
        has_cross_join = "CROSS JOIN" in total_text
        has_value_gt_500 = "value > 500" in total_text
        has_count = "COUNT(*)" in total_text
        assert has_cross_join or has_value_gt_500 or has_count, "None of our test queries appeared in the results"
    finally:
        await cleanup_test_data(db_full)


@pytest.mark.asyncio
async def test_extension_not_available(db_full: DbAccess) -> None:
    """When pg_stat_statements is not installed, a user-facing error carries installation instructions."""
    from postgres_fastmcp.postgres.extensions import ExtensionStatus

    calc = TopQueriesCalc(
        sql_driver=db_full.sql_driver,
        catalog_driver=db_full.catalog_driver,
        connection_id=db_full.connection_id,
    )
    not_installed_status = ExtensionStatus(
        is_installed=False,
        is_available=True,
        name=PG_STAT_STATEMENTS,
        message="Extension not installed",
        default_version=None,
    )

    async def mock_check_extension(*args: object, **kwargs: object) -> ExtensionStatus:
        return not_installed_status

    calc._ext_inspector.check_extension = mock_check_extension  # type: ignore[method-assign]
    with pytest.raises(PgStatStatementsNotInstalledError, match="CREATE EXTENSION pg_stat_statements"):
        await calc.get_top_queries_by_time()
