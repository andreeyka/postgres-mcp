# mypy: ignore-errors
"""Integration tests for table_prefix (postgres_fastmcp: DbAccess, AccessMode, SafeSqlExecutor)."""

import logging

import pytest

from postgres_fastmcp.access import EffectiveAccess
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.domains.catalog.service import CatalogService
from postgres_fastmcp.domains.db_access import DbAccess, DbAccessService
from postgres_fastmcp.domains.explain.service import ExplainService
from postgres_fastmcp.postgres.security.driver import SafeSqlExecutor
from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.shared.errors import ObjectNotFoundError, SchemaNotAllowedError, TablePrefixAccessError


logger = logging.getLogger(__name__)


async def setup_test_tables(driver: DbAccess) -> None:
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


async def setup_catalog_objects(driver: DbAccess) -> None:
    """Неуникальный индекс и представление с префиксом для проверки деталей каталога."""
    await driver.sql_driver.execute(
        "CREATE INDEX IF NOT EXISTS app_orders_user_id_idx ON app_orders (user_id)", readonly=False
    )
    await driver.sql_driver.execute(
        "CREATE OR REPLACE VIEW app_active_users AS SELECT id, name FROM app_users", readonly=False
    )


async def setup_shared_constraint_name_tables(driver: DbAccess) -> None:
    """Создать две таблицы в public с FK-ограничением, у которых совпадает имя constraint."""
    sql = driver.sql_driver

    await sql.execute(
        """
        CREATE TABLE IF NOT EXISTS app_shipments (
            id SERIAL PRIMARY KEY,
            buyer_id INTEGER,
            CONSTRAINT fk_owner FOREIGN KEY (buyer_id) REFERENCES app_users (id)
        )
        """,
        readonly=False,
    )
    await sql.execute(
        """
        CREATE TABLE IF NOT EXISTS shipments_x (
            id SERIAL PRIMARY KEY,
            owner_ref INTEGER,
            CONSTRAINT fk_owner FOREIGN KEY (owner_ref) REFERENCES other_users (id)
        )
        """,
        readonly=False,
    )


@pytest.mark.asyncio
async def test_table_prefix_allows_prefixed_tables(
    db_full: DbAccess,
    db_user_prefix: DbAccess,
) -> None:
    """Tables with prefix are accessible when table_prefix is set."""
    await setup_test_tables(db_full)

    sql_driver = db_user_prefix.sql_driver
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
    db_full: DbAccess,
    db_user_prefix: DbAccess,
) -> None:
    """Tables without prefix are blocked when table_prefix is set."""
    await setup_test_tables(db_full)

    sql_driver = db_user_prefix.sql_driver
    assert isinstance(sql_driver, SafeSqlExecutor)

    with pytest.raises(TablePrefixAccessError):
        await sql_driver.execute("SELECT * FROM other_users LIMIT 1", readonly=True)

    with pytest.raises(TablePrefixAccessError):
        await sql_driver.execute("SELECT * FROM test_users LIMIT 1", readonly=True)

    result = await sql_driver.execute("SELECT * FROM app_users LIMIT 1", readonly=True)
    assert result is not None


@pytest.mark.asyncio
async def test_table_prefix_is_case_insensitive(
    db_full: DbAccess,
    db_user_prefix: DbAccess,
) -> None:
    """Table prefix matching is case-insensitive (PG lowercases unquoted identifiers)."""
    await setup_test_tables(db_full)
    await db_full.sql_driver.execute(
        "CREATE TABLE IF NOT EXISTS APP_UPPER_TABLE (id INTEGER)",
        readonly=False,
    )

    sql_driver = db_user_prefix.sql_driver
    result = await sql_driver.execute("SELECT * FROM APP_UPPER_TABLE LIMIT 1", readonly=True)
    assert result is not None


@pytest.mark.asyncio
async def test_table_prefix_blocks_system_schemas(db_user_prefix: DbAccess) -> None:
    """System schemas are blocked in user mode with table_prefix."""
    sql_driver = db_user_prefix.sql_driver
    assert isinstance(sql_driver, SafeSqlExecutor)

    with pytest.raises(SchemaNotAllowedError):
        await sql_driver.execute("SELECT * FROM pg_catalog.pg_class LIMIT 1", readonly=True)


@pytest.mark.asyncio
async def test_list_objects_filters_by_prefix(
    db_full: DbAccess,
    db_user_prefix: DbAccess,
) -> None:
    """list_objects returns only objects with prefix."""
    await setup_test_tables(db_full)

    objects_service = CatalogService(db_user_prefix)
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
async def test_get_object_details_constraint_matched_by_table(
    db_full: DbAccess,
    db_user_prefix: DbAccess,
) -> None:
    """Constraint columns come from the requested table, not another table with the same constraint name."""
    await setup_test_tables(db_full)
    await setup_shared_constraint_name_tables(db_full)

    details = await CatalogService(db_user_prefix).get_object_details("public", "app_shipments", "table")

    constraints = {c["name"]: c for c in details["constraints"]}
    assert "fk_owner" in constraints
    assert constraints["fk_owner"]["columns"] == ["buyer_id"]


@pytest.mark.asyncio
async def test_table_prefix_ignored_in_full_access_mode(
    db_full: DbAccess,
) -> None:
    """table_prefix is ignored for access_mode=full (full executor, no prefix filter)."""
    await setup_test_tables(db_full)

    sql_driver = db_full.sql_driver
    result = await sql_driver.execute("SELECT * FROM test_users LIMIT 1", readonly=True)
    assert result is not None

    result2 = await sql_driver.execute("SELECT * FROM app_users LIMIT 1", readonly=True)
    assert result2 is not None


