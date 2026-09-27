# Global Rules for AI Agents

**Python Version:** 3.13+

**Language Policy:**

- **Rules**: English only (optimal for LLM understanding and processing)
- **Code docstrings**: Russian (project standard for user-facing documentation)
- **Inline comments**: Russian (project standard for code comments)
- **Error messages**: English (consistent with tooling, search, and ops)
- **Git commit messages**: English (consistent with version history and tooling)
- **Logger messages**: English (consistent with tooling, search, and ops)

---

## Git Commit Messages

**Description:** Commit messages in the repository MUST be written **in English**. This is the project standard for version history and tooling.

**DO NOT:**

- ❌ Write commit messages in Russian (project standard is English)
- ❌ Mix languages in one message without necessity
- ❌ Leave message empty or generic ("fix", "update") without explanation

**DO:**

- ✅ **Start with the feature/branch name, a dash, then the message** — format: `FEATURE-NAME - what was done`
- ✅ **Write commit messages in English** — describe **what was done** (completed change), not what to do
- ✅ Keep it concise; add reason/context in the body when needed
- ✅ Phrase as completed change: "Add …", "Fix …", "Remove …" (what was done)

**Examples (feature name, dash, then message in English):**

```text
FEATURE-123 - Add agent configuration validation
FEATURE-123 - Fix timeout handling when calling MCP
FEATURE-123 - Remove deprecated dependency
FEATURE-123 - Update AGENTS.md: language policy for errors, commits, and logger
```

---

## Using MCP for Documentation

**Description:**
Always use Model Context Protocol (MCP) to get up-to-date documentation for libraries, frameworks, and APIs. Do not rely on your own knowledge of code, as it may be outdated or incomplete.

**DO NOT:**

- ❌ Use your own knowledge about library versions without checking through MCP
- ❌ Assume API relevance without consulting documentation through MCP
- ❌ Use outdated code examples without checking through MCP servers

**DO:**

- ✅ **Always use MCP servers** to get documentation:
  - `mcp_context7_resolve-library-id` - for library search
  - `mcp_context7_query-docs` - to get documentation from Context7
  - `mcp_docs-prefect_SearchPrefect` - for Prefect documentation
  - `mcp_docs-langchain_SearchDocsByLangChain` - for LangChain documentation
  - `mcp_docs-fastmcp_SearchFastMcp` - for FastMCP documentation
- ✅ Check API relevance and usage examples through MCP before writing code
- ✅ Use MCP to get code examples and best practices

**Explanations:**

- MCP provides access to up-to-date documentation from official sources
- Using MCP ensures code compliance with current library versions
- Always check documentation through MCP before using new APIs or patterns

### MCP Usage Examples

**Getting Library Documentation:**

1. First, resolve the library ID:

   ```text
   Use mcp_context7_resolve-library-id to search for library "fastapi"
   ```

2. Then get the documentation:

   ```text
   Use mcp_context7_query-docs with libraryId="/tiangolo/fastapi"
   to get information about routing
   ```

**Searching Specialized Documentation:**

```text
Use mcp_docs-prefect_SearchPrefect to search for information
about creating Prefect flows
```

---

## Using else in try-except Blocks

**Description:**
When using `try-except` blocks with code that should only execute when the `try` block succeeds (without exceptions), you must use the `else` block.
This improves readability and explicitly separates exception handling code from the main execution flow.

**DO NOT:**

- ❌ Return values in the main `try` block after all operations
- ❌ Duplicate return code in `except` and at the end of `try`
- ❌ Use flags to track successful execution

**DO:**

- ✅ Use the `else` block for code that should only execute when `try` succeeds
- ✅ Place return code in the `else` block if it should only execute when there are no exceptions
- ✅ Explicitly separate exception handling (`except`) and successful execution (`else`)

**Example of correct usage:**

