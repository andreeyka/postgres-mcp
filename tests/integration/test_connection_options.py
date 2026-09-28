# mypy: ignore-errors
"""Слой подключения на живом Postgres: параметры libpq доходят до сессии, hypopg сбрасывается на том же соединении."""

import logging

import pytest

from postgres_fastmcp.access import EffectiveAccess
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.domains.db_access import DbAccessService
from postgres_fastmcp.domains.explain.service import ExplainService
from postgres_fastmcp.shared.enums import AccessMode


logger = logging.getLogger(__name__)

_FULL_WRITE = EffectiveAccess(AccessMode.FULL, write_mode=True)


@pytest.mark.asyncio
async def test_connect_options_reach_the_session(test_postgres_connection_string: tuple[str, str]) -> None:
    connection_string, _ = test_postgres_connection_string
    config = DatabaseConfig.from_uri(
        connection_string,
        access_mode=AccessMode.FULL,
        write_mode=True,
        connect_options={"application_name": "mcp-test"},
    )
    service = DbAccessService(config)
    try:
        rows = await service.view(_FULL_WRITE).sql_driver.execute(
            "SELECT current_setting('application_name') AS app", readonly=True
        )
    finally:
        await service.close()

    assert rows[0].cells["app"] == "mcp-test"


@pytest.mark.asyncio
async def test_uri_query_parameters_reach_the_session(test_postgres_connection_string: tuple[str, str]) -> None:
    """Параметр options с пробелом доходит до сервера как есть: пробел кодируется %20, а не '+'."""
    connection_string, _ = test_postgres_connection_string
    uri = f"{connection_string}?application_name=mcp-uri&options=-c%20statement_timeout%3D4321"
    service = DbAccessService(DatabaseConfig.from_uri(uri, access_mode=AccessMode.FULL, write_mode=True))
    try:
        rows = await service.view(_FULL_WRITE).sql_driver.execute(
            "SELECT current_setting('application_name') AS app, current_setting('statement_timeout') AS st",
            readonly=True,
        )
    finally:
        await service.close()

    assert (rows[0].cells["app"], rows[0].cells["st"]) == ("mcp-uri", "4321ms")


@pytest.mark.asyncio
async def test_hypothetical_indexes_are_reset_when_the_connection_returns(
    test_postgres_connection_string: tuple[str, str],
) -> None:
    """Пул из одного соединения: следующий запрос получает то же соединение, и гипотетических индексов на нём нет."""
    connection_string, _ = test_postgres_connection_string
    config = DatabaseConfig.from_uri(
        connection_string, access_mode=AccessMode.FULL, write_mode=True, pool_min_size=1, pool_max_size=1
    )
    service = DbAccessService(config)
    db = service.view(_FULL_WRITE)
    try:
        await db.sql_driver.execute("CREATE TABLE IF NOT EXISTS hypo_reset_t (id int, v text)", readonly=False)
        try:
            await db.sql_driver.execute("CREATE EXTENSION IF NOT EXISTS hypopg", readonly=False)
        except Exception as e:
            logger.warning("hypopg not available: %s", e)
            pytest.skip("hypopg extension is not available")

        await ExplainService(db).explain(
            "SELECT * FROM hypo_reset_t WHERE v = 'x'",
            hypothetical_indexes=[{"table": "hypo_reset_t", "columns": ["v"]}],
        )
        after_explain = await db.sql_driver.execute("SELECT count(*) AS n FROM hypopg_list_indexes", readonly=True)

        await db.sql_driver.execute("SELECT hypopg_create_index('CREATE INDEX ON hypo_reset_t (id)')", readonly=True)
        after_direct_call = await db.sql_driver.execute("SELECT count(*) AS n FROM hypopg_list_indexes", readonly=True)
    finally:
        await service.close()

    assert after_explain[0].cells["n"] == 0
    assert after_direct_call[0].cells["n"] == 0
