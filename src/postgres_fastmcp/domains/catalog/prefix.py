"""Префикс имён объектов в BASIC: одно правило для списков и деталей каталога."""

from postgres_fastmcp.domains.db_access import DbAccessPort
from postgres_fastmcp.shared.enums import AccessMode


def active_prefix(db: DbAccessPort) -> str | None:
    """Префикс, который действует в запросе: только BASIC с непустым table_prefix."""
    if db.access_mode == AccessMode.BASIC and db.table_prefix:
        return db.table_prefix
    return None


def matches_prefix(name: str, prefix: str) -> bool:
    """Имя начинается с префикса без учёта регистра — то же правило, что у валидатора SQL (schema_guard)."""
    return name.lower().startswith(prefix.lower())
