# ruff: noqa: E501
"""Обработчик list_objects (описание по access_mode из AppConfig)."""

from typing import Annotated, Any

from fastmcp.tools import tool
from pydantic import Field

from postgres_fastmcp.config import app_config
from postgres_fastmcp.enums import AccessMode, ToolTag
from postgres_fastmcp.providers.objects_provider import ObjectsServiceProvider
from postgres_fastmcp.services.objects.service import ObjectsService


_DESC_USER = (
    "List objects (tables, views, sequences, or extensions) in the 'public' schema. "
    "Input: schema_name (optional, will be automatically set to 'public') and object_type (optional: 'table', 'view', 'sequence', or 'extension', default: 'table'). "
    "Output: JSON array of object names with their types. "
    "\n\nIMPORTANT: You have access only to the 'public' schema. The schema_name parameter is automatically set to 'public' if not specified. "
    "After listing objects, use get_object_details to examine the structure of specific objects. "
    "Example Input: object_type='table' (schema_name will be 'public' automatically)"
)
_DESC_FULL = (
    "List objects (tables, views, sequences, or extensions) in a specified schema. "
    "Input: schema_name (required) and object_type (optional: 'table', 'view', 'sequence', or 'extension', default: 'table'). "
    "Output: JSON array of object names with their types. "
    "\n\nIMPORTANT: Use this tool after list_schemas to discover what objects exist in a schema. "
    "Then use get_object_details to examine the structure of specific objects. "
    "Example Input: schema_name='public', object_type='table'"
)
_DESC = _DESC_USER if app_config.current.database.access_mode == AccessMode.BASIC else _DESC_FULL


@tool(
    description=_DESC,
    tags={ToolTag.BASIC},
    annotations={
        "title": "List Objects",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def list_objects(
    schema_name: Annotated[
        str,
        Field(description="Schema name as string value for filtering objects by database schema location"),
    ],
    object_type: Annotated[
        str,
        Field(
            default="table",
            description=(
                "Object type as string value: 'table' for tables, 'view' for views, "
                "'sequence' for sequences, or 'extension' for PostgreSQL extensions"
            ),
        ),
    ] = "table",
    objects_service: ObjectsService = ObjectsServiceProvider,
) -> list[dict[str, Any]]:
    """Список объектов в схеме.

    Returns:
        Список объектов. FastMCP преобразует в ответ. При ошибке — исключение наружу.
    """
    return await objects_service.list_objects(schema_name=schema_name, object_type=object_type)
