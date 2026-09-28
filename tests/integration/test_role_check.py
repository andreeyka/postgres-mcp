# mypy: ignore-errors
"""Проверка прав роли basic на живом Postgres: суперпользователь CI и роль с лишними правами."""

import pytest

from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.domains.db_access import DbAccess, DbAccessService
from postgres_fastmcp.domains.role_check import basic_role_findings
from postgres_fastmcp.shared.enums import AccessMode


# Роли кластерные, контейнер живёт на класс тестов: создание идемпотентно.
_PROBE_SETUP = """
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'mcp_role_probe') THEN
    CREATE ROLE mcp_role_probe LOGIN PASSWORD 'probe-pw';
  END IF;
END $$;
GRANT pg_read_all_stats TO mcp_role_probe;
CREATE SCHEMA IF NOT EXISTS probe_other;
GRANT USAGE ON SCHEMA probe_other TO mcp_role_probe;
CREATE TABLE IF NOT EXISTS public.probe_plain (id int);
GRANT SELECT ON public.probe_plain TO mcp_role_probe;
"""


@pytest.mark.asyncio
async def test_ci_superuser_is_reported(db_full: DbAccess) -> None:
    result = await basic_role_findings(db_full.catalog_driver, "app_")

    assert result.role == "postgres"
    assert result.findings == ["superuser"]


@pytest.mark.asyncio
async def test_role_with_extra_privileges_is_reported(
    db_full: DbAccess, test_postgres_connection_string: tuple[str, str]
) -> None:
    await db_full.sql_driver.execute(_PROBE_SETUP, readonly=False)
    connection_string, _ = test_postgres_connection_string
    config = DatabaseConfig.from_uri(
        connection_string,
        user="mcp_role_probe",
        password="probe-pw",
        access_mode=AccessMode.BASIC,
        table_prefix="app_",
    )
    service = DbAccessService(config)
    try:
        result = await basic_role_findings(service.catalog_driver, "app_")
    finally:
        await service.close()

    assert result.role == "mcp_role_probe"
    assert "superuser" not in result.findings
    assert "member of pg_read_all_stats" in result.findings
    assert any(f.startswith("USAGE on schemas: ") and "probe_other" in f for f in result.findings), result.findings
    assert any(f.endswith("without prefix 'app_'") for f in result.findings), result.findings
