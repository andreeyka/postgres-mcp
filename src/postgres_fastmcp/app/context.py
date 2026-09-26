"""Типизированный контракт lifespan-контекста сервера и аксессор для тулов.

Единая точка, описывающая форму ``ctx.lifespan_context``: его наполняет
``lifespan.build_lifespan`` (продюсер), а читает ToolSet через ``get_db`` (потребитель).
Это убирает дублирование нетипизированного доступа ``ctx.lifespan_context["db"]``.
"""

from typing import TypedDict, cast

from fastmcp.server.dependencies import get_context

from postgres_fastmcp.app.config import Settings
from postgres_fastmcp.domains.db_access import DbAccessPort


class LifespanContext(TypedDict):
    """Форма словаря, который lifespan кладёт в ``ctx.lifespan_context``."""

    db: DbAccessPort
    settings: Settings


def get_db() -> DbAccessPort:
    """Вернуть доступ к БД из lifespan-контекста текущего запроса (для ToolSet)."""
    return cast("DbAccessPort", get_context().lifespan_context["db"])
