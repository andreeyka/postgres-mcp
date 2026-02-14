"""Extension availability and hypopg status. Implements ExtensionInspectorPort adapter."""

import logging
from typing import Any, Literal, cast

from postgres_fastmcp.services.ports.extensions import ExtensionStatus
from postgres_fastmcp.sql.extensions.version import get_postgres_version


logger = logging.getLogger(__name__)

EXT_INSTALLED_QUERY = "SELECT extversion FROM pg_extension WHERE extname = {}"
EXT_AVAILABLE_QUERY = "SELECT default_version FROM pg_available_extensions WHERE name = {}"


class ExtensionInspectorAdapter:
    """Adapter that implements ExtensionInspectorPort using an executor and connection id."""

    def __init__(
        self,
        executor: Any,
        template: Any,
        connection_id: str,
    ) -> None:
        """Initialize with executor, template (for param queries), and connection id for cache."""
        self._executor = executor
        self._template = template
        self._connection_id = connection_id

    async def get_postgres_version(self) -> int:
        return await get_postgres_version(self._executor, self._connection_id)

    async def check_postgres_version_requirement(self, min_version: int, feature_name: str) -> tuple[bool, str]:
        version = await self.get_postgres_version()
        if version >= min_version:
            return True, f"PostgreSQL version {version} meets the requirement for {feature_name}"
        return False, (
            f"This feature ({feature_name}) requires PostgreSQL {min_version} or later. "
            f"Your current version is PostgreSQL {version or 'unknown'}."
        )

    async def _run_param(self, query: str, params: list[Any]) -> list[Any] | None:
        rendered = self._template.render(query, params)
        result = await self._executor.execute(rendered, params=None, readonly=True)
        return cast("list[Any] | None", result)

    async def check_extension(
        self,
        extension_name: str,
        *,
        include_messages: bool = True,
        message_type: Literal["plain", "markdown"] = "plain",
    ) -> ExtensionStatus:
        result = ExtensionStatus(
            is_installed=False,
            is_available=False,
            name=extension_name,
            message="",
            default_version=None,
        )
        installed = await self._run_param(EXT_INSTALLED_QUERY, [extension_name])
        if installed and len(installed) > 0:
            version = installed[0].cells.get("extversion", "unknown")
            result.is_installed = True
            result.is_available = True
            if include_messages:
                if message_type == "markdown":
                    result.message = f"The **{extension_name}** extension (version {version}) is already installed."
                else:
                    result.message = f"The {extension_name} extension (version {version}) is already installed."
            return result

        available = await self._run_param(EXT_AVAILABLE_QUERY, [extension_name])
        if available and len(available) > 0:
            result.is_available = True
            result.default_version = available[0].cells.get("default_version")
            if include_messages:
                if message_type == "markdown":
                    result.message = (
                        f"The **{extension_name}** extension is available but not installed.\n\n"
                        f"You can install it by running: `CREATE EXTENSION {extension_name};`."
                    )
                else:
                    result.message = (
                        f"The {extension_name} extension is available but not installed.\n"
                        f"You can install it by running: CREATE EXTENSION {extension_name};"
                    )
        elif include_messages:
            if message_type == "markdown":
                result.message = (
                    f"The **{extension_name}** extension is not available on this PostgreSQL server.\n\n"
                    "To install it, you need to:\n"
                    "1. Install the extension package on the server\n"
                    f"2. Run: `CREATE EXTENSION {extension_name};`"
                )
            else:
                result.message = (
                    f"The {extension_name} extension is not available on this PostgreSQL server.\n"
                    "To install it, you need to:\n"
                    "1. Install the extension package on the server\n"
                    f"2. Run: CREATE EXTENSION {extension_name};"
                )
        return result

    async def check_hypopg_installation_status(
        self, message_type: Literal["plain", "markdown"] = "markdown"
    ) -> tuple[bool, str]:
        status = await self.check_extension("hypopg", include_messages=False)
        if status.is_installed:
            if message_type == "markdown":
                return True, "The **hypopg** extension is already installed."
            return True, "The hypopg extension is already installed."
        if status.is_available:
            if message_type == "markdown":
                return False, (
                    "The **hypopg** extension is required to test hypothetical indexes, but it is not currently installed."
                    "\n\nYou can ask me to install 'hypopg' using the 'execute_query' tool.\n\n"
                    "**Is it safe?** Installing 'hypopg' is generally safe and a standard practice for index testing. "
                    "It adds a virtual layer that simulates indexes without actually creating them in the database. "
                    "It requires database privileges (often superuser) to install.\n\n"
                    "**What does it do?** It allows you to create virtual indexes and test how they would affect "
                    "query performance without the overhead of actually creating the indexes.\n\n"
                    "**How to undo?** If you later decide to remove it, you can ask me to run 'DROP EXTENSION hypopg;'."
                )
            return False, (
                "The hypopg extension is required to test hypothetical indexes, but it is not currently installed.\n"
                "You can ask me to install it using the 'execute_query' tool.\n"
                "It is generally safe to install and allows testing indexes without creating them."
            )
        pg_version = await self.get_postgres_version()
        major_str = f"{pg_version}" if pg_version > 0 else "XX"
        if message_type == "markdown":
            return False, (
                "The **hypopg** extension is not available on this PostgreSQL server.\n\n"
                "To install HypoPG:\n"
                f"1. For Debian/Ubuntu: `sudo apt-get install postgresql-{major_str}-hypopg`\n"
                f"2. For RHEL/CentOS: `sudo yum install postgresql{major_str}-hypopg`\n"
                "3. For MacOS with Homebrew: `brew install hypopg`\n"
                "4. For other systems, build from source: `git clone https://github.com/HypoPG/hypopg`\n\n"
                "After installing the extension packages, connect to your database and run: `CREATE EXTENSION hypopg;`"
            )
        return False, (
            "The hypopg extension is not available on this PostgreSQL server.\n"
            "To install HypoPG:\n"
            f"1. For Debian/Ubuntu: sudo apt-get install postgresql-{major_str}-hypopg\n"
            f"2. For RHEL/CentOS: sudo yum install postgresql{major_str}-hypopg\n"
            "3. For MacOS with Homebrew: brew install hypopg\n"
            "4. For other systems, build from source: git clone https://github.com/HypoPG/hypopg\n"
            "After installing the extension packages, connect to your database and run: CREATE EXTENSION hypopg;"
        )
