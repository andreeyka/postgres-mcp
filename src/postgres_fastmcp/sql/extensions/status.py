"""DTO статуса расширения PostgreSQL (результат проверки в sql.extensions.checker)."""

from dataclasses import dataclass


@dataclass
class ExtensionStatus:
    """Результат проверки расширения."""

    is_installed: bool
    is_available: bool
    name: str
    message: str
    default_version: str | None
    catalog_error: str | None = None
