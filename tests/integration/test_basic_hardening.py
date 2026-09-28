# mypy: ignore-errors
"""Вторая волна закалки basic на живом Postgres: information_schema с секретами, опции EXPLAIN, скрытые индексы."""

import logging

import pytest

from postgres_fastmcp.access import EffectiveAccess
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.domains.db_access import DbAccess, DbAccessService
from postgres_fastmcp.shared.enums import AccessMode
from postgres_fastmcp.shared.errors import ExplainOptionNotAllowedError, SystemRelationAccessError


logger = logging.getLogger(__name__)

_FULL_WRITE = EffectiveAccess(AccessMode.FULL, write_mode=True)


@pytest.mark.asyncio
async def test_user_mapping_options_are_rejected_in_basic(db_user_prefix: DbAccess) -> None:
    with pytest.raises(SystemRelationAccessError, match=r"information_schema\.user_mapping_options"):
        await db_user_prefix.sql_driver.execute("SELECT * FROM information_schema.user_mapping_options", readonly=True)


@pytest.mark.asyncio
async def test_information_schema_columns_still_work_in_basic(db_full: DbAccess, db_user_prefix: DbAccess) -> None:
    """information_schema.columns не секретный: basic видит реальные колонки известной таблицы public."""
    await db_full.sql_driver.execute(
        "CREATE TABLE IF NOT EXISTS app_probe_columns (id int PRIMARY KEY, name text)", readonly=False
    )
    rows = await db_user_prefix.sql_driver.execute(
        "SELECT count(*) AS n FROM information_schema.columns"
        " WHERE table_schema = 'public' AND table_name = 'app_probe_columns'",
        readonly=True,
    )
    assert rows[0].cells["n"] >= 1


@pytest.mark.asyncio
async def test_explain_settings_is_rejected_and_safe_options_run_in_basic(db_user_prefix: DbAccess) -> None:
    with pytest.raises(ExplainOptionNotAllowedError, match="SETTINGS"):
        await db_user_prefix.sql_driver.execute("EXPLAIN (SETTINGS) SELECT 1", readonly=True)

    rows = await db_user_prefix.sql_driver.execute(
        "EXPLAIN (FORMAT JSON, COSTS false, VERBOSE) SELECT 1", readonly=True
    )
    assert rows[0].cells["QUERY PLAN"][0]["Plan"]["Node Type"] == "Result"


@pytest.mark.asyncio
async def test_hidden_indexes_are_unhidden_when_the_connection_returns(
    test_postgres_connection_string: tuple[str, str],
) -> None:
    """Пул из одного соединения: следующий запрос получает то же соединение, и скрытых индексов на нём нет."""
    connection_string, _ = test_postgres_connection_string
    config = DatabaseConfig.from_uri(
        connection_string, access_mode=AccessMode.FULL, write_mode=True, pool_min_size=1, pool_max_size=1
    )
    service = DbAccessService(config)
    db = service.view(_FULL_WRITE)
    try:
        await db.sql_driver.execute("CREATE TABLE IF NOT EXISTS hypo_hide_t (id int PRIMARY KEY)", readonly=False)
        try:
            await db.sql_driver.execute("CREATE EXTENSION IF NOT EXISTS hypopg", readonly=False)
        except Exception as e:
            logger.warning("hypopg not available: %s", e)
            pytest.skip("hypopg extension is not available")

        available = await db.sql_driver.execute(
            "SELECT to_regproc('hypopg_hide_index') IS NOT NULL AS available", readonly=True
        )
        if available[0].cells["available"] is not True:
            pytest.skip("hypopg_hide_index is not available (hypopg < 1.4)")

        hidden = await db.sql_driver.execute(
            "SELECT hypopg_hide_index('hypo_hide_t_pkey'::regclass) AS hidden", readonly=True
        )
        after_return = await db.sql_driver.execute("SELECT count(*) AS n FROM hypopg_hidden_indexes", readonly=True)
    finally:
        await db.sql_driver.execute("DROP TABLE IF EXISTS hypo_hide_t", readonly=False)
        await service.close()

    assert hidden[0].cells["hidden"] is True
    assert after_return[0].cells["n"] == 0
