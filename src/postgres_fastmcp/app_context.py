"""Типизированный контракт lifespan-контекста сервера и аксессор для тулов.

Единая точка, описывающая форму ``ctx.lifespan_context``: его наполняет
``lifespan.build_lifespan`` (продюсер), а читают тулы через ``get_db`` (потребитель).
Это убирает дублирование нетипизированного доступа ``ctx.lifespan_context["db"]``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict, cast


if TYPE_CHECKING:
    from fastmcp.server.context import Context

    from postgres_fastmcp.config import Settings
    from postgres_fastmcp.services.db_access_service import DbAccessService


class LifespanContext(TypedDict):
    """Форма словаря, который lifespan кладёт в ``ctx.lifespan_context``."""

    db: DbAccessService
    settings: Settings


def get_db(ctx: Context) -> DbAccessService:
    """Вернуть DbAccessService из lifespan-контекста (единый типизированный доступ для всех тулов)."""
    return cast("DbAccessService", ctx.lifespan_context["db"])
