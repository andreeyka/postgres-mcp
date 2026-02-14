"""Schema MCP tools registration."""

from fastmcp.server.providers import LocalProvider
from fastmcp.tools.tool import ToolResult

from postgres_fastmcp.common.errors import BaseApplicationError
from postgres_fastmcp.di.schema_provider import SchemaServiceProvider
from postgres_fastmcp.enums import ToolTag
from postgres_fastmcp.services.schema.service import SchemaService
from postgres_fastmcp.tools.common import ToolDescriptions


def register_schema_tools(provider: LocalProvider, descriptions: ToolDescriptions) -> None:
    """Register schema tools."""

    @provider.tool(
        description=descriptions.list_schemas,
        tags={ToolTag.FULL},
        annotations={"readOnlyHint": True},
    )
    async def list_schemas(schema_service: SchemaService = SchemaServiceProvider) -> ToolResult:
        """List all schemas in the database."""
        try:
            return ToolResult(content=await schema_service.list_schemas())
        except BaseApplicationError as exc:
            return ToolResult(content=f"Error: {exc}")
        except Exception as exc:  # pragma: no cover - defensive fallback
            return ToolResult(content=f"Error: {exc}")