@pytest.mark.asyncio
async def test_list_schemas_returns_only_public_in_user_mode(
    db_user_prefix: DbAccess,
) -> None:
    """list_schemas returns only public schema in user mode."""
    schemas = await CatalogService(db_user_prefix).list_schemas()
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
        full_sql = full_svc.view(EffectiveAccess(AccessMode.FULL, write_mode=True)).sql_driver
        await full_sql.execute(
            "CREATE TABLE IF NOT EXISTS user_data (id INTEGER)",
            readonly=False,
        )
        await full_sql.execute(
            "CREATE TABLE IF NOT EXISTS user_settings (id INTEGER)",
            readonly=False,
        )
        await full_sql.execute(
            "CREATE TABLE IF NOT EXISTS admin_logs (id INTEGER)",
            readonly=False,
        )
    finally:
        await full_svc.close()

    user_svc = DbAccessService(user_config)
    try:
        sql_driver = user_svc.view(EffectiveAccess(AccessMode.BASIC, write_mode=False)).sql_driver

        result1 = await sql_driver.execute("SELECT * FROM user_data LIMIT 1", readonly=True)
        assert result1 is not None

        result2 = await sql_driver.execute("SELECT * FROM user_settings LIMIT 1", readonly=True)
        assert result2 is not None

        with pytest.raises(TablePrefixAccessError):
            await sql_driver.execute("SELECT * FROM admin_logs LIMIT 1", readonly=True)
    finally:
        await user_svc.close()


@pytest.mark.asyncio
async def test_get_object_details_shows_indexes_of_prefixed_tables(
    db_full: DbAccess,
    db_user_prefix: DbAccess,
) -> None:
    """BASIC + table_prefix: колонки, ограничения и все индексы, включая неуникальный."""
    await setup_test_tables(db_full)
    await setup_catalog_objects(db_full)
    catalog = CatalogService(db_user_prefix)

    users = await catalog.get_object_details("public", "app_users", "table")
    assert [c["column"] for c in users["columns"]] == ["id", "name", "email"]
    assert {"PRIMARY KEY", "UNIQUE"} <= {c["type"] for c in users["constraints"]}
    assert {"app_users_pkey", "app_users_email_key"} <= {i["name"] for i in users["indexes"]}

    orders = await catalog.get_object_details("public", "app_orders", "table")
    assert {"app_orders_pkey", "app_orders_user_id_idx"} <= {i["name"] for i in orders["indexes"]}

    view = await catalog.get_object_details("public", "app_active_users", "view")
    assert [c["column"] for c in view["columns"]] == ["id", "name"]
    assert view["indexes"] == []


@pytest.mark.asyncio
async def test_get_object_details_rejects_unprefixed_and_reports_missing(
    db_full: DbAccess,
    db_user_prefix: DbAccess,
) -> None:
    await setup_test_tables(db_full)
    catalog = CatalogService(db_user_prefix)

    with pytest.raises(TablePrefixAccessError, match="'other_users'"):
        await catalog.get_object_details("public", "other_users", "table")
    with pytest.raises(ObjectNotFoundError):
        await catalog.get_object_details("public", "app_ghost", "table")

    sequence = await catalog.get_object_details("public", "app_users_id_seq", "sequence")
    assert sequence["name"] == "app_users_id_seq"
    with pytest.raises(TablePrefixAccessError, match="'other_users_id_seq'"):
        await catalog.get_object_details("public", "other_users_id_seq", "sequence")


@pytest.mark.asyncio
async def test_extensions_are_listed_and_detailed_regardless_of_prefix(db_user_prefix: DbAccess) -> None:
    catalog = CatalogService(db_user_prefix)

    listed = await catalog.list_objects("public", "extension")
    assert "plpgsql" in {e["name"] for e in listed}

    details = await catalog.get_object_details("public", "plpgsql", "extension")
    assert details["name"] == "plpgsql"


@pytest.mark.asyncio
async def test_agent_sql_still_cannot_read_system_catalogs(db_user_prefix: DbAccess) -> None:
    """Путь каталога не открывает системные представления для execute_sql."""
    with pytest.raises(TablePrefixAccessError):
        await db_user_prefix.sql_driver.execute("SELECT indexname FROM pg_indexes", readonly=True)
    with pytest.raises(SchemaNotAllowedError):
        await db_user_prefix.sql_driver.execute("SELECT indexname FROM pg_catalog.pg_indexes", readonly=True)


@pytest.mark.asyncio
async def test_explain_with_hypothetical_index_in_basic_with_prefix(
    db_full: DbAccess,
    db_user_prefix: DbAccess,
) -> None:
    """Basic + table_prefix: проверка hypopg идёт по каналу сервера, план с гипотетическим индексом строится."""
    await setup_test_tables(db_full)
    try:
        await db_full.sql_driver.execute("CREATE EXTENSION IF NOT EXISTS hypopg", readonly=False)
    except Exception as e:
        logger.warning("hypopg not available: %s", e)
        pytest.skip("hypopg extension is not available")

    result = await ExplainService(db_user_prefix).explain(
        "SELECT * FROM app_users WHERE name = 'x'",
        hypothetical_indexes=[{"table": "app_users", "columns": ["name"]}],
    )

    # Таблица из одной строки: планировщик вправе выбрать Seq Scan, поэтому проверяем только,
    # что план построен (проверка hypopg не отказала), а не что гипотетический индекс использован.
    assert "app_users" in result
