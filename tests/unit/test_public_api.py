"""Тесты публичного API пакета postgres_fastmcp."""

import postgres_fastmcp as pkg
from postgres_fastmcp.access import AccessPolicy, EffectiveAccess, full_access_check
from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.config.database import DatabaseConfig
from postgres_fastmcp.app.middleware.response_budget import ResponseBudgetMiddleware
from postgres_fastmcp.app.server import create_server
from postgres_fastmcp.provider import PostgresProvider


_EXPECTED = {
    "AccessPolicy",
    "AccessResolver",
    "DatabaseConfig",
    "EffectiveAccess",
    "PostgresProvider",
    "ResponseBudgetMiddleware",
    "Settings",
    "__version__",
    "create_server",
    "full_access_check",
}


def test_public_api_exports_exactly_the_spec_list() -> None:
    assert set(pkg.__all__) == _EXPECTED
    missing = {name for name in _EXPECTED if not hasattr(pkg, name)}
    assert not missing, f"Missing exports: {missing}"


def test_fastmcp_reexports_are_gone() -> None:
    """Middleware, LocalProvider, FileSystemProvider импортируются из fastmcp, а не из пакета."""
    for name in ("Middleware", "LocalProvider", "FileSystemProvider"):
        assert not hasattr(pkg, name), name


def test_exports_are_the_implementation_objects() -> None:
    assert pkg.PostgresProvider is PostgresProvider
    assert pkg.DatabaseConfig is DatabaseConfig
    assert pkg.AccessPolicy is AccessPolicy
    assert pkg.EffectiveAccess is EffectiveAccess
    assert pkg.full_access_check is full_access_check
    assert pkg.ResponseBudgetMiddleware is ResponseBudgetMiddleware
    assert pkg.Settings is Settings
    assert pkg.create_server is create_server
