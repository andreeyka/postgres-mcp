"""FastMCP configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class FastMCPSettings(BaseSettings):
    """FastMCP settings."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="MCP_", extra="ignore")

    server_name: str = Field(default="postgres-fastmcp", description="MCP server name (MCP_SERVER_NAME)")
    instructions: str = Field(
        default=(
            "MCP server for PostgreSQL: schema discovery, query execution, "
            "EXPLAIN analysis, index recommendations, and database health checks."
        ),
        description="Instructions describing the server's purpose for LLM clients (MCP_INSTRUCTIONS)",
    )
    mask_error_details: bool = Field(
        default=True, description="Mask internal error details for security (MCP_MASK_ERROR_DETAILS)"
    )
    return_errors_as_strings: bool = Field(
        default=True,
        description="Return errors as strings in LLM responses instead of standard MCP errors",
    )
    error_traceback_in_strings: bool = Field(
        default=False, description="Include traceback in error strings when return_errors_as_strings=True"
    )
