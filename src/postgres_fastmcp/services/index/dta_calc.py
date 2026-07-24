"""Реализация Database Tuning Advisor для рекомендаций индексов."""

import logging
import time
from itertools import combinations
from typing import Any, override

import humanize
from pglast import parser
from pglast.ast import ColumnRef, FuncCall, JoinExpr, Node, SelectStmt

from postgres_fastmcp.sql.ast.visitors import ColumnCollector, TableAliasVisitor
from postgres_fastmcp.sql.ports import SqlDriverPort

from .index_opt_base import IndexRecommendation, IndexTuningBase, candidate_str, pp_list


logger = logging.getLogger(__name__)


class DatabaseTuningAdvisor(IndexTuningBase):
    """Database Tuning Advisor for generating index recommendations.

    Uses a hybrid 'seed + greedy' approach with time cutoff to recommend
    optimal indexes for query workloads.
    """

    def __init__(  # noqa: PLR0913
        self,
        sql_driver: SqlDriverPort,
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
            connection_id: Stable connection id for extension/version cache.
            budget_mb: Storage budget in MB (-1 for no limit).
            max_runtime_seconds: Time limit for entire analysis (anytime approach).
            max_index_width: Maximum columns in an index.
            min_column_usage: Skip columns that appear in fewer than X queries.
            seed_columns_count: How many top single-column indexes to pick as seeds.
            pareto_alpha: Stop when relative improvement falls below this threshold.
            min_time_improvement: Stop when relative improvement falls below this threshold.
        """
        super().__init__(sql_driver, connection_id=connection_id)
        self.budget_mb = budget_mb
        self.max_runtime_seconds = max_runtime_seconds
        self.max_index_width = max_index_width
        self.min_column_usage = min_column_usage
        self.seed_columns_count = seed_columns_count
        self._analysis_start_time = 0.0
        self.pareto_alpha = pareto_alpha
        self.min_time_improvement = min_time_improvement

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
        existing_index_defs: set[str] = {idx["definition"] for idx in await self._get_existing_indexes()}

        logger.debug("Existing indexes (%d): %s", len(existing_index_defs), pp_list(list(existing_index_defs)))

        # generate initial candidates
        all_candidates = await self.generate_candidates(query_weights, existing_index_defs)

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
            current_cost = await self._evaluate_configuration_cost(query_weights, seed_definitions)
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

    async def _build_column_cache(self, tables: set[str]) -> dict[str, set[str]]:
        """Build a cache of table -> set of column names from the database.

        Args:
            tables: Set of table names to cache columns for.

        Returns:
            Dictionary mapping table names (lowercase) to sets of column names (lowercase).
        """
        if not tables:
            return {}

        column_cache: dict[str, set[str]] = {}

        # Build query to get all columns for the tables
        tables_list = list(tables)

        query = """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
            AND table_name = ANY({})
            ORDER BY table_name, ordinal_position
        """

        try:
            result = await self.sql_driver.execute(query, params=[tables_list], readonly=True)
            if result:
                for row in result:
                    table_name = row.cells["table_name"].lower()
                    column_name = row.cells["column_name"].lower()
                    if table_name not in column_cache:
                        column_cache[table_name] = set()
                    column_cache[table_name].add(column_name)
        except Exception as e:
            logger.warning("Error building column cache: %s. Continuing without cache.", e)
            return {}

        return column_cache

    async def generate_candidates(  # noqa: C901
        self, workload: list[tuple[str, SelectStmt, float]], existing_defs: set[str]
    ) -> list[IndexRecommendation]:
        """Generate index candidates from queries with batch creation.

        Args:
            workload: List of tuples containing query text, parsed statement, and weight.
            existing_defs: Set of existing index definitions to filter out.

        Returns:
            List of index recommendations.
        """
        table_columns_usage: dict[str, dict[str, int]] = {}  # table -> {col -> usage_count}
        # Extract columns from all queries
        for _q, stmt, _ in workload:
            columns_per_table = self._param_replacer.extract_stmt_columns(stmt)
            for tbl, cols in columns_per_table.items():
                if tbl not in table_columns_usage:
                    table_columns_usage[tbl] = {}
                for c in cols:
                    table_columns_usage[tbl][c] = table_columns_usage[tbl].get(c, 0) + 1

        # Filter out rarely used columns
        # e.g. skip columns that appear in fewer than self.min_column_usage queries
        table_columns: dict[str, set[str]] = {}
        for tbl, usage_map in table_columns_usage.items():
            kept_cols = {c for c, usage in usage_map.items() if usage >= self.min_column_usage}
            if kept_cols:
                table_columns[tbl] = kept_cols

        # Build column cache for accurate column existence checks
        all_tables = set(table_columns.keys())
        column_cache = await self._build_column_cache(all_tables)

        # Extract columns used in conditions (WHERE/JOIN/HAVING/ORDER BY) for optimization
        # This allows us to generate only relevant index candidates instead of all combinations
        condition_columns: dict[str, set[str]] = {}  # table -> set of columns used in conditions
        for _, stmt, _ in workload:
            try:
                collector = ConditionColumnCollector(column_cache=column_cache)
                collector(stmt)
                query_condition_columns = collector.condition_columns
                for table, cols in query_condition_columns.items():
                    if table not in condition_columns:
                        condition_columns[table] = set()
                    condition_columns[table].update(cols)
            except Exception as e:
                error_msg = "Error extracting condition columns from query"
                raise ValueError(error_msg) from e

        # Generate candidates only from columns used in conditions (optimized approach)
        # Intersect with table_columns to ensure we only use frequently used columns
        candidates = []
        for table, cols in table_columns.items():
            # Use intersection: columns that are both frequently used AND in conditions
            condition_cols = condition_columns.get(table, set())
            relevant_cols = cols & condition_cols  # Intersection

            # If no condition columns found, fall back to all columns (safety fallback)
            # This handles edge cases where conditions might not be detected
            if not relevant_cols and cols:
                relevant_cols = cols

            if relevant_cols:
                col_list = list(relevant_cols)
                for width in range(1, min(self.max_index_width, len(col_list)) + 1):
                    candidates.extend(
                        [
                            IndexRecommendation(table=table, columns=tuple(combo))
                            for combo in combinations(col_list, width)
                        ]
                    )

        # filter out duplicates with existing indexes
        filtered_candidates = [c for c in candidates if not self._index_exists(c, existing_defs)]

        # Note: Filtering by query conditions is no longer needed since we already
        # generate candidates only from condition columns, but we keep it for safety
        condition_filtered1 = await self._filter_candidates_by_query_conditions(workload, filtered_candidates)

        # filter out long text columns
        condition_filtered = await self._filter_long_text_columns(condition_filtered1)

        self.dta_trace(f"Generated {len(candidates)} total candidates")
        self.dta_trace(f"Filtered to {len(filtered_candidates)} after removing existing indexes.")
        self.dta_trace(f"Filtered to {len(condition_filtered1)} after removing unused columns.")
        self.dta_trace(f"Filtered to {len(condition_filtered)} after removing long text columns.")
        # Batch create all hypothetical indexes and store their size estimates
        if len(condition_filtered) > 0:
            query = "SELECT hypopg_create_index({});" * len(condition_filtered)
            await self.sql_driver.execute(
                query,
                params=[idx.definition for idx in condition_filtered],
                readonly=True,
            )

            # Get estimated sizes without resetting indexes yet
            result = await self.sql_driver.execute(
                "SELECT index_name, hypopg_relation_size(indexrelid) as index_size FROM hypopg_list_indexes;",
                params=None,
                readonly=True,
            )
            if result is not None:
                index_map = {r.cells["index_name"]: r.cells["index_size"] for r in result}
                for idx in condition_filtered:
                    if idx.name in index_map:
                        idx.estimated_size_bytes = index_map[idx.name]

            await self.sql_driver.execute("SELECT hypopg_reset();", params=None, readonly=True)
        return condition_filtered

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
        import math  # noqa: PLC0415

        # Parameters
        alpha = self.pareto_alpha
        min_time_improvement = self.min_time_improvement  # 5% default

        self.dta_trace("\n[GREEDY SEARCH] Starting enumeration")
        self.dta_trace(f"  - Parameters: alpha={alpha}, min_time_improvement={min_time_improvement}")
        self.dta_trace(f"  - Initial indexes: {len(current_indexes)}, Candidates: {len(candidate_indexes)}")

        # Get the tables involved in this analysis
        tables = set()
        for idx in candidate_indexes:
            tables.add(idx.table)

        # Estimate base relation size for each table
        base_relation_size = sum([await self._get_table_size(table) for table in tables])

        self.dta_trace(f"  - Base relation size: {humanize.naturalsize(base_relation_size)}")

        # Calculate current indexes size
        indexes_size = sum([await self._estimate_index_size(idx.table, list(idx.columns)) for idx in current_indexes])

        # Total space is base relation plus indexes
        current_space = base_relation_size + indexes_size
        current_time = current_cost
        current_objective = (
            math.log(current_time) + alpha * math.log(current_space)
            if current_cost > 0 and current_space > 0
            else float("inf")
        )

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
                index_size = await self._estimate_index_size(candidate.table, list(candidate.columns))
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
                test_time = await self._evaluate_configuration_cost(
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
                test_objective = math.log(test_time) + alpha * math.log(test_space)

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
                [await self._estimate_index_size(idx.table, list(idx.columns)) for idx in current_indexes]
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

    async def _filter_candidates_by_query_conditions(
        self, workload: list[tuple[str, SelectStmt, float]], candidates: list[IndexRecommendation]
    ) -> list[IndexRecommendation]:
        """Filter out index candidates that contain columns not used in query conditions.

        Args:
            workload: List of tuples containing query text, parsed statement, and weight.
            candidates: List of candidate indexes to filter.

        Returns:
            Filtered list of index recommendations.
        """
        if not workload or not candidates:
            return candidates

        # Extract all columns used in conditions across all queries
        condition_columns: dict[str, set[str]] = {}  # Dictionary of table -> set of columns

        # Build column cache for accurate column existence checks
        all_tables = set()
        for candidate in candidates:
            all_tables.add(candidate.table)
        column_cache = await self._build_column_cache(all_tables)

        for _, stmt, _ in workload:
            try:
                # Use our enhanced collector to extract condition columns
                collector = ConditionColumnCollector(column_cache=column_cache)
                collector(stmt)
                query_condition_columns = collector.condition_columns

                # Merge with overall condition columns
                for table, cols in query_condition_columns.items():
                    if table not in condition_columns:
                        condition_columns[table] = set()
                    condition_columns[table].update(cols)

            except Exception as e:
                error_msg = "Error extracting condition columns from query"
                raise ValueError(error_msg) from e

        # Filter candidates - keep only those where all columns are in condition_columns
        filtered_candidates = []
        for candidate in candidates:
            table = candidate.table
            if table not in condition_columns:
                continue

            # Check if all columns in the index are used in conditions
            all_columns_used = all(col in condition_columns[table] for col in candidate.columns)
            if all_columns_used:
                filtered_candidates.append(candidate)

        return filtered_candidates

    async def _filter_long_text_columns(  # noqa: C901
        self, candidates: list[IndexRecommendation], max_text_length: int = 100
    ) -> list[IndexRecommendation]:
        """Filter out indexes that contain long text columns based on catalog information.

        Args:
            candidates: List of candidate indexes
            max_text_length: Maximum allowed text length (default: 100)

        Returns:
            Filtered list of indexes
        """
        if not candidates:
            return []

        # First, get all unique table.column combinations
        table_columns = set()
        for candidate in candidates:
            for column in candidate.columns:
                table_columns.add((candidate.table, column))

        # Parameterized lists of table / column names (names come from parsed user queries,
        # so they must never be string-interpolated into the SQL).
        pairs = list(table_columns)
        tables = [table for table, _ in pairs]
        columns = [col for _, col in pairs]

        # Query to get column types and their length limits from catalog
        type_query = """
            SELECT
                c.table_name,
                c.column_name,
                c.data_type,
                c.character_maximum_length,
                pg_stats.avg_width,
                CASE
                    WHEN c.data_type = 'text' THEN true
                    WHEN (c.data_type = 'character varying' OR c.data_type = 'varchar' OR
                         c.data_type = 'character' OR c.data_type = 'char') AND
                         (c.character_maximum_length IS NULL OR c.character_maximum_length > {})
                    THEN true
                    ELSE false
                END as potential_long_text
            FROM information_schema.columns c
            LEFT JOIN pg_stats ON
                pg_stats.tablename = c.table_name AND
                pg_stats.attname = c.column_name
            WHERE c.table_name = ANY({})
            AND c.column_name = ANY({})
        """

        result = await self.sql_driver.execute(type_query, params=[max_text_length, tables, columns], readonly=True)

        logger.debug("Column types and length limits: %s", result)

        if not result:
            logger.debug("No column types and length limits found")
            return []

        # Process results and identify problematic columns
        problematic_columns = set()
        potential_problematic_columns = set()

        for row in result:
            table = row.cells["table_name"]
            column = row.cells["column_name"]
            potential_long = row.cells["potential_long_text"]
            avg_width = row.cells.get("avg_width")

            # Use avg_width from pg_stats as a heuristic - if it's high, likely contains long text
            if potential_long and (avg_width is None or avg_width > max_text_length * 0.4):
                problematic_columns.add((table, column))
                logger.debug("Identified potentially long text column: %s.%s (avg_width: %s)", table, column, avg_width)
            elif potential_long:
                potential_problematic_columns.add((table, column))

        # Filter candidates based on column information
        filtered_candidates = []
        for candidate in candidates:
            valid = True
            for column in candidate.columns:
                if (candidate.table, column) in problematic_columns:
                    valid = False
                    logger.debug("Skipping index candidate with long text column: %s.%s", candidate.table, column)
                    break
                if (candidate.table, column) in potential_problematic_columns:
                    candidate.potential_problematic_reason = "long_text_column"

            if valid:
                filtered_candidates.append(candidate)

        return filtered_candidates

    async def _get_existing_indexes(self) -> list[dict[str, Any]]:
        """Get all existing indexes.

        Returns:
            List of dictionaries containing index information.

        TODO: We should get the indexes that are relevant to the query.
        """
        query = """
        SELECT schemaname as schema,
               tablename as table,
               indexname as name,
               indexdef as definition
        FROM pg_indexes
        WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
        ORDER BY schemaname, tablename, indexname
        """
        result = await self.sql_driver.execute(query, params=None, readonly=True)
        if result is not None:
            return [dict(row.cells) for row in result]
        return []

    def _index_exists(self, index: IndexRecommendation, existing_defs: set[str]) -> bool:
        """Check if an index with the same table, columns, and type already exists.

        Uses pglast to parse index definitions and compare their structure rather than
        doing simple string matching.

        Args:
            index: Index recommendation to check.
            existing_defs: Set of existing index definitions.

        Returns:
            True if index exists, False otherwise.
        """
        try:
            # Parse the candidate index
            candidate_stmt = parser.parse_sql(index.definition)[0]
            candidate_node = candidate_stmt.stmt

            # Extract key information from candidate index
            candidate_info = self._extract_index_info(candidate_node)

            # If we couldn't parse the candidate index, fall back to string comparison
            if not candidate_info:
                return index.definition in existing_defs

            # Check each existing index
            for existing_def in existing_defs:
                try:
                    # Skip if it's obviously not an index
                    if not ("CREATE INDEX" in existing_def.upper() or "CREATE UNIQUE INDEX" in existing_def.upper()):
                        continue

                    # Parse the existing index
                    existing_stmt = parser.parse_sql(existing_def)[0]
                    existing_node = existing_stmt.stmt

                    # Extract key information
                    existing_info = self._extract_index_info(existing_node)

                    # Compare the key components
                    if existing_info and self._is_same_index(candidate_info, existing_info):
                        return True
                except Exception as e:
                    error_msg = "Error parsing existing index"
                    raise ValueError(error_msg) from e

        except Exception as e:
            error_msg = "Error in robust index comparison"
            raise ValueError(error_msg) from e
        else:
            return False

    def _extract_index_info(self, node: Any) -> dict[str, Any] | None:  # noqa: ANN401
        """Extract key information from a parsed index node.

        Args:
            node: Parsed index AST node.

        Returns:
            Dictionary with index information or None if extraction fails.
        """
        try:
            # Handle differences in node structure between pglast versions
            index_stmt = node.IndexStmt if hasattr(node, "IndexStmt") else node

            # Extract table name
            if hasattr(index_stmt.relation, "relname"):
                table_name = index_stmt.relation.relname
            else:
                # Extract from RangeVar
                table_name = index_stmt.relation.RangeVar.relname

            # Extract columns
            columns = []
            for idx_elem in index_stmt.indexParams:
                if hasattr(idx_elem, "name") and idx_elem.name:
                    columns.append(idx_elem.name)
                elif hasattr(idx_elem, "IndexElem") and idx_elem.IndexElem:
                    columns.append(idx_elem.IndexElem.name)
                elif hasattr(idx_elem, "expr") and idx_elem.expr:
                    # Convert the expression to a proper string representation
                    expr_str = self._ast_expr_to_string(idx_elem.expr)
                    columns.append(expr_str)
            # Extract index type
            index_type = "btree"  # default
            if hasattr(index_stmt, "accessMethod") and index_stmt.accessMethod:
                index_type = index_stmt.accessMethod

            # Check if unique
            is_unique = False
            if hasattr(index_stmt, "unique"):
                is_unique = index_stmt.unique

            return {
                "table": table_name.lower(),
                "columns": [col.lower() for col in columns],
                "type": index_type.lower(),
                "unique": is_unique,
            }
        except Exception as e:
            self.dta_trace(f"Error extracting index info: {e}")
            error_msg = "Error extracting index info"
            raise ValueError(error_msg) from e

    def _ast_expr_to_string(self, expr: Node) -> str:  # noqa: PLR0911
        """Convert an AST expression (like FuncCall) to a proper string representation.

        For example, converts a FuncCall node representing lower(name) to "lower(name)"
        """
        try:
            # Import FuncCall and ColumnRef for type checking

            # Check for FuncCall type directly
            if isinstance(expr, FuncCall):
                # Extract function name
                if hasattr(expr, "funcname") and expr.funcname:
                    func_name = ".".join([name.sval for name in expr.funcname if hasattr(name, "sval")])
                else:
                    func_name = "unknown_func"

                # Extract arguments
                args = []
                if hasattr(expr, "args") and expr.args:
                    args.extend([self._ast_expr_to_string(arg) for arg in expr.args])

                # Format as function call
                return f"{func_name}({','.join(args)})"

            # Check for ColumnRef type directly
            if isinstance(expr, ColumnRef):
                if hasattr(expr, "fields") and expr.fields:
                    return ".".join([field.sval for field in expr.fields if hasattr(field, "sval")])
                return "unknown_column"

            # Try to handle direct values
            if hasattr(expr, "sval"):  # String value
                return str(expr.sval)
            if hasattr(expr, "ival"):  # Integer value
                return str(expr.ival)
            if hasattr(expr, "fval"):  # Float value
                return str(expr.fval)

            # Fallback for other expression types
            return str(expr)
        except Exception as e:
            error_msg = "Error converting expression to string"
            raise ValueError(error_msg) from e

    def _is_same_index(self, index1: dict[str, Any], index2: dict[str, Any]) -> bool:
        """Check if two indexes are functionally equivalent.

        Args:
            index1: First index information dictionary.
            index2: Second index information dictionary.

        Returns:
            True if indexes are equivalent, False otherwise.
        """
        if not index1 or not index2:
            return False

        # Same table?
        if index1["table"] != index2["table"]:
            return False

        # Same index type?
        if index1["type"] != index2["type"]:
            return False

        # Same columns (order matters for most index types)?
        if index1["columns"] != index2["columns"]:
            # For hash indexes, order doesn't matter
            return bool(index1["type"] == "hash" and set(index1["columns"]) == set(index2["columns"]))

        # If one is unique and the other is not, they're different
        # Except when a primary key (which is unique) exists and we're considering a non-unique index on same column
        # Same core definition
        return not (index1["unique"] and not index2["unique"])


class ConditionColumnCollector(ColumnCollector):
    """Specialized ColumnCollector for condition columns.

    A specialized version of ColumnCollector that only collects columns used in
    WHERE, JOIN, HAVING conditions, and properly resolves column aliases.
    """

    # Constants for column reference field counts
    UNQUALIFIED_COLUMN_FIELDS = 1  # Single column name (e.g., "name")
    QUALIFIED_COLUMN_FIELDS = 2  # Table.column format (e.g., "users.name")

    def __init__(self, column_cache: dict[str, set[str]] | None = None) -> None:
        """Initialize ConditionColumnCollector.

        Args:
            column_cache: Optional cache of table -> set of column names.
                If provided, used to verify column existence. If None, falls back to
                permissive behavior (returns True for all checks).
        """
        super().__init__(column_cache=column_cache)
        self.column_cache: dict[str, set[str]] | None = column_cache  # type: ignore[assignment]
        self.condition_columns: dict[str, set[str]] = {}  # Specifically for columns in conditions
        self.in_condition = False  # Flag to track if we're inside a condition

    def __call__(self, node: Any) -> dict[str, set[str]]:  # noqa: ANN401
        """Call the collector on a node.

        Args:
            node: AST node to process.

        Returns:
            Dictionary mapping table names to sets of column names.
        """
        super().__call__(node)
        return self.condition_columns

    def visit_SelectStmt(self, _ancestors: list[Node], node: Node) -> None:  # noqa: C901, N802
        """Visit a SelectStmt node focusing on condition-related clauses.

        Focuses on condition-related clauses while still collecting column aliases.

        Args:
            ancestors: List of ancestor nodes.
            node: SelectStmt node to visit.
        """
        if isinstance(node, SelectStmt):
            self.inside_select = True
            self.current_query_level += 1
            query_level = self.current_query_level

            # Get table aliases first
            alias_visitor = TableAliasVisitor()
            if hasattr(node, "fromClause") and node.fromClause:
                for from_item in node.fromClause:
                    alias_visitor(from_item)
            tables = alias_visitor.tables
            aliases = alias_visitor.aliases

            # Store the context for this query
            self.context_stack.append((tables, aliases))

            # First pass: collect column aliases from targetList
            if hasattr(node, "targetList") and node.targetList:
                self.target_list = node.targetList
                target_list = node.targetList
                if target_list is not None:
                    for target_entry in target_list:
                        if hasattr(target_entry, "name") and target_entry.name:
                            # This is a column alias
                            col_alias = target_entry.name
                            # Store the expression node for this alias
                            if hasattr(target_entry, "val"):
                                self.column_aliases[col_alias] = {
                                    "node": target_entry.val,
                                    "level": query_level,
                                }

            # Process WHERE clause
            if node.whereClause:
                in_condition_cache = self.in_condition
                self.in_condition = True
                self(node.whereClause)
                self.in_condition = in_condition_cache

            # Process JOIN conditions in fromClause
            if node.fromClause:
                for item in node.fromClause:
                    if isinstance(item, JoinExpr) and item.quals:
                        in_condition_cache = self.in_condition
                        self.in_condition = True
                        self(item.quals)
                        self.in_condition = in_condition_cache

            # Process HAVING clause - may reference aliases
            if node.havingClause:
                in_condition_cache = self.in_condition
                self.in_condition = True
                self._process_having_with_aliases(node.havingClause)
                self.in_condition = in_condition_cache

            # Process ORDER BY clause - also important for indexes
            if hasattr(node, "sortClause") and node.sortClause:
                in_condition_cache = self.in_condition
                self.in_condition = True
                for sort_item in node.sortClause:
                    self._process_node_with_aliases(sort_item.node)
                self.in_condition = in_condition_cache

            # Clean up the context stack
            self.context_stack.pop()
            self.inside_select = False
            self.current_query_level -= 1

    def _process_having_with_aliases(self, having_clause: Any) -> None:  # noqa: ANN401
        """Process HAVING clause with special handling for column aliases.

        Args:
            having_clause: HAVING clause node to process.
        """
        self._process_node_with_aliases(having_clause)

    def _process_node_with_aliases(self, node: Node | None) -> None:
        """Process a node, resolving any column aliases it contains.

        Args:
            node: AST node to process.
        """
        if node is None:
            return

        # If node is a column reference, it might be an alias
        if isinstance(node, ColumnRef) and hasattr(node, "fields") and node.fields:
            fields = [f.sval for f in node.fields if hasattr(f, "sval")] if node.fields else []
            if len(fields) == 1:
                col_name = fields[0]
                # Check if this is a known alias
                if col_name in self.column_aliases:
                    # Process the original expression instead
                    alias_info = self.column_aliases[col_name]
                    if alias_info["level"] == self.current_query_level:
                        self(alias_info["node"])
                        return

        # For non-alias nodes, process normally
        self(node)

    def visit_ColumnRef(self, _ancestors: list[Node], node: Node) -> None:  # noqa: C901, N802
        """Process column references in condition context.

        Process column references, but only if we're in a condition context.
        Skip known column aliases but process their underlying expressions.

        Args:
            ancestors: List of ancestor nodes.
            node: ColumnRef node to visit.
        """
        if not self.in_condition:
            return  # Skip if not in a condition context

        if not isinstance(node, ColumnRef) or not self.context_stack:
            return

        # Get the current query context
        tables, aliases = self.context_stack[-1]

        # Extract table and column names
        fields = [f.sval for f in node.fields if hasattr(f, "sval")] if node.fields else []

        # Check if this is a reference to a column alias
        if len(fields) == 1 and fields[0] in self.column_aliases:
            # Process the original expression node instead
            alias_info = self.column_aliases[fields[0]]
            if alias_info["level"] == self.current_query_level:
                self.in_condition = True  # Ensure we collect from the aliased expression
                self(alias_info["node"])
            return

        if len(fields) == self.QUALIFIED_COLUMN_FIELDS:  # Table.column format
            table_or_alias, column = fields
            # Resolve alias to actual table
            table = aliases.get(table_or_alias, table_or_alias)

            # Add to condition columns
            if table not in self.condition_columns:
                self.condition_columns[table] = set()
            self.condition_columns[table].add(column)

        elif len(fields) == self.UNQUALIFIED_COLUMN_FIELDS:  # Unqualified column
            column = fields[0]

            # For unqualified columns, check all tables in context
            found_match = False
            for table_name in tables:
                # Skip schema qualification if present
                actual_table = table_name
                if "." in table_name:
                    _, actual_table = table_name.split(".", 1)

                # Add column to all tables that have it
                if self._column_exists(actual_table, column):
                    if actual_table not in self.condition_columns:
                        self.condition_columns[actual_table] = set()
                    self.condition_columns[actual_table].add(column)
                    found_match = True

            if not found_match:
                logger.debug("Could not resolve unqualified column '%s' to any table", column)

    def _column_exists(self, table: str, column: str) -> bool:
        """Check if column exists in table.

        Args:
            table: Table name.
            column: Column name.

        Returns:
            True if column exists in the table according to column_cache.
            If column_cache is None, returns True (permissive mode).
            If column_cache is empty dict, returns False (strict mode).
        """
        if self.column_cache is None:
            # If cache is not provided (None), use permissive behavior
            # This allows the collector to work without a cache
            return True
        if not self.column_cache:
            # If cache is empty dict, return False for safety
            # This prevents adding non-existent columns when cache was attempted but failed
            return False

        # Check if table exists in cache
        table_columns = self.column_cache.get(table.lower())
        if table_columns is None:
            return False

        # Check if column exists in table
        return column.lower() in {col.lower() for col in table_columns}
