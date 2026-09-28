"""Модели данных слоя SQL: строка результата, результат оператора и определение индекса."""

from dataclasses import dataclass
from typing import Any


@dataclass
class RowResult:
    """Одна строка из результата запроса (словарь с ячейками)."""

    cells: dict[str, Any]  # Ячейки результата в виде словаря


@dataclass(frozen=True, slots=True)
class StatementResult:
    """Результат одного оператора: строки и тег команды Postgres.

    Attributes:
        rows: Строки результата; None, если у оператора нет результирующего набора (DML без RETURNING, DDL).
        status: Тег команды (cursor.statusmessage), например "UPDATE 3" или "CREATE TABLE".
        affected_rows: Число строк из тега (cursor.rowcount = libpq PQcmdTuples): INSERT/UPDATE/DELETE/MERGE,
            SELECT, CREATE TABLE AS, COPY, FETCH, MOVE. None, если в теге нет числа (DDL, DO, SET).
    """

    rows: list[RowResult] | None
    status: str | None
    affected_rows: int | None


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
        # Имя индекса не может быть квалифицировано схемой: точку из "schema.table" заменяем.
        base = f"dba_idx_{self.table.replace('.', '_')}_{column_part}_{len(self.columns)}"
        return f"{base}{suffix}"

    def __str__(self) -> str:
        """Строковое представление определения индекса."""
        return self.definition

    def __repr__(self) -> str:
        """Точное строковое представление объекта IndexDefinition."""
        return f"IndexDefinition(table='{self.table}', columns={self.columns}, using='{self.using}')"
