# mypy: ignore-errors
"""Гигиена сессии пула на живом Postgres: возврат соединения не переносит состояние сессии между запросами."""

import pytest
from psycopg.errors import InvalidCursorName, UndefinedTable

from postgres_fastmcp.access import EffectiveAccess
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.domains.db_access import DbAccess, DbAccessService
from postgres_fastmcp.shared.enums import AccessMode


_FULL_WRITE = EffectiveAccess(AccessMode.FULL, write_mode=True)
_FULL_READ_ONLY = EffectiveAccess(AccessMode.FULL, write_mode=False)
_BASIC_READ_ONLY = EffectiveAccess(AccessMode.BASIC, write_mode=False)

# Роль кластерная, контейнер живёт на класс тестов: создание идемпотентно (как _PROBE_SETUP в test_role_check.py).
_SCS_PROBE_SETUP = """
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'scs_probe') THEN
    CREATE ROLE scs_probe LOGIN PASSWORD 'scs-probe-pw';
  END IF;
END $$;
ALTER ROLE scs_probe SET standard_conforming_strings = off;
"""


@pytest.mark.asyncio
async def test_temp_table_does_not_leak_to_basic_view(test_postgres_connection_string: tuple[str, str]) -> None:
    """Пул из одного соединения: временная таблица full+write не видна следующему basic-запросу (DISCARD ALL)."""
    connection_string, _ = test_postgres_connection_string
    config = DatabaseConfig.from_uri(
        connection_string, access_mode=AccessMode.FULL, write_mode=True, pool_min_size=1, pool_max_size=1
    )
    service = DbAccessService(config)
    try:
        await service.view(_FULL_WRITE).sql_driver.execute(
            "CREATE TEMP TABLE app_tmp_leak AS SELECT 1 AS x", readonly=False
        )
        with pytest.raises(UndefinedTable):
            await service.view(_BASIC_READ_ONLY).sql_driver.execute("SELECT * FROM app_tmp_leak", readonly=True)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_held_cursor_does_not_leak_to_basic_view(test_postgres_connection_string: tuple[str, str]) -> None:
    """Курсор WITH HOLD full+write закрывается при возврате соединения (DISCARD ALL) и не виден basic-запросу."""
    connection_string, _ = test_postgres_connection_string
    config = DatabaseConfig.from_uri(
        connection_string, access_mode=AccessMode.FULL, write_mode=True, pool_min_size=1, pool_max_size=1
    )
    service = DbAccessService(config)
    try:
        await service.view(_FULL_WRITE).sql_driver.execute(
            "DECLARE leak_cur CURSOR WITH HOLD FOR SELECT 1", readonly=False
        )
        with pytest.raises(InvalidCursorName):
            await service.view(_BASIC_READ_ONLY).sql_driver.execute("FETCH ALL FROM leak_cur", readonly=True)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_session_parameter_does_not_leak_across_views(test_postgres_connection_string: tuple[str, str]) -> None:
    """SET (не SET LOCAL) full+write не переживает возврат соединения: следующий запрос видит значение по умолчанию."""
    connection_string, _ = test_postgres_connection_string
    config = DatabaseConfig.from_uri(
        connection_string, access_mode=AccessMode.FULL, write_mode=True, pool_min_size=1, pool_max_size=1
    )
    service = DbAccessService(config)
    try:
        await service.view(_FULL_WRITE).sql_driver.execute("SET application_name = 'leak'", readonly=False)
        rows = await service.view(_FULL_READ_ONLY).sql_driver.execute(
            "SELECT current_setting('application_name') AS v", readonly=True
        )
    finally:
        await service.close()

    assert rows[0].cells["v"] != "leak"


@pytest.mark.asyncio
async def test_standard_conforming_strings_is_forced_on_for_role_with_it_off(
    db_full: DbAccess, test_postgres_connection_string: tuple[str, str]
) -> None:
    """Роль со standard_conforming_strings=off по умолчанию: транзакция агента всё равно лексится под on."""
    await db_full.sql_driver.execute(_SCS_PROBE_SETUP, readonly=False)
    connection_string, _ = test_postgres_connection_string
    config = DatabaseConfig.from_uri(
        connection_string, user="scs_probe", password="scs-probe-pw", access_mode=AccessMode.BASIC
    )
    service = DbAccessService(config)
    try:
        rows = await service.view(_BASIC_READ_ONLY).sql_driver.execute(r"SELECT 'a\' AS v", readonly=True)
    finally:
        await service.close()

    # Под standard_conforming_strings=on 'a\' — строка из двух символов, 'a' и обратный слэш: закрывающая
    # кавычка не экранирована. Если бы лексинг шёл под off (умолчание роли), тот же слэш экранировал бы
    # кавычку, и литерал остался бы незакрытым — ровно уязвимость, которую фиксирует Task 1.
    assert rows[0].cells["v"] == "a\\"
