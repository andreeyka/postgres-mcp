"""Port for replacing $1, $2, ... placeholders with concrete values."""

from typing import Protocol


class ParamReplacerPort(Protocol):
    """Port for replacing pg_stat_statements-style parameters with values from stats."""

    async def replace_parameters(self, query: str) -> str:
        """Replace $N placeholders in query with appropriate literal values."""
        ...
