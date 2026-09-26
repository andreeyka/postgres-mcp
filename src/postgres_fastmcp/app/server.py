"""Фабрика MCP-сервера: FastMCP поверх PostgresProvider с middleware сервера."""

from collections.abc import Sequence

from fastmcp import FastMCP
from fastmcp.server.auth import AuthProvider
from fastmcp.server.middleware import Middleware
from fastmcp.server.middleware.logging import LoggingMiddleware
from fastmcp.server.middleware.timing import TimingMiddleware
from fastmcp.server.providers import Provider

from postgres_fastmcp.access import AccessResolver
from postgres_fastmcp.app.auth import build_auth_provider
from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.middleware.response_budget import ResponseBudgetMiddleware
from postgres_fastmcp.provider import PostgresProvider
from postgres_fastmcp.shared.enums import TransportConfig
from postgres_fastmcp.shared.logger import get_logger


logger = get_logger(__name__)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _warn_about_auth(settings: Settings, auth: AuthProvider | None, *, custom_resolver: bool) -> None:
    """Предупредить на старте о конфигурации, где auth не защищает так, как ожидает оператор."""
    transport = settings.server.transport
    if transport == TransportConfig.HTTP and auth is None and settings.server.host not in _LOOPBACK_HOSTS:
        logger.warning(
            "HTTP server on %s has no authentication: every client that can reach it gets the full access "
            "ceiling of the database config. Set MCP_AUTH_MODE or bind to 127.0.0.1.",
            settings.server.host,
        )
    if transport == TransportConfig.STDIO and auth is not None:
        logger.warning(
            "Authentication (%s) applies only to the HTTP transport; over stdio every request gets the access "
            "ceiling of the database config.",
            type(auth).__name__,
        )
    if auth is None and settings.auth.access_policy.enforced and not custom_resolver:
        logger.warning(
            "auth.access_policy.enforced=true has no effect without authentication: requests carry no token, "
            "so every request gets the access ceiling. Set MCP_AUTH_MODE."
        )


def create_server(
    settings: Settings,
    *,
    auth: AuthProvider | None = None,
    access_resolver: AccessResolver | None = None,
    extra_providers: Sequence[Provider] = (),
    extra_middleware: Sequence[Middleware] = (),
) -> FastMCP:
    """Собрать FastMCP-сервер: PostgresProvider + auth + middleware.

    Args:
        settings: Конфигурация (database, server, fastmcp, auth блоки).
        auth: Auth-провайдер FastMCP; если не задан, строится из settings.auth
            (mode=none — без аутентификации).
        access_resolver: Свой резолвер токен -> права для PostgresProvider; приоритетнее
            settings.auth.access_policy (результат ограничивается потолком из settings.database).
        extra_providers: Дополнительные FastMCP-провайдеры от потребителя библиотеки.
        extra_middleware: Дополнительные middleware (встают после встроенных; бюджет ответа
            стоит первым и проверяет и их результат).

    Returns:
        Готовый FastMCP, на котором можно сразу вызывать `.run(...)`.
    """
    auth = auth if auth is not None else build_auth_provider(settings.auth)
    _warn_about_auth(settings, auth, custom_resolver=access_resolver is not None)
    provider = PostgresProvider(
        settings.database,
        access_policy=settings.auth.access_policy,
        access_resolver=access_resolver,
    )
    return FastMCP(
        name=settings.fastmcp.server_name,
        instructions=settings.fastmcp.instructions or None,
        auth=auth,
        providers=[provider, *extra_providers],
        # Первым = внешним: бюджет проверяет ровно то, что уходит клиенту, включая результат extra_middleware
        middleware=[
            ResponseBudgetMiddleware(settings.server.response_max_tokens),
            TimingMiddleware(),
            LoggingMiddleware(),
            *extra_middleware,
        ],
        mask_error_details=True,
        on_duplicate="error",
    )
