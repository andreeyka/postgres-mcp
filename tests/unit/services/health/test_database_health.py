# mypy: ignore-errors
"""Unit tests for DatabaseHealthTool orchestration."""

import asyncio
import time
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_health_checks_run_in_parallel(mock_db_access) -> None:
    """All requested health checks should start concurrently via asyncio.gather, not sequentially."""
    from postgres_fastmcp.services.health import database_health as dh

    delay = 0.2

    async def fake_check(self, *args, **kwargs):
        await asyncio.sleep(delay)
        return "ok"

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
