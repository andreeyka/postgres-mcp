"""Тул get_object_details — детальная информация об объекте."""

from typing import Annotated, Any

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from pydantic import Field

from postgres_fastmcp.enums import ObjectType
from postgres_fastmcp.services.objects.service import ObjectsService


async def get_object_details(
    schema_name: Annotated[str, Field(description="Schema name.")],
    object_name: Annotated[str, Field(description="Object name.")],
    object_type: Annotated[
        ObjectType,
        Field(default="table", description="Object kind: 'table', 'view', 'sequence', 'extension'."),
    ] = "table",
    ctx: Context = CurrentContext(),
) -> dict[str, Any]:
    """Детали объекта: колонки, констрейнты, индексы и пр."""
    db = ctx.lifespan_context["db"]
    service = ObjectsService(db=db)
    return await service.get_object_details(
        schema_name=schema_name, object_name=object_name, object_type=object_type
    )
