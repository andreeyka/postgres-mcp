"""Модель определения индекса для гипотетического создания индекса."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IndexDefinition:
    """Немутабельная конфигурация индекса для хеширования и генерации CREATE INDEX."""

    table: str
    columns: tuple[str, ...]
    using: str = "btree"

    def to_dict(self) -> dict[str, Any]:
        """Преобразование в словарь (например, для сериализации).

        Returns:
            Словарь с table, columns, using, definition.
        """
        return {
            "table": self.table,
            "columns": list(self.columns),
            "using": self.using,
            "definition": self.definition,
        }

    @property
    def definition(self) -> str:
        """SQL оператор CREATE INDEX для этого индекса."""
        return f"CREATE INDEX {self.name} ON {self.table} USING {self.using} ({', '.join(self.columns)})"

    @property
    def name(self) -> str:
        """Сгенерированное имя индекса из имени таблицы, столбцов и метода."""
        cleaned_columns = []
        for col in self.columns:
            cleaned = col.replace("(", "_").replace(")", "_").replace(" ", "_").replace(",", "_")
            while "__" in cleaned:
                cleaned = cleaned.replace("__", "_")
            cleaned = cleaned.rstrip("_")
            cleaned_columns.append(cleaned)
        column_part = "_".join(cleaned_columns)
        suffix = "" if self.using == "btree" else f"_{self.using}"
        base = f"crystaldba_idx_{self.table}_{column_part}_{len(self.columns)}"
        return f"{base}{suffix}"

    def __str__(self) -> str:
        """Строковое представление определения индекса."""
        return self.definition

    def __repr__(self) -> str:
        """Точное строковое представление объекта IndexDefinition."""
        return f"IndexDefinition(table='{self.table}', columns={self.columns}, using='{self.using}')"
