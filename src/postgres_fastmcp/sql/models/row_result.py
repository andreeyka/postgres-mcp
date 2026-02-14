"""Query result row model."""

from dataclasses import dataclass
from typing import Any


@dataclass
class RowResult:
    """Single row from a query result (dict-like cells)."""

    cells: dict[str, Any]
