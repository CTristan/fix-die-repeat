# AGENTS.md - Living Document for Coding Agents

This is a living document for coding agents (pi, assistants, etc.) working on **Fix. Die. Repeat.** Update it as you make changes.

---

## Project Overview

**Fix. Die. Repeat.** is a Python rewrite of a bash script that automates an iterative check-review-fix loop using [pi](https://github.com/mariozechner/pi) AI coding agent.

### Purpose

1. Run a check command (e.g., `./scripts/ci.sh`, `uv run pytest`)
2. If checks fail → use pi to fix the errors
3. If checks pass → use pi to review the changes
4. If review finds issues → fix them
5. Repeat until all checks pass and no issues are found

### Tech Stack

- **Language**: Python 3.12+
- **Package Manager**: uv
- **CLI**: Click
- **Configuration**: Pydantic + pydantic-settings
- **Prompt Templates**: Jinja2
- **Terminal Output**: Rich (colors for errors/warnings)
- **Testing**: pytest, pytest-cov
- **Linting**: ruff
- **Type Checking**: mypy (loose mode)

---

## Project Structure

```
fix-die-repeat/
├── fix_die_repeat/          # Main package
│   ├── __init__.py         # Version and package init
│   ├── cli.py             # Click-based CLI interface
│   ├── config.py          # Pydantic settings and path management
│   ├── prompts.py         # Jinja template rendering helpers
│   ├── runner.py          # Core PiRunner class - main loop
│   ├── utils.py           # Utility functions (logging, git, file ops)
│   └── templates/         # Prompt templates consumed by prompts.py
├── tests/                  # Test suite
│   ├── __init__.py
│   ├── test_config.py    # Settings and Paths tests
│   ├── test_prompts.py   # Prompt rendering tests
│   └── test_utils.py     # Utility function tests
├── pyproject.toml          # uv configuration, dependencies, tooling
├── README.md              # User documentation
└── AGENTS.md              # This file - for coding agents
```

---

## Core Architecture

### `PiRunner` (runner.py)

The main class that orchestrates the check-fix-review loop.

```python
runner = PiRunner(settings, paths)
exit_code = runner.run()  # Runs the full loop
```

**Key Methods**:

- `run()` - Main loop, calls steps in sequence
- `run_pi()` / `run_pi_safe()` - Execute pi with logging
- `run_checks()` - Execute the check command
- `filter_checks_log()` - Extract error-relevant lines from check output
- `check_oscillation()` - Detect repeated identical failure output
- `check_and_compact_artifacts()` - Compact large history files
- `_fetch_pr_threads()` - Get PR threads from GitHub via GraphQL
- `_run_local_review()` - Review code changes using pi
- `_process_review_results()` - Handle pi's review findings
- `_resolve_pr_threads()` - Resolve threads on GitHub via pi tool

**State Tracked**:
- `iteration` - Current iteration number (starts at 1, increments each loop)
- `start_sha` - Git commit SHA before any changes (for rollback)
- `pr_review_no_progress_count` - Stagnation counter for PR review mode (max 3)
- `consecutive_toolless_attempts` - Times pi reported success without editing files

### `Settings` (config.py)

Configuration with environment variable support via Pydantic.

**Actual Default Values** (from code):
- `check_cmd`: `"./scripts/ci.sh"`
- `max_iters`: `10`
- `max_pr_threads`: `5`
- `auto_attach_threshold`: `200 * 1024` (200KB)
- `compact_artifacts`: `True`
- `compact_threshold_lines`: `150`
- `emergency_threshold_lines`: `200`
- `large_file_lines`: `2000`
- `pi_sequential_delay_seconds`: `1`
- `ntfy_enabled`: `True`
- `ntfy_url`: `"http://localhost:2586"`

**Optional/Null Defaults**:
- `model`: `None`
- `test_model`: `None`
- `archive_artifacts`: `False`
- `pr_review`: `False`
- `debug`: `False`

**Environment Variables**: All prefixed with `FDR_` (e.g., `FDR_CHECK_CMD`, `FDR_MAX_ITERS`)

### `Paths` (config.py)

Centralized path management for `.fix-die-repeat/` directory.

**Key Paths**:
- `fdr_dir` - `.fix-die-repeat/` root directory
- `review_file` - Historical review entries (preserved across runs)
- `review_current_file` - Current issues to fix (deleted after each iteration)
- `review_recent_file` - Last 50 lines of review.md (used in PR review mode)
- `build_history_file` - `git diff --stat` for each iteration
- `checks_log` - Full check output
- `checks_filtered_log` - Filtered error lines (context + tail)
- `checks_hash_file` - History of check output git hashes
- `pi_log` - All pi invocations and output
- `fdr_log` - Fix. Die. Repeat. internal logging
- `pr_threads_cache` - Cached PR threads (to avoid refetching)
- `pr_threads_hash_file` - Hash of PR key (cache validation)
- `start_sha_file` - Starting git SHA (for rollback)
- `pr_thread_ids_file` - Thread IDs in current PR (for safety check)
- `pr_resolved_threads_file` - Thread IDs pi claims it resolved
- `diff_file` - Git diff of all changes (for review)
- `run_timestamps_file` - Start/end timestamps of run
- `session_log` - Combined output (or timestamped if debug mode)

### Logging (`configure_logger` in utils.py)

Uses Python's standard `logging` module with `RichHandler` for console output.

**Features**:
- Standard `logging.Logger` instance configured by `configure_logger(...)`
- Rich-colored console output plus file handlers for both `fdr_log` and `session_log`
- Supports lazy formatting (`logger.info("... %s", value)`) for G004 compliance
- Supports `logger.exception(...)` in exception handlers

### Prompt Rendering (`prompts.py` + `templates/*.j2`)

Prompt text is stored in Jinja templates under `fix_die_repeat/templates/` and rendered via `render_prompt()`.

**Why**:
- Keeps long prompts out of Python control flow logic
- Makes prompt updates safer and easier to review
- Avoids broad lint rule exceptions for long inline strings

**Current templates**:
- `fix_checks.j2` - Prompt for check-failure fix attempts
- `local_review.j2` - Prompt for local review of generated diff
- `resolve_review_issues.j2` - Prompt for applying review fixes
- `pr_threads_header.j2` - Header/instructions for fetched PR thread context

---

## Key Design Decisions

### 1. Subprocess Wrapping

All shell commands run through `run_command()` (utils.py):

```python
returncode, stdout, stderr = run_command(
    command="git status --porcelain",
    cwd=project_root,
    capture_output=True,
    check=False,
)
```

- Uses `subprocess.run()` with `text=True` and `shell=False`
- Accepts either a command string (tokenized via `shlex.split`) or argv list
- Returns tuple of `(exit_code, stdout, stderr)`
- `capture_output=True` redirects stdout/stderr, `check=False` doesn't raise on failure
- Shell operators (`|`, `&&`, redirection) require explicit wrapping (for example: `bash -lc '...'`)

### 2. Context Management

To avoid exceeding pi's context limit:

- **Auto-attach threshold**: Files <200KB are attached to pi's prompt via `@file.txt`
- **Pull mode**: If total changed files exceed 200KB, they're listed in a string and pi must use `read` tool
- **Artifact compaction**: When `review.md` or `build_history.md` exceed 150-200 lines, they're truncated (last 50-100 lines kept)

### 3. State Persistence

All state stored in `.fix-die-repeat/`:
- Gitignored automatically on first run (added to `.gitignore`)
- Survives crashes and interruptions
- Enables rollback via `git checkout $START_SHA`

### 4. Oscillation Detection

Tracks git hash of check output (`git hash-object checks.log`):

```python
# If hash matches a previous entry
if current_hash in self.paths.checks_hash_file.read_text():
    logger.warning("Check output is IDENTICAL to iteration X. You are going in CIRCLES.")
```

### 5. PR Review Safety

Only resolves PR threads that were actually in the original fetch AND that pi reported as fixed:

```python
# Read what pi claims it resolved
resolved_ids = self.paths.pr_resolved_threads_file.read_text().split("\n")

# Read what was originally in scope
in_scope_ids = self.paths.pr_thread_ids_file.read_text().split("\n")

# Resolve only the intersection
safe_ids = set(resolved_ids) & set(in_scope_ids)

# Only call pi with safe IDs
pi -p "resolve_pr_threads(threadIds: [...safe_ids...])"
```

This prevents accidental resolution of threads that pi didn't actually address.

### 6. No-Progress Detection (PR Review Mode)

In PR review mode, tracks when the same PR threads remain AND git state is unchanged across 3 iterations:

```python
if current_review_hash == last_review_hash and current_git_state == last_git_state:
    pr_review_no_progress_count += 1
    if pr_review_no_progress_count >= 3:
        logger.error("No progress made after 3 iterations in PR review mode.")
        sys.exit(1)
```

---

## Testing

### Python Tooling Policy (uv required)

All Python-based commands (pytest, ruff, mypy, CLI invocations, scripts) must be run via `uv run ...`. Do not activate a virtualenv or call these tools directly; this includes pytest.

### Run All Tests

```bash
uv run pytest
```

### Run with Coverage

```bash
uv run pytest --cov=fix_die_repeat --cov-report=term-missing --cov-report=html
```

### Current Coverage

- **Overall**: ~22%
- **config.py**: ~86% (well tested)
- **utils.py**: ~66% (good coverage)
- **runner.py**: ~0% (main loop, needs tests)
- **cli.py**: ~0% (CLI, needs tests)

### ⚠️ CRITICAL: Test Configuration Policy

**NEVER modify test configuration settings, including coverage thresholds, without EXPLICIT human approval.**

This includes but is not limited to:
- Coverage threshold (`--cov-fail-under`)
- pytest `addopts` in `[tool.pytest.ini_options]`
- Coverage report options (`--cov-report`)
- Test discovery patterns (`testpaths`, `python_files`, `python_classes`, `python_functions`)
- Test command flags in `scripts/ci.sh`

**Why this matters:**
- Coverage thresholds are a quality gate intended to prevent regressions
- Lowering thresholds to make CI pass undermines the entire purpose of testing
- Changes to test configuration affect all future contributors and reviewers

**What to do instead:**
- If coverage drops below 80%, write MORE tests to raise it back up
- If a module is genuinely difficult to test (e.g., `runner.py`), document why and seek approval to exclude it with `--cov-omit`
- Never lower `--cov-fail-under` to make CI pass

**Approval required:**
- Any change to `[tool.pytest.ini_options]` requires explicit approval
- Any change to pytest flags in `scripts/ci.sh` requires explicit approval
- Any change to coverage configuration requires explicit approval

### Test Structure

```python
class TestClassName:
    """Tests for <module>."""

    def test_feature(self) -> None:
        """Test description."""
        # Arrange
        # Act
        # Assert
```

Use pytest fixtures for temporary directories:

```python
import pytest
from pathlib import Path

def test_something(tmp_path: Path) -> None:
    """Test with temporary directory."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("content")
```

---

## Code Quality Tools

### Ruff (Linting + Formatting)

```bash
# Check
uv run ruff check fix_die_repeat tests

# Format
uv run ruff format fix_die_repeat tests

# Both (auto-fix + format)
uv run ruff check --fix fix_die_repeat tests
uv run ruff format fix_die_repeat tests
```

**Configuration**: `pyproject.toml` `[tool.ruff]`

**Per-File Exceptions Policy**: If a rule exception is ever required, configure it on a per-file basis in `pyproject.toml` under `[tool.ruff.lint.per-file-ignores]`, not by adding to global `ignore`.

**⚠️ CRITICAL: Per-file ignore comments MUST explain why we're ignoring the rule instead of fixing the underlying issue.**

Each per-file ignore entry must include a comment for each individual rule code that:
- Explains WHY the rule cannot be reasonably fixed (not just "this file is complex")
- Provides justification for why an exception is appropriate
- Allows future maintainers to reevaluate whether the exception is still valid

**Comments that simply restate the rule (e.g., "# ignore too-many-branches") are NOT acceptable.** The comment must explain the tradeoff being made and why fixing the issue would be worse than ignoring it.

Examples of acceptable comments:
```toml
[tool.ruff.lint.per-file-ignores]
"fix_die_repeat/runner.py" = [
    "PLR0913",  # Refactoring to reduce parameter count would require extracting multiple intermediate types with unclear abstraction boundaries
    "PLR0915",  # Main loop has 12 distinct responsibilities; splitting would break the single-responsibility principle into artificial fragments
]
```

Examples of UNACCEPTABLE comments:
```toml
[tool.ruff.lint.per-file-ignores]
"fix_die_repeat/runner.py" = [
    "PLR0913",  # Too many arguments - BAD: just restates the rule name
    "PLR0915",  # This file is complex - BAD: vague, doesn't explain why it can't be fixed
]
```

This ensures that future maintainers understand the rationale for each rule and can reevaluate whether each exception is still appropriate.

**C901 (complex-structure) - NEVER IGNORE**: The C901 rule checks for functions with high McCabe complexity (cyclomatic complexity). This is a critical code quality metric - functions with high complexity are difficult to understand, test, and maintain, and are more likely to contain bugs.

**C901 MUST NEVER be ignored.** Instead, refactor complex functions into smaller, more focused functions. The default complexity threshold is 10, which is generous. If a function exceeds this threshold:

1. Identify logical sections within the function
2. Extract helper methods/classes for distinct responsibilities
3. Consider breaking down switch/case chains or nested conditionals
4. Use early returns to reduce nesting levels
5. Apply the Strategy pattern or similar design patterns for complex branching

Refactoring for lower complexity improves:
- Testability (smaller functions are easier to unit test)
- Readability (smaller functions are easier to understand)
- Maintainability (changes are less likely to have unintended side effects)
- Debuggability (smaller functions have less state to track)

**PLR0913 (too-many-arguments) - NEVER IGNORE**: The PLR0913 rule checks for function definitions that include too many arguments. By default, this rule allows up to five arguments. Functions with many arguments are harder to understand, maintain, and call.

**PLR0913 MUST NEVER be ignored.** Instead, refactor functions with many arguments using one of these strategies:

1. **Group related arguments into objects** using `dataclass`, `NamedTuple`, or a custom class
2. **Extract helper functions** that handle subsets of the arguments
3. **Use builder patterns** for complex object construction
4. **Apply default arguments** or keyword-only arguments to clarify required vs. optional parameters
5. **Use `@typing.override` decorator** if the function must override a parent class method with a fixed signature
6. **Use `**kwargs` for framework callbacks** when a framework (e.g., Click) injects one parameter per decorator — capture them with `**kwargs` and map into a typed object via a helper function

Example of grouping related arguments:

```python
# Before: too many arguments (PLR0913 violation)
def calculate_position(x_pos, y_pos, z_pos, x_vel, y_vel, z_vel, time):
    new_x = x_pos + x_vel * time
    new_y = y_pos + y_vel * time
    new_z = z_pos + z_vel * time
    return new_x, new_y, new_z

# After: grouped into NamedTuple
from typing import NamedTuple

class Vector(NamedTuple):
    x: float
    y: float
    z: float

def calculate_position(pos: Vector, vel: Vector, time: float) -> Vector:
    return Vector(*(p + v * time for p, v in zip(pos, vel)))
```

Example of using `**kwargs` for Click callbacks:

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class CliOptions:
    name: str | None = None
    count: int | None = None
    verbose: bool = False
    debug: bool = False
    output: str | None = None
    format: str | None = None

def _build_cli_options(kwargs: dict[str, str | int | bool | None]) -> CliOptions:
    """Map Click's kwargs dict into a typed dataclass.

    Click guarantees value types via each option's ``type=`` parameter,
    so the casts below are safe.
    """
    name = kwargs.get("name")
    count = kwargs.get("count")
    output = kwargs.get("output")
    fmt = kwargs.get("format")
    return CliOptions(
        name=str(name) if name is not None else None,
        count=int(count) if count is not None else None,
        verbose=bool(kwargs.get("verbose", False)),
        debug=bool(kwargs.get("debug", False)),
        output=str(output) if output is not None else None,
        format=str(fmt) if fmt is not None else None,
    )

# Before: PLR0913 violation — one param per @click.option
@click.command()
@click.option("--name")
@click.option("--count", type=int)
@click.option("--verbose", is_flag=True)
@click.option("--debug", is_flag=True)
@click.option("--output")
@click.option("--format")
def main(name, count, verbose, debug, output, format):  # 6 args!
    ...

# After: capture with **kwargs, build typed object
@click.command()
@click.option("--name")
@click.option("--count", type=int)
@click.option("--verbose", is_flag=True)
@click.option("--debug", is_flag=True)
@click.option("--output")
@click.option("--format")
def main(**kwargs: str | int | bool | None) -> None:  # 1 param
    options = _build_cli_options(kwargs)
    ...
```

**Key notes for the `**kwargs` + Click pattern:**
- Use `str | int | bool | None` as the kwargs type annotation to avoid ANN401 (`Any` disallowed)
- `dict.get()` returns the full union type, so use explicit casts (`str(val)`, `int(val)`) with `is not None` guards to satisfy mypy
- Click guarantees runtime types via each option's `type=` parameter, making the casts safe
- The `_build_cli_options` helper keeps conversion logic testable and out of the callback

Refactoring to reduce argument count improves:
- **Readability**: Related parameters are clearly grouped together
- **Maintainability**: Changes to related parameters are isolated in one place
- **Testability**: Easier to create test fixtures for parameter groups
- **Extensibility**: New parameters can be added to the group without changing function signatures
- **IDE support**: Better autocomplete and type hints for parameter groups

**PLC0415 (import-outside-top-level) - NEVER IGNORE**: The PLC0415 rule checks for `import` statements outside module top-level scope (for example, inside functions or classes).

**PLC0415 MUST NEVER be ignored.** Follow PEP 8 guidance and place imports at the top of the file, after module docstrings/comments and before module globals/constants. Top-level imports make dependencies explicit and ensure import failures are caught immediately instead of only at runtime on specific code paths.

If PLC0415 fires, fix it by default with this order of operations:
1. Move the import to module top-level.
2. If a circular dependency exists, refactor module boundaries to remove the cycle.
3. If startup cost is the concern, isolate the expensive dependency behind a dedicated adapter/module with clear ownership.
4. Only keep a local import when it is truly required for runtime/environment constraints, and document the rationale in code comments.

Common acceptable reasons for temporary local imports are:
- Avoiding unavoidable circular dependencies during staged refactors
- Deferring very costly module loads when measured and justified
- Avoiding loading optional dependencies in specific runtime environments

Even in those cases, do not add PLC0415 to ignore lists; prefer structural fixes and explicit documentation.

**PLR2004 (magic-value-comparison) - NEVER IGNORE**: The PLR2004 rule checks for the use of unnamed numerical constants ("magic") values in comparisons.

**PLR2004 MUST NEVER be ignored.** The use of "magic" values can make code harder to read and maintain, as readers will have to infer the meaning of the value from the context. Such values are discouraged by PEP 8. For convenience, this rule excludes a variety of common values from the "magic" value definition, such as `0`, `1`, `""`, and `"__main__"`.

If PLR2004 fires, replace the magic value with a named constant:

```python
# Before: magic value (PLR2004 violation)
def apply_discount(price: float) -> float:
    if price <= 100:
        return price / 2
    else:
        return price

# After: named constant
MAX_DISCOUNT = 100

def apply_discount(price: float) -> float:
    if price <= MAX_DISCOUNT:
        return price / 2
    else:
        return price
```

Using named constants improves:
- **Readability**: The constant name explains the meaning and purpose of the value
- **Maintainability**: Changes to the value only need to be made in one place
- **Self-documentation**: Code becomes clearer without needing additional comments
- **Consistency**: The same constant can be reused across the codebase

**Current state**: Per-file ruff ignores are configured for `runner.py`, `cli.py`, `config.py`, `utils.py`, `prompts.py`, and test files.

To add a targeted exception (only when unavoidable), use:

```toml
[tool.ruff.lint.per-file-ignores]
"path/to/file.py" = [
    "RULE_CODE_1",  # Reason for this specific rule
    "RULE_CODE_2",  # Reason for this specific rule
]
```

### MyPy (Type Checking)

```bash
uv run mypy fix_die_repeat
```

**Configuration**: `pyproject.toml` `[tool.mypy]`

**Mode**: Loose - `disallow_untyped_defs = false`, `attr-defined` disabled.

**Per-File Exceptions Policy**: If a type checking exception is required for a specific file, configure it using per-module `ignore_errors` in `pyproject.toml`.

**⚠️ CRITICAL: Per-file ignore comments MUST explain why we're ignoring the rule instead of fixing the underlying issue.**

Each per-module ignore must include a comment that:
- Explains WHY the type error cannot be reasonably fixed (not just "this is third-party code")
- Provides justification for why an exception is appropriate
- Allows future maintainers to reevaluate whether the exception is still valid

**Comments that simply state the module name (e.g., "# ignore mypy errors") are NOT acceptable.** The comment must explain the tradeoff being made.

Examples:
```toml
[[tool.mypy.overrides]]
module = "some_third_party_module"
ignore_errors = true  # This package has no type stubs and fixing would require vendoring the entire library

[[tool.mypy.overrides]]
module = "fix_die_repeat.runner"
ignore_errors = true  # Legacy module with circular imports; fixing requires architectural rewrite tracked in issue #42
```

This ensures that future maintainers understand the rationale for each exception and can reevaluate whether it's still valid or whether the code can now be properly typed.

### File Size Policy

**CRITICAL: All code and documentation files must be kept under 2000 lines.**

If ANY code file (`.py`, `.js`, `.ts`, `.rs`, etc.) or documentation file (`.md`, `.rst`, `.txt`) reaches 2000 lines or more, it **MUST** be refactored into separate files or compacted as necessary.

**Why this matters:**
- Large files are difficult to navigate, understand, and maintain
- Code review becomes exponentially harder with large files
- Merge conflicts are more frequent and harder to resolve in large files
- Loading and processing large files slows down tooling and editor performance
- Long files often indicate multiple responsibilities (violates Single Responsibility Principle)

**When a file reaches the 2000-line threshold, take one of the following actions:**

1. **For code files**:
   - Split into logical modules (e.g., split `utils.py` into `utils.py`, `git_utils.py`, `file_utils.py`)
   - Extract classes or groups of related functions into separate files
   - Consider using packages/directories to organize related modules
   - Keep cohesive functionality together but separate concerns

2. **For documentation files**:
   - Split into topical sections (e.g., split `AGENTS.md` into `AGENTS.md` + `ARCHITECTURE.md` + `WORKFLOW.md`)
   - Move reference-style content to appendix documents
   - Use includes or cross-references instead of duplicating content
   - Preserve narrative flow while offloading details to separate files

3. **For configuration or template files**:
   - Extract reusable sections into separate files
   - Use composition/includes where possible
   - Consolidate similar entries into tables or lists

**Examples:**

```python
# Before: runner.py at 2100 lines
class PiRunner:
    # ... 2100 lines ...

# After: split into 3 files
# runner.py (core loop, ~800 lines)
# runner_review.py (review logic, ~700 lines)
# runner_pr.py (PR-specific logic, ~600 lines)
```

```markdown
<!-- Before: AGENTS.md at 2100 lines -->
# AGENTS.md
<!-- ... 2100 lines ... -->

<!-- After: split into 3 files -->
<!-- AGENTS.md (overview + quick start, ~800 lines) -->
<!-- ARCHITECTURE.md (design decisions, ~700 lines) -->
<!-- CONTRIBUTING.md (patterns + workflows, ~600 lines) -->
```

**Implementation:**
- Use `wc -l <file>` or `rg -l "" -c <file>` to check line counts
- When splitting files, maintain imports/exports appropriately
- Update all references to moved code (imports, links)
- Run full test suite after refactoring to ensure nothing broke

**Exceptions:**
- Generated files (e.g., auto-generated protocol buffers) are exempt
- Third-party vendor files (e.g., minified JavaScript) are exempt
- Data files (e.g., large JSON, CSV) are measured by readability, not line count

**Monitoring:**
- Agents should check file sizes before making large additions
- Reviewers should flag files approaching 1500 lines for early intervention
- CI can optionally add a check to flag files exceeding 2000 lines

### Combined Check

```bash
# Run tests + linting + type check
uv run pytest && uv run ruff check fix_die_repeat tests && uv run mypy fix_die_repeat
```

---

## Common Patterns

### Adding a New Configuration Option

1. Add to `Settings` class (`config.py`):

```python
class Settings(BaseSettings):
    my_new_option: bool = Field(
        default=False,
        alias="FDR_MY_NEW_OPTION",
        description="Description",
    )
```

2. Add to `get_settings()` if CLI support needed:

```python
def get_settings(my_new_option: bool = False, ...) -> Settings:
    settings = Settings()
    if my_new_option is not None:
        settings.my_new_option = my_new_option
    return settings
```

3. Add CLI option (`cli.py`):

```python
@click.option("--my-new-option", is_flag=True)
def main(..., my_new_option: bool, ...):
    settings = get_settings(my_new_option=my_new_option, ...)
```

### Adding a New Utility Function

1. Add to `utils.py` with docstring and type hints:

```python
def my_utility(arg1: str, arg2: int | None = None) -> bool:
    """Brief description.

    Args:
        arg1: Description
        arg2: Description (optional)

    Returns:
        Description
    """
    # Implementation
```

2. Add tests to `tests/test_utils.py`:

```python
class TestMyUtility:
    """Tests for my_utility function."""

    def test_basic_case(self) -> None:
        """Test basic functionality."""
        result = my_utility("test", 5)
        assert result is True
```

### Running Pi Commands

Use `run_pi_safe()` for automatic retry:

```python
pi_args = ["-p", "--tools", "read,edit,write,bash"]
pi_args.append("@file.txt")

returncode, _stdout, _stderr = self.run_pi_safe(*pi_args, "Prompt text")

if returncode != 0:
    self.logger.error("pi failed")
```

### Logging

Always use logger instance:

```python
self.logger.info("Information message")
self.logger.warning("Warning message")
self.logger.error("Error message")
self.logger.debug("Debug details: %s", detail)
```

---

## Areas for Improvement

### High Priority

1. **Integration Tests** (`runner.py`, `cli.py`)
   - Mock pi commands
   - Test full loop scenarios
   - Test PR review flow

2. **Error Recovery**
   - Better handling of pi crashes
   - Graceful degradation when tools unavailable
   - More informative error messages

3. **Configuration Validation**
   - Validate `check_cmd` exists before running
   - Validate model names if specified
   - Check `gh` is available in PR review mode

### Medium Priority

4. **Performance**
   - Parallelize independent operations
   - Cache git operations
   - Reduce redundant file reads

5. **Artifact Management**
   - Better compaction strategies (pi-based summarization)
   - Configurable retention policies
   - Archive rotation

6. **User Experience**
   - Progress bars (Rich)
   - Colored output for key events
   - Better exit codes for different failure modes

### Low Priority

7. **Additional Features**
   - Resume interrupted runs
   - Dry-run mode (don't apply fixes)
   - Diff preview before accepting changes
   - Custom prompt templates

---

## Known Limitations

1. **No Resume Capability**: If interrupted, must start from scratch (artifacts preserved but loop resets).

2. **Limited Pi Integration**: Assumes specific pi behavior (tool names, exit codes). May break with pi updates.

3. **Platform Specific**: Some features (sound playback) are macOS/Linux-specific.

4. **No Remote Pi Support**: Assumes pi is locally installed (no support for remote/hybrid execution).

---

## Working with This Project

### Quick Start for Agents

1. **Read this file** (AGENTS.md) to understand the codebase.
2. **Read README.md** to understand user-facing functionality.
3. **Check existing tests** to understand expected behavior.
4. **Use type hints** — they're comprehensive and guide usage.

### Before Making Changes

1. **Run tests**: `uv run pytest` — ensure baseline passes.
2. **Check linting**: `uv run ruff check fix_die_repeat tests`.
3. **Understand context**: Why does this code exist? What problem does it solve?

### After Making Changes

1. **Update tests**: Add coverage for new code.
2. **Update this document** (AGENTS.md) if architecture changes.
3. **Update README.md** if user-facing changes.
4. **Run full check**: `uv run pytest && uv run ruff check --fix fix_die_repeat tests && uv run ruff format fix_die_repeat tests`.

---

## Dependencies and Why They're Used

- **click** - CLI framework (industry standard, well-documented)
- **jinja2** - Prompt templating (keeps long prompts out of Python code)
- **pydantic** - Configuration validation (robust, type-safe)
- **pydantic-settings** - Environment variable support (complements pydantic)
- **rich** - Terminal output with colors (cross-platform, clean API)
- **pytest** - Testing (fast, modern, extensible)
- **pytest-cov** - Coverage measurement (works seamlessly with pytest)
- **ruff** - Linting/formatting (fast, replaces flake8/black/isort)
- **mypy** - Type checking (static analysis, catches bugs early)

---

## File Reference

### File Content Formats

| File | Format | Purpose |
|-------|---------|---------|
| `review.md` | Markdown | Appended with each iteration, preserves history |
| `review_current.md` | Markdown | Current issues, deleted after each iteration |
| `build_history.md` | Markdown | `git diff --stat` per iteration |
| `checks.log` | Text | Full check command output |
| `checks_filtered.log` | Text | Error lines with 3-line context + last 80 lines |
| `checks_hashes` | Text | Lines of `hash:iteration` for oscillation detection |
| `pi.log` | Text | Pi invocations with command and output |
| `.start_sha` | Text | Single git commit SHA |
| `.pr_thread_ids_in_scope` | Text | One thread ID per line |
| `.resolved_threads` | Text | Thread IDs pi claimed to fix, one per line |
| `diff_file` | Unified diff format | Git diff of all changes |

---

## Git Workflow

### Branching

- Main branch: `main`
- Feature branches: `feature/description` or `fix/description`

### Commit Message Format

```
<type>: <description>

<optional details>

Types: feat, fix, docs, test, refactor, chore
```

Example:
```
feat: add model compatibility testing

Adds --test-model flag to test a model's compatibility
with pi's tools before running the full loop.
```

---

## Contact & Support

- **Original Bash Script**: `~/.local/bin/fix-die-repeat`
- **Pi Documentation**: https://github.com/mariozechner/pi
- **Issues**: Track bugs and feature requests in GitHub issues

---

**Last Updated**: 2026-02-24
**Python Version**: 3.12
**pi Version Tested**: Latest (assumes tool compatibility)
