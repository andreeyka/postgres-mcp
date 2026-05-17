"""Общие фикстуры для тестов тулов."""

from collections.abc import Callable
from typing import Any
from unittest import mock

import pytest


class FakeCtx:
    """Минимальный stand-in для fastmcp.server.context.Context в тестах."""

    def __init__(self, db: Any) -> None:  # noqa: ANN401
        self.lifespan_context: dict[str, Any] = {"db": db}


@pytest.fixture
def make_ctx() -> Callable[..., FakeCtx]:
    """Фабрика FakeCtx с подставленным DbAccessService-моком."""

    def factory(db: Any | None = None) -> FakeCtx:  # noqa: ANN401
        return FakeCtx(db=db if db is not None else mock.AsyncMock())

    return factory


@pytest.fixture
def db_mock() -> mock.AsyncMock:
    """Готовый AsyncMock на роль DbAccessService."""
    return mock.AsyncMock()
