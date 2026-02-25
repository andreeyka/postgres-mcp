"""Протокол для замены параметров $1, $2, ... конкретными значениями."""

from typing import Protocol


class ParamReplacerPort(Protocol):
    """Протокол для замены параметров pg_stat_statements-style значениями из статистики."""

    async def replace_parameters(self, query: str) -> str:
        """Замена параметров $N в запросе соответствующими литеральными значениями."""
        ...
