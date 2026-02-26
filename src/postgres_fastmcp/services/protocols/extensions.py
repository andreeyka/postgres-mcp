"""Протокол и DTO для проверки расширений PostgreSQL."""

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass
class ExtensionStatus:
    """Результат проверки расширения."""

    is_installed: bool
    is_available: bool
    name: str
    message: str
    default_version: str | None
    catalog_error: str | None = None


class ExtensionInspectorPort(Protocol):
    """Протокол для проверки версии PostgreSQL и расширений."""

    async def get_postgres_version(self) -> int:
        """Возвращает основную версию PostgreSQL (например, 16)."""
        ...

    async def check_postgres_version_requirement(self, min_version: int, feature_name: str) -> tuple[bool, str]:
        """Возвращает (соответствие требованиям, сообщение)."""
        ...

    async def check_extension(
        self,
        extension_name: str,
        *,
        include_messages: bool = True,
        message_type: Literal["plain", "markdown"] = "plain",
    ) -> ExtensionStatus:
        """Проверка, установлено ли расширение или доступно ли оно."""
        ...

    async def check_hypopg_installation_status(
        self, message_type: Literal["plain", "markdown"] = "markdown"
    ) -> tuple[bool, str]:
        """Возвращает (установка успешна, сообщение) для расширения hypopg."""
        ...
