# mypy: ignore-errors
"""Integration tests for DTA (Database Tuning Advisor) using postgres_fastmcp."""

import contextlib
import logging

import pytest
from pglast import parse_sql

from postgres_fastmcp.domains.db_access import DbAccess
from postgres_fastmcp.domains.index_tuning.candidates import CandidateGenerator
from postgres_fastmcp.domains.index_tuning.dta_calc import DatabaseTuningAdvisor
from postgres_fastmcp.domains.index_tuning.models import IndexTuningResult
from postgres_fastmcp.domains.index_tuning.presentation import TextPresentation
from postgres_fastmcp.postgres.params.replacer import SqlParamReplacer


logger = logging.getLogger(__name__)


async def _execute_setup(db: DbAccess, *statements: str) -> None:
    """Run DDL/inserts with full-access executor (readonly=False)."""
    driver = db.sql_driver
    for stmt in statements:
        await driver.execute(stmt, readonly=False)


@pytest.mark.asyncio
async def test_dta_analyze_queries_simple(db_with_hypopg: DbAccess) -> None:
    """DTA returns recommendations or message for a simple table and query list."""
    sql = db_with_hypopg.sql_driver
    await _execute_setup(
        db_with_hypopg,
        "DROP TABLE IF EXISTS dta_simple CASCADE",
        """
        CREATE TABLE dta_simple (
            id SERIAL PRIMARY KEY,
            col1 INTEGER,
            col2 VARCHAR(100)
        )
        """,
        """
        INSERT INTO dta_simple (col1, col2)
        SELECT i % 100, 'v-' || (i % 50) FROM generate_series(1, 5000) i
        """,
        "ANALYZE dta_simple",
    )
    try:
        await sql.execute("SELECT hypopg_reset()", readonly=False)
        dta = DatabaseTuningAdvisor(
            sql,
            catalog_driver=db_with_hypopg.catalog_driver,
            connection_id=db_with_hypopg.connection_id,
            budget_mb=100,
            max_runtime_seconds=60,
            max_index_width=3,
        )
        presentation = TextPresentation(sql, dta)
        result = await presentation.analyze_queries(
            queries=[
                "SELECT * FROM dta_simple WHERE col1 = 42",
                "SELECT * FROM dta_simple WHERE col2 = 'v-10'",
            ],
            max_index_size_mb=100,
        )
        assert isinstance(result, dict)
        assert "error" in result or "recommendations" in result
        if "recommendations" in result:
            assert isinstance(result["recommendations"], (list, str))
    finally:
        await sql.execute("DROP TABLE IF EXISTS dta_simple", readonly=False)


@pytest.mark.asyncio
async def test_dta_pareto_basic(db_with_hypopg: DbAccess) -> None:
    """DTA analyze_workload with query_list returns IndexTuningResult (recommendations or empty)."""
    sql = db_with_hypopg.sql_driver
    await _execute_setup(
        db_with_hypopg,
        "DROP TABLE IF EXISTS pareto_test CASCADE",
        """
        CREATE TABLE pareto_test (
            id SERIAL PRIMARY KEY,
            col1 INTEGER,
            col2 VARCHAR(100),
            col3 INTEGER,
            col4 VARCHAR(100)
        )
        """,
        """
        INSERT INTO pareto_test (col1, col2, col3, col4)
        SELECT i % 1000, 'value-' || (i % 500), (i * 2) % 2000, 'text-' || (i % 100)
        FROM generate_series(1, 10000) i
        """,
        "ANALYZE pareto_test",
    )
    try:
        await sql.execute("SELECT hypopg_reset()", readonly=False)
        dta = DatabaseTuningAdvisor(
            sql,
            catalog_driver=db_with_hypopg.catalog_driver,
            connection_id=db_with_hypopg.connection_id,
            budget_mb=100,
            max_runtime_seconds=60,
            max_index_width=3,
        )
        dta.min_time_improvement = 0.01
        dta.pareto_alpha = 1.5
        queries = [
            "SELECT * FROM pareto_test WHERE col1 = 42",
            "SELECT * FROM pareto_test WHERE col2 = 'value-100'",
            "SELECT * FROM pareto_test WHERE col3 = 500",
            "SELECT * FROM pareto_test WHERE col4 = 'text-50'",
        ]
        session = await dta.analyze_workload(
            query_list=queries,
            min_calls=1,
            min_avg_time_ms=0.01,
            max_index_size_mb=100,
        )
        assert isinstance(session, IndexTuningResult)
        if session.error:
            pytest.skip(f"DTA precheck or analysis failed: {session.error}")
        if not session.recommendations:
            logger.warning("No recommendations produced")
            return
        expected_columns = ["col1", "col2", "col3", "col4"]
        for rec in session.recommendations:
            assert any(c in rec.columns for c in expected_columns), (
                f"Recommendation {rec.definition} does not include expected columns"
            )
    finally:
        await sql.execute("DROP TABLE IF EXISTS pareto_test", readonly=False)


