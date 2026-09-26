"""Тесты DbAccessService.view: исполнитель по правам запроса, кэш и один пул."""

from unittest.mock import AsyncMock

import pytest

from postgres_fastmcp.access import EffectiveAccess
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.domains.db_access import DbAccess, DbAccessService
from postgres_fastmcp.postgres.driver import SqlExecutor
from postgres_fastmcp.postgres.security.driver import SafeSqlExecutor
from postgres_fastmcp.shared.enums import AccessMode


_ALL_ACCESS = [
    EffectiveAccess(AccessMode.BASIC, write_mode=False),
    EffectiveAccess(AccessMode.BASIC, write_mode=True),
    EffectiveAccess(AccessMode.FULL, write_mode=False),
    EffectiveAccess(AccessMode.FULL, write_mode=True),
]


def _service(**overrides: object) -> DbAccessService:
    config = DatabaseConfig(host="h", user="u", password="p", name="d", **overrides)
    return DbAccessService(config)


def test_view_carries_request_access_and_config_fields() -> None:
    service = _service(table_prefix="app_")
    view = service.view(EffectiveAccess(AccessMode.BASIC, write_mode=True))
    assert isinstance(view, DbAccess)
    assert view.access_mode == AccessMode.BASIC
    assert view.write_mode is True
    assert view.table_prefix == "app_"
    assert view.connection_id.startswith("postgresql://u:p@h:5432/d")


def test_only_full_write_gets_the_unrestricted_executor() -> None:
    service = _service()
    for access in _ALL_ACCESS:
        driver = service.view(access).sql_driver
        unrestricted = access == EffectiveAccess(AccessMode.FULL, write_mode=True)
        assert isinstance(driver, SqlExecutor if unrestricted else SafeSqlExecutor), access


@pytest.mark.parametrize(
    ("access", "schema", "read_only", "prefix", "explain_analyze"),
    [
        (EffectiveAccess(AccessMode.BASIC, write_mode=False), "public", True, "app_", False),
        (EffectiveAccess(AccessMode.BASIC, write_mode=True), "public", False, "app_", False),
        (EffectiveAccess(AccessMode.FULL, write_mode=False), None, True, None, True),
    ],
)
def test_safe_executor_is_built_from_access_not_config(
    access: EffectiveAccess, schema: str | None, *, read_only: bool, prefix: str | None, explain_analyze: bool
) -> None:
    """Конфиг сервиса — FULL+write, но исполнитель собирается по правам запроса."""
    service = _service(access_mode=AccessMode.FULL, write_mode=True, table_prefix="app_", safe_sql_timeout=7)
    driver = service.view(access).sql_driver
    assert isinstance(driver, SafeSqlExecutor)
    assert driver._config.allowed_schema == schema
    assert driver._config.read_only is read_only
    assert driver._config.table_prefix == prefix
    assert driver._config.timeout == 7
    assert driver._config.query_tag == "postgres_fastmcp"
    assert driver._validator.allow_explain_analyze is explain_analyze


def test_executors_are_cached_per_access_and_share_one_pool() -> None:
    service = _service()
    first = {access: service.view(access).sql_driver for access in _ALL_ACCESS}
    again = {access: service.view(access).sql_driver for access in _ALL_ACCESS}
    assert first == again
    assert len(service._executors) == 4
    pools = {
        id(d._delegate.conn if isinstance(d, SafeSqlExecutor) else d.conn)  # type: ignore[attr-defined]
        for d in first.values()
    }
    assert pools == {id(service._pool)}


async def test_close_closes_the_pool() -> None:
    service = _service()
    service._pool.close = AsyncMock()  # type: ignore[method-assign]
    await service.close()
    service._pool.close.assert_awaited_once_with()


def test_service_has_no_sql_driver() -> None:
    """Исполнитель выдаётся только через view(access): у сервиса нет «общего» sql_driver."""
    assert not hasattr(_service(), "sql_driver")
