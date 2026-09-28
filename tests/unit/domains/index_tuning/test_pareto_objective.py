# mypy: ignore-errors
"""Unit tests for the shared Pareto objective in IndexTuningBase."""

import math
from unittest.mock import MagicMock

from postgres_fastmcp.domains.index_tuning.dta_calc import DatabaseTuningAdvisor


class TestParetoObjective:
    """Single formula log(cost) + alpha * log(size) shared by all tuning algorithms."""

    def test_formula_matches_log_cost_plus_alpha_log_size(self) -> None:
        """Objective equals log(cost) + alpha * log(size) for positive inputs."""
        dta = DatabaseTuningAdvisor(MagicMock(), catalog_driver=MagicMock(), pareto_alpha=2.0)
        assert dta._pareto_objective(100.0, 1000.0) == math.log(100.0) + 2.0 * math.log(1000.0)

    def test_alpha_is_configurable_via_constructor(self) -> None:
        """pareto_alpha passed to the subclass constructor reaches the shared formula."""
        dta = DatabaseTuningAdvisor(MagicMock(), catalog_driver=MagicMock(), pareto_alpha=0.5)
        assert dta._pareto_objective(10.0, 10.0) == math.log(10.0) + 0.5 * math.log(10.0)

    def test_non_positive_inputs_give_infinity(self) -> None:
        """Zero or negative cost/size means the configuration is invalid: objective is inf."""
        dta = DatabaseTuningAdvisor(MagicMock(), catalog_driver=MagicMock())
        assert dta._pareto_objective(0.0, 1000.0) == float("inf")
        assert dta._pareto_objective(100.0, 0.0) == float("inf")
        assert dta._pareto_objective(-1.0, -1.0) == float("inf")
