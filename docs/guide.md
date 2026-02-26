# User's Guide

Full reference for [Fix. Die. Repeat.](../README.md) — CLI options, configuration, and detailed usage.

---

## Command Line Options

```
Usage: fix-die-repeat [OPTIONS]

Options:
  -c, --check-cmd TEXT      Command to run checks (default: auto-detected)
  -n, --max-iters INTEGER   Maximum loop iterations (default: 10)
  -m, --model TEXT          Override model selection (e.g., anthropic/claude-sonnet-4-5)
  --max-pr-threads INTEGER  Maximum PR threads to process per iteration (default: 5)
  --archive-artifacts       Archive existing artifacts to a timestamped folder
  --no-compact              Skip automatic compaction of large artifacts
  --pr-review               Enable PR review mode
  --test-model TEXT         Test model compatibility before running (exits after test)
  -d, --debug               Enable debug mode (timestamped session logs and verbose logging)
  --version                 Show the version and exit.
  --help                    Show this message and exit.
```

## Environment Variables

All options can be set via `FDR_`-prefixed environment variables:

| Variable | Description | Default |
|-----------|-------------|----------|
| `FDR_CHECK_CMD` | Command to run checks | (auto-detected) |
| `FDR_MAX_ITERS` | Maximum loop iterations | `10` |
| `FDR_MODEL` | Override model selection | (none) |
| `FDR_TEST_MODEL` | Test model compatibility and exit | (none) |
| `FDR_MAX_PR_THREADS` | PR threads per iteration | `5` |
| `FDR_ARCHIVE_ARTIFACTS` | Archive existing artifacts | `0` |
| `FDR_COMPACT_ARTIFACTS` | Auto-compact large artifacts | `1` |
| `FDR_PR_REVIEW` | Enable PR review mode | `0` |
| `FDR_DEBUG` | Enable debug mode | `0` |
| `FDR_NTFY_ENABLED` | Enable ntfy notifications | `1` |
| `FDR_NTFY_URL` | ntfy server URL | `http://localhost:2586` |

---

## Check Command Resolution

fix-die-repeat automatically finds your project's check command using this priority chain:

1. **CLI flag / env var** (`-c` / `FDR_CHECK_CMD`)
2. **Project config** (`.fix-die-repeat/config`)
3. **System config** (`~/.config/fix-die-repeat/config`)
4. **Auto-detect** from project files
5. **Interactive prompt**
6. **Error** (non-interactive with no config)

### Auto-Detection

| File Present | Detected Command |
|--------------|-----------------|
| `scripts/ci.sh` | `./scripts/ci.sh` |
| `Makefile` with `test` target | `make test` |
| `Makefile` with `check` target | `make check` |
| `package.json` with `scripts.test` | `npm test` |
| `Cargo.toml` | `cargo test` |
| `pyproject.toml` with `[tool.pytest]` | `uv run pytest` |
| `pyproject.toml` (no pytest) | `uv run python -m pytest` |
| `go.mod` | `go test ./...` |
| `build.gradle` / `build.gradle.kts` | `./gradlew test` |
| `pom.xml` | `mvn test` |
| `mix.exs` | `mix test` |
| `Gemfile` | `bundle exec rake test` |

### Config Files

**Project config** (`.fix-die-repeat/config`) — per-project, automatically gitignored:
```toml
check_cmd = "uv run pytest"
```

**System config** (`~/.config/fix-die-repeat/config`) — shared across projects:
```toml
check_cmd = "pytest"
```

### CI/CD Usage

In non-interactive environments, always provide the check command explicitly:

```bash
fix-die-repeat -c "pytest"
# or
export FDR_CHECK_CMD="pytest"
```

---

## How It Works

### The Time Loop

1. **Checkpoint**: Record the starting commit SHA (for rollback).
2. **Check Phase**: Run your check command.
   - Exit code **0** → proceed to review.
   - Non-zero → enter Fix Phase.
3. **Fix Phase**: Filter check output to error-relevant lines, invoke pi to fix them, re-run checks.
4. **Review Phase**: Generate a git diff, invoke pi to review. If pi finds **[CRITICAL]** issues, fix them and loop back. If `NO_ISSUES` → exit successfully.

### Context Management

- Files under 200KB are attached directly to pi's prompt
- Larger changesets are listed for pi to selectively `read`
- Historical artifacts are compacted when they exceed 150–200 lines

### Oscillation Detection

Check output hashes are tracked across iterations. If the same output repeats, the tool warns you're going in circles — time to change tactics.

---

## PR Review Mode

Process GitHub PR review comments automatically:

```bash
fix-die-repeat --pr-review
```

This will:
- Fetch unresolved threads from the current branch's PR
- Have pi analyze and fix the issues
- Only resolve threads that pi actually addressed (safety intersection)
- Repeat until all threads are resolved

Requirements: you must be on a branch with an open PR, and `gh auth status` must succeed.

### Limiting PR Threads

```bash
fix-die-repeat --pr-review --max-pr-threads 3
```

---

## Debug Mode

```bash
fix-die-repeat --debug
```

Creates timestamped session logs in `.fix-die-repeat/` and enables verbose console output for troubleshooting.

---

## Notifications (Optional)

Notifications are optional. If you have an [ntfy](https://ntfy.sh/) server running, fix-die-repeat can notify you when runs complete. If no server is reachable, the tool silently continues — no setup is required to use fix-die-repeat without notifications.

The notification topic is derived from your repository name.

```bash
# Disable notifications explicitly
export FDR_NTFY_ENABLED=0

# Point to your ntfy server (default: http://localhost:2586)
export FDR_NTFY_URL="http://your-server:2586"
```

---

## Exit Codes

| Code | Meaning |
|-------|----------|
| 0 | Escaped the loop — checks pass and review reports no issues |
| 1 | Max iterations exceeded or unexpected error |
| 130 | Interrupted by user (Ctrl+C) |

On failure, the tool logs instructions for viewing or reverting changes:

```bash
git diff <start-sha>
git checkout <start-sha> -- .
```

---

## Troubleshooting

### pi not found

Install [pi](https://github.com/mariozechner/pi) and ensure it's on your PATH.

### Not a git repository

fix-die-repeat requires a Git repository to track changes and generate diffs. Initialize one with `git init` or navigate to an existing repo before running.

### Oscillation warning

The same check output has repeated — the current fix strategy isn't working. Try a fundamentally different approach.

### PR review: "No open PR found"

- Verify you're on a branch with an associated PR: `gh pr view --web`
- Verify authentication: `gh auth status`

### "Edit commands failed" but files show as changed

pi tried to edit but the old text didn't match exactly (whitespace or encoding differences). The tool retries automatically.

---

## Alternative Installation

If you prefer to clone the repository first:

```bash
git clone https://github.com/CTristan/fix-die-repeat.git
cd fix-die-repeat
uv tool install .
```

For editable dev installs and tooling setup, see [CONTRIBUTING.md](../CONTRIBUTING.md).
