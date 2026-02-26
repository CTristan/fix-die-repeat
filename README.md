# Fix. Die. Repeat.

[![CI](https://github.com/CTristan/fix-die-repeat/actions/workflows/ci.yml/badge.svg)](https://github.com/CTristan/fix-die-repeat/actions/workflows/ci.yml)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Fix. Die. Repeat.** automatically runs your project's checks, uses an AI coding agent to fix failures, reviews the result, and loops until everything passes.

> **What is pi?** [pi](https://github.com/mariozechner/pi) is an AI coding agent that reads files, edits code, runs commands, and helps you fix issues — like a pair programmer you can call from your terminal.

---

### Inspired by *Live. Die. Repeat.* aka *Edge of Tomorrow*

> *"I'm not going to give up on this. Not today, not tomorrow, not ever."*

A soldier relives the same day until the battle finally goes right. Fix. Die. Repeat. does the same thing to your repository — it's a polite form of cruelty that traps your coding agent in a time loop until reality stabilizes.

- **The "day"**: one full iteration of checks and review
- **The "death"**: a failing check or a critical review finding
- **The "reset"**: run pi, apply changes, and start the day again
- **The "escape"**: checks pass *and* the review reports `NO_ISSUES`

It's Groundhog Day, but for CI.

---

## What It Does

1. **Run your check command** (e.g., `./scripts/ci.sh`, `pytest`)
2. **If checks fail** → the loop "kills" the run and pi attempts a fix
3. **If checks pass** → pi reviews the diff for problems
4. **If review finds issues** → pi fixes them and the day restarts
5. **Repeat** until the run survives both checks *and* review

## Quick Start

### Requirements

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- [pi](https://github.com/mariozechner/pi) on your PATH
- Git
- (Optional) GitHub CLI (`gh`) for [PR review mode](docs/guide.md#pr-review-mode)

### Install

```bash
uv tool install "fix-die-repeat @ git+https://github.com/CTristan/fix-die-repeat.git"
```

### Usage

```bash
# Auto-detects your project's check command
fix-die-repeat

# Custom check command
fix-die-repeat -c "pytest -xvs"

# With a specific model
fix-die-repeat -c "make test" -m anthropic/claude-sonnet-4-5
```

On first run, fix-die-repeat [detects your project type](docs/guide.md#check-command-resolution) and asks you to confirm the check command. The choice is saved so subsequent runs use it automatically.

## Features

- **[Auto-detection](docs/guide.md#check-command-resolution)** — finds your check command from project files (`Makefile`, `package.json`, `Cargo.toml`, `pyproject.toml`, and more)
- **[PR review mode](docs/guide.md#pr-review-mode)** — fetches and fixes GitHub PR review comments automatically
- **[Oscillation detection](docs/guide.md#oscillation-detection)** — warns when the same failure keeps repeating
- **[Context management](docs/guide.md#context-management)** — keeps pi's prompt efficient by attaching or compacting artifacts
- **[Notifications](docs/guide.md#notifications-optional)** — optional [ntfy](https://ntfy.sh/) alerts when runs complete
- **[Debug mode](docs/guide.md#debug-mode)** — timestamped session logs and verbose output

For full CLI reference, environment variables, and detailed usage, see the **[User's Guide](docs/guide.md)**.

---

[Contributing](CONTRIBUTING.md) · [License (MIT)](LICENSE) · Built on [pi](https://github.com/mariozechner/pi)

> *"On your feet, maggot."*
