"""Тесты ExtensionInspectorAdapter: подсказки агенту называют существующие тулы."""

from unittest.mock import AsyncMock, MagicMock

import psycopg
import pytest

from postgres_fastmcp.postgres.catalog import QUERY_EXTENSION_AVAILABLE, QUERY_EXTENSION_INSTALLED, QUERY_SERVER_VERSION
from postgres_fastmcp.postgres.extensions import (
    CATALOG_ERROR_MESSAGE,
    ExtensionInspectorAdapter,
    ExtensionStatus,
    get_postgres_version,
)
from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.shared.errors import TablePrefixAccessError


@pytest.mark.parametrize("message_type", ["plain", "markdown"])
async def test_hypopg_available_hint_names_execute_sql(message_type: str) -> None:
    """Hypopg доступно, но не установлено: подсказка ведёт к execute_sql, а не к несуществующему execute_query."""
    adapter = ExtensionInspectorAdapter(MagicMock(), "test")
    status = ExtensionStatus(is_installed=False, is_available=True, name="hypopg", message="", default_version="1.4")
    adapter.check_extension = AsyncMock(return_value=status)  # type: ignore[method-assign]

    installed, message = await adapter.check_hypopg_installation_status(message_type=message_type)  # type: ignore[arg-type]

    assert installed is False
    assert "'execute_sql' tool" in message
    assert "execute_query" not in message


def _executor(*answers: object) -> MagicMock:
    executor = MagicMock()
    executor.execute = AsyncMock(side_effect=list(answers))
    return executor


async def test_check_extension_passes_the_template_and_a_str_param() -> None:
    """Параметр уходит исполнителю отдельно: CatalogSqlExecutor проверяет шаблон до рендера."""
    executor = _executor([RowResult(cells={"extversion": "1.5.1"})])

    status = await ExtensionInspectorAdapter(executor, "test").check_extension("hypopg")

    assert status.is_installed is True
    executor.execute.assert_awaited_once_with(QUERY_EXTENSION_INSTALLED, params=["hypopg"], readonly=True)


async def test_database_error_from_available_extensions_is_a_catalog_error() -> None:
    """Битый .control ломает pg_available_extensions: статус неизвестен, а не «не установлено»."""
    executor = _executor([], psycopg.Error("could not read control file"))

    status = await ExtensionInspectorAdapter(executor, "test").check_extension("hypopg")

    assert status.catalog_error == CATALOG_ERROR_MESSAGE
    assert executor.execute.await_args_list[1].args[0] is QUERY_EXTENSION_AVAILABLE


async def test_validator_error_is_not_reported_as_missing_extension() -> None:
    executor = _executor(TablePrefixAccessError("pg_extension", "app_"))

    with pytest.raises(TablePrefixAccessError):
        await ExtensionInspectorAdapter(executor, "test").check_extension("hypopg")


async def test_server_version_uses_the_catalog_template() -> None:
    executor = _executor([RowResult(cells={"server_version": "16.4 (Debian 16.4-1)"})])

    assert await get_postgres_version(executor, "version-test") == 16
    executor.execute.assert_awaited_once_with(QUERY_SERVER_VERSION, params=None, readonly=True)
