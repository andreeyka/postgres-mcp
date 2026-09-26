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
