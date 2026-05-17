"""Тесты для тула analyze_db_health."""

from unittest import mock

import pytest

from postgres_fastmcp.tools.full import analyze_db_health as analyze_db_health_mod
from postgres_fastmcp.tools.full.analyze_db_health import analyze_db_health


@pytest.mark.asyncio
async def test_analyze_db_health_default(monkeypatch, db_mock, make_ctx) -> None:
    fake_service = mock.AsyncMock()
    fake_service.analyze_db_health.return_value = "OK"
    monkeypatch.setattr(
        analyze_db_health_mod, "HealthService", lambda **kw: fake_service
    )

    result = await analyze_db_health(ctx=make_ctx(db_mock))

    assert result == "OK"
    fake_service.analyze_db_health.assert_awaited_once_with(health_type="all")


@pytest.mark.asyncio
async def test_analyze_db_health_custom_type(monkeypatch, db_mock, make_ctx) -> None:
    fake_service = mock.AsyncMock()
    fake_service.analyze_db_health.return_value = "INDEX_REPORT"
    monkeypatch.setattr(
        analyze_db_health_mod, "HealthService", lambda **kw: fake_service
    )

    result = await analyze_db_health(health_type="index", ctx=make_ctx(db_mock))

    assert result == "INDEX_REPORT"
    fake_service.analyze_db_health.assert_awaited_once_with(health_type="index")
