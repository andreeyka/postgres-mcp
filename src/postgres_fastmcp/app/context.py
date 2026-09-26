"""Типизированный контракт lifespan-контекста сервера и аксессор для тулов.

Единая точка, описывающая форму ``ctx.lifespan_context``: его наполняет
``lifespan.build_lifespan`` (продюсер), а читают тулы через ``get_db`` (потребитель).
Это убирает дублирование нетипизированного доступа ``ctx.lifespan_context["db"]``.
"""

from typing import TypedDict, cast

from fastmcp.server.context import Context

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.domains.db_access import DbAccessPort


class LifespanContext(TypedDict):
    """Форма словаря, который lifespan кладёт в ``ctx.lifespan_context``."""

    db: DbAccessPort
    settings: Settings


def get_db(ctx: Context) -> DbAccessPort:
    """Вернуть доступ к БД из lifespan-контекста (единый типизированный доступ для всех тулов)."""
    return cast("DbAccessPort", ctx.lifespan_context["db"])
