# PostgreSQL error messages implementation plan

**Goal:** Expose SQLSTATE and sanitized PostgreSQL primary messages through MCP.

**Architecture:** Convert psycopg errors at the registered tool boundary before
FastMCP masks them. Keep driver exceptions intact inside the server so
transaction recovery, plan checks and extension fallbacks retain their behavior.

**Scope:** Generic SQL errors only. No domain-specific schemas or normalization.

## Tasks

- [x] Verify origin/main and the deployed production source files.
- [x] Reproduce masked errors on production with read-only queries.
- [x] Add client-level tests for SQLSTATE, primary messages and redaction.
- [x] Observe failing tests, implement the handler and verify passing tests.
- [x] Preserve tool signatures, user-facing errors and internal masking.
- [x] Verify real PostgreSQL errors and subsequent queries in basic/full modes.
- [x] Verify valid JSON SRFs in SELECT, FROM and subqueries.
- [x] Run unit/integration tests, Ruff, mypy and Markdown checks.
- [x] Independently review the diff and resolve findings with regression tests.
- [ ] Create a Russian draft PR targeting main; do not merge or publish.

## Error disclosure contract

Only SQLSTATE and diag.message_primary may reach the client. Never fall back
to str(psycopg_error). Omit DETAIL, HINT, CONTEXT and internal queries. Redact
connection URIs, connection parameters and credential assignments even inside
primary messages. Missing diagnostics receive a generic database error.
Unexpected application exceptions stay masked.

Connection/authentication errors (SQLSTATE classes 08/28) retain their SQLSTATE
with generic text: even a username could equal the configured password.

## Decisions

- Use the existing isolated workspace and origin/main as the base.
- Archived wrappers and the data loader are outside this universal server fix.
- Use the tool boundary rather than the driver: internal psycopg handlers must
  still distinguish original exceptions, cancellation and plan-check fallbacks.

## Validation

- Python 3.13: 2218 unit tests passed. Local sockets require sandbox escalation.
  Disable the replacer logger during this run: an existing test globally mocks
  re.sub, which breaks Python 3.13 traceback formatting in that logger.
- PostgreSQL 15/16: 44 integration tests passed using a temporary fixture with
  vanilla PostgreSQL images. Standard HypoPG image preparation skipped the tests
  locally; the new regressions do not require HypoPG.
- Ruff check, mypy and formatting passed.
- New Markdown plan passed markdownlint. README has 236 existing violations,
  the same count as origin/main; no new violations were introduced.
- Independent review found credential suffix leaks with comma/semicolon values,
  escaped spaces and URI apostrophes. Regression tests failed before the fix and
  passed afterward. A second review found no remaining important issue.