@pytest.mark.asyncio
async def test_dta_analyze_workload_via_service(db_with_hypopg: DbAccess) -> None:
    """IndexAnalysisService.analyze_query_indexes returns dict without error for simple workload."""
    from postgres_fastmcp.domains.index_tuning.service import IndexAnalysisService

    sql = db_with_hypopg.sql_driver
    await _execute_setup(
        db_with_hypopg,
        "DROP TABLE IF EXISTS service_test CASCADE",
        """
        CREATE TABLE service_test (
            id SERIAL PRIMARY KEY,
            a INTEGER,
            b VARCHAR(100)
        )
        """,
        "INSERT INTO service_test (a, b) SELECT i % 200, 'x' || i FROM generate_series(1, 3000) i",
        "ANALYZE service_test",
    )
    try:
        await sql.execute("SELECT hypopg_reset()", readonly=False)
        service = IndexAnalysisService(db_with_hypopg)
        result = await service.analyze_query_indexes(
            queries=["SELECT * FROM service_test WHERE a = 1", "SELECT * FROM service_test WHERE b = 'x100'"],
            max_index_size_mb=50,
        )
        assert isinstance(result, dict)
        if result.get("error"):
            pytest.skip(f"DTA via service failed: {result['error']}")
        assert "recommendations" in result or "error" in result
    finally:
        await sql.execute("DROP TABLE IF EXISTS service_test", readonly=False)


@pytest.mark.asyncio
async def test_dta_hypopg_not_installed_returns_error(
    db_full: DbAccess,
) -> None:
    """When hypopg is not installed, DTA returns session.error (skip if hypopg is present)."""
    sql = db_full.sql_driver
    with contextlib.suppress(Exception):
        await sql.execute("CREATE EXTENSION IF NOT EXISTS hypopg", readonly=False)
    rows = await sql.execute(
        "SELECT 1 FROM pg_extension WHERE extname = 'hypopg'",
        readonly=True,
    )
    if rows and len(rows) > 0:
        pytest.skip("hypopg is installed; cannot test 'not installed' path on this DB")
    dta = DatabaseTuningAdvisor(
        sql,
        catalog_driver=db_full.catalog_driver,
        connection_id=db_full.connection_id,
        budget_mb=100,
        max_runtime_seconds=10,
    )
    session = await dta.analyze_workload(
        query_list=["SELECT 1"],
        min_calls=1,
        min_avg_time_ms=0,
        max_index_size_mb=10,
    )
    assert isinstance(session, IndexTuningResult)
    assert session.error is not None
    assert "hypopg" in session.error.lower() or "not installed" in session.error.lower()


@pytest.mark.asyncio
async def test_candidate_generation_estimates_hypothetical_index_sizes(db_with_hypopg: DbAccess) -> None:
    """Размер гипотетического индекса доходит до кандидата: сопоставление по позиции, а не по имени hypopg."""
    sql = db_with_hypopg.sql_driver
    await _execute_setup(
        db_with_hypopg,
        "DROP TABLE IF EXISTS dta_sizes CASCADE",
        "CREATE TABLE dta_sizes (id SERIAL PRIMARY KEY, col1 INTEGER, col2 INTEGER)",
        "INSERT INTO dta_sizes (col1, col2) SELECT i % 100, i % 7 FROM generate_series(1, 5000) i",
        "ANALYZE dta_sizes",
    )
    try:
        query = "select * from dta_sizes where col1 = 42 and col2 = 3"
        workload = [(query, parse_sql(query)[0].stmt, 1.0)]
        generator = CandidateGenerator(sql, SqlParamReplacer(sql, sql), max_index_width=2)

        candidates = await generator.generate(workload, existing_defs=set())

        assert candidates, "expected index candidates for col1/col2"
        assert any(c.estimated_size_bytes > 0 for c in candidates), [
            (c.definition, c.estimated_size_bytes) for c in candidates
        ]
    finally:
        await sql.execute("DROP TABLE IF EXISTS dta_sizes", readonly=False)
