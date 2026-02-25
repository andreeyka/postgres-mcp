# mypy: ignore-errors
"""Integration tests for table_prefix (postgres_fastmcp: DbAccessService, AccessMode, SafeSqlExecutor)."""

import pytest

from postgres_fastmcp.config.database import DatabaseConfig
from postgres_fastmcp.enums import AccessMode
from postgres_fastmcp.services.db_access_service import DbAccessService
from postgres_fastmcp.services.objects.service import ObjectsService
from postgres_fastmcp.services.schema.service import SchemaService
from postgres_fastmcp.sql.security.driver import SafeSqlExecutor


async def setup_test_tables(driver: DbAccessService) -> None:
    """Create test tables with and without prefix using full-access executor."""
    sql = driver.sql_driver

    await sql.execute(
        """
        CREATE TABLE IF NOT EXISTS app_users (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            email VARCHAR(255) UNIQUE NOT NULL
        )
        """,
        readonly=False,
    )
    await sql.execute(
        """
        CREATE TABLE IF NOT EXISTS app_orders (
            id SERIAL PRIMARY KEY,
            user_id INTEGER,
            amount DECIMAL(10,2) NOT NULL
        )
        """,
        readonly=False,
    )
    await sql.execute(
        """
        CREATE TABLE IF NOT EXISTS other_users (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL
        )
        """,
        readonly=False,
    )
    await sql.execute(
        """
        CREATE TABLE IF NOT EXISTS test_users (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL
        )
        """,
        readonly=False,
    )
    await sql.execute(
        "INSERT INTO app_users (name, email) VALUES ('App User 1', 'app1@test.com') ON CONFLICT DO NOTHING",
        readonly=False,
    )
    await sql.execute(
        "INSERT INTO other_users (name) VALUES ('Other User 1') ON CONFLICT DO NOTHING",
        readonly=False,
    )
    await sql.execute(
        "INSERT INTO test_users (name) VALUES ('Test User 1') ON CONFLICT DO NOTHING",
        readonly=False,
    )


@pytest.mark.asyncio
async def test_table_prefix_allows_prefixed_tables(
    db_service_full: DbAccessService,
    db_service_user_prefix: DbAccessService,
) -> None:
    """Tables with prefix are accessible when table_prefix is set."""
    await setup_test_tables(db_service_full)

    sql_driver = db_service_user_prefix.sql_driver
    assert isinstance(sql_driver, SafeSqlExecutor)

    result = await sql_driver.execute("SELECT * FROM app_users LIMIT 1", readonly=True)
    assert result is not None
    assert len(result) > 0
    assert "name" in result[0].cells or "email" in result[0].cells

    result2 = await sql_driver.execute("SELECT COUNT(*) as cnt FROM app_orders", readonly=True)
    assert result2 is not None
    assert len(result2) > 0


@pytest.mark.asyncio
async def test_table_prefix_blocks_non_prefixed_tables(
    db_service_full: DbAccessService,
    db_service_user_prefix: DbAccessService,
) -> None:
    """Tables without prefix are blocked when table_prefix is set."""
    await setup_test_tables(db_service_full)

    sql_driver = db_service_user_prefix.sql_driver
    assert isinstance(sql_driver, SafeSqlExecutor)

    with pytest.raises(ValueError):
        await sql_driver.execute("SELECT * FROM other_users LIMIT 1", readonly=True)

    with pytest.raises(ValueError):
        await sql_driver.execute("SELECT * FROM test_users LIMIT 1", readonly=True)

    result = await sql_driver.execute("SELECT * FROM app_users LIMIT 1", readonly=True)
    assert result is not None


@pytest.mark.asyncio
async def test_table_prefix_is_case_insensitive(
    db_service_full: DbAccessService,
    db_service_user_prefix: DbAccessService,
) -> None:
    """Table prefix matching is case-insensitive (PG lowercases unquoted identifiers)."""
    await setup_test_tables(db_service_full)
    await db_service_full.sql_driver.execute(
        "CREATE TABLE IF NOT EXISTS APP_UPPER_TABLE (id INTEGER)",
        readonly=False,
    )

    sql_driver = db_service_user_prefix.sql_driver
    result = await sql_driver.execute("SELECT * FROM APP_UPPER_TABLE LIMIT 1", readonly=True)
    assert result is not None


