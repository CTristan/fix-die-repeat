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
  --pr-review-introspect    Enable PR review mode with prompt introspection
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
| `FDR_PR_REVIEW_INTROSPECT` | Enable PR review with introspection | `0` |
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

## PR Review Introspection

PR review introspection analyzes completed PR review runs to identify gaps in your review and fix prompt templates. This helps you understand what types of issues are being missed and how to improve your prompts over time.

### The Full Workflow

Introspection works in two phases:

**Phase 1: Collect** — Run `--pr-review-introspect` on each PR to accumulate data
**Phase 2: Analyze** — Use the `prompt-introspect` skill to update prompts based on patterns

### Phase 1: Collecting Introspection Data

When enabled, introspection runs as a post-processing step after a successful PR review. It:

1. **Collects data** about the PR threads processed, which ones were fixed, and which were declined
2. **Analyzes the diff** of changes made by the agent
3. **Categorizes each thread** with metadata about the issue type, language-specific gaps, and prompt improvement opportunities
4. **Appends the analysis** to the global introspection inbox file

#### Enabling Introspection

```bash
# Enable with CLI flag
fix-die-repeat --pr-review-introspect

# Or with environment variable
export FDR_PR_REVIEW_INTROSPECT=1
fix-die-repeat --pr-review
```

The `--pr-review-introspect` flag automatically enables PR review mode.

Repeat this for each PR you want to analyze. Each run appends to the same inbox file.

### Introspection Files

| File | Purpose |
|------|---------|
| `introspection.yaml` | Inbox — pending entries waiting to be processed |
| `introspection-summary.md` | Cumulative Markdown summary for trend context |
| `introspection-archive.yaml` | Processed entries (marked `reviewed`) |

Location: `~/.config/fix-die-repeat/` (respects `XDG_CONFIG_HOME`)

### Understanding the Output

Each introspection entry contains:

| Field | Description |
|-------|-------------|
| `date` | Date of the introspection run |
| `project` | Project name |
| `pr_number` | PR number |
| `pr_url` | URL to the PR |
| `status` | `pending` or `reviewed` |
| `threads` | List of thread analyses |

Each thread includes:

| Field | Description |
|-------|-------------|
| `id` | GraphQL thread ID |
| `title` | Concise title (max 10 words) |
| `category` | Issue category: security, error-handling, performance, correctness, code-quality, testing, documentation, configuration |
| `outcome` | `fixed` or `wont-fix` |
| `summary` | 1-2 sentences describing what was flagged and what the agent did |
| `reason` | (wont-fix only) Why the agent declined to fix |
| `relevance` | Assessment of whether improved prompts could catch this earlier |
| `lang_check_gap` | For language-specific issues: would a lang_checks partial have caught it? If not, suggests a checklist item. |

### Example Entry

```yaml
date: "2026-03-12"
project: "my-project"
pr_number: 42
pr_url: "https://github.com/user/my-project/pull/42"
status: pending
threads:
  - id: "PRR_abc123"
    title: "Add null check for user object"
    category: "error-handling"
    outcome: fixed
    summary: >
      Reviewer flagged potential NPE if user object is null.
      Agent added null check before accessing user fields.
    relevance: >
      Could be caught by requiring defensive null checks in prompts.
    lang_check_gap: n/a
```

### Phase 2: Analyzing and Updating Prompts

Once you've collected entries from multiple PRs, use the `prompt-introspect` skill to analyze patterns and update templates:

```
/skill:prompt-introspect
```

This skill:

1. Reads pending entries from `introspection.yaml`
2. Analyzes patterns across all entries using the summary for context
3. Updates prompt templates to address identified gaps:
   - `templates/local_review.j2` — for general review checklist items
   - `templates/fix_checks.j2` — for fix patterns
   - `templates/resolve_review_issues.j2` — for issue types the agent struggled with
   - `templates/lang_checks/*.j2` — for language-specific patterns
4. Marks processed entries as `status: reviewed`
5. Archives reviewed entries and regenerates the summary

#### Template Size Budgets

Templates have size limits to ensure high-quality LLM instruction-following:

| Template | Budget (Lines) | Budget (Bytes) |
|----------|----------------|----------------|
| `local_review.j2` | 100 lines | 8.5KB |
| `fix_checks.j2` | 50 lines | 5KB |
| `resolve_review_issues.j2` | 40 lines | 3KB |
| `introspect_pr_review.j2` | 60 lines | 4KB |
| Lang check partials (each) | 15 lines | 1KB |

The skill enforces these budgets by consolidating items if needed.

### Managing Introspection Files

fix-die-repeat provides CLI commands to manage introspection files:

```bash
# Rotate a file when it exceeds a size limit
fix-die-repeat introspection rotate ~/.config/fix-die-repeat/introspection.yaml

# Append content safely with locking
fix-die-repeat introspection append ~/.config/fix-die-repeat/introspection.yaml \
  --content-file new-entry.yaml --use-yaml-separator
```

### Best Practices

- **Collect data first**: Run `--pr-review-introspect` on several PRs before running the analysis skill
- **Be conservative**: Only add template changes when there's a clear pattern (2+ entries in the same category)
- **Stay language-agnostic**: Use `lang_checks/*.j2` for language-specific patterns, keep main templates generic
- **Review changes**: The skill summarizes what was changed — verify each change makes sense

---

## Debug Mode

```bash
fix-die-repeat --debug
```

Creates timestamped session logs in `.fix-die-repeat/` and enables verbose console output for troubleshooting.

---

## Notifications (Optional)

Notifications are optional. fix-die-repeat can notify you when runs complete or fail, and when oscillation is detected. If a notification backend is not configured, notifications are simply skipped; if a configured backend is unreachable or fails, the tool logs the error but continues running — no setup is required to use fix-die-repeat without notifications.

### ntfy

If you have an [ntfy](https://ntfy.sh/) server running, fix-die-repeat can send notifications when runs complete. The notification topic is derived from your repository name.

```bash
# Disable ntfy notifications explicitly
export FDR_NTFY_ENABLED=0

# Point to your ntfy server (default: http://localhost:2586)
export FDR_NTFY_URL="http://your-server:2586"
```

### Zulip

fix-die-repeat can also send notifications to [Zulip](https://zulip.com/) chats. Notifications include repository name, branch, duration, and iteration count.

```bash
# Enable Zulip notifications
export FDR_ZULIP_ENABLED=1

# Zulip server base URL (required if enabled)
export FDR_ZULIP_SERVER_URL="https://your-zulip-server.example.com"

# Zulip bot email for authentication (required if enabled)
export FDR_ZULIP_BOT_EMAIL="your-bot@example.com"

# Zulip bot API key for authentication (required if enabled)
export FDR_ZULIP_BOT_API_KEY="your-api-key-here"

# Zulip stream name (optional, defaults to "fix-die-repeat")
export FDR_ZULIP_STREAM="fix-die-repeat"
```

**Note**: The Zulip stream must exist or the bot must have permission to create it.

### Notification Events

fix-die-repeat sends notifications for these events:

| Event | Description |
|-------|-------------|
| **Run completed** | All checks pass and no review issues found |
| **Run failed** | Max iterations exceeded or unexpected error |
| **Oscillation detected** | Same check output repeats across iterations |

**Best-effort behavior**: Notification failures never block or crash the main fix loop. If a backend fails, it's logged and the tool continues.

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
