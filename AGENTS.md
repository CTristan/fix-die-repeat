# AGENTS.md - Living Document for Coding Agents

This is a living document for coding agents (pi, assistants, etc.) working on **Fix. Die. Repeat.** Update it as you make changes.

---

## Project Overview

**Fix. Die. Repeat.** is a Python rewrite of a bash script that automates an iterative check-review-fix loop using [pi](https://github.com/mariozechner/pi) AI coding agent.

### Purpose

1. Run a check command (e.g., `./scripts/ci.sh`, `pytest`)
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

### `Logger` (utils.py)

Custom logger with dual output (console + file).

**Features**:
- Uses Rich for colored console output (bold red for errors, bold yellow for warnings, dim for debug)
- File logging to both `fdr_log` and `session_log`
- Debug messages only shown when `debug=True`

**Methods**:
- `info(message)` - Standard logging (white, both outputs)
- `warning(message)` - Warning logging (bold yellow, both outputs)
- `error(message)` - Error logging (bold red, both outputs, also to stderr)
- `debug_log(message)` - Debug logging (dim, both outputs, only if debug mode)

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

- Uses `subprocess.run()` with `text=True` for string output
- Returns tuple of `(exit_code, stdout, stderr)`
- `capture_output=True` redirects stdout/stderr, `check=False` doesn't raise on failure

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

### Run All Tests

```bash
source .venv/bin/activate
pytest
```

### Run with Coverage

```bash
pytest --cov=fix_die_repeat --cov-report=term-missing --cov-report=html
```

### Current Coverage

- **Overall**: ~22%
- **config.py**: ~86% (well tested)
- **utils.py**: ~66% (good coverage)
- **runner.py**: ~0% (main loop, needs integration tests)
- **cli.py**: ~0% (CLI, needs tests)

**Note**: Low coverage in `runner.py` and `cli.py` is expected — they're integration-heavy and require pi mocking or end-to-end testing.

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
ruff check fix_die_repeat tests

# Format
ruff format fix_die_repeat tests

# Both (auto-fix + format)
ruff check --fix fix_die_repeat tests
ruff format fix_die_repeat tests
```

**Configuration**: `pyproject.toml` `[tool.ruff]`

**Per-File Exceptions Policy**: If a rule exception is ever required, configure it on a per-file basis in `pyproject.toml` under `[tool.ruff.lint.per-file-ignores]`, not by adding to global `ignore`.

**Each per-file ignore entry must include a comment for each individual rule code explaining why the exception was needed.** This ensures that future maintainers understand the rationale for each rule and can reevaluate whether each exception is still appropriate.

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
mypy fix_die_repeat
```

**Configuration**: `pyproject.toml` `[tool.mypy]`

**Mode**: Loose - `disallow_untyped_defs = false`, `attr-defined` disabled.

### Combined Check

```bash
# Run tests + linting + type check
pytest && ruff check fix_die_repeat tests && mypy fix_die_repeat
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
self.logger.debug_log("Debug message")  # Only shown in debug mode
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

1. **Run tests**: `pytest` — ensure baseline passes.
2. **Check linting**: `ruff check fix_die_repeat tests`.
3. **Understand context**: Why does this code exist? What problem does it solve?

### After Making Changes

1. **Update tests**: Add coverage for new code.
2. **Update this document** (AGENTS.md) if architecture changes.
3. **Update README.md** if user-facing changes.
4. **Run full check**: `pytest && ruff check --fix fix_die_repeat tests && ruff format fix_die_repeat tests`.

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

**Last Updated**: 2026-02-23
**Python Version**: 3.12
**pi Version Tested**: Latest (assumes tool compatibility)
