# Fix. Die. Repeat.

> *"I'm not going to give up on this. Not today, not tomorrow, not ever."* — Cage, Live. Die. Repeat.

Automated check, review, and fix loop using [pi](https://github.com/mariozechner/pi).

---

## What It Does

Fix. Die. Repeat. runs a loop:

1. **Runs your check command** (e.g., `./scripts/ci.sh`, `pytest`)
2. **If checks fail** → invokes pi to fix the errors
3. **If checks pass** → invokes pi to review your changes
4. **If review finds issues** → invokes pi to fix them
5. **If pi reports low confidence** (<80% by default) → launches an interactive pi session to ask you clarifying questions
6. **Repeats** until checks pass and review finds no issues

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
uv pip install -e .
```

Use this if you're developing on the project or want to run directly from the source directory.

### Dev Install with testing tools

```bash
uv pip install -e ".[dev]"
```

Also installs pytest, ruff, and mypy.

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
| `FDR_CONFIDENCE_THRESHOLD` | Minimum confidence (0-1) before triggering interactive mode | `0.8` |

---

## How It Works

### The Loop

1. **Check Phase**: Run your check command. If exit code is 0 → success. If non-zero → continue to fix phase.
2. **Fix Phase** (if checks failed):
   - Filter check output to error-relevant lines
   - Collect changed files as context (up to 200KB total)
   - Invoke pi with the filtered errors to fix them
   - Re-run checks
   - Repeat until checks pass
3. **Review Phase** (if checks passed):
   - Generate a git diff of all changes
   - Invoke pi to review for issues
   - If pi finds [CRITICAL] issues, write them to `.fix-die-repeat/review_current.md`
   - If pi finds no issues, write the explicit `NO_ISSUES` marker to `.fix-die-repeat/review_current.md`
4. **Resolution Phase** (if review found issues):
   - Invoke pi with `review_current.md` to fix issues
   - If in PR review mode, track which threads were resolved
   - Loop back to check phase
5. **Confidence Check**: After each successful pi invocation, check the output for a `CONFIDENCE=` footer:
   - If confidence is below the threshold (default 0.8), launch an interactive pi session
   - The interactive session asks clarifying questions to help proceed with higher confidence
   - After you answer and exit the interactive session, the loop automatically resumes
   - Loop back to check phase
   - Invoke pi with `review_current.md` to fix issues
   - If in PR review mode, track which threads were resolved
   - Loop back to check phase
5. **Exit**: When checks pass and `review_current.md` contains the `NO_ISSUES` marker

### Context Management

To avoid exceeding pi's context limit:

- Files smaller than 200KB are attached to the prompt
- If total changed files exceed 200KB, they are listed and pi uses the `read` tool to inspect what it needs
- Historical artifacts (`.fix-die-repeat/review.md`, `build_history.md`) are compacted when they exceed 150-200 lines

### Oscillation Detection

The tool tracks git hashes of check output. If the current output matches a previous iteration's output, it warns: *"Check output is IDENTICAL to iteration X. You are going in CIRCLES."*

### State Storage

All state is stored in `.fix-die-repeat/`:

| File | Purpose |
|------|----------|
| `review.md` | Historical review entries, preserved across runs |
| `review_current.md` | Current issues to fix (deleted after each iteration) |
| `build_history.md` | `git diff --stat` for changes made each iteration |
| `checks.log` | Full output from check command |
| `checks_filtered.log` | Error-relevant lines extracted from checks.log |
| `checks_hashes` | History of check output hashes (for oscillation detection) |
| `pi.log` | All pi invocations and their output |
| `fdr.log` | Fix. Die. Repeat. internal logging |
| `session.log` | Combined output for current run (timestamped if debug mode) |
| `.start_sha` | Git commit SHA before any changes (for rollback) |

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
| 0 | Success — all checks pass and no review issues |
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
uv pip install -e ".[dev]"
```

### Run Tests

```bash
pytest
```

### Run Tests with Coverage

```bash
pytest --cov=fix_die_repeat --cov-report=term-missing --cov-report=html
```

### Lint

```bash
ruff check fix_die_repeat tests
ruff format fix_die_repeat tests
```

### Type Check

```bash
mypy fix_die_repeat
```

### All Checks

```bash
pytest && ruff check --fix fix_die_repeat tests && ruff format fix_die_repeat tests && mypy fix_die_repeat
```

---

## Architecture

```
fix_die_repeat/
├── cli.py          # CLI — uses Click
├── config.py       # Settings and paths — uses Pydantic
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

## Comparison to Original Bash Script

The Python rewrite maintains feature parity with the original `~/.local/bin/fix-die-repeat` bash script, with these changes:

| Aspect | Original | Python |
|---------|-----------|---------|
| Language | Bash | Python 3.12+ |
| Configuration | Shell variables | Pydantic with env var support |
| Logging | Echo to files | Structured logging with Rich |
| Error handling | `set -euo pipefail` | Python exceptions |
| Type hints | None | Full type hints throughout |

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

This typically means pi tried to edit but the old text didn't match exactly (whitespace, encoding differences). The tool will retry up to 3 times.

### Oscillation warning

If you see this warning, the same check output has repeated. You're likely applying the same fix strategy that doesn't work. Try a fundamentally different approach.

### PR review mode says "No open PR found"

Ensure:
- You're on a branch that has an associated PR
- Run `gh pr view --web` to verify the PR exists
- Run `gh auth status` to verify you're authenticated

### "Low confidence detected" message

When pi reports confidence below the threshold (default 0.8), the tool launches an interactive pi session to ask you clarifying questions. This is intentional behavior to get human guidance when pi is uncertain.

**What happens**:
1. An interactive pi session opens with context about the low-confidence situation
2. Pi asks one clarifying question at a time
3. Answer the questions asked by pi
4. Exit the interactive session (Ctrl+C twice or `/quit`)
5. The fix-die-repeat loop automatically resumes and re-runs checks

**To adjust the threshold**:
```bash
export FDR_CONFIDENCE_THRESHOLD=0.7  # Lower threshold to 70%
fix-die-repeat
```

**To disable interactive mode**, set threshold to 0:
```bash
export FDR_CONFIDENCE_THRESHOLD=0  # Never trigger interactive mode
fix-die-repeat
```

---

## License

MIT

---

## Acknowledgments

- [pi](https://github.com/mariozechner/pi) — AI coding agent
- Original bash script by the same author

---

> *"Get me an Alpha. Kill it."* — Cage, Live. Die. Repeat.
