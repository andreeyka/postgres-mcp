# mypy: ignore-errors
"""Unit tests for HealthService."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from postgres_fastmcp.services.health.service import HealthService


class TestHealthService:
    """Tests for HealthService.analyze_db_health."""

    @patch("postgres_fastmcp.services.health.service.DatabaseHealthTool")
    async def test_analyze_db_health_returns_string_report(
        self,
        mock_health_tool_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """analyze_db_health(health_type) returns string report from health tool."""
        mock_tool = MagicMock()
        mock_tool.health = AsyncMock(return_value="Health report")
        mock_health_tool_cls.return_value = mock_tool

        service = HealthService(db=mock_db_access)
        result = await service.analyze_db_health(health_type="all")
        assert result == "Health report"

    @patch("postgres_fastmcp.services.health.service.DatabaseHealthTool")
    async def test_analyze_db_health_returns_report_for_given_health_type(
        self,
        mock_health_tool_cls: MagicMock,
        mock_db_access: MagicMock,
    ) -> None:
        """analyze_db_health returns report content for requested health_type (e.g. index,vacuum)."""
        mock_tool = MagicMock()
        mock_tool.health = AsyncMock(return_value="Index health")
        mock_health_tool_cls.return_value = mock_tool

        service = HealthService(db=mock_db_access)
        result = await service.analyze_db_health(health_type="index,vacuum")
        assert result == "Index health"


@pytest.mark.asyncio
async def test_health_checks_run_in_parallel(mock_db_access) -> None:
    """All requested health checks should start concurrently via asyncio.gather, not sequentially."""
    from postgres_fastmcp.services.health import database_health as dh

    delay = 0.2

    async def fake_check(self, *args, **kwargs):
        await asyncio.sleep(delay)
        return "ok"

    calc_methods = [
        (dh.IndexHealthCalc, "invalid_index_check"),
        (dh.IndexHealthCalc, "duplicate_index_check"),
        (dh.IndexHealthCalc, "index_bloat"),
        (dh.IndexHealthCalc, "unused_indexes"),
        (dh.ConnectionHealthCalc, "connection_health_check"),
        (dh.VacuumHealthCalc, "transaction_id_danger_check"),
        (dh.SequenceHealthCalc, "sequence_danger_check"),
        (dh.ReplicationCalc, "replication_health_check"),
        (dh.BufferHealthCalc, "index_hit_rate"),
        (dh.BufferHealthCalc, "table_hit_rate"),
        (dh.ConstraintHealthCalc, "invalid_constraints_check"),
    ]

    with patch.multiple(
        dh.IndexHealthCalc,
        invalid_index_check=fake_check,
        duplicate_index_check=fake_check,
        index_bloat=fake_check,
        unused_indexes=fake_check,
    ), patch.object(dh.ConnectionHealthCalc, "connection_health_check", fake_check), \
         patch.object(dh.VacuumHealthCalc, "transaction_id_danger_check", fake_check), \
         patch.object(dh.SequenceHealthCalc, "sequence_danger_check", fake_check), \
         patch.object(dh.ReplicationCalc, "replication_health_check", fake_check), \
         patch.multiple(dh.BufferHealthCalc, index_hit_rate=fake_check, table_hit_rate=fake_check), \
         patch.object(dh.ConstraintHealthCalc, "invalid_constraints_check", fake_check):
        tool = dh.DatabaseHealthTool(sql_driver=mock_db_access.sql_driver)

        start = time.monotonic()
        result = await tool.health(health_type="all")
        elapsed = time.monotonic() - start

    # 11 parallel calls × 0.2s should finish well under 1.0s; sequential would be ~2.2s.
    assert elapsed < delay * 3, f"checks ran sequentially: {elapsed:.2f}s"
    assert result  # non-empty report
    _ = calc_methods  # silence unused
