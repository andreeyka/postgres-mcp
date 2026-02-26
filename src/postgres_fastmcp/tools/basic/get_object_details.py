# ruff: noqa: E501
"""Обработчик get_object_details (описание по access_mode из AppConfig)."""

from typing import Annotated, Any

from fastmcp.tools import tool
from pydantic import Field

from postgres_fastmcp.config import app_config
from postgres_fastmcp.enums import AccessMode, ToolTag
from postgres_fastmcp.providers.objects_provider import ObjectsServiceProvider
from postgres_fastmcp.services.objects.service import ObjectsService


_DESC_USER = (
    "Show detailed information about a database object (table, view, sequence, or extension) in the 'public' schema. "
    "Input: schema_name (optional, will be automatically set to 'public'), object_name (required), and object_type (optional: 'table', 'view', 'sequence', or 'extension', default: 'table'). "
    "Output: Detailed object information including columns, constraints, indexes (for tables), and other metadata. "
    "\n\nIMPORTANT: You have access only to the 'public' schema. The schema_name parameter is automatically set to 'public' if not specified. "
    "Use this tool after list_objects to understand the structure of tables before writing SQL queries. "
    "This tool shows column names, data types, constraints, and indexes - essential information for writing correct SQL. "
    "Example workflow: 1) list_objects, 2) get_object_details, 3) execute_sql with proper column names."
)
_DESC_FULL = (
    "Show detailed information about a database object (table, view, sequence, or extension). "
    "Input: schema_name (required), object_name (required), and object_type (optional: 'table', 'view', 'sequence', or 'extension', default: 'table'). "
    "Output: Detailed object information including columns, constraints, indexes (for tables), and other metadata. "
    "\n\nIMPORTANT: Use this tool after list_objects to understand the structure of tables before writing SQL queries. "
    "This tool shows column names, data types, constraints, and indexes - essential information for writing correct SQL. "
    "Example workflow: 1) list_schemas, 2) list_objects, 3) get_object_details, 4) execute_sql with proper column names."
)
_DESC = _DESC_USER if app_config.current.database.access_mode == AccessMode.BASIC else _DESC_FULL


@tool(
    description=_DESC,
    tags={ToolTag.BASIC},
    annotations={
        "title": "Get Object Details",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def get_object_details(
    schema_name: Annotated[
        str,
        Field(description="Schema name as string value for identifying the database schema containing the object"),
    ],
    object_name: Annotated[
        str,
        Field(
            description=(
                "Object name as string value for identifying the specific database object "
                "(table, view, sequence, or extension)"
            )
        ),
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
) -> dict[str, Any]:
    """Получить детальную информацию об объекте.

    Returns:
        Детали объекта. FastMCP преобразует в ответ. При ошибке — исключение наружу.
    """
    return await objects_service.get_object_details(
        schema_name=schema_name,
        object_name=object_name,
        object_type=object_type,
    )
