"""Конфигурация безопасного выполнения SQL."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SafeSqlConfig:
    """Конфигурация SafeSqlExecutor.

    Attributes:
        query_tag: Тег добавляется к запросам для логирования/мониторинга.
        timeout: Необязательный таймаут выполнения в секундах.
        allowed_schema: Разрешенная схема (например, 'public'); None означает все.
        read_only: Если True, только операторы чтения; если False, разрешен DML.
        table_prefix: Если задан вместе с allowed_schema, только таблицы с этим префиксом.
    """

    query_tag: str = "postgres-fastmcp"
    timeout: float | None = None
    allowed_schema: str | None = None
    read_only: bool = True
    table_prefix: str | None = None
