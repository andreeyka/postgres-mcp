"""Application configuration and settings (one MCP server = one database)."""

import json
from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from postgres_fastmcp.config.database import DatabaseConfig
from postgres_fastmcp.config.fastmcp import FastMCPSettings
from postgres_fastmcp.config.server import ServerSettings
from postgres_fastmcp.enums import AccessMode, UserRole


__all__ = ["Settings", "build_settings_from_cli", "get_settings", "settings"]


class Settings(BaseSettings):
    """Application settings (single database per server).

    Loaded from: environment variables, .env, config.json, defaults.
    Consumers use nested config via DI or direct access: settings.server, settings.fastmcp, settings.database.

    Example env: MCP_SERVER_HOST=0.0.0.0, MCP_DATABASE__DATABASE_URI=postgresql://...
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_prefix="MCP_",
        env_nested_delimiter="__",
    )

    server: ServerSettings = Field(default_factory=ServerSettings)
    fastmcp: FastMCPSettings = Field(default_factory=FastMCPSettings)
    database: DatabaseConfig = Field(..., description="Single database configuration")


def load_json_config(json_path: Path) -> dict[str, Any] | None:
    """Load configuration from JSON file.

    Args:
        json_path: Path to JSON file.

    Returns:
        Configuration dictionary or None if file not found.
    """
    if not json_path.exists():
        return None

    try:
        with json_path.open(encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
            return data
    except (json.JSONDecodeError, OSError):
        return None


def get_settings(**overrides: Any) -> Settings:
    """Factory function to create settings instance.

    Loads configuration in the following priority order:
    1. **overrides parameters (highest priority)
    2. config.json file (if exists)
    3. Environment variables
    4. .env file (if exists)
    5. Default values from class

    Args:
        **overrides: Parameters to override default values.

    Returns:
        Settings instance with loaded configuration.

    Examples:
        >>> settings = get_settings()
        >>> test_settings = get_settings(server={"host": "127.0.0.1", "port": 9000})
    """
    # Try to find config.json in current directory
    json_config = load_json_config(Path("config.json"))

    if overrides:
        if json_config:
            merged_config = {**json_config, **dict(overrides)}
            return Settings(**merged_config)
        return Settings(**overrides)

    if json_config:
        return Settings(**json_config)

    # Otherwise use standard BaseSettings loading (env, .env, defaults)
    return Settings()


def build_settings_from_cli(  # noqa: PLR0913
    *,
    database_uri: str | None = None,
    transport: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
    workers: int = 1,
    access_mode: str | None = None,
    role: str | None = None,
) -> Settings:
    """Build Settings from CLI arguments (single source for current rights/config).

    Encapsulates branching: CLI database_uri vs config file, transport overrides.
    Use the returned settings.database as the single "current permissions" for the app.
    """
    if database_uri:
        database_config = DatabaseConfig(
            database_uri=SecretStr(database_uri),
            access_mode=AccessMode(access_mode) if access_mode else AccessMode.RESTRICTED,
            role=UserRole(role) if role else UserRole.USER,
        )
        server_overrides: dict[str, Any] = {"host": host, "port": port, "workers": workers}
        if transport is not None:
            server_overrides["transport"] = transport
        return get_settings(database=database_config, server=server_overrides)
    if transport is not None:
        return get_settings(server={"transport": transport})
    return get_settings()


settings = get_settings()
