"""Port and DTO for PostgreSQL extension inspection."""

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass
class ExtensionStatus:
    """Result of checking an extension."""

    is_installed: bool
    is_available: bool
    name: str
    message: str
    default_version: str | None


class ExtensionInspectorPort(Protocol):
    """Port for PostgreSQL version and extension checks."""

    async def get_postgres_version(self) -> int:
        """Return major PostgreSQL version (e.g. 16)."""
        ...

    async def check_postgres_version_requirement(self, min_version: int, feature_name: str) -> tuple[bool, str]:
        """Return (meets_requirement, message)."""
        ...

    async def check_extension(
        self,
        extension_name: str,
        *,
        include_messages: bool = True,
        message_type: Literal["plain", "markdown"] = "plain",
    ) -> ExtensionStatus:
        """Check if extension is installed or available."""
        ...

    async def check_hypopg_installation_status(
        self, message_type: Literal["plain", "markdown"] = "markdown"
    ) -> tuple[bool, str]:
        """Return (installed_ok, message) for hypopg extension."""
        ...
