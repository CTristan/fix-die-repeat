# Instructions

This file provides guidance when working with code in this repository.

## What this project is

`fix-die-repeat` is a CLI that wraps the [pi](https://github.com/mariozechner/pi) AI coding agent in a check/fix/review loop: run checks → if they fail, have pi fix them → if they pass, have pi review the diff → loop until both checks and review pass. It's a Python 3.12+ Click CLI installed via `uv`.

## Common commands

All tooling is run through `uv` (install deps with `uv sync --all-extras`):

```bash
# Full CI suite (lint fix + format + mypy + pytest w/ coverage + ruff-rule policy check)
./scripts/ci.sh                 # auto-fix mode
./scripts/ci.sh --check-only    # CI-friendly, no mutations

# Individual steps
uv run pytest                                    # all tests (coverage gate: --cov-fail-under=80)
uv run pytest tests/test_runner.py::TestFoo::test_bar   # single test
uv run pytest -k "introspection and not pr"     # keyword filter
uv run ruff check --fix fix_die_repeat tests
uv run ruff format fix_die_repeat tests
uv run mypy fix_die_repeat

# Run the local editable CLI
uv run fix-die-repeat [--debug] [-c "pytest"] [-m anthropic/claude-sonnet-4-5]
```

Dev-mode runs print `⚡ Running in DEV mode (editable install)` — use this to confirm you're exercising local code, not the globally-installed tool.

## Architecture

The runner is split across several sibling modules under `fix_die_repeat/` that collaborate through `PiRunner` in `runner.py`:

- `cli.py` — Click entrypoint; assembles `Settings` + `Paths` and hands off to `PiRunner`.
- `config.py` — Pydantic `Settings` (env vars prefixed `FDR_`), `CliOptions`, and `Paths`. Paths centralize **all** runtime state under `~/.fix-die-repeat/repos/<basename>-<8charhash>/` (override via `FDR_HOME`). **Nothing is written inside the target repo and `.gitignore` is never touched.** See `CONTRIBUTING.md` for the full state-file inventory.
- `detection.py` — auto-detects the project's check command from `Makefile`, `package.json`, `Cargo.toml`, `pyproject.toml`, etc., and persists confirmed choices.
- `lang.py` + `templates/lang_checks/` — per-language checklists injected into prompts.
- `prompts.py` + `templates/*.j2` — Jinja2 rendering of all pi prompts. Edit templates, not string literals in Python.
- `runner.py` — the main loop; orchestrates iterations, oscillation detection (via `.checks_hashes`), rollback via `.start_sha`, and completion conditions.
- `runner_artifacts.py` — `ArtifactManager`; attaches or compacts logs/diffs to keep pi's context window efficient.
- `runner_review.py` — `ReviewManager`; local post-fix review pass over the diff.
- `runner_pr.py` — `PrReviewManager`; GitHub PR-review mode (requires `gh`), including thread caching (`.pr_threads_cache`, `.resolved_threads`).
- `runner_introspection.py` — `IntrospectionManager`; "analyze PR reviews to find prompt-improvement opportunities" mode, and `--pr-threads-introspect-only` (fetch/analyze, don't fix).
- `utils.py` — git helpers, `run_command`, logging setup, ntfy notifications, completion-sound playback, and the shared ruff-rule-policy parser used by `scripts/validate_ruff_rules.py`.

Operating modes (selected via CLI flags / env vars) reuse the same core loop but swap which manager drives iteration: default (fix + review), `--pr-review`, `--pr-review-introspect`, `--full-codebase-review` (report-only), `--pr-threads-introspect-only` (fetch + analyze then exit).

## Ruff rule NEVER-IGNORE policy

`scripts/ci.sh` runs `scripts/validate_ruff_rules.py` before anything else. The following ruff rules **must never** appear in `[tool.ruff.lint.per-file-ignores]` — CI will fail if they do:

- `C901` — refactor complex functions, don't silence them.
- `PLR0913` — group arguments into a dataclass / NamedTuple (or `**kwargs` for Click).
- `PLR2004` — name magic values as module-level constants.
- `PLC0415` — imports go at module top-level; fix circular deps instead of moving imports.

Any other per-file ignore **must** be accompanied by an inline comment explaining why (see existing entries in `pyproject.toml` for the expected style).

## Testing conventions

- TDD is mandatory for bug fixes: write a failing regression test **before** touching the code. New features prefer TDD but don't require it.
- Coverage gate is 80% (`--cov-fail-under=80` in `pyproject.toml`); don't lower it to land a change.
- Tests live under `tests/` mirroring module names (`test_runner_pi.py`, `test_runner_review.py`, etc.). Per-file ignores in `pyproject.toml` allow `assert` (S101) and trusted `subprocess` (S603) in tests — use them idiomatically rather than refactoring around them.

## When touching prompts

Prompts rendered to pi live in `fix_die_repeat/templates/*.j2`. Changing wording that ships to the agent is a user-visible behavior change — cross-check the corresponding template test (`tests/test_prompts.py`, `tests/test_runner_*.py`) and the per-language checklists under `templates/lang_checks/`.
