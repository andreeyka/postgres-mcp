"""Фабрика MCP-сервера: create_server(settings, *, auth, extra_providers, extra_middleware) -> FastMCP."""

from collections.abc import Sequence
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.middleware.logging import LoggingMiddleware
from fastmcp.server.middleware.timing import TimingMiddleware

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.app.lifespan import build_lifespan
from postgres_fastmcp.app.middleware.response_budget import ResponseBudgetMiddleware
from postgres_fastmcp.shared.enums import AccessMode, ToolTag
from postgres_fastmcp.tools.registry import register_tools


def create_server(
    settings: Settings,
    *,
    auth: Any = None,  # noqa: ANN401
    extra_providers: Sequence[Any] = (),
    extra_middleware: Sequence[Any] = (),
) -> FastMCP:
    """Собрать FastMCP-сервер: lifespan + middleware + регистрация тулов + visibility.

    Args:
        settings: Конфигурация (database, server, fastmcp блоки).
        auth: Опциональный auth-provider FastMCP (Bearer/JWT/custom).
            По умолчанию без авторизации.
        extra_providers: Дополнительные FastMCP-провайдеры от потребителя библиотеки.
        extra_middleware: Дополнительные middleware (встают после встроенных; бюджет ответа
            стоит первым и проверяет и их результат).

    Returns:
        Готовый FastMCP, на котором можно сразу вызывать `.run(...)`.
    """
    lifespan_cm = build_lifespan(settings)

    fastmcp_kwargs: dict[str, Any] = {
        "name": settings.fastmcp.server_name,
        "lifespan": lifespan_cm,
        "mask_error_details": True,
        "on_duplicate": "error",
    }
    instructions = getattr(settings.fastmcp, "instructions", None)
    if instructions:
        fastmcp_kwargs["instructions"] = instructions
    if auth is not None:
        fastmcp_kwargs["auth"] = auth
    if extra_providers:
        fastmcp_kwargs["providers"] = list(extra_providers)

    mcp = FastMCP(**fastmcp_kwargs)

    # Первым = внешним: бюджет проверяет ровно то, что уходит клиенту, включая результат extra_middleware
    mcp.add_middleware(ResponseBudgetMiddleware(settings.server.response_max_tokens))
    mcp.add_middleware(TimingMiddleware())
    mcp.add_middleware(LoggingMiddleware())
    for m in extra_middleware:
        mcp.add_middleware(m)

    register_tools(mcp, settings)

    if settings.database.access_mode == AccessMode.BASIC:
        mcp.disable(tags={ToolTag.FULL.value})

    return mcp
