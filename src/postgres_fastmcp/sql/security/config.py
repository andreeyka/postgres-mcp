"""Configuration for safe SQL execution."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SafeSqlConfig:
    """Configuration for SafeSqlExecutor.

    Attributes:
        query_tag: Tag added to queries for logging/monitoring.
        timeout: Optional execution timeout in seconds.
        allowed_schema: Allowed schema (e.g. 'public'); None means all.
        read_only: If True, only read statements; if False, DML allowed.
        table_prefix: If set with allowed_schema, only tables with this prefix.
    """

    query_tag: str = "postgres-fastmcp"
    timeout: float | None = None
    allowed_schema: str | None = None
    read_only: bool = True
    table_prefix: str | None = None
