"""Configuration models for database (single DB per server)."""

from pydantic import BaseModel, Field, SecretStr

from postgres_fastmcp.enums import AccessMode, ToolName, UserRole


# Convenience constants for tool tagging (basic vs admin)
AVAILABLE_TOOLS: list[ToolName] = ToolName.available_tools()
BASIC_TOOLS: list[ToolName] = ToolName.basic_tools()
ADMIN_TOOLS: list[ToolName] = ToolName.admin_tools()


class DatabaseConfig(BaseModel):
    """Single-database configuration (one DB per MCP server)."""

    database_uri: SecretStr = Field(description="Database connection URL")
    extra_kwargs: dict[str, str] = Field(default_factory=dict, description="Extra keyword arguments")
    access_mode: AccessMode = Field(
        default=AccessMode.RESTRICTED,
        description=(
            "SQL access level for the server. "
            "Available modes: 'restricted' (read-only, SELECT only), "
            "'unrestricted' (read-write, DML: INSERT/UPDATE/DELETE, or full access with DDL for full role)."
        ),
    )
    role: UserRole = Field(
        default=UserRole.USER,
        description=(
            "User role that determines schema access and available tools. "
            "Available roles: 'user' (only public schema, basic tools - 4 tools), "
            "'full' (all schemas, all tools - 9 tools, extended privileges)."
        ),
    )
    # Connection pool settings
    pool_min_size: int = Field(default=1, description="Minimum number of connections in the pool")
    pool_max_size: int = Field(default=5, description="Maximum number of connections in the pool")
    safe_sql_timeout: int = Field(
        default=30,
        description=(
            "Timeout in seconds for SafeSqlDriver. "
            "Used for all modes except 'full' role with 'unrestricted' access_mode."
        ),
    )
    table_prefix: str | None = Field(
        default=None,
        description=(
            "Optional table name prefix for 'user' role. "
            "If set, only tables/views/sequences with names starting with this prefix are accessible. "
            "Works only for 'user' role. Ignored for 'full' role."
        ),
    )
    query_tag: str | None = Field(
        default=None,
        description="Optional tag for SafeSqlDriver (e.g. server name). Set when building MCP when not provided.",
    )
