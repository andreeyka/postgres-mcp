"""Server configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from postgres_fastmcp.enums import TransportConfig


class ServerSettings(BaseSettings):
    """Server settings."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="MCP_", extra="ignore")

    host: str = Field(default="127.0.0.1", description="Host to bind the server to")
    port: int = Field(default=8000, description="Port to bind the server to")
    transport: TransportConfig = Field(
        default=TransportConfig.HTTP, description="Global transport type: 'http' or 'stdio'"
    )
    endpoint: str = Field(default="mcp", description="Default endpoint path")
    workers: int = Field(default=1, description="Number of workers to run")
    deprecation_warnings: bool = Field(default=True, description="Suppress deprecation warnings")
    health_endpoint_enabled: bool = Field(
        default=True, description="Enable health check endpoint at /health (no authorization required)"
    )
