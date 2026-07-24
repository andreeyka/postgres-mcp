"""Расширения PostgreSQL: статусы, версия сервера и адаптер-инспектор."""

import logging
from dataclasses import dataclass
from typing import Any, Literal, cast

from postgres_fastmcp.sql.ports import QueryExecutorPort, QueryTemplatePort


@dataclass
class ExtensionStatus:
    """Результат проверки расширения."""

    is_installed: bool
    is_available: bool
    name: str
    message: str
    default_version: str | None
    catalog_error: str | None = None


logger = logging.getLogger(__name__)


class PostgresVersionRegistry:
    """Кэш основной версии PostgreSQL по идентификатору подключения."""

    def __init__(self) -> None:
        self._cache: dict[str, int] = {}

    def get(self, connection_id: str) -> int | None:
        """Получение версии PostgreSQL из кэша по идентификатору подключения."""
        return self._cache.get(connection_id)

    def set(self, connection_id: str, version: int) -> None:
        """Установка версии PostgreSQL в кэш по идентификатору подключения."""
        self._cache[connection_id] = version

    def clear(self, connection_id: str | None = None) -> None:
        """Очистка кэша версий (например, для тестов)."""
        if connection_id is not None:
            self._cache.pop(connection_id, None)
        else:
            self._cache.clear()


_version_registry = PostgresVersionRegistry()


def reset_postgres_version_cache(connection_id: str | None = None) -> None:
    """Очистка кэша версий (например, для тестов)."""
    _version_registry.clear(connection_id)


async def get_postgres_version(executor: QueryExecutorPort, connection_id: str) -> int:
    """Возвращает основную версию PostgreSQL (например, 16), используя кэш по идентификатору подключения."""
    cached = _version_registry.get(connection_id)
    if cached is not None:
        return cached
    try:
        rows = await executor.execute("SHOW server_version", params=None, readonly=True)
        if not rows:
            logger.warning("Could not determine PostgreSQL version")
            return 0
        version_string = rows[0].cells.get("server_version")
        if version_string is None:
            return 0
        if isinstance(version_string, bytes):
            version_string = version_string.decode("utf-8")
        if not isinstance(version_string, str):
            version_string = str(version_string)
        major = version_string.split(".")[0]
        version = int(major)
        _version_registry.set(connection_id, version)
    except Exception as e:
        logger.warning("Error determining PostgreSQL version: %s", e)
        return 0
    else:
        return version


EXT_INSTALLED_QUERY = "SELECT extversion FROM pg_extension WHERE extname = {}"
EXT_AVAILABLE_QUERY = "SELECT default_version FROM pg_available_extensions WHERE name = {}"

CATALOG_ERROR_MESSAGE = (
    "Unable to determine extension status: the extension catalog reported an error. "
    "This is often caused by another extension's control file (e.g. unrecognized parameter such as "
    "'no_relocate' in a .control file). Fix or remove the problematic extension on the server, "
    "or upgrade PostgreSQL to a version that supports the parameter."
)


class ExtensionInspectorAdapter:
    """Адаптер проверки расширений и версии PostgreSQL поверх исполнителя SQL и идентификатора подключения."""

    def __init__(
        self,
        executor: QueryExecutorPort,
        template: QueryTemplatePort,
        connection_id: str,
    ) -> None:
        """Инициализация с исполнителем, шаблоном (для параметризованных запросов).

        Идентификатор подключения используется для кэша.
        """
        self._executor = executor
        self._template = template
        self._connection_id = connection_id

    async def get_postgres_version(self) -> int:
        """Получение версии PostgreSQL."""
        return await get_postgres_version(self._executor, self._connection_id)

    async def check_postgres_version_requirement(self, min_version: int, feature_name: str) -> tuple[bool, str]:
        """Проверка требований к версии PostgreSQL."""
        version = await self.get_postgres_version()
        if version >= min_version:
            return True, f"Версия PostgreSQL {version} соответствует требованию для {feature_name}"
        return False, (
            f"Для этой функции ({feature_name}) требуется PostgreSQL {min_version} или выше. "
            f"Ваша текущая версия PostgreSQL {version or 'неизвестна'}."
        )

    async def _run_param(self, query: str, params: list[Any]) -> list[Any] | None:
        rendered = self._template.render(query, params)
        result = await self._executor.execute(rendered, params=None, readonly=True)
        return cast("list[Any] | None", result)

    async def check_extension(  # noqa: C901
        self,
        extension_name: str,
        *,
        include_messages: bool = True,
        message_type: Literal["plain", "markdown"] = "plain",
    ) -> ExtensionStatus:
        """Проверка установки расширения.

        Args:
            extension_name: Имя расширения.
            include_messages: Включить сообщения.
            message_type: Тип сообщения.

        Returns:
            ExtensionStatus.
        """
        result = ExtensionStatus(
            is_installed=False,
            is_available=False,
            name=extension_name,
            message="",
            default_version=None,
        )
        try:
            installed = await self._run_param(EXT_INSTALLED_QUERY, [extension_name])
        except Exception as e:
            logger.warning("Extension catalog query failed (pg_extension): %s", e)
            result.catalog_error = CATALOG_ERROR_MESSAGE
            return result
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

        try:
            available = await self._run_param(EXT_AVAILABLE_QUERY, [extension_name])
        except Exception as e:
            logger.warning("Extension catalog query failed (pg_available_extensions): %s", e)
            result.catalog_error = CATALOG_ERROR_MESSAGE
            return result
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

    async def check_hypopg_installation_status(  # noqa: PLR0911
        self, message_type: Literal["plain", "markdown"] = "markdown"
    ) -> tuple[bool, str]:
        """Проверка установки расширения hypopg."""
        status = await self.check_extension("hypopg", include_messages=False)
        if status.catalog_error:
            return False, status.catalog_error
        if status.is_installed:
            if message_type == "markdown":
                return True, "The **hypopg** extension is already installed."
            return True, "The hypopg extension is already installed."
        if status.is_available:
            if message_type == "markdown":
                return False, (
                    "The **hypopg** extension is required to test hypothetical indexes, "
                    "but it is not currently installed."
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
