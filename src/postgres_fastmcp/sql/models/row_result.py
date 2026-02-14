"""Модель строки результата запроса."""

from dataclasses import dataclass
from typing import Any


@dataclass
class RowResult:
    """Одна строка из результата запроса (словарь с ячейками)."""

    cells: dict[str, Any]  # Ячейки результата в виде словаря
