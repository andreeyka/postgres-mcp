"""Реальные ошибки SQL и JSON проходят от PostgreSQL до MCP-клиента."""

import pytest
from fastmcp import Client

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.server import create_server
from postgres_fastmcp.shared.enums import AccessMode


@pytest.mark.parametrize("access_mode", [AccessMode.BASIC, AccessMode.FULL])
class TestDatabaseErrors:
    async def test_explain_error_is_explicit(self, integration_settings: Settings, access_mode: AccessMode) -> None:
        settings = integration_settings.model_copy(deep=True)
        settings.database.access_mode = access_mode
        async with Client(create_server(settings)) as client:
            result = await client.call_tool("explain_query", {"sql": "SELECT missing_column"}, raise_on_error=False)
        assert result.is_error
        assert result.content[0].text == 'PostgreSQL error [42703]: column "missing_column" does not exist'

    @pytest.mark.parametrize(
        ("sql", "sqlstate", "message"),
        [
            ("SELECT missing_column", "42703", 'column "missing_column" does not exist'),
            (
                "SELECT round(1.25::double precision, 1)",
                "42883",
                "function round(double precision, integer) does not exist",
            ),
            ("SELECT 1/0", "22012", "division by zero"),
            ("SELECT jsonb_array_length('null'::jsonb)", "22023", "cannot get array length of a scalar"),
            ("SELECT jsonb_array_elements_text('null'::jsonb)", "22023", "cannot extract elements from a scalar"),
            (
                "SELECT * FROM jsonb_array_elements_text('null'::jsonb)",
                "22023",
                "cannot extract elements from a scalar",
            ),
            (
                "SELECT count(*) FROM (SELECT jsonb_array_elements_text('null'::jsonb)) s",
                "22023",
                "cannot extract elements from a scalar",
            ),
        ],
    )
    async def test_error_is_explicit_and_next_query_succeeds(
        self, integration_settings: Settings, access_mode: AccessMode, sql: str, sqlstate: str, message: str
    ) -> None:
        settings = integration_settings.model_copy(deep=True)
        settings.database.access_mode = access_mode
        async with Client(create_server(settings)) as client:
            failed = await client.call_tool("execute_sql", {"sql": sql}, raise_on_error=False)
            assert failed.is_error
            assert failed.content[0].text == f"PostgreSQL error [{sqlstate}]: {message}"
            recovered = await client.call_tool("execute_sql", {"sql": "SELECT 1 AS ok", "output": "json"})
            assert recovered.structured_content["rows"] == [{"ok": 1}]

    @pytest.mark.parametrize(
        "sql",
        [
            'SELECT jsonb_array_elements_text(\'["a","b"]\'::jsonb) AS tag',
            'SELECT tag FROM jsonb_array_elements_text(\'["a","b"]\'::jsonb) AS tag',
            'SELECT tag FROM (SELECT jsonb_array_elements_text(\'["a","b"]\'::jsonb) AS tag) s',
        ],
    )
    async def test_array_srf_still_works(
        self, integration_settings: Settings, access_mode: AccessMode, sql: str
    ) -> None:
        settings = integration_settings.model_copy(deep=True)
        settings.database.access_mode = access_mode
        async with Client(create_server(settings)) as client:
            result = await client.call_tool("execute_sql", {"sql": sql, "output": "json"})
        assert result.structured_content["rows"] == [{"tag": "a"}, {"tag": "b"}]
