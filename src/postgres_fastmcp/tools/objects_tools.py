"""Object metadata MCP tools registration."""

from typing import Annotated

from fastmcp.server.providers import LocalProvider
from fastmcp.tools.tool import ToolResult
from pydantic import Field

from postgres_fastmcp.common.errors import BaseApplicationError
from postgres_fastmcp.di.objects_provider import ObjectsServiceProvider
from postgres_fastmcp.enums import ToolTag
from postgres_fastmcp.services.objects.service import ObjectsService
from postgres_fastmcp.tools.common import ToolDescriptions


def register_objects_tools(provider: LocalProvider, descriptions: ToolDescriptions) -> None:
    """Register object listing and details tools."""

    @provider.tool(
        description=descriptions.list_objects,
        tags={ToolTag.BASIC},
        annotations={"readOnlyHint": True},
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
    ) -> ToolResult:
        """List objects in schema."""
        try:
            content = await objects_service.list_objects(schema_name=schema_name, object_type=object_type)
            return ToolResult(content=content)
        except BaseApplicationError as exc:
            return ToolResult(content=f"Error: {exc}")
        except Exception as exc:  # pragma: no cover - defensive fallback
            return ToolResult(content=f"Error: {exc}")

    @provider.tool(
        description=descriptions.get_object_details,
        tags={ToolTag.BASIC},
        annotations={"readOnlyHint": True},
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
    ) -> ToolResult:
        """Get detailed information about object."""
        try:
            content = await objects_service.get_object_details(
                schema_name=schema_name,
                object_name=object_name,
                object_type=object_type,
            )
            return ToolResult(content=content)
        except BaseApplicationError as exc:
            return ToolResult(content=f"Error: {exc}")
        except Exception as exc:  # pragma: no cover - defensive fallback
            return ToolResult(content=f"Error: {exc}")
