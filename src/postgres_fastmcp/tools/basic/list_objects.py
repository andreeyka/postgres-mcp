"""Тул list_objects — список объектов в схеме."""

from typing import Annotated, Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import Field

from postgres_fastmcp.enums import ObjectType
from postgres_fastmcp.services.objects.service import ObjectsService


async def list_objects(
    schema_name: Annotated[str, Field(description="Schema name to inspect.")],
    object_type: Annotated[
        ObjectType,
        Field(default="table", description="Object kind: 'table', 'view', 'sequence', 'extension'."),
    ] = "table",
    ctx: Context = CurrentContext(),
) -> list[dict[str, Any]]:
    """Получить список объектов указанного типа в схеме."""
    db = ctx.lifespan_context["db"]
    service = ObjectsService(db=db)
    return await service.list_objects(schema_name=schema_name, object_type=object_type)
