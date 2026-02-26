"""Инструмент list_schemas (full)."""

from typing import Any

from fastmcp.tools import tool

from postgres_fastmcp.enums import ToolTag
from postgres_fastmcp.providers.schema_provider import SchemaServiceProvider
from postgres_fastmcp.services.schema.service import SchemaService


_DESC = (
    "List all schemas in the PostgreSQL database. "
    "Output: JSON array with schema names, owners, and types (System Schema, User Schema, etc.). "
    "Use this tool first to discover available schemas before exploring tables and other objects. "
    "Example workflow: 1) list_schemas, 2) list_objects with schema_name, 3) get_object_details for specific objects."
)


@tool(
    description=_DESC,
    tags={ToolTag.FULL},
    annotations={
        "title": "List Schemas",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def list_schemas(
    schema_service: SchemaService = SchemaServiceProvider,
) -> list[dict[str, Any]]:
    """Список всех схем в базе данных.

    Returns:
        Список схем. FastMCP преобразует в ответ. При ошибке — исключение наружу.
    """
    return await schema_service.list_schemas()
