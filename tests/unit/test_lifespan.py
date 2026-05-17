"""Тесты для фабрики lifespan: создание и закрытие DbAccessService."""

import pytest

from postgres_fastmcp.config import Settings
from postgres_fastmcp.lifespan import build_lifespan


@pytest.mark.asyncio
async def test_lifespan_yields_db_and_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lifespan кладёт DbAccessService и Settings в context, на выходе закрывает пул."""
    closed = {"count": 0}

    class FakeDb:
        def __init__(self, cfg: object) -> None:
            self.cfg = cfg

        async def close(self) -> None:
            closed["count"] += 1

    monkeypatch.setattr("postgres_fastmcp.lifespan.DbAccessService", FakeDb)

    settings = Settings()
    lifespan_cm = build_lifespan(settings)
    async with lifespan_cm(server=None) as ctx:
        assert ctx["settings"] is settings
        assert isinstance(ctx["db"], FakeDb)
        assert ctx["db"].cfg is settings.database

    assert closed["count"] == 1


@pytest.mark.asyncio
async def test_lifespan_closes_db_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """При исключении внутри async-with пул должен быть закрыт через finally."""
    closed = {"count": 0}

    class FakeDb:
        def __init__(self, cfg: object) -> None: ...

        async def close(self) -> None:
            closed["count"] += 1

    monkeypatch.setattr("postgres_fastmcp.lifespan.DbAccessService", FakeDb)

    settings = Settings()
    lifespan_cm = build_lifespan(settings)
    with pytest.raises(ValueError, match="boom"):
        async with lifespan_cm(server=None):
            raise ValueError("boom")
    assert closed["count"] == 1