@pytest.mark.asyncio
async def test_table_prefix_blocks_system_schemas(db_service_user_prefix: DbAccessService) -> None:
    """System schemas are blocked in user mode with table_prefix."""
    sql_driver = db_service_user_prefix.sql_driver
    assert isinstance(sql_driver, SafeSqlExecutor)

    with pytest.raises(ValueError):
        await sql_driver.execute("SELECT * FROM pg_catalog.pg_class LIMIT 1", readonly=True)


@pytest.mark.asyncio
async def test_list_objects_filters_by_prefix(
    db_service_full: DbAccessService,
    db_service_user_prefix: DbAccessService,
) -> None:
    """list_objects returns only objects with prefix."""
    await setup_test_tables(db_service_full)

    objects_service = ObjectsService(db_service_user_prefix)
    tables = await objects_service.list_objects(schema_name="public", object_type="table")
    assert isinstance(tables, list)

    table_names = [t["name"] for t in tables]
    assert "app_users" in table_names
    assert "app_orders" in table_names
    assert "other_users" not in table_names
    assert "test_users" not in table_names
    for name in table_names:
        assert name.lower().startswith("app_"), f"Table {name} should have prefix 'app_'"


@pytest.mark.asyncio
async def test_table_prefix_ignored_in_full_access_mode(
    db_service_full: DbAccessService,
) -> None:
    """table_prefix is ignored for access_mode=full (full executor, no prefix filter)."""
    await setup_test_tables(db_service_full)

    sql_driver = db_service_full.sql_driver
    result = await sql_driver.execute("SELECT * FROM test_users LIMIT 1", readonly=True)
    assert result is not None

    result2 = await sql_driver.execute("SELECT * FROM app_users LIMIT 1", readonly=True)
    assert result2 is not None


@pytest.mark.asyncio
async def test_list_schemas_returns_only_public_in_user_mode(
    db_service_user_prefix: DbAccessService,
) -> None:
    """list_schemas returns only public schema in user mode."""
    schema_service = SchemaService(db_service_user_prefix)
    schemas = await schema_service.list_schemas()
    assert isinstance(schemas, list)
    assert len(schemas) == 1
    assert schemas[0]["schema_name"] == "public"

    schema_names = [s["schema_name"] for s in schemas]
    assert "pg_catalog" not in schema_names
    assert "information_schema" not in schema_names


@pytest.mark.asyncio
async def test_table_prefix_with_different_prefixes(
    test_postgres_connection_string: tuple[str, str],
) -> None:
    """Different table_prefix values work correctly."""
    connection_string, _ = test_postgres_connection_string
    full_config = DatabaseConfig.from_uri(
        connection_string,
        access_mode=AccessMode.FULL,
        write_mode=True,
    )
    user_config = DatabaseConfig.from_uri(
        connection_string,
        access_mode=AccessMode.BASIC,
        write_mode=False,
        table_prefix="user_",
    )

    full_svc = DbAccessService(full_config)
    try:
        await full_svc.sql_driver.execute(
            "CREATE TABLE IF NOT EXISTS user_data (id INTEGER)",
            readonly=False,
        )
        await full_svc.sql_driver.execute(
            "CREATE TABLE IF NOT EXISTS user_settings (id INTEGER)",
            readonly=False,
        )
        await full_svc.sql_driver.execute(
            "CREATE TABLE IF NOT EXISTS admin_logs (id INTEGER)",
            readonly=False,
        )
    finally:
        await full_svc.close()

    user_svc = DbAccessService(user_config)
    try:
        sql_driver = user_svc.sql_driver

        result1 = await sql_driver.execute("SELECT * FROM user_data LIMIT 1", readonly=True)
        assert result1 is not None

        result2 = await sql_driver.execute("SELECT * FROM user_settings LIMIT 1", readonly=True)
        assert result2 is not None

        with pytest.raises(ValueError):
            await sql_driver.execute("SELECT * FROM admin_logs LIMIT 1", readonly=True)
    finally:
        await user_svc.close()
