# Copilot Instructions

This file provides guidance when working with code in this repository.

## Project Overview

Fix. Die. Repeat. is a Python CLI tool that automates check/fix/review loops using an AI coding agent (pi). It is implemented in Python but designed to work with **any development repository** regardless of language or framework. The prompts and review logic are language-agnostic.

## Commands

```bash
# Install dependencies
uv sync --all-extras

# Run the tool
uv run fix-die-repeat
uv run fix-die-repeat -c "pytest" --debug

# Run all tests (includes coverage check, 80% minimum enforced)
uv run pytest

# Run a single test file
uv run pytest tests/test_runner.py

# Run a single test
uv run pytest tests/test_runner.py::TestClassName::test_name -v

# Lint and format
uv run ruff check fix_die_repeat tests
uv run ruff check --fix fix_die_repeat tests
uv run ruff format fix_die_repeat tests

# Type check
uv run mypy fix_die_repeat

# Full CI check
uv run pytest && uv run ruff check --fix fix_die_repeat tests && uv run ruff format fix_die_repeat tests && uv run mypy fix_die_repeat
```

## Critical Policies

### NEVER-IGNORE Ruff Rules
These ruff rules must NEVER be added to per-file-ignores in pyproject.toml. CI will block violations.

| Rule | What to do instead |
|------|-------------------|
| **C901** (complexity) | Extract helper functions, use early returns |
| **PLR0913** (too many args) | Group into dataclass or NamedTuple |
| **PLR2004** (magic numbers) | Extract to SCREAMING_SNAKE_CASE constants |
| **PLC0415** (import not at top) | Move imports to top of file |

### File Size Guidelines
- Target ~100 lines per function (refactor or extract helpers when functions grow larger)
- Target ~400 lines per file (consider splitting into modules when much larger, especially for non-test code)
- Max 2000 lines per file

### Ruff Configuration
- Line length: 100
- Target: Python 3.12
- Selects ALL rules, with specific per-file exceptions documented in pyproject.toml

## Architecture

**Core loop** (`runner.py` → `PiRunner`): Runs check command → if fails, invokes pi to fix → if passes, reviews diff → repeats until checks pass AND review finds no issues.

**Manager pattern**: Separate manager classes handle distinct responsibilities:
- `runner_artifacts.py` → `ArtifactManager`: file compaction and context management
- `runner_review.py` → `ReviewManager`: diff generation and review validation
- `runner_pr.py` → `PrReviewManager`: GitHub PR thread processing
- `runner_introspection.py` → `IntrospectionManager`: prompt improvement analysis

**Configuration** (`config.py`): Pydantic BaseSettings with `FDR_*` env var prefix. `CliOptions` dataclass groups CLI overrides. Settings merge env vars + config files + CLI flags.

**Check command resolution** (`detection.py`): Priority chain — CLI flag → project config (`.fix-die-repeat/config`) → system config → auto-detect from Makefile/package.json/Cargo.toml/pyproject.toml → interactive prompt.

**Prompt templates** (`templates/`): Jinja2 templates with strict undefined mode. Language-specific check templates in `templates/lang_checks/`.

**Notifications** (`notifications/`): Pluggable backend pattern with `Notifier` protocol. Implementations: ntfy, Zulip. Best-effort (failures logged, never block main loop).

**State files**: All runtime state stored in `.fix-die-repeat/` (gitignored). Includes review history, check logs, build history, oscillation detection hashes, and PR thread caches.

## Testing
- Framework: pytest with pytest-cov
- Coverage minimum: 80% (enforced via `--cov-fail-under=80`)
- Test files mirror source structure in `tests/`
- Use assert statements (pytest idiom)

## PR Review Introspection

Fix-die-repeat has a built-in introspection system for analyzing PR review patterns and improving prompts. This is a two-phase workflow:

### Phase 1: Collect

Run with `--pr-review-introspect` on each PR to accumulate data:

```bash
fix-die-repeat --pr-review-introspect
```

This appends YAML entries to `~/.config/fix-die-repeat/introspection.yaml`.

### Phase 2: Analyze

Use the `prompt-introspect` skill to analyze patterns and update templates:

```
/skill:prompt-introspect
```

This skill:
- Reads pending entries from `introspection.yaml`
- Analyzes patterns using `introspection-summary.md` for trend context
- Updates templates to close identified gaps
- Marks processed entries as `status: reviewed`
- Archives entries and regenerates the summary

### Key Files and Locations

| Path | Purpose |
|------|---------|
| `.pi/skills/prompt-introspect/SKILL.md` | Full skill documentation |
| `.pi/skills/prompt-introspect/references/` | Reference docs (categories, budgets, summary format) |
| `~/.config/fix-die-repeat/introspection.yaml` | Inbox with pending entries |
| `~/.config/fix-die-repeat/introspection-summary.md` | Cumulative Markdown summary |
| `~/.config/fix-die-repeat/introspection-archive.yaml` | Processed entries |

### Template Budgets

Templates have size limits enforced by the skill:

| Template | Max Lines | Max Bytes |
|----------|-----------|-----------|
| `local_review.j2` | 100 | 8.5KB |
| `fix_checks.j2` | 50 | 5KB |
| `resolve_review_issues.j2` | 40 | 3KB |
| `introspect_pr_review.j2` | 60 | 4KB |
| Lang check partials | 15 | 1KB |

### When Working on Introspection

1. **Read the skill first**: `.pi/skills/prompt-introspect/SKILL.md` has the authoritative workflow
2. **Check reference docs**: `.pi/skills/prompt-introspect/references/` has categories, budgets, and summary format
3. **Don't exceed budgets**: The skill enforces template size limits — consolidate items rather than adding more
4. **Be conservative**: Only add template changes for clear patterns (2+ similar entries)
5. **Keep templates language-agnostic**: Use `lang_checks/*.j2` for language-specific issues

## See Also
- `CONTRIBUTING.md` — Dev setup, aliases, state file reference
