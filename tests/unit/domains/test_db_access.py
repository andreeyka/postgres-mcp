"""Тесты DbAccessService.view: исполнитель по правам запроса, кэш и один пул."""

from unittest.mock import AsyncMock

import pytest

from postgres_fastmcp.access import EffectiveAccess
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.domains.db_access import DbAccess, DbAccessService
from postgres_fastmcp.postgres.driver import SqlExecutor
from postgres_fastmcp.postgres.security.catalog_driver import CatalogSqlExecutor
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
    service = _service(access_mode=AccessMode.FULL, write_mode=True, table_prefix="app_")
    view = service.view(EffectiveAccess(AccessMode.BASIC, write_mode=True))
    assert isinstance(view, DbAccess)
    assert view.access_mode == AccessMode.BASIC
    assert view.write_mode is True
    assert view.table_prefix == "app_"
    assert view.connection_id.startswith("postgresql://u:p@h:5432/d")


def test_only_full_write_gets_the_unrestricted_executor() -> None:
    service = _service(access_mode=AccessMode.FULL, write_mode=True)
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
    service = _service(access_mode=AccessMode.FULL, write_mode=True)
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


def test_view_clamps_request_access_to_configured_ceiling() -> None:
    """Сервис с потолком BASIC read-only не выдаёт больше прав, даже если запрос просит FULL+write."""
    service = _service(access_mode=AccessMode.BASIC, write_mode=False, table_prefix="app_")
    view = service.view(EffectiveAccess(AccessMode.FULL, write_mode=True))
    assert view.access_mode == AccessMode.BASIC
    assert view.write_mode is False
    driver = view.sql_driver
    assert isinstance(driver, SafeSqlExecutor)
    assert driver._config.allowed_schema == "public"
    assert driver._config.read_only is True
    assert driver._config.table_prefix == "app_"
    assert driver._validator.allow_explain_analyze is False


def test_truthy_non_bool_write_mode_never_gets_unrestricted_executor() -> None:
    """Небулево «истинное» write_mode не считается явной записью: только write_mode is True."""
    service = _service(access_mode=AccessMode.FULL, write_mode=True)
    crafted = EffectiveAccess(AccessMode.FULL, write_mode=True)
    object.__setattr__(crafted, "write_mode", "yes")
    driver = service.view(crafted).sql_driver
    assert not isinstance(driver, SqlExecutor)
    assert isinstance(driver, SafeSqlExecutor)
    assert driver._config.read_only is True


def test_catalog_driver_is_one_read_only_executor_for_every_access() -> None:
    """Каталог: один исполнитель на сервис, без схемы и префикса, только чтение, с таймаутом; не sql_driver агента."""
    service = _service(access_mode=AccessMode.FULL, write_mode=True, table_prefix="app_", safe_sql_timeout=7)
    views = [service.view(access) for access in _ALL_ACCESS]

    catalogs = {id(view.catalog_driver) for view in views}
    assert len(catalogs) == 1
    catalog = views[0].catalog_driver
    assert isinstance(catalog, CatalogSqlExecutor)
    assert all(view.catalog_driver is not view.sql_driver for view in views)

    inner = catalog._inner
    assert inner._config.read_only is True
    assert inner._config.allowed_schema is None
    assert inner._config.table_prefix is None
    assert inner._config.timeout == 7
    assert inner._config.query_tag == "postgres_fastmcp"
    assert inner._validator.read_only is True
    assert inner._validator.allowed_schema is None
    assert inner._validator.table_prefix is None
    assert inner._validator.allow_explain_analyze is False
    assert inner._delegate.conn is service._pool


def test_basic_agent_driver_keeps_prefix_next_to_catalog_driver() -> None:
    """Появление catalog_driver не меняет исполнитель агента в BASIC."""
    service = _service(access_mode=AccessMode.BASIC, write_mode=True, table_prefix="app_")
    for write_mode in (False, True):
        driver = service.view(EffectiveAccess(AccessMode.BASIC, write_mode=write_mode)).sql_driver
        assert isinstance(driver, SafeSqlExecutor)
        assert driver._validator.allowed_schema == "public"
        assert driver._validator.table_prefix == "app_"


def test_service_exposes_the_catalog_driver_of_its_views() -> None:
    """Проверка роли при старте идёт через тот же канал сервера, что и каталог в view()."""
    service = _service(access_mode=AccessMode.BASIC)
    assert service.catalog_driver is service.view(EffectiveAccess(AccessMode.BASIC, write_mode=False)).catalog_driver
    assert isinstance(service.catalog_driver, CatalogSqlExecutor)


def test_inactive_connection_lifetime_becomes_pool_max_idle() -> None:
    assert _service(max_inactive_connection_lifetime=42)._pool.max_idle == 42