```python
# ✅ CORRECT
def update_section(self, title: str, body: str) -> str:
    """Добавляет или обновляет секцию в контенте по заголовку."""
    if not self.content:
        self.content = f"## {title}\n\n{body}"
        return self.content

    try:
        title_escaped = re.escape(title)
        pattern = rf"## {title_escaped}\s*\n.*?(?=\n## |\Z)"
        if re.search(pattern, self.content, re.DOTALL):
            self.content = re.sub(pattern, f"## {title}\n\n{body}", self.content, flags=re.DOTALL)
        else:
            self.content = f"{self.content}\n\n## {title}\n\n{body}"
    except re.error:
        self.content = f"{self.content}\n\n## {title}\n\n{body}" if self.content else f"## {title}\n\n{body}"
        return self.content
    else:
        return self.content
```

**Explanations:**

- The `else` block executes only if no exception was raised in the `try` block
- This explicitly separates successful execution logic from error handling
- Improves readability and follows PEP 8 recommendations
- Helps avoid duplicating return code

---

## Error Handling Rules

**Description:**
All errors in the application must use custom exception classes instead of standard Python exceptions. This ensures uniform error handling, improves code readability, and simplifies debugging.

**DO NOT:**

- ❌ Use standard exceptions (`ValueError`, `RuntimeError`, `TypeError`, `KeyError`, etc.) for business logic
- ❌ Create exceptions without inheriting from the project's base error class (e.g. `a2a_agent.exceptions.AgentApplicationError` when present)
- ❌ Use generic exception types in docstrings (e.g., `ValueError` instead of a specific class)
- ❌ Duplicate error messages in different places in the code

**DO:**

- ✅ **Always use custom error classes** from the project's errors module instead of standard exceptions (e.g. `a2a_agent.exceptions` when defined)
- ✅ **All custom errors must inherit from the project's base error class** — single base for all application errors
- ✅ **Use clear class names** that reflect the essence of the error (e.g., `TaskNotFoundError`, `ConfigurationError`)
- ✅ **Error messages should be in English** (consistent with tooling, search, and ops)
- ✅ **Store additional data in class attributes** if needed for error handling (e.g., task id in `TaskNotFoundError`)
- ✅ **Specify specific error classes in docstrings** in the `Raises` section, not generic types

**Example of correct usage:**

```python
# ✅ CORRECT (when project has exceptions module)
from a2a_agent.exceptions import BaseApplicationError, TaskNotFoundError

async def get_task(task_id: str) -> Task:
    """Получить задачу по идентификатору.

    Args:
        task_id: Идентификатор задачи.

    Returns:
        Модель задачи.

    Raises:
        TaskNotFoundError: Если задача не найдена.
    """
    task = await store.fetch_task(task_id)
    if task is None:
        raise TaskNotFoundError(task_id)
    return task
```

**Example of error class structure:**

```python
# ✅ CORRECT (base class — из проекта, например BaseApplicationError)
class TaskNotFoundError(BaseApplicationError):
    """Исключение при отсутствии задачи."""

    def __init__(self, task_id: str) -> None:
        """Инициализация с идентификатором задачи.

        Args:
            task_id: Идентификатор задачи, которая не найдена.
        """
        message = f"Task not found (id={task_id})"
        super().__init__(message)
        self.task_id = task_id
```

**Explanations:**

- Custom exceptions allow precise error type identification during handling
- Inheriting from the project's base error class ensures uniform handling of all application errors
- Storing additional data in attributes allows using it for logging or error handling
- Specific error classes in docstrings help developers understand which exceptions a method may raise

---

## Python Common Development Rules

### Core Principles (MANDATORY)

Agent MUST follow DRY, KISS, YAGNI and actively refactor violations.

**DO NOT:**

- ❌ Duplicate code logic 3+ times
- ❌ Create functions longer than 20 lines
- ❌ Create classes longer than 200 lines
- ❌ Create modules longer than 400 lines
- ❌ Create classes with multiple responsibilities
- ❌ Hardcode secrets or configuration values
- ❌ Skip error handling

**DO:**

