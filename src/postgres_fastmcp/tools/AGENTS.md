# Rules for the MCP tools layer (`src/postgres_fastmcp/tools/`)

These rules add to the root `AGENTS.md`; where they differ, this file wins for `tools/`.

## Language

Everything an agent or an external system sees is written in **English**:

- tool descriptions in `registry.py`;
- `Field(description=...)` of every tool parameter;
- docstrings of `ToolSet` methods in `definitions.py` (FastMCP shows them to the client);
- error messages and log messages.

Russian is allowed only for comments and module/helper docstrings that never reach the agent.
`tests/unit/tools/test_tool_schema.py` fails on Cyrillic in `tools/list` and in tool docstrings;
`tests/unit/shared/test_errors.py` fails on Cyrillic in any `UserFacingError`.

## Parameters

- Declare constraints with `Annotated[Type, Field(...)]`: `ge`/`le`, `min_length`/`max_length`,
  `description`. Every user-facing parameter has an English `description` that lists the accepted
  forms and gives an example.
- Shared parameter types live in `params.py` (`ObjectTypeParam`, `HealthTypesParam`,
  `TopQueriesSortByParam`, `TopQueriesLimitParam`, `IndexQueriesParam`, `OutputParam`). Reuse them
  instead of redeclaring a parameter.
- Shared aliases are plain assignments (`X = Annotated[...]`), not PEP 695 `type X = ...`: with a
  `type` alias the function docstring overrides the `Field` description.

## Tool methods and registration

- A tool is an async method of `ToolSet` in `definitions.py`. It takes no FastMCP `Context`: it gets
  database access with `self._get_db()`, which returns a `DbAccessPort` carrying the current
  request's access (executor, `access_mode`, `write_mode`). Call it once per tool call and pass the
  result to the domain; never store it on `self`.
- Register the tool in `registry.py` (`_basic_specs` or `_full_specs`) with `"fn": toolset.<method>`,
  an explicit `name`, a description, `tags`, `annotations`, `timeout` and `meta`. A `full` tool also
  gets `"auth": full_tool_auth`: `PostgresProvider` passes `full_access_check(...)` there, so the
  tool is listed and callable only when the request's effective access is `full`.
- Descriptions follow the server ceiling (`access_mode`). The exception is `execute_sql`: which
  statements it accepts depends on the request's access, so it has one description for all modes;
  its annotations still follow the ceiling.

## Normalize input instead of rejecting it

- If the input has one obvious meaning (`'Tables'`, `'JSON'`, `'index,vacuum'`, `limit=500`),
  convert it to the canonical value in a `BeforeValidator` / `AfterValidator`.
- Reject only input that cannot be converted, with a `UserFacingError` that ends with a hint:
  `Did you mean '...'?` and the list of accepted values.
- The JSON Schema must not be stricter than what the server accepts, or the client rejects valid
  input before the call. Widen it with `Field(json_schema_extra=callable)` (see `_any_string`,
  `_string_or_list` in `params.py`). Do not use `WithJsonSchema`: it replaces the whole schema and
  drops `Field(description=...)`.

## Tools that return rows

- Take `output: OutputParam = "table"` and return `ToolResult` built by `rows_result()` or
  `sections_result()` from `rendering.py`.
- Register the tool with `"output_schema": None`; otherwise FastMCP wraps the answer into
  `{"result": ...}` and duplicates it.
- Never return Markdown and a JSON copy of the same data in one response.
- Do not cut rows or wrap SQL in `LIMIT`: `ResponseBudgetMiddleware` (`app/middleware/response_budget.py`)
  replaces an answer above `MCP_SERVER_RESPONSE_MAX_TOKENS` with an error that asks the agent to refine the request.
  If the tool's annotations say `read_only_hint=False` (`execute_sql` with `write_mode`), the error is worded
  conditionally: a write-capable tool can still run a plain SELECT, so the text says *if* the statement
  modified data, do not re-run it — query the affected rows with a narrower SELECT instead. Keep
  `read_only_hint` honest: a tool that can write must not be registered with a read-only preset.

## Errors

- Errors meant for the agent inherit `UserFacingError` (`shared/errors.py`); any other exception
  is hidden by `mask_error_details=True` and the agent sees only `Error calling tool`.
- A new `UserFacingError` subclass needs a sample in `tests/unit/shared/test_errors.py::_SAMPLES`.

## Schema budget

`tests/unit/tools/test_tool_schema.py::test_tools_list_fits_budget` caps the size of `tools/list`
in FULL mode. If a deliberate change grows it, re-measure and set the cap to the new size plus 15 %.
