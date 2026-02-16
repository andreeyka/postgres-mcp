# mypy: ignore-errors
"""Unit tests for ReplicationCalc and related dataclasses."""

from unittest.mock import AsyncMock

import pytest

from postgres_fastmcp.services.health.replication_calc import ReplicationCalc, ReplicationMetrics, ReplicationSlot


class TestReplicationSlot:
    """Tests for ReplicationSlot dataclass."""

    def test_fields(self) -> None:
        slot = ReplicationSlot(slot_name="slot_a", database="db1", active=True)
        assert slot.slot_name == "slot_a"
        assert slot.database == "db1"
        assert slot.active is True


class TestReplicationMetrics:
    """Tests for ReplicationMetrics dataclass."""

    def test_replica_with_lag(self) -> None:
        m = ReplicationMetrics(
            is_replica=True,
            replication_lag_seconds=5.0,
            is_replicating=True,
            replication_slots=[],
        )
        assert m.is_replica is True
        assert m.replication_lag_seconds == 5.0
        assert m.is_replicating is True


class TestReplicationCalcHealthCheck:
    """Tests for replication_health_check output."""

    @pytest.mark.asyncio
    async def test_primary_no_replicas(self) -> None:
        """Scenario: primary node, no replicas; report contains 'primary' and 'No active replicas'."""
        mock_driver = AsyncMock()
        calc = ReplicationCalc(mock_driver)
        calc._get_replication_metrics = AsyncMock(
            return_value=ReplicationMetrics(
                is_replica=False,
                replication_lag_seconds=None,
                is_replicating=False,
                replication_slots=[],
            )
        )
        result = await calc.replication_health_check()
        assert "primary" in result.lower()
        assert "No active replicas" in result

    @pytest.mark.asyncio
    async def test_replica_replicating_no_lag(self) -> None:
        """Scenario: replica, actively replicating, zero lag; report contains replica and 'No replication lag'."""
        mock_driver = AsyncMock()
        calc = ReplicationCalc(mock_driver)
        calc._get_replication_metrics = AsyncMock(
            return_value=ReplicationMetrics(
                is_replica=True,
                replication_lag_seconds=0.0,
                is_replicating=True,
                replication_slots=[],
            )
        )
        result = await calc.replication_health_check()
        assert "replica" in result.lower()
        assert "replicating" in result.lower()
        assert "No replication lag" in result

    @pytest.mark.asyncio
    async def test_replica_not_replicating_warning(self) -> None:
        """Scenario: replica but not actively replicating; report contains WARNING and 'not actively replicating'."""
        mock_driver = AsyncMock()
        calc = ReplicationCalc(mock_driver)
        calc._get_replication_metrics = AsyncMock(
            return_value=ReplicationMetrics(
                is_replica=True,
                replication_lag_seconds=None,
                is_replicating=False,
                replication_slots=[],
            )
        )
        result = await calc.replication_health_check()
        assert "WARNING" in result
        assert "not actively replicating" in result

    @pytest.mark.asyncio
    async def test_replication_slots_listed(self) -> None:
        """Scenario: replication slots present; report lists active and inactive slots by name."""
        mock_driver = AsyncMock()
        calc = ReplicationCalc(mock_driver)
        calc._get_replication_metrics = AsyncMock(
            return_value=ReplicationMetrics(
                is_replica=False,
                replication_lag_seconds=None,
                is_replicating=False,
                replication_slots=[
                    ReplicationSlot("s1", "db1", active=True),
                    ReplicationSlot("s2", "db2", active=False),
                ],
            )
        )
        result = await calc.replication_health_check()
        assert "Active replication slots" in result
        assert "s1" in result
        assert "Inactive replication slots" in result
        assert "s2" in result

    @pytest.mark.asyncio
    async def test_no_replication_slots_message(self) -> None:
        """Scenario: no replication slots; report contains 'No replication slots found'."""
        mock_driver = AsyncMock()
        calc = ReplicationCalc(mock_driver)
        calc._get_replication_metrics = AsyncMock(
            return_value=ReplicationMetrics(
                is_replica=False,
                replication_lag_seconds=None,
                is_replicating=False,
                replication_slots=[],
            )
        )
        result = await calc.replication_health_check()
        assert "No replication slots found" in result


class TestReplicationCalcGetReplicationMetrics:
    """Tests for _get_replication_metrics delegation."""

    @pytest.mark.asyncio
    async def test_aggregates_sub_calls(self) -> None:
        """Scenario: metrics built from is_replica, lag, is_replicating, slots; returns ReplicationMetrics."""
        mock_driver = AsyncMock()
        calc = ReplicationCalc(mock_driver)
        calc._is_replica = AsyncMock(return_value=False)
        calc._get_replication_lag = AsyncMock(return_value=None)
        calc._is_replicating = AsyncMock(return_value=False)
        calc._get_replication_slots = AsyncMock(return_value=[])

        metrics = await calc._get_replication_metrics()
        assert isinstance(metrics, ReplicationMetrics)
        assert metrics.is_replica is False
        assert metrics.replication_slots == []


class TestReplicationCalcIsReplica:
    """Tests for _is_replica."""

    @pytest.mark.asyncio
    async def test_true_when_in_recovery(self) -> None:
        """Scenario: pg_is_in_recovery returns true; _is_replica returns True."""
        from postgres_fastmcp.sql.models.row_result import RowResult

        mock_driver = AsyncMock()
        mock_driver.execute = AsyncMock(return_value=[RowResult(cells={"pg_is_in_recovery": True})])
        calc = ReplicationCalc(mock_driver)
        result = await calc._is_replica()
        assert result is True

    @pytest.mark.asyncio
    async def test_false_when_not_in_recovery(self) -> None:
        """Scenario: pg_is_in_recovery returns false; _is_replica returns False."""
        from postgres_fastmcp.sql.models.row_result import RowResult

        mock_driver = AsyncMock()
        mock_driver.execute = AsyncMock(return_value=[RowResult(cells={"pg_is_in_recovery": False})])
        calc = ReplicationCalc(mock_driver)
        result = await calc._is_replica()
        assert result is False


class TestReplicationCalcFeatureSupported:
    """Tests for _feature_supported."""

    def test_default_true(self) -> None:
        """Scenario: unknown feature; _feature_supported returns True by default."""
        calc = ReplicationCalc(AsyncMock())
        assert calc._feature_supported("unknown_feature") is True

    @pytest.mark.asyncio
    async def test_cached_false_after_exception(self) -> None:
        """Scenario: driver raises; _is_replicating returns False and feature is cached as unsupported."""
        mock_driver = AsyncMock()
        mock_driver.execute = AsyncMock(side_effect=Exception("db error"))
        calc = ReplicationCalc(mock_driver)
        result = await calc._is_replicating()
        assert result is False
        assert calc._feature_supported("replicating") is False
