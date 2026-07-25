# User's Guide

Full reference for [Fix. Die. Repeat.](../README.md) — CLI options, configuration, and detailed usage.

---

## Command Line Options

```
Usage: fix-die-repeat [OPTIONS]

Options:
  -c, --check-cmd TEXT            Command to run checks (default: auto-detected)
  -n, --max-iters INTEGER         Maximum loop iterations (default: 10)
  -m, --model TEXT                Override model selection (e.g., anthropic/claude-sonnet-4-5)
  --max-pr-threads INTEGER        Maximum PR threads to process per iteration (default: 5)
  --archive-artifacts             Archive existing artifacts to a timestamped folder
  --no-compact                    Skip automatic compaction of large artifacts
  --pr-review                     Enable PR review mode
  --pr-review-introspect          Enable PR review mode with prompt introspection (implies --pr-review)
  --contextual-review             Smart contextual review (report-only). Reviews uncommitted
                                  changes, branch diff vs default branch, or full codebase
                                  if neither applies.
  --full-codebase-review          Audit the entire codebase instead of a diff. Report-only:
                                  never attempts fixes. Ignores --pr-review if also set.
  --pr-threads-introspect-only    Fetch the PR's unresolved review threads, run introspection
                                  on them, then exit. Does not run checks, local review, or
                                  attempt fixes.
  --improve-prompts               Ask pi to update the user-owned prompt templates under
                                  <FDR_HOME>/templates/ based on accumulated introspection data.
                                  Seeds user copies on first use; never mutates the package.
  --test-model TEXT               Test model compatibility before running (exits after test)
  -d, --debug                     Enable debug mode (timestamped session logs and verbose logging)
  --version                       Show the version and exit.
  --help                          Show this message and exit.
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
| `FDR_PR_REVIEW_INTROSPECT` | Enable PR review mode with prompt introspection | `0` |
| `FDR_CONTEXTUAL_REVIEW` | Smart contextual review (report-only) | `0` |
| `FDR_FULL_CODEBASE_REVIEW` | Audit the entire codebase (report-only) | `0` |
| `FDR_PR_THREADS_INTROSPECT_ONLY` | Fetch unresolved PR threads, introspect, then exit | `0` |
| `FDR_IMPROVE_PROMPTS` | Update user templates from introspection data, then exit | `0` |
| `FDR_HOME` | Base directory for central state (`~/.fix-die-repeat/`) | (unset) |
| `FDR_DEBUG` | Enable debug mode | `0` |
| `FDR_NTFY_ENABLED` | Enable ntfy notifications | `1` |
| `FDR_NTFY_URL` | ntfy server URL | `http://localhost:2586` |

---

## External Workflow Sequencer

The sequencer owns workflow state and routing, which means your external agent keeps ownership of
repository changes. It reads the target repository, writes state and artifacts under `FDR_HOME`,
and returns exactly one JSON response for every completed non-help command.

The public commands are:

```text
fix-die-repeat sequencer --run-id RUN_ID [--repo PATH] init --workflow PATH [--flag NAME=VALUE ...]
fix-die-repeat sequencer --run-id RUN_ID [--repo PATH] next [--workflow PATH]
fix-die-repeat sequencer --run-id RUN_ID [--repo PATH] done STEP [--workflow PATH] [--force | --recover]
fix-die-repeat sequencer --run-id RUN_ID [--repo PATH] status [--workflow PATH]
```

`--repo` defaults to your current directory. `init` freezes the validated workflow semantics and
resolved flags for that repository-scoped run ID. Later commands use the stored workflow source
unless you provide a matching replacement path.

Each response tells you what happened through `outcome`, `message`, and the process exit code:

| Exit | `outcome` | Meaning |
|---:|---|---|
| `0` | `proceed` | Continue with the returned step. |
| `2` | `environment_error` | A required Git, filesystem, or configuration probe failed. |
| `3` | `terminal` | The workflow reached its declared terminal state. |
| `4` | `blocked` | Validation, ordering, or configuration state blocked progress. |
| `5` | `recovery` | Reconcile a previously issued mutating step before retrying it. |
| `64` | `usage_error` | The command or supplied values were invalid. |
| `70` | `internal_error` | A sequencer invariant failed unexpectedly. |
| `130` | `interrupted` | The command received an interrupt. |

