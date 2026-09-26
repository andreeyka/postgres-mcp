"""Сводные политики валидации: типы операторов, функции, расширения."""

from postgres_fastmcp.postgres.security._allowed_functions import ALLOWED_FUNCTIONS
from postgres_fastmcp.postgres.security.statement_policies import ALLOWED_NODE_TYPES


# Единственные расширения, которые сервер сам просит установить (hypothetical indexes и топ запросов).
# Всё остальное (dblink, file_fdw, procedural languages, ...) даёт побочные эффекты за пределами БД.
ALLOWED_EXTENSIONS: frozenset[str] = frozenset({"hypopg", "pg_stat_statements"})

__all__ = ["ALLOWED_EXTENSIONS", "ALLOWED_FUNCTIONS", "ALLOWED_NODE_TYPES"]
