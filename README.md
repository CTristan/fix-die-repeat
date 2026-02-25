# Fix. Die. Repeat.

> *"I'm not going to give up on this. Not today, not tomorrow, not ever."* — *Live. Die. Repeat.* aka *Edge of Tomorrow*

A relentless check → fix → review loop powered by [pi](https://github.com/mariozechner/pi).

This tool is a polite form of cruelty: it traps your coding agent in a time loop until reality stabilizes.

---

## The Premise

In *Live. Die. Repeat.* aka *Edge of Tomorrow*, a soldier relives the same day until the battle finally goes right.

*Fix-Die-Repeat* does the same thing to your repository:

- **The “day”**: one full iteration of checks and review
- **The “death”**: a failing check or a critical review finding
- **The “reset”**: run pi, apply changes, and start the day again
- **The “escape”**: checks pass *and* the review reports `NO_ISSUES`

It’s Groundhog Day, but for CI.

## What It Does

*Fix-Die-Repeat* runs a loop (a.k.a. one more reset):

1. **Run your check command** (e.g., `./scripts/ci.sh`, `pytest`)
2. **If checks fail** → the loop “kills” the run and pi attempts a fix
3. **If checks pass** → pi reviews the diff for problems
4. **If review finds issues** → pi fixes them and the day restarts
5. **Repeat** until the run survives both checks *and* review

## Installation

### Standard Install (adds command to PATH)

```bash
# Install uv if needed
pip install uv

# Install Fix. Die. Repeat. globally
uv tool install .
```

This installs `fix-die-repeat` as a command on your PATH.

### Dev Install (editable)

```bash
uv sync --all-extras
```

This installs the project in editable mode along with all dev dependencies (pytest, ruff, mypy, etc.) into a managed virtual environment. Use `uv run <command>` to invoke tools within it.

---

## Requirements

- Python 3.12+
- [pi](https://github.com/mariozechner/pi) — must be installed and on your PATH
- Git — for tracking changes and diffing
- (Optional) GitHub CLI (`gh`) — required only for `--pr-review` mode

---

## Quick Start

```bash
# Basic usage with default check command
fix-die-repeat

# Custom check command
fix-die-repeat -c "pytest -xvs"

# Custom check command with specific model
fix-die-repeat -c "make test" -m anthropic/claude-sonnet-4-5
```

---

## Command Line Options

```
Usage: fix-die-repeat [OPTIONS]

  Automated check, review, and fix loop using pi.

Options:
  -c, --check-cmd TEXT      Command to run checks (default: ./scripts/ci.sh)
  -n, --max-iters INTEGER   Maximum loop iterations (default: 10)
  -m, --model TEXT          Override model selection (e.g., anthropic/claude-
                            sonnet-4-5)
      --max-pr-threads INTEGER  Maximum PR threads to process per iteration
                            (default: 5)
      --archive-artifacts       Archive existing artifacts to a timestamped folder
      --no-compact              Skip automatic compaction of large artifacts
      --pr-review               Enable PR review mode
      --test-model TEXT         Test model compatibility before running (exits
                            after test)
  -d, --debug               Enable debug mode (timestamped session logs and
                            verbose logging)
      --version                 Show the version and exit.
      --help                    Show this message and exit.
```

## Environment Variables

All CLI options can be set via environment variables (prefixed with `FDR_`):

```bash
export FDR_CHECK_CMD="pytest"
export FDR_MAX_ITERS=15
export FDR_MODEL="anthropic/claude-sonnet-4-5"
export FDR_PR_REVIEW=1
export FDR_DEBUG=1

fix-die-repeat
```

### All Environment Variables

| Variable | Description | Default |
|-----------|-------------|----------|
| `FDR_CHECK_CMD` | Command to run checks | `./scripts/ci.sh` |
| `FDR_MAX_ITERS` | Maximum loop iterations | `10` |
| `FDR_MODEL` | Override model selection | (none) |
| `FDR_TEST_MODEL` | Test model compatibility and exit | (none) |
| `FDR_MAX_PR_THREADS` | PR threads to process per iteration | `5` |
| `FDR_ARCHIVE_ARTIFACTS` | Archive existing artifacts | `0` |
| `FDR_COMPACT_ARTIFACTS` | Auto-compact large artifacts | `1` |
| `FDR_PR_REVIEW` | Enable PR review mode | `0` |
| `FDR_DEBUG` | Enable debug mode | `0` |
| `FDR_NTFY_ENABLED` | Enable ntfy notifications | `1` |
| `FDR_NTFY_URL` | ntfy server URL | `http://localhost:2586` |

---

## How It Works

### The Time Loop

1. **Checkpoint**: Record the starting commit SHA (for rollback).
2. **Check Phase**: Run your check command.
   - Exit code **0** → proceed.
   - Non-zero → the run “dies” and we enter Fix Phase.
3. **Fix Phase** (only if checks failed):
   - Filter check output to error-relevant lines
   - Collect changed files as context (up to 200KB total)
   - Invoke pi with the filtered errors to fix them
   - Re-run checks
4. **Review Phase** (only if checks passed):
   - Generate a git diff of all changes
   - Invoke pi to review for issues
   - If pi finds **[CRITICAL]** issues, write them to `.fix-die-repeat/review_current.md`
   - If pi finds no issues, write the explicit `NO_ISSUES` marker to `.fix-die-repeat/review_current.md`
5. **Reset or Escape**:
   - If `review_current.md` contains issues → invoke pi to fix them, then reset back to Check Phase.
   - If `review_current.md` contains `NO_ISSUES` → escape the loop and exit successfully.

### Context Management

To avoid exceeding pi's context limit:

- Files smaller than 200KB are attached to the prompt
- If total changed files exceed 200KB, they are listed and pi uses the `read` tool to inspect what it needs
- Historical artifacts (`.fix-die-repeat/review.md`, `build_history.md`) are compacted when they exceed 150-200 lines

### Oscillation Detection

The tool tracks hashes of the check output. If the current output matches a previous iteration, it warns: *"Check output is IDENTICAL to iteration X. You are going in CIRCLES."* In movie terms: you’re waking up on the same beach again—time to change tactics.

### State Storage

All state is stored in `.fix-die-repeat/`:

| File | Purpose |
|------|----------|
| `review.md` | Historical review entries, preserved across runs |
| `review_current.md` | Current issues to fix (deleted after each iteration) |
| `build_history.md` | `git diff --stat` for changes made each iteration |
| `checks.log` | Full output from check command |
| `checks_filtered.log` | Error-relevant lines extracted from checks.log |
| `.checks_hashes` | History of check output hashes (for oscillation detection) |
| `pi.log` | All pi invocations and their output |
| `fdr.log` | *Fix-Die-Repeat* internal logging |
| `session.log` | Combined output for current run (timestamped if debug mode) |
| `changes.diff` | Git diff of all changes (used for review phase) |
| `run_timestamps.md` | Start/end timestamps of each run |
| `.start_sha` | Git commit SHA before any changes (for rollback) |
| `.pr_threads_cache` | Cached PR threads markdown (avoids refetching) |
| `.pr_threads_hash` | Cache key for PR threads (owner/repo/number) |
| `.pr_thread_ids_in_scope` | Thread IDs from the original PR fetch (one per line) |
| `.resolved_threads` | Thread IDs pi claimed to have fixed (one per line) |

The `.fix-die-repeat/` directory is added to `.gitignore` automatically on first run.

### PR Review Mode

When `--pr-review` is enabled:

1. Fetches unresolved GitHub PR review threads using `gh pr view` and GraphQL
2. Writes threads to `review_current.md` for pi to process
3. Pi reads the file, fixes issues, and writes resolved thread IDs to `.resolved_threads`
4. Tool reads both `.pr_thread_ids_in_scope` (original threads) and `.resolved_threads` (pi's claimed resolutions)
5. Only the **intersection** of both sets is actually resolved on GitHub
6. Threads are resolved via `pi -p 'resolve_pr_threads(threadIds: [...])'`
7. If no threads remain, exits successfully

This safety check prevents accidental resolution of threads that pi didn't actually address.

---

## Examples

### Python Project

```bash
# Run pytest
fix-die-repeat -c "pytest"

# Run pytest with verbose output
fix-die-repeat -c "pytest -xvs"

# Run specific test module
fix-die-repeat -c "pytest tests/test_main.py"
```

### JavaScript/TypeScript

```bash
# Run npm test
fix-die-repeat -c "npm test"

# Run yarn test
fix-die-repeat -c "yarn test"
```

### Rust

```bash
# Run cargo test
fix-die-repeat -c "cargo test"

# Run cargo clippy
fix-die-repeat -c "cargo clippy -- -D warnings"
```

### Go

```bash
# Run go test
fix-die-repeat -c "go test ./..."

# Run go vet
fix-die-repeat -c "go vet ./..."
```

### Make

```bash
# Run make test
fix-die-repeat -c "make test"

# Run make check
fix-die-repeat -c "make check"
```

### Custom Script

```bash
fix-die-repeat -c "./run-tests.sh"
```

---

## PR Review Mode

Process GitHub PR review comments automatically:

```bash
fix-die-repeat --pr-review
```

This will:
- Fetch unresolved threads from the current branch's PR
- Present threads to pi for analysis and fixing
- Track which threads pi actually fixed
- Only resolve threads that were truly addressed (safety intersection)
- Repeat until all threads are resolved

You must be on a branch with an open PR, and GitHub CLI must be authenticated (`gh auth status`).

### Limiting PR Threads

To avoid overwhelming pi with too many threads at once:

```bash
fix-die-repeat --pr-review --max-pr-threads 3
```

---

## Debug Mode

Enable debug mode for troubleshooting:

```bash
fix-die-repeat --debug
```

This:
- Creates a timestamped session log: `.fix-die-repeat/session_YYYYMMDD_HHMMSS.log`
- Enables verbose debug logging to console
- Preserves the full pi output for inspection

---

## Notifications

The tool can send notifications via [ntfy](https://ntfy.sh/) when runs complete.

### Setup

```bash
# Start ntfy server
ntfy serve
```

### Configuration

Notifications are enabled by default (`FDR_NTFY_ENABLED=1`). The topic is derived from your repository name (lowercase, sanitized to alphanumeric/dash/underscore/dot).

### Custom Server

```bash
export FDR_NTFY_URL="http://your-ntfy-server:2586"
fix-die-repeat
```

### Disable Notifications

```bash
export FDR_NTFY_ENABLED=0
fix-die-repeat
```

---

## Exit Codes

| Code | Meaning |
|-------|----------|
| 0 | Success — escaped the loop (checks pass and review reports no issues) |
| 1 | Failure — max iterations exceeded or unexpected error |
| 130 | Interrupted by user (Ctrl+C) |

On failure, the tool logs instructions for seeing or reverting changes:

```bash
git diff <start-sha>
git checkout <start-sha> -- .
```

---

## Development

### Install Dev Dependencies

```bash
uv sync --all-extras
```

### Run Tests

```bash
uv run pytest
```

### Run Tests with Coverage

```bash
uv run pytest --cov=fix_die_repeat --cov-report=term-missing --cov-report=html
```

### Lint

```bash
uv run ruff check fix_die_repeat tests
uv run ruff format fix_die_repeat tests
```

### Type Check

```bash
uv run mypy fix_die_repeat
```

### All Checks

```bash
uv run pytest && uv run ruff check --fix fix_die_repeat tests && uv run ruff format fix_die_repeat tests && uv run mypy fix_die_repeat
```

---

## Architecture

```
fix_die_repeat/
├── cli.py          # CLI — uses Click
├── config.py       # Settings and paths — uses Pydantic
├── messages.py     # Constants and message generators for user-facing text
├── prompts.py      # Jinja template renderer for pi prompts
├── runner.py       # Main loop — orchestrates check/fix/review
├── utils.py        # Utilities — logging, git, file operations
└── templates/      # Prompt templates used by runner (Jinja2)
```

### Dependencies

| Package | Purpose |
|---------|----------|
| click | CLI framework |
| jinja2 | Prompt templating |
| pydantic | Configuration validation |
| pydantic-settings | Environment variable support |
| rich | Console output with colors |

### Dev Dependencies

| Package | Purpose |
|---------|----------|
| pytest | Test framework |
| pytest-cov | Coverage measurement |
| ruff | Linting and formatting |
| mypy | Type checking |

---

## Troubleshooting

### pi not found

```
command not found: pi
```

Install pi and ensure it's on your PATH. See: https://github.com/mariozechner/pi

### Checks pass but tool says "no issues found"

This is expected behavior — the loop exits successfully when:
1. Your check command returns exit code 0
2. pi writes `NO_ISSUES` to `review_current.md`

Note: an empty `review_current.md` is treated as an ambiguous legacy fallback and logs a warning.

### "Edit commands failed" but files show as changed

This typically means pi tried to edit but the old text didn't match exactly (whitespace, encoding differences). The tool will retry once automatically via `run_pi_safe()`.

### Oscillation warning

If you see this warning, the same check output has repeated. You're likely applying the same fix strategy that doesn't work. Try a fundamentally different approach.

### PR review mode says "No open PR found"

Ensure:
- You're on a branch that has an associated PR
- Run `gh pr view --web` to verify the PR exists
- Run `gh auth status` to verify you're authenticated

---

## License

MIT

---

## Acknowledgments

- [pi](https://github.com/mariozechner/pi) — AI coding agent

---

> *"On your feet, maggot."*