When a response contains `step`, run the work described by `step.instruction`. Write requested
artifacts under `step.artifact_root`, then call `done` with the same step ID. A repeated `next`
for an issued mutating step returns `recovery` because the sequencer cannot tell whether the
external work started before the interruption. Reconcile the repository, then call
`done STEP --recover` to acknowledge and reissue it.

`--force` bypasses failed postconditions and records every bypassed gap in the transition history.
Use it only when you intend to accept those missing proofs.

See the [check, fix, and review example](../examples/sequencer/check-fix-review/) for a complete
workflow. The [sequencer ADR](adr/0001-sequencer-contract.md) documents the schema, validators,
state model, trust boundary, rejected alternatives, and adversarial findings.

---

## Check Command Resolution

fix-die-repeat automatically finds your project's check command using this priority chain:

1. **CLI flag / env var** (`-c` / `FDR_CHECK_CMD`)
2. **Project config** (`~/.fix-die-repeat/repos/<slug>/config`)
3. **System config** (`~/.fix-die-repeat/config`)
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

**Project config** (`~/.fix-die-repeat/repos/<slug>/config`) — per-project, stored
in a central directory so no files are written inside the repo. The slug is
`<project-directory-name>-<8charhash>`: the basename comes from the local
project directory name, while the hash is computed from the git `origin`
remote URL (or the absolute path if there is no remote). This means two clones
of the same remote in differently named local directories will not share the
same state dir. Override the base directory with `FDR_HOME` if you want state
somewhere other than `~/.fix-die-repeat/`.
```toml
check_cmd = "uv run pytest"
```

**System config** (`~/.fix-die-repeat/config`) — shared across projects:
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

### PR Review with Introspection

```bash
fix-die-repeat --pr-review-introspect
```

Implies `--pr-review` and additionally runs prompt introspection on the
processed threads, analyzing real-world feedback to surface prompt-improvement
opportunities.

### PR Threads Introspect-Only

```bash
fix-die-repeat --pr-threads-introspect-only
```

Fetches the PR's unresolved review threads, runs introspection on them, and
exits. Skips checks, local review, and fixes — useful when you only want the
analysis without touching the code.

### Improve Prompts

```bash
fix-die-repeat --improve-prompts
```

Reads `~/.fix-die-repeat/introspection.yaml` and asks pi to update the
language-agnostic prompt templates (`fix_checks.j2`, `local_review.j2`,
`resolve_review_issues.j2`, `pr_threads_header.j2`) and the shared review
partials under `partials/` (checklist, issue classification, reporting rules,
output contract, etc.) so future runs close the gaps surfaced by real PR
feedback. The user-owned copies live under `~/.fix-die-repeat/templates/` and
take precedence over the shipped defaults; they are seeded from the package on
first use and never mutated inside the install. Editing a partial changes every
review mode that composes it (`local_review`, `contextual_review`,
`full_codebase_review`). Pending introspection entries are marked `reviewed`
once pi has processed them.

---

## Review-Only Modes

These report-only modes never attempt fixes; they produce a review report
and exit.

### Contextual Review

```bash
fix-die-repeat --contextual-review
```

Smart scope detection:
- **Uncommitted:** reviews staged, unstaged, and untracked changes
- **Branch:** reviews the diff between the current branch and the default branch
- **Full:** falls back to full-codebase review when neither applies

### Full-Codebase Review

```bash
fix-die-repeat --full-codebase-review
```

Audits the entire codebase instead of a diff. Takes precedence over
`--pr-review` if both are set.

---

## Debug Mode

```bash
fix-die-repeat --debug
```

Creates timestamped session logs in `~/.fix-die-repeat/repos/<slug>/` and enables verbose console output for troubleshooting.

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
