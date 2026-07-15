"""Проверки коэффициента попаданий в буферный кэш (индексы и таблицы)."""

from postgres_fastmcp.services.health.base import BaseHealthCalc


QUERY_INDEX_HIT_RATE = """
    SELECT
        (sum(idx_blks_hit)) / nullif(sum(idx_blks_hit + idx_blks_read), 0) AS rate
    FROM
        pg_statio_user_indexes
"""

QUERY_TABLE_HIT_RATE = """
    SELECT
        sum(heap_blks_hit) / nullif(sum(heap_blks_hit + heap_blks_read), 0) AS rate
    FROM
        pg_statio_user_tables
"""


class BufferHealthCalc(BaseHealthCalc):
    """Калькулятор для проверок состояния кэша буферов базы данных."""

    async def _hit_rate(self, label: str, query: str, threshold: float) -> str:
        """Общий расчёт коэффициента попаданий в кэш для индексов/таблиц.

        Args:
            label: Человекочитаемая метка ("Index"/"Table") для сообщения.
            query: SQL, возвращающий одну строку с полем ``rate``.
            threshold: Минимальный порог коэффициента (0..1).

        Returns:
            Строка с процентом попаданий и сравнением с порогом.
        """
        rows = await self._rows(query)
        if not rows or rows[0]["rate"] is None:
            return f"No {label.lower()} cache statistics available."

        hit_rate = float(rows[0]["rate"]) * 100
        threshold_pct = threshold * 100
        position = "above" if hit_rate >= threshold_pct else "below"
        return f"{label} cache hit rate: {hit_rate:.1f}% ({position} {threshold_pct:.1f}% threshold)"

    async def index_hit_rate(self, threshold: float = 0.95) -> str:
        """Коэффициент попаданий в кэш индексов относительно порога (по умолчанию 0.95)."""
        return await self._hit_rate("Index", QUERY_INDEX_HIT_RATE, threshold)

    async def table_hit_rate(self, threshold: float = 0.95) -> str:
        """Коэффициент попаданий в кэш таблиц относительно порога (по умолчанию 0.95)."""
        return await self._hit_rate("Table", QUERY_TABLE_HIT_RATE, threshold)
