"""Реализация Database Tuning Advisor для рекомендаций индексов."""

import logging
import time
from typing import override

import humanize
from pglast.ast import SelectStmt

from postgres_fastmcp.postgres.ports import QueryExecutorPort, SqlDriverPort

from .base import IndexTuningBase
from .candidates import CandidateGenerator
from .models import IndexRecommendation, candidate_str, pp_list


logger = logging.getLogger(__name__)


class DatabaseTuningAdvisor(IndexTuningBase):
    """Database Tuning Advisor for generating index recommendations.

    Uses a hybrid 'seed + greedy' approach with time cutoff to recommend
    optimal indexes for query workloads.
    """

    def __init__(  # noqa: PLR0913
        self,
        sql_driver: SqlDriverPort,
        *,
        catalog_driver: QueryExecutorPort,
        connection_id: str = "",
        budget_mb: int = -1,  # no limit by default
        max_runtime_seconds: int = 30,  # 30 seconds
        max_index_width: int = 3,
        min_column_usage: int = 1,  # skip columns used in fewer than this many queries
        seed_columns_count: int = 3,  # how many single-col seeds to pick
        pareto_alpha: float = 2.0,
        min_time_improvement: float = 0.1,
    ) -> None:
        """Initialize Database Tuning Advisor.

        Args:
            sql_driver: SQL executor for database access.
            catalog_driver: Исполнитель служебных запросов (проверка расширений и версии).
            connection_id: Stable connection id for extension/version cache.
            budget_mb: Storage budget in MB (-1 for no limit).
            max_runtime_seconds: Time limit for entire analysis (anytime approach).
            max_index_width: Maximum columns in an index.
            min_column_usage: Skip columns that appear in fewer than X queries.
            seed_columns_count: How many top single-column indexes to pick as seeds.
            pareto_alpha: Stop when relative improvement falls below this threshold.
            min_time_improvement: Stop when relative improvement falls below this threshold.
        """
        super().__init__(
            sql_driver,
            catalog_driver=catalog_driver,
            connection_id=connection_id,
            pareto_alpha=pareto_alpha,
            budget_mb=budget_mb,
        )
        self.max_runtime_seconds = max_runtime_seconds
        self.seed_columns_count = seed_columns_count
        self.min_time_improvement = min_time_improvement
        self._candidates = CandidateGenerator(
            sql_driver,
            self._param_replacer,
            max_index_width=max_index_width,
            min_column_usage=min_column_usage,
            trace=self.dta_trace,
        )

    def _check_time(self) -> bool:
        """Check if max runtime has been exceeded.

        Returns:
            True if we have exceeded max_runtime_seconds, False otherwise.
        """
        if self.max_runtime_seconds <= 0:
            return False
        elapsed = time.time() - self._analysis_start_time
        return elapsed > self.max_runtime_seconds

    @override
    async def _generate_recommendations(
        self, query_weights: list[tuple[str, SelectStmt, float]]
    ) -> tuple[set[IndexRecommendation], float]:
        """Generate index recommendations using a hybrid 'seed + greedy' approach.

        Args:
            query_weights: List of tuples containing query text, parsed statement, and weight.

        Returns:
            Tuple of recommended indexes and final cost.
        """
        # Get existing indexes
        existing_index_defs: set[str] = {idx["definition"] for idx in await self._candidates.get_existing_indexes()}

        logger.debug("Existing indexes (%d): %s", len(existing_index_defs), pp_list(list(existing_index_defs)))

        # generate initial candidates
        all_candidates = await self._candidates.generate(query_weights, existing_index_defs)

        self.dta_trace(f"All candidates ({len(all_candidates)}): {candidate_str(all_candidates)}")

        seeds = set()
        if self.seed_columns_count > 0 and not self._check_time():
            seeds = await self._quick_pass_seeds(query_weights, all_candidates)

        seeds_list: list[set[IndexRecommendation]] = [
            seeds,
            set(),
        ]

        best_config: tuple[set[IndexRecommendation], float] = (set(), float("inf"))

        # Evaluate each seed
        for seed in seeds_list:
            if self._check_time():
                break

            self.dta_trace("Evaluating seed:")
            seed_definitions = frozenset(idx.index_definition for idx in seed)
            current_cost = await self.cost_eval.evaluate_configuration_cost(query_weights, seed_definitions)
            candidate_indexes = set(
                {
                    IndexRecommendation(
                        c.table,
                        tuple(c.columns),
                        c.using,
                    )
                    for c in all_candidates
                }
            )
            final_indexes, final_cost = await self._enumerate_greedy(
                query_weights, seed.copy(), current_cost, candidate_indexes - seed
            )

            if final_cost < best_config[1]:
                best_config = (final_indexes, final_cost)

        # Sort recs by benefit desc
        return best_config

    async def _quick_pass_seeds(
        self,
        query_weights: list[tuple[str, SelectStmt, float]],
        all_candidates: list[IndexRecommendation],
    ) -> set[IndexRecommendation]:
        """Generate seed indexes by selecting top single-column indexes.

        Selects the most frequently used single-column indexes as starting points
        for the greedy search algorithm.

        Args:
            query_weights: List of tuples containing query text, parsed statement, and weight.
            all_candidates: List of all candidate indexes.

        Returns:
            Set of seed index recommendations (top single-column indexes).
        """
        # Filter only single-column indexes
        single_column_candidates = [idx for idx in all_candidates if len(idx.columns) == 1]

        if not single_column_candidates or self.seed_columns_count <= 0:
            return set()

        # Calculate column usage frequency (weighted by query weight)
        column_usage: dict[tuple[str, str], float] = {}  # (table, column) -> weighted_usage
        for _query_text, stmt, weight in query_weights:
            columns_per_table = self._param_replacer.extract_stmt_columns(stmt)
            for table, cols in columns_per_table.items():
                for col in cols:
                    key = (table, col)
                    column_usage[key] = column_usage.get(key, 0.0) + weight

        # Score each single-column candidate by usage frequency
        scored_candidates: list[tuple[IndexRecommendation, float]] = []
        for candidate in single_column_candidates:
            if len(candidate.columns) == 1:
                table = candidate.table
                column = candidate.columns[0]
                usage_score = column_usage.get((table, column), 0.0)
                scored_candidates.append((candidate, usage_score))

        # Sort by usage score (descending) and take top N
        scored_candidates.sort(key=lambda x: x[1], reverse=True)
        top_seeds = scored_candidates[: self.seed_columns_count]

        self.dta_trace(
            f"Selected {len(top_seeds)} seed indexes from {len(single_column_candidates)} single-column candidates"
        )
        for seed, score in top_seeds:
            self.dta_trace(f"  - Seed: {candidate_str([seed])} (usage_score={score:.2f})")

        return {seed for seed, _ in top_seeds}

    async def _enumerate_greedy(
        self,
        queries: list[tuple[str, SelectStmt, float]],
        current_indexes: set[IndexRecommendation],
        current_cost: float,
        candidate_indexes: set[IndexRecommendation],
    ) -> tuple[set[IndexRecommendation], float]:
        """Enumerate indexes using Pareto optimal greedy approach.

        Uses cost/benefit analysis:
        - Cost: Size of base relation plus size of indexes (in bytes)
        - Benefit: Inverse of query execution time (1/time)
        - Objective function: log(time) + alpha * log(space)
        - We want to minimize this function, with alpha=2 for 2x emphasis on performance
        - Primary stopping criterion: minimum relative time improvement threshold

        Args:
            queries: List of tuples containing query text, parsed statement, and weight.
            current_indexes: Current set of indexes.
            current_cost: Current cost of the configuration.
            candidate_indexes: Set of candidate indexes to evaluate.

        Returns:
            Tuple of final indexes and final cost.
        """
        # Parameters
        min_time_improvement = self.min_time_improvement  # 5% default

        self.dta_trace("\n[GREEDY SEARCH] Starting enumeration")
        self.dta_trace(f"  - Parameters: alpha={self.pareto_alpha}, min_time_improvement={min_time_improvement}")
        self.dta_trace(f"  - Initial indexes: {len(current_indexes)}, Candidates: {len(candidate_indexes)}")

        # Get the tables involved in this analysis
        tables = set()
        for idx in candidate_indexes:
            tables.add(idx.table)

        # Estimate base relation size for each table
        base_relation_size = sum([await self.cost_eval.get_table_size(table) for table in tables])

        self.dta_trace(f"  - Base relation size: {humanize.naturalsize(base_relation_size)}")

        # Calculate current indexes size
        indexes_size = sum(
            [await self.cost_eval.estimate_index_size(idx.table, list(idx.columns)) for idx in current_indexes]
        )

        # Total space is base relation plus indexes
        current_space = base_relation_size + indexes_size
        current_time = current_cost
        current_objective = self._pareto_objective(current_time, current_space)

        self.dta_trace(
            f"  - Initial configuration: Time={current_time:.2f}, "
            f"Space={humanize.naturalsize(current_space)} (Base: {humanize.naturalsize(base_relation_size)}, "
            f"Indexes: {humanize.naturalsize(indexes_size)}), "
            f"Objective={current_objective:.4f}"
        )

        added_indexes = []  # Keep track of added indexes in order
        iteration = 1

        while True:
            self.dta_trace(f"\n[ITERATION {iteration}] Evaluating candidates")
            best_index = None
            best_time = current_time
            best_space = current_space
            best_objective = current_objective
            best_time_improvement = 0.0

            for candidate in candidate_indexes:
                self.dta_trace(f"Evaluating candidate: {candidate_str([candidate])}")
                # Calculate additional size from this index
                index_size = await self.cost_eval.estimate_index_size(candidate.table, list(candidate.columns))
                self.dta_trace(f"    + Index size: {humanize.naturalsize(index_size)}")
                # Total space with this index = current space + new index size
                test_space = current_space + index_size
                self.dta_trace(f"    + Total space: {humanize.naturalsize(test_space)}")

                # Check budget constraint
                if self.budget_mb > 0 and (test_space - base_relation_size) > self.budget_mb * 1024 * 1024:
                    self.dta_trace(
                        f"  - Skipping candidate: {candidate_str([candidate])} because total "
                        f"index size ({humanize.naturalsize(test_space - base_relation_size)}) exceeds "
                        f"budget ({humanize.naturalsize(self.budget_mb * 1024 * 1024)})"
                    )
                    continue

                # Calculate new time (cost) with this index
                test_time = await self.cost_eval.evaluate_configuration_cost(
                    queries, frozenset(idx.index_definition for idx in current_indexes | {candidate})
                )
                self.dta_trace(f"    + Eval cost (time): {test_time}")

                # Calculate relative time improvement
                time_improvement = (current_time - test_time) / current_time

                # Skip if time improvement is below threshold
                if time_improvement < min_time_improvement:
                    self.dta_trace(
                        f"  - Skipping candidate: {candidate_str([candidate])} "
                        "because time improvement is below threshold"
                    )
                    continue

                # Calculate objective for this configuration
                test_objective = self._pareto_objective(test_time, test_space)

                # Select the index with the best time improvement that meets our threshold
                if test_objective < best_objective and time_improvement > best_time_improvement:
                    self.dta_trace(f"  - Updating best candidate: {candidate_str([candidate])}")
                    best_index = candidate
                    best_time = test_time
                    best_space = test_space
                    best_objective = test_objective
                    best_time_improvement = time_improvement
                else:
                    self.dta_trace(
                        f"  - Skipping candidate: {candidate_str([candidate])} "
                        "because it doesn't have the best objective improvement"
                    )

            # If no improvement or no valid candidates, stop
            if best_index is None:
                self.dta_trace(f"STOPPED SEARCH: No indexes found with time improvement >= {min_time_improvement:.2%}")
                break

            # Calculate improvements/changes
            time_improvement = (current_time - best_time) / current_time
            space_increase = (best_space - current_space) / current_space
            objective_improvement = current_objective - best_objective

            # Log this step
            self.dta_trace(
                f"  - Selected index: {candidate_str([best_index])}"
                f"\n    + Time improvement: {time_improvement:.2%}"
                f"\n    + Space increase: {space_increase:.2%}"
                f"\n    + New objective: {best_objective:.4f} (improvement: {objective_improvement:.4f})"
            )

            # Add the best index and update metrics
            current_indexes.add(best_index)
            candidate_indexes.remove(best_index)
            added_indexes.append(best_index)

            # Update current metrics
            current_time = best_time
            current_space = best_space
            current_objective = best_objective

            iteration += 1

            # Check if we've exceeded the time limit after doing at least one iteration
            if self._check_time():
                self.dta_trace("STOPPED SEARCH: Time limit reached")
                break

        # Log final configuration
        self.dta_trace("\n[SEARCH COMPLETE]")
        if added_indexes:
            indexes_size = sum(
                [await self.cost_eval.estimate_index_size(idx.table, list(idx.columns)) for idx in current_indexes]
            )
            self.dta_trace(
                f"  - Final configuration: {len(added_indexes)} indexes added"
                f"\n    + Final time: {current_time:.2f}"
                f"\n    + Final space: {humanize.naturalsize(current_space)} "
                f"(Base: {humanize.naturalsize(base_relation_size)}, "
                f"Indexes: {humanize.naturalsize(indexes_size)})"
                f"\n    + Final objective: {current_objective:.4f}"
            )
        else:
            self.dta_trace("No indexes added - baseline configuration is optimal")

        return current_indexes, current_time
