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
from postgres_fastmcp.shared.enums import AuthMode, TransportConfig
from postgres_fastmcp.shared.logger import get_logger


logger = get_logger(__name__)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _warn_about_auth(
    settings: Settings,
    auth: AuthProvider | None,
    *,
    custom_resolver: bool,
    explicit_auth: bool,
    build_auth: bool,
) -> None:
    """Предупредить на старте о конфигурации, где auth не защищает так, как ожидает оператор.

    ``explicit_auth`` — вызывающий передал свой ``AuthProvider`` в ``create_server``, а не полагался
    на ``settings.auth``. ``build_auth`` — как вызвали ``create_server``: False означает, что сборка
    провайдера из ``settings.auth`` была сознательно пропущена вызывающим (см. ``create_server``).
    ``settings.server.transport`` — только конфигурационное значение для этих сообщений, не гарантия
    того, каким транспортом вызывающий реально поднимет сервер.
    """
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
    skipped_by_flag = not explicit_auth and not build_auth and settings.auth.mode != AuthMode.NONE
    if skipped_by_flag:
        logger.warning(
            "Authentication (mode=%s) is configured but build_auth=False skipped building the provider for it: "
            "every request gets the access ceiling of the database config unless the caller enforces auth itself.",
            settings.auth.mode,
        )
    if auth is None and settings.auth.access_policy.enforced and not custom_resolver and not skipped_by_flag:
        logger.warning(
            "auth.access_policy.enforced=true has no effect without authentication: requests carry no token, "
            "so every request gets the access ceiling. Set MCP_AUTH_MODE."
        )


def create_server(  # noqa: PLR0913
    settings: Settings,
    *,
    auth: AuthProvider | None = None,
    build_auth: bool = True,
    access_resolver: AccessResolver | None = None,
    extra_providers: Sequence[Provider] = (),
    extra_middleware: Sequence[Middleware] = (),
) -> FastMCP:
    """Собрать FastMCP-сервер: PostgresProvider + auth + middleware.

    Args:
        settings: Конфигурация (database, server, fastmcp, auth блоки).
        auth: Auth-провайдер FastMCP; если не задан, строится из settings.auth при
            build_auth=True (по умолчанию); mode=none — без аутентификации.
        build_auth: Строить ли провайдер из settings.auth, когда auth не передан явно.
            По умолчанию True: библиотечный вызов всегда fail-closed, независимо от
            settings.server.transport — это конфигурационное значение и не обязано
            совпадать с транспортом, которым вызывающий реально поднимет сервер через
            ``mcp.run(transport=...)``; settings.server.transport="stdio" с последующим
            ``mcp.run(transport="http")`` обслуживал бы HTTP вовсе без аутентификации,
            если бы create_server решал по нему. CLI (``app/main.py``) передаёт
            build_auth=False, только когда сам действительно запускает stdio: тогда
            oidc не делает синхронный discovery-запрос и не пишет в FASTMCP_HOME на
            старте, и недоступный IdP не мешает stdio-серверу запуститься.
        access_resolver: Свой резолвер токен -> права для PostgresProvider; приоритетнее
            settings.auth.access_policy (результат ограничивается потолком из settings.database).
        extra_providers: Дополнительные FastMCP-провайдеры от потребителя библиотеки.
        extra_middleware: Дополнительные middleware (встают после встроенных; бюджет ответа
            стоит первым и проверяет и их результат).

    Returns:
        Готовый FastMCP, на котором можно сразу вызывать `.run(...)`.
    """
    explicit_auth = auth is not None
    if not explicit_auth and build_auth:
        auth = build_auth_provider(settings.auth)
    _warn_about_auth(
        settings,
        auth,
        custom_resolver=access_resolver is not None,
        explicit_auth=explicit_auth,
        build_auth=build_auth,
    )
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
