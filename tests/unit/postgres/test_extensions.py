"""Тесты ExtensionInspectorAdapter: подсказки агенту называют существующие тулы."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from postgres_fastmcp.postgres.extensions import ExtensionInspectorAdapter, ExtensionStatus


@pytest.mark.parametrize("message_type", ["plain", "markdown"])
async def test_hypopg_available_hint_names_execute_sql(message_type: str) -> None:
    """Hypopg доступно, но не установлено: подсказка ведёт к execute_sql, а не к несуществующему execute_query."""
    adapter = ExtensionInspectorAdapter(MagicMock(), MagicMock(), "test")
    status = ExtensionStatus(is_installed=False, is_available=True, name="hypopg", message="", default_version="1.4")
    adapter.check_extension = AsyncMock(return_value=status)  # type: ignore[method-assign]

    installed, message = await adapter.check_hypopg_installation_status(message_type=message_type)  # type: ignore[arg-type]

    assert installed is False
    assert "'execute_sql' tool" in message
    assert "execute_query" not in message
