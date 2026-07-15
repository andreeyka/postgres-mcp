# mypy: ignore-errors
"""Unit tests for ConnectionHealthCalc threshold derived from server max_connections."""

from unittest.mock import AsyncMock

import pytest

from postgres_fastmcp.services.health.connection_health_calc import ConnectionHealthCalc
from postgres_fastmcp.sql.models.row_result import RowResult


class TestConnectionHealthCheck:
    """connection_health_check must warn relative to the server's real max_connections."""

    @pytest.mark.asyncio
    async def test_warns_when_near_server_max_connections(self) -> None:
        """95 of 100 max_connections exceeds the 90% limit and is reported as high."""
        calc = ConnectionHealthCalc(AsyncMock())
        calc._get_total_connections = AsyncMock(return_value=95)
        calc._get_idle_connections = AsyncMock(return_value=0)
        calc._get_max_connections = AsyncMock(return_value=100)  # limit = 90

        result = await calc.connection_health_check()

        assert "High number of connections: 95" in result

    @pytest.mark.asyncio
    async def test_healthy_below_server_limit(self) -> None:
        """50 of 100 max_connections is below the 90% limit and reported healthy."""
        calc = ConnectionHealthCalc(AsyncMock())
        calc._get_total_connections = AsyncMock(return_value=50)
        calc._get_idle_connections = AsyncMock(return_value=1)
        calc._get_max_connections = AsyncMock(return_value=100)

        result = await calc.connection_health_check()

        assert "Connections healthy: 50 total, 1 idle" in result

    @pytest.mark.asyncio
    async def test_falls_back_to_configured_cap_when_max_unavailable(self) -> None:
        """When max_connections cannot be read, the configured cap (500) is used."""
        calc = ConnectionHealthCalc(AsyncMock(), max_total_connections=500)
        calc._get_total_connections = AsyncMock(return_value=600)
        calc._get_idle_connections = AsyncMock(return_value=0)
        calc._get_max_connections = AsyncMock(return_value=None)

        result = await calc.connection_health_check()

        assert "High number of connections: 600" in result

    @pytest.mark.asyncio
    async def test_get_max_connections_reads_setting(self) -> None:
        """_get_max_connections returns the integer server setting."""
        driver = AsyncMock()
        driver.execute = AsyncMock(return_value=[RowResult(cells={"max_connections": 200})])
        calc = ConnectionHealthCalc(driver)

        assert await calc._get_max_connections() == 200