- ✅ **DRY (Don't Repeat Yourself)**: Extract duplicated code (>5 lines) into reusable functions
- ✅ **KISS (Keep It Simple, Stupid)**: Simplify over-engineering, avoid unnecessary abstractions
- ✅ **YAGNI (You Aren't Gonna Need It)**: Don't add functionality until it's needed
- ✅ **Single Responsibility Principle**: One class/function = one reason to change
- ✅ **Dependency Injection**: Use injectable dependencies for testability
- ✅ **Active Refactoring**: Identify and fix violations immediately

### Architecture

**Single Responsibility:**

- One class/function = one reason to change
- Each component should have a single, well-defined purpose

**Dependency Injection:**

- Injectable dependencies for testability
- Avoid hardcoded dependencies
- Use constructor injection or dependency injection frameworks

### Code Organization

**Project package:** `a2a_agent` (or the actual project package name).

Organize code by feature/domain; one integration layer (e.g. providers, services) per subpackage.

**DO:**

- ✅ Organize code by feature/domain, not by technical layer
- ✅ Keep related code together
- ✅ Use clear, descriptive module and package names
- ✅ Use consistent naming per integration (e.g. `*_service.py`, `*_provider.py`).

**Type aliases:** Define type aliases (including `NewType`) in the **same file** where the classes or functions that create or use them are defined. Do not move aliases to a separate "models" or "types" module just for re-export.

**DO:**

- ✅ Define the alias in the module where the provider/class that returns or consumes it lives (e.g. `MCPTools` in the FastMCP provider module that returns `MCPTools`).

**DO NOT:**

- ❌ Put type aliases in a central `models` or `types` package when the only consumers are in one feature module.

### Limits

**Functions:**

- **Maximum: 20 lines** (excluding docstrings and type hints)
- If a function exceeds this limit, split it into smaller functions
- Each function should do one thing well

**Classes:**

- **Maximum: 200 lines** (excluding docstrings and type hints)
- **Single responsibility**: One class = one reason to change
- If a class exceeds this limit or has multiple responsibilities, split it

**Modules:**

- **Maximum: 400 lines**
- **Maximum: 7 public functions/classes** per module
- If a module exceeds these limits, split it into multiple modules

**Explanations:**

- Smaller functions and classes are easier to understand, test, and maintain
- Limits help prevent code complexity and maintainability issues
- These are hard limits - refactor immediately when exceeded

### Documentation

**Rule:** All docstrings must follow Google Style Docstrings format.

**DO:**

- ✅ Write docstrings for all public functions, classes, and modules
- ✅ Use Google Style format with sections: Args, Returns, Raises
- ✅ Write docstrings and inline comments in Russian for user-facing documentation
- ✅ Include type information in docstrings when helpful
- ✅ Document complex logic and business rules

**Example:**

```python
def process_device(device_id: str, config: DeviceConfig) -> DeviceResult:
    """Обрабатывает устройство согласно конфигурации.

    Args:
        device_id: Уникальный идентификатор устройства.
        config: Конфигурация обработки устройства.

    Returns:
        Результат обработки устройства с деталями операции.

    Raises:
        DeviceNotFoundError: Если устройство не найдено.
        InvalidConfigError: Если конфигурация некорректна.
    """
    # Implementation
```

### Agent Workflow

When working with code, the agent MUST:

1. **Identify violations** (DRY, KISS, YAGNI, SRP)
2. **Extract duplicated code** (>5 lines) into reusable functions
3. **Simplify over-engineering** - remove unnecessary abstractions
4. **Split large classes/functions** that exceed limits
5. **Preserve original behavior** - refactoring should not change functionality

**Workflow Steps:**

1. Analyze code for violations
2. Identify refactoring opportunities
3. Extract common patterns
4. Split large components
5. Verify behavior is preserved
6. Update tests if needed

### Red Flags

Immediate refactoring required when detecting:

- ❌ **Duplicate logic 3+ times** - Extract to function/class
- ❌ **Functions > 20 lines** - Split into smaller functions
- ❌ **Classes with multiple responsibilities** - Apply Single Responsibility Principle
- ❌ **Missing error handling** - Add proper exception handling
- ❌ **Hardcoded secrets** - Move to configuration/environment variables
- ❌ **Modules > 400 lines** - Split into multiple modules
- ❌ **Modules with > 7 public functions** - Split into multiple modules
- ❌ **Deeply nested conditionals** - Simplify logic
- ❌ **Magic numbers/strings** - Extract to named constants
- ❌ **God objects/classes** - Split into smaller, focused classes

**Explanations:**

- Red flags indicate code quality issues that must be addressed immediately
- These violations make code harder to maintain, test, and understand
- Refactoring should be done proactively, not deferred

---

## Python UV Execution Rules

**MANDATORY:** Agent MUST use `uv run python` instead of bare `python` for all Python execution.

**DO NOT:**

- ❌ Use `python` or `python3` directly
- ❌ Use `./script.py` to execute scripts
- ❌ Execute Python code without `uv run` prefix
- ❌ Assume Python is available in PATH

**DO:**

- ✅ **Always use `uv run python`** for all Python execution
- ✅ Use `uv run python script.py` to run scripts
- ✅ Use `uv run python -m module_name` for module execution
- ✅ Use `uv run python -c "code"` for short inline code
- ✅ Create temp files for longer test code

### Core Command

```bash
uv run python script.py
```

**Explanations:**

- `uv run` ensures code runs in the project's isolated virtual environment
- Dependencies from `pyproject.toml` are automatically available
- Cross-platform consistency (works the same on all platforms)
- No need to manually activate virtual environment

### Command Patterns

**✅ Correct:**

```bash
# Run a script
uv run python main.py

# Run as module
uv run python -m module_name

# Execute inline code
uv run python -c "print('Hello')"

# Run tests
uv run python -m pytest

# Type checking
uv run python -m mypy src/
```

**❌ Never use:**

```bash
# ❌ Direct Python execution
python main.py
python3 main.py

# ❌ Direct script execution
./main.py

# ❌ Without uv run
python -m pytest
mypy src/
```

### Test Code Rules

**For code < 20 lines:**

Use `uv run python -c "code"`:

```bash
uv run python -c "from pathlib import Path; print(Path.cwd())"
```

**For code ≥ 20 lines:**

1. Create temporary file
2. Execute with `uv run python temp.py`
3. Delete temporary file

```bash
# Create temp file, run, then delete
echo 'long_code_here' > temp.py && uv run python temp.py && rm temp.py
```

**DO:**

- ✅ Use `-c` flag for short code snippets (< 20 lines)
- ✅ Create temp files for longer code
- ✅ Always clean up temp files after execution
- ✅ Use descriptive temp file names when possible

**DO NOT:**

- ❌ Use `-c` for long code (hard to read and maintain)
- ❌ Leave temp files in the project
- ❌ Use generic names like `test.py` that might conflict

**Unit test conventions** (behavior over implementation, assert on outcome and contract, one mock at boundary): see [tests/README.md](tests/README.md#testing-conventions).

### Common Usage

```bash
# Run application script
uv run python app.py

# Run tests
uv run python -m pytest

# Type checking
uv run python -m mypy src/

# Run specific test file
uv run python -m pytest tests/test_module.py

# Execute module
uv run python -m myapp.cli

# Long test code (create temp file)
cat > /tmp/test_code.py << 'EOF'
# Your test code here
print("Testing...")
EOF
uv run python /tmp/test_code.py
rm /tmp/test_code.py
```

### Quick Reference

| Old Command                | New Command                      |
| -------------------------- | -------------------------------- |
| `python script.py`         | `uv run python script.py`        |
| `python3 script.py`       | `uv run python script.py`        |
| `./script.py`              | `uv run python script.py`        |
| `python -m pytest`        | `uv run python -m pytest`        |
| `python -c "code"`         | `uv run python -c "code"`        |

**Benefits:**

- ✅ **Isolated environment** - Each project has its own dependencies
- ✅ **Correct dependencies** - Uses versions from `pyproject.toml` and `uv.lock`
- ✅ **Cross-platform consistency** - Works the same on macOS, Linux, Windows
- ✅ **No manual activation** - Virtual environment is managed automatically
- ✅ **Reproducible** - Same environment for all developers

**Explanations:**

- `uv run` automatically creates and manages virtual environments
- Dependencies are resolved from `pyproject.toml` and locked in `uv.lock`
- No need to manually activate virtual environments
- Ensures all team members use the same Python version and dependencies
- Prevents conflicts between different projects' dependencies

---

## Python Linting and Type Checking Rules

**MANDATORY:** Agent MUST achieve zero errors at each linting step through iterative fixes.

### Core Commands (Execute in Sequence)

Agent MUST achieve zero errors at each step:

1. `uv run ruff check --fix --show-fixes` - Auto-fix code issues
2. `uv run mypy --show-error-codes --pretty .` - Type checking
3. `uv run ruff format` - Final formatting
4. `markdownlint **/*.md --fix` - Markdown linting (if .md files exist)

**DO NOT:**

- ❌ Skip any linting step
- ❌ Proceed to next step with errors remaining
- ❌ Use legacy type syntax (`Union`, `Optional`, `List`, `Dict`, etc.)
- ❌ Ignore critical errors (E9xx, F8xx)
- ❌ Ignore security issues (B, S)
- ❌ Guess types without analyzing context
- ❌ Use `Any` without justification
- ❌ Skip type annotations
- ❌ Use `# type: ignore` without specific error code and justification

**DO:**

- ✅ **Execute commands in sequence** - each step must pass before proceeding
- ✅ **Achieve zero errors** at each step before moving to the next
- ✅ **Use modern type syntax** - `str | None` instead of `Optional[str]`, `int | str` instead of `Union[int, str]`
- ✅ **Fix errors by priority** - Critical → Security → Logic → Style
- ✅ **Preserve original logic** - fixes should not change behavior
- ✅ **Log manual fixes** with comments explaining the change
- ✅ **Infer types from context** - analyze code, not guesswork
- ✅ **Add explicit annotations** - for all functions, methods, and variables
- ✅ **Use `# type: ignore[error-code]`** - only with specific error code and justification

### Modern Type Syntax (Required)

**✅ Use these:**

```python
# Built-in generic types
list[str]
dict[str, int]
set[float]
tuple[int, ...]

# Union types
int | str | None

# Generic functions
def func[T](x: T) -> T: ...

# Collections from collections.abc
from collections.abc import Callable, Iterable, Mapping
```

**❌ Don't use legacy:**

```python
# ❌ Legacy imports
from typing import List, Dict, Union, Optional

# ❌ Legacy union syntax
Union[int, str]  # Use: int | str

# ❌ Legacy optional syntax
Optional[str]    # Use: str | None

# ❌ Legacy generic types
List[int]        # Use: list[int]
Dict[str, int]   # Use: dict[str, int]
```

**Allowed imports from typing:**

```python
from typing import Protocol, TypeAlias, TypeGuard  # Only these from typing
from collections.abc import Callable, Iterable, Mapping  # Prefer these
```

**Explanations:**

- Modern syntax (Python 3.10+) is required for Python 3.13+
- Built-in generics (`list`, `dict`, `set`, `tuple`) are preferred over `typing` equivalents
- Union operator `|` is more readable than `Union`
- `collections.abc` provides abstract base classes for type hints

### Process

#### 1. Ruff Check

**Command:**

```bash
uv run ruff check --fix --show-fixes
```

**Process:**

1. Run the command to auto-fix issues
2. Analyze remaining errors by priority:
   - **Critical (E9xx, F8xx)** - syntax/logic errors - fix immediately
   - **Security (B, S)** - vulnerabilities - fix immediately
   - **Logic (SIM, PLR)** - simplifications - fix to improve code quality
   - **Style (E, W)** - formatting - fix for consistency
3. Manually fix remaining errors
4. Re-run until 0 errors remain

**DO:**

- ✅ Fix critical errors first (E9xx, F8xx)
- ✅ Address security issues immediately (B, S)
- ✅ Apply simplifications (SIM, PLR) to improve code
- ✅ Fix style issues for consistency
- ✅ Re-run command after each fix batch

#### 2. MyPy Type Check

**Command:**

```bash
uv run mypy --show-error-codes --pretty .
```

**Process:**

1. Run the command to check type annotations
2. Analyze errors by category:
   - Missing type annotations
   - Incorrect type annotations
   - Incompatible types
3. For each mypy error:
   - **Analyze context** - Study function body, usage patterns
   - **Infer types** - From actual code, not guesswork:
     - **Returns**: Check all return statements
     - **Parameters**: Check operations called on them
     - **Variables**: Study assignments and usage
   - **Apply modern syntax** - Use `|` not `Union`
   - **Add None handling** - Use `| None` for optionals
4. Re-run until 0 errors remain

**DO:**

- ✅ Use modern type syntax (Python 3.10+)
- ✅ Add explicit type annotations for all functions, methods, and variables
- ✅ Fix type incompatibilities
- ✅ Use `--show-error-codes` to understand specific issues
- ✅ Re-run after each fix batch
- ✅ Infer types from actual code usage, not assumptions

**Example of modern type syntax:**

```python
# ✅ CORRECT (Modern syntax)
def process_data(data: dict[str, int] | None) -> list[str]:
    """Process data and return results."""
    if data is None:
        return []
    return [str(v) for v in data.values()]

# ❌ INCORRECT (Old syntax)
from typing import Optional, Union, Dict, List

def process_data(data: Optional[Dict[str, int]]) -> List[str]:
    """Process data and return results."""
    if data is None:
        return []
    return [str(v) for v in data.values()]
```

**Example of context-based inference:**

```python
# Analyze the function body to infer types
def process_data(data: dict[str, list[int]]) -> list[str]:
    """Process data and return string results.

    Args:
        data: Dictionary mapping keys to lists of integers.

    Returns:
        List of string representations of sums.
    """
    # From this code, we can see:
    # - data.values() returns list[list[int]]
    # - sum(values) returns int
    # - str(...) returns str
    # - List comprehension returns list[str]
    return [str(sum(values)) for values in data.values()]
```

**Common Fixes:**

| Error                              | Fix                                                              |
| ---------------------------------- | ---------------------------------------------------------------- |
| `missing return type`              | Add return annotation: `-> ReturnType`                           |
| `missing parameter annotation`     | Add parameter type: `param: ParamType`                           |
| `incompatible types`               | Fix type mismatch in assignment or operation                     |
| `item "None" has no attribute`     | Add None check: `if x is not None:` or use `x \| None`           |

**Example fixes:**

```python
# ❌ Error: missing return type
def get_items():
    return [1, 2, 3]

# ✅ Fixed: Added return type
def get_items() -> list[int]:
    return [1, 2, 3]

# ❌ Error: missing parameter annotation
def process(data):
    return len(data)

# ✅ Fixed: Added parameter type
def process(data: list[str]) -> int:
    return len(data)

# ❌ Error: item "None" has no attribute "upper"
def process(text: str | None) -> str:
    return text.upper()

# ✅ Fixed: Added None check
def process(text: str | None) -> str:
    if text is None:
        return ""
    return text.upper()
```

**Exception Handling for Type Checking:**

**Legacy code (justified use of type: ignore):**

```python
# Legacy code that can't be easily typed
def legacy(*args, **kwargs):  # type: ignore[misc]
    """Legacy function with untyped arguments."""
    pass
```

**Third-party libraries (justified use of type: ignore):**

```python
# Third-party library without type stubs
result = external_lib.call()  # type: ignore[no-untyped-call]
```

**Dynamic code (justified use of Any):**

```python
from typing import Any

# JUSTIFIED: External API varies, type is truly unknown
data: Any  # External API response structure varies
```

**DO:**

- ✅ Use `# type: ignore[error-code]` with specific error code
- ✅ Add comment explaining why ignore is needed
- ✅ Use `Any` only when truly necessary and justify with comment
- ✅ Prefer fixing types over ignoring errors

**DO NOT:**

- ❌ Use `# type: ignore` without error code
- ❌ Use `Any` without justification
- ❌ Ignore errors that can be fixed with proper types

#### 3. Formatting & Markdown

**Commands:**

```bash
uv run ruff format
markdownlint **/*.md --fix  # if .md files exist
```

**Process:**

1. Run `ruff format` to format Python code
2. Run `markdownlint` to format Markdown files (if they exist)
3. Verify no formatting issues remain

**DO:**

- ✅ Run formatting after all code fixes
- ✅ Format Markdown files if project contains .md files
- ✅ Verify formatting is consistent

### Success Criteria

All of the following must be true:

- ✅ **All commands return 0 errors** - no errors or warnings
- ✅ **Manual fixes logged with comments** - explain non-obvious fixes
- ✅ **Original logic preserved** - fixes don't change behavior
- ✅ **Modern type syntax used** - `|` instead of `Union`, built-in generics not `typing` equivalents
- ✅ **Types inferred from context** - not guessed, based on actual code analysis
- ✅ **`Any` used only with justification** - comment explaining why it's necessary

**Example of logging manual fixes:**

```python
# Fixed: Added type annotation to satisfy mypy (error code: missing-return-type)
def process_item(item: Item) -> ProcessedItem:
    """Process an item and return processed result."""
    # Original logic preserved
    return ProcessedItem.from_item(item)
```

### Configuration

**DO:**

- ✅ Use project's `pyproject.toml` settings for ruff and mypy
- ✅ Follow project-specific linting rules
- ✅ Use standard defaults if project config is missing

**Explanations:**

- Sequential execution ensures each tool validates the code properly
- Zero errors requirement maintains code quality standards
- Modern type syntax improves readability and is required for Python 3.13+
- Priority-based fixing ensures critical issues are addressed first
- Preserving original logic ensures refactoring doesn't break functionality
- Context-based type inference ensures accuracy and prevents errors

### Type System Quick Reference

**Type alias for complex types:**

```python
type Processor = Callable[[dict[str, int]], list[str]]
```

**Protocol for structural typing:**

```python
from typing import Protocol

class Processable(Protocol):
    def process(self, data: dict[str, int]) -> list[str]: ...
```

**Explanations:**

- Use `Protocol` for structural typing (duck typing with type safety)
- Use `TypeAlias` for complex type aliases
- Use `TypeGuard` for type narrowing functions
- Prefer `collections.abc` over `typing` for collection types
- Always infer types from actual code usage, not assumptions

---

# Agent Rules for postgres-fastmcp

## Imports: full paths only, no re-exports in `__init__.py`

- **Do not re-export** symbols from package `__init__.py` files. Keep `__init__.py` minimal (docstring only or package-specific definitions like `Settings` in `config`).
- **Use full module paths** for imports: import from the concrete module where the symbol is defined, not from the package.
  - Good: `from postgres_fastmcp.domains.db_access import DbAccessService`, `from postgres_fastmcp.postgres.driver import SqlExecutor`
  - Bad: `from postgres_fastmcp.domains import DbAccessService`, `from postgres_fastmcp.postgres import SqlExecutor`

## No `from __future__ import annotations`

Do **not** use `from __future__ import annotations` anywhere in the codebase.

- For self-referential types inside class definitions, use string annotations:

  ```python
  class PlanNode:
      children: list["PlanNode"] = field(factory=list)

      @classmethod
      def from_json_data(cls, data: dict) -> "PlanNode": ...
  ```

- For circular imports, use lazy imports inside functions (with `# noqa: PLC0415`):

  ```python
  async def _get_plan(self, query: str) -> str:
      from postgres_fastmcp.domains.explain.explain_plan import ExplainPlanBuilder  # noqa: PLC0415
      ...
  ```

- Move `TYPE_CHECKING`-guarded imports to regular imports unless they cause circular dependencies.

## Tool Definitions

Rules for the MCP tools layer (English-only agent-facing text, parameter types and normalization,
`output` and `ToolResult`, the response budget, adding a tool) live in
[`src/postgres_fastmcp/tools/AGENTS.md`](src/postgres_fastmcp/tools/AGENTS.md).

In short: tools are async methods of `ToolSet` (`tools/definitions.py`); they take no FastMCP
`Context` and get database access through `self._get_db()`, which returns a `DbAccessPort` with the
current request's access. `tools/registry.py` registers them on a `LocalProvider` with
`Tool.from_function` (description, tags, annotations, timeout, `auth` for `full` tools).
`PostgresProvider` (`provider.py`) owns the registration, the connection pool and tool visibility.

## Server Startup

`app/main.py` builds `Settings` from the CLI flags (`build_settings_from_cli`: an explicit flag >
config.json > env/.env > defaults), builds the server with `create_server` **before** disabling logs for
stdio (startup auth warnings go to stderr), and runs it with `mcp.run(transport=...)`:

```python
mcp = create_server(settings, build_auth=actual_transport != "stdio")
if actual_transport == "stdio":
    configure_logging(disable=True)

if actual_transport == "http":
    mcp.run(
        transport="http",
        host=settings.server.host,
        port=settings.server.port,
        path=settings.server.endpoint,
        uvicorn_config={"ws": "websockets-sansio", "log_config": None},
    )
else:
    mcp.run(transport="stdio")
```

`create_server` also registers `GET /health` (`custom_route`, outside auth) unless
`server.health_endpoint_enabled` is false; it calls `PostgresProvider.ping()` with a 2 s timeout.

## Layered Architecture

- **App / composition root** (`app/`): config (`app/config/`: `Settings` with one block per env prefix `MCP_<SECTION>_`, `DatabaseConfig` without env for library code and `DatabaseSettings` with env, `AuthSettings` in `app/config/auth.py`), the auth provider factory (`app/auth.py::build_auth_provider`), server assembly, `/health` and startup auth warnings (`app/server.py::create_server`), middleware, entry point (`app/main.py`)
- **Provider** (`provider.py`): `PostgresProvider(LocalProvider)` — owns `DbAccessService` (pool closed in the provider `lifespan`), resolves the request's access and registers the tools
- **Access** (`access.py`): `EffectiveAccess`, `AccessPolicy`, `AccessResolver`, `resolve_access` (claim rules), `full_access_check`; depends only on `shared/` and `fastmcp.server.auth`
- **Presentation** (`tools/`): `ToolSet` with the tool methods (`tools/definitions.py`) and registration with descriptions/annotations (`tools/registry.py`)
- **Domains** (`domains/`): one package or module per feature — `catalog`, `querying`, `explain`, `health`, `index_tuning`, `top_queries`, plus `db_access` (pool, executors per `EffectiveAccess`, `DbAccessPort`). Domain services take a `DbAccessPort`, never `DbAccessService`
  - `index_tuning` is the largest domain package and is split by responsibility:
    - `models.py` — dataclasses (`IndexRecommendation`, `IndexRecommendationAnalysis`, `IndexTuningResult`) and string helpers
    - `workload.py` — workload sources (SQL file, `pg_stat_statements`, explicit query list), validation/parsing, query weights — plain functions
    - `cost_eval.py` — `CostEvaluator`: memoized what-if cost/size evaluation (EXPLAIN plans, hypopg, `pg_stats`) plus `extract_cost_from_json_plan`
    - `base.py` — `IndexTuningBase`: `analyze_workload` orchestration, prechecks, shared Pareto objective, recommendation formatting
    - `dta_calc.py` — `DatabaseTuningAdvisor`: seed + greedy search algorithm
    - `candidates.py` — `CandidateGenerator`: candidate enumeration and filtering (existing indexes, condition columns, long text columns), hypopg batch sizing
    - `index_compare.py` — structural comparison of index definitions via pglast (pure functions)
    - `condition_collector.py` — `ConditionColumnCollector` AST visitor
    - `presentation.py` — `TextPresentation`: result rendering; `service.py` — `IndexAnalysisService` facade consumed by `tools/`
- **Infrastructure** (`postgres/`): SQL driver, connection pool, safe execution and validation (`postgres/security/`), param substitution, AST utils. Domains type against `postgres/ports.py` protocols (`SqlDriverPort` / `QueryExecutorPort`), never against concrete executors
- **Shared kernel** (`shared/`): errors, utils, enums, logger — importable from any layer

Dependency rules:

- Direction: `app -> provider -> tools -> domains -> postgres -> shared`; `access.py` is imported by `provider`, `app` and `domains/db_access`. The one upward import: `provider.py` takes the `DatabaseConfig` model from `app/config/database.py` (plain data, no app logic).
- No upward imports (`postgres/` must not import from `domains/`, `domains/` must not import from `tools/`, `provider` or `app/`).
- Domains must not import each other. The single allowed exception: `index_tuning` -> `explain` (index tuning consumes explain plans).
- A module earns a separate file at roughly >100 lines of own logic or a distinct dependency set; a package needs >=3 substantive modules; a stateless one-method class should be a function.

## Library Documentation

When implementing changes, verify all library APIs via MCP tools (especially FastMCP 4 docs).
