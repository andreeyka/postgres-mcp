"""Тул list_schemas — все схемы БД."""

from typing import Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context

from postgres_fastmcp.app_context import get_db
from postgres_fastmcp.services.schema.service import SchemaService


async def list_schemas(ctx: Context = CurrentContext()) -> list[dict[str, Any]]:
    """Список схем БД."""
    db = get_db(ctx)
    service = SchemaService(db=db)
    return await service.list_schemas()
