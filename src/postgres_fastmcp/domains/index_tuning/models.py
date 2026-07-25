"""Модели данных и строковые помощники для настройки индексов."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from postgres_fastmcp.postgres.models import IndexDefinition
from postgres_fastmcp.shared.utils import calculate_improvement_multiple


MAX_NUM_INDEX_TUNING_QUERIES = 10


def pp_list(lst: list[Any]) -> str:
    """Красиво вывести список для отладки.

    Args:
        lst: Список для красивого вывода.

    Returns:
        Форматированная строка с представлением списка.
    """
    return ("\n  - " if len(lst) > 0 else "") + "\n  - ".join([str(item) for item in lst])


@dataclass
class IndexRecommendation:
    """Представляет индекс базы данных с оценкой размера и определением."""

    _definition: IndexDefinition
    estimated_size_bytes: int = 0
    potential_problematic_reason: str | None = None

    def __init__(
        self,
        table: str,
        columns: tuple[str, ...],
        using: str = "btree",
        estimated_size_bytes: int = 0,
        potential_problematic_reason: str | None = None,
    ) -> None:
        """Инициализация IndexRecommendation.

        Args:
            table: Имя таблицы.
            columns: Кортеж имен столбцов.
            using: Тип индекса (по умолчанию "btree").
            estimated_size_bytes: Оценочный размер в байтах.
            potential_problematic_reason: Причина если индекс потенциально проблематичен.
        """
        self._definition = IndexDefinition(table, columns, using)
        self.estimated_size_bytes = estimated_size_bytes
        self.potential_problematic_reason = potential_problematic_reason

    @property
    def index_definition(self) -> IndexDefinition:
        """Получить объект определения индекса.

        Returns:
            Объект IndexDefinition с именем таблицы, столбцами и типом индекса.
        """
        return self._definition

    @property
    def definition(self) -> str:
        """Получить строку SQL определения для этого индекса.

        Returns:
            Строка с SQL CREATE INDEX.
        """
        return self._definition.definition

    @property
    def name(self) -> str:
        """Получить сгенерированное имя индекса.

        Returns:
            Строка с именем индекса.
        """
        return self._definition.name

    @property
    def columns(self) -> tuple[str, ...]:
        """Получить имена столбцов для этого индекса.

        Returns:
            Кортеж имен столбцов.
        """
        return self._definition.columns

    @property
    def table(self) -> str:
        """Получить имя таблицы для этого индекса.

        Returns:
            Строка с именем таблицы.
        """
        return self._definition.table

    @property
    def using(self) -> str:
        """Получить тип индекса (например, 'btree', 'hash').

        Returns:
            Строка с типом индекса.
        """
        return self._definition.using

    def __hash__(self) -> int:
        return self._definition.__hash__()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, IndexRecommendation):
            return False
        return self._definition.__eq__(other.index_definition)

    def __str__(self) -> str:
        return self._definition.__str__() + f" (estimated_size_bytes: {self.estimated_size_bytes})"

    def __repr__(self) -> str:
        return self._definition.__repr__() + f" (estimated_size_bytes: {self.estimated_size_bytes})"


@dataclass
class IndexRecommendationAnalysis:
    """Представляет рекомендованный индекс с оценкой выгоды."""

    index_recommendation: IndexRecommendation

    progressive_base_cost: float
    progressive_recommendation_cost: float
    individual_base_cost: float
    individual_recommendation_cost: float
    queries: list[str]
    definition: str

    @property
    def table(self) -> str:
        """Получить имя таблицы для этой рекомендации индекса.

        Returns:
            Строка с именем таблицы.
        """
        return self.index_recommendation.table

    @property
    def columns(self) -> tuple[str, ...]:
        """Получить имена столбцов для этой рекомендации индекса.

        Returns:
            Кортеж имен столбцов.
        """
        return self.index_recommendation.columns

    @property
    def using(self) -> str:
        """Получить тип индекса для этой рекомендации.

        Returns:
            Строка с типом индекса (например, 'btree', 'hash').
        """
        return self.index_recommendation.using

    @property
    def progressive_improvement_multiple(self) -> float:
        """Вычислить процентное улучшение по прогрессивной рекомендации.

        Returns:
            Множитель улучшения как число с плавающей точкой.
        """
        return calculate_improvement_multiple(self.progressive_base_cost, self.progressive_recommendation_cost)

    @property
    def potential_problematic_reason(self) -> str | None:
        """Получить причину если индекс потенциально проблематичен.

        Returns:
            Строка с описанием проблемы или None если нет проблем.
        """
        return self.index_recommendation.potential_problematic_reason

    @property
    def estimated_size_bytes(self) -> int:
        """Получить оценочный размер этого индекса в байтах.

        Returns:
            Оценочный размер в байтах.
        """
        return self.index_recommendation.estimated_size_bytes

    @property
    def individual_improvement_multiple(self) -> float:
        """Вычислить процентное улучшение по индивидуальной рекомендации.

        Returns:
            Множитель улучшения как число с плавающей точкой.
        """
        return calculate_improvement_multiple(self.individual_base_cost, self.individual_recommendation_cost)

    def to_index(self) -> IndexRecommendation:
        """Преобразовать этот анализ в IndexRecommendation.

        Returns:
            Объект IndexRecommendation.
        """
        return self.index_recommendation


@dataclass
class IndexTuningResult:
    """Результаты анализа настройки индексов."""

    # Session ID for tracing
    session_id: str

    # Input parameters
    budget_mb: int  # Tuning budget in MB
    workload_source: str = "n/a"  # 'args', 'query_list', 'query_store', 'sql_file'
    workload: list[dict[str, Any]] | None = None

    # Output results
    recommendations: list[IndexRecommendationAnalysis] = field(default_factory=list)
    error: str | None = None
    dta_traces: list[str] = field(default_factory=list)


def candidate_str(
    indexes: Iterable[IndexDefinition] | Iterable[IndexRecommendation] | Iterable[IndexRecommendationAnalysis],
) -> str:
    """Преобразовать индексы в строковое представление.

    Args:
        indexes: Итерируемый объект определений или рекомендаций индексов.

    Returns:
        Строковое представление индексов.
    """
    return ", ".join(f"{idx.table}({','.join(idx.columns)})" for idx in indexes) if indexes else "(no indexes)"
