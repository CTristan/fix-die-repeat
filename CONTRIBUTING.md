# Contributing to Fix. Die. Repeat.

Thanks for your interest in contributing! This guide covers development setup, running tests, and the project architecture.

---

## Dev Install

```bash
uv sync --all-extras
```

This installs the project in editable mode along with all dev dependencies (pytest, ruff, mypy, etc.) into a managed virtual environment. Use `uv run <command>` to invoke tools within it.

### Running the Local Dev Version

After running `uv sync --all-extras`, prefix commands with `uv run`:

```bash
# Run the local editable version
uv run fix-die-repeat

# Run with options
uv run fix-die-repeat -c "pytest" --debug
```

When running from an editable install, you'll see a cyan indicator confirming you're in dev mode:

```
⚡ Running in DEV mode (editable install)
```

### Creating a `fix-die-repeat-dev` Alias

For convenience, you can create a separate command name so `fix-die-repeat` stays as the global stable install.

<details>
<summary><strong>macOS / Linux</strong></summary>

Add to your shell config (`~/.bashrc`, `~/.zshrc`, `~/.config/fish/config.fish`):

```bash
alias fix-die-repeat-dev='uv run --directory ~/projects/fix-die-repeat fix-die-repeat'
```

Replace `~/projects/fix-die-repeat` with your actual project path. Reload your shell (`source ~/.zshrc` etc.) and use:

```bash
fix-die-repeat-dev --debug
```

Alternatively, create a script at `~/.local/bin/fix-die-repeat-dev`:

```bash
#!/bin/bash
cd ~/projects/fix-die-repeat
uv run fix-die-repeat "$@"
```

Make it executable: `chmod +x ~/.local/bin/fix-die-repeat-dev`

</details>

<details>
<summary><strong>Windows</strong></summary>

Add to your PowerShell profile (`notepad $PROFILE`):

```powershell
function fix-die-repeat-dev {
    uv run --directory $env:USERPROFILE\projects\fix-die-repeat fix-die-repeat $args
}
```

Replace the path with your actual project path. Reload with `. $PROFILE`.

Or create a batch file `fix-die-repeat-dev.bat` on your PATH:

```batch
@echo off
cd /d C:\Users\you\projects\fix-die-repeat
uv run fix-die-repeat %*
```

</details>

### Verifying Which Version You're Running

Look for the **dev mode indicator** at the start of each run:

```
⚡ Running in DEV mode (editable install)
```

Or check with `which fix-die-repeat-dev` (macOS/Linux) / `Get-Command fix-die-repeat-dev` (Windows).

---

## Running Tests

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=fix_die_repeat --cov-report=term-missing --cov-report=html
```

---

## Linting & Formatting

```bash
# Lint
uv run ruff check fix_die_repeat tests

# Format
uv run ruff format fix_die_repeat tests

# Auto-fix + format
uv run ruff check --fix fix_die_repeat tests
uv run ruff format fix_die_repeat tests
```

---

## Type Checking

```bash
uv run mypy fix_die_repeat
```

---

## All Checks

```bash
uv run pytest && uv run ruff check --fix fix_die_repeat tests && uv run ruff format fix_die_repeat tests && uv run mypy fix_die_repeat
```

---

## Architecture

```
fix_die_repeat/
├── cli.py          # CLI — uses Click
├── config.py       # Settings and paths — uses Pydantic
├── detection.py    # Check command resolution and auto-detection
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

## State Files

All runtime state is stored in `.fix-die-repeat/` (automatically gitignored):

| File | Purpose |
|------|----------|
| `review.md` | Historical review entries, preserved across runs |
| `review_current.md` | Current issues to fix (deleted after each iteration) |
| `build_history.md` | `git diff --stat` for changes made each iteration |
| `checks.log` | Full output from check command |
| `checks_filtered.log` | Error-relevant lines extracted from checks.log |
| `.checks_hashes` | History of check output hashes (for oscillation detection) |
| `pi.log` | All pi invocations and their output |
| `fdr.log` | Internal logging |
| `session.log` | Combined output for current run (timestamped if debug mode) |
| `changes.diff` | Git diff of all changes (used for review phase) |
| `run_timestamps.md` | Start/end timestamps of each run |
| `.start_sha` | Git commit SHA before any changes (for rollback) |
| `.pr_threads_cache` | Cached PR threads markdown |
| `.pr_threads_hash` | Cache key for PR threads (owner/repo/number) |
| `.pr_thread_ids_in_scope` | Thread IDs from the original PR fetch |
| `.resolved_threads` | Thread IDs pi claimed to have fixed |

---

## Coding Guidelines

For detailed coding guidelines, architecture decisions, and policies (ruff rules, test configuration, file size limits), see [AGENTS.md](AGENTS.md).
