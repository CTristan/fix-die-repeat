# PLAN: Smart Check Command Resolution

## Overview

Make fix-die-repeat new-user friendly by replacing the hardcoded `./scripts/ci.sh` default with a smart resolution chain that auto-detects, confirms, persists, and validates the check command.

**Current problem:** A new user who runs `fix-die-repeat` without `-c` gets exit code 127 ("Command not found: ./scripts/ci.sh"), which the runner treats as a check *failure*, causing pi to try to "fix" a non-existent script.

---

## Design

### Resolution Chain (priority order)

| Priority | Source | Behavior |
|----------|--------|----------|
| 1 | CLI flag (`-c` / `--check-cmd`) or `FDR_CHECK_CMD` env var | Use directly, no validation beyond pre-flight |
| 2 | Project-level config (`.fix-die-repeat/config`) | Use directly, no validation beyond pre-flight |
| 3 | System-wide config (`~/.config/fix-die-repeat/config`) | **Validate command exists** — if not found, log a warning and fall through to step 4 |
| 4 | Auto-detect from project files | Scan for common patterns → confirm with user → persist to project-level config |
| 5 | Interactive prompt | Ask user what command to run → persist to project-level config |
| 6 | No TTY (non-interactive) | Hard error with guidance message |

### Pre-Flight Validation

After resolution completes (regardless of source), verify the resolved command is executable before entering the main loop:

- Tokenize the command with `shlex.split()`
- Check if the first token is an executable via `shutil.which()` or by testing if it's a valid file path
- If not found → **hard error**: `"Check command '<cmd>' not found. Is it installed and on your PATH?"`
- Special handling: commands wrapped in `bash -lc '...'` or similar should validate `bash`, not the inner command

This pre-flight check prevents the runner from wasting cycles (and pi invocations) on a command that can never succeed.

### Config File Format

Both project-level and system-wide config files use simple TOML-like key-value format:

```toml
# .fix-die-repeat/config
check_cmd = "uv run pytest"
```

**Why not full TOML?** Keep it simple — the only persisted value today is `check_cmd`. If more settings need persistence later, upgrading to proper TOML with a `tomllib` parser is straightforward. For now, a simple `key = "value"` or `key = value` parser avoids adding a dependency.

**Parsing rules:**
- Lines starting with `#` are comments
- Empty lines are ignored
- Format: `key = value` or `key = "value"` (strip surrounding quotes)
- Only recognized key today: `check_cmd`

### Config File Locations

| Level | Path | Gitignored? |
|-------|------|-------------|
| Project | `.fix-die-repeat/config` | Yes (`.fix-die-repeat/` is already gitignored) |
| System | `~/.config/fix-die-repeat/config` | N/A (user's home directory) |

### Auto-Detection Rules

Scan the project root for these files, **in this order**. Use the first match:

| File Present | Detected Command | Rationale |
|--------------|-----------------|-----------|
| `scripts/ci.sh` | `./scripts/ci.sh` | Existing FDR convention; honors projects already set up |
| `Makefile` with a `test` target | `make test` | Very common cross-language pattern |
| `Makefile` with a `check` target | `make check` | Common alternative to `test` |
| `package.json` with `scripts.test` | `npm test` | Standard Node.js convention |
| `Cargo.toml` | `cargo test` | Standard Rust convention |
| `pyproject.toml` with `[tool.pytest]` section | `uv run pytest` | Python with pytest configured |
| `pyproject.toml` (no pytest config) | `uv run python -m pytest` | Python fallback |
| `go.mod` | `go test ./...` | Standard Go convention |
| `build.gradle` or `build.gradle.kts` | `./gradlew test` | Standard Gradle convention |
| `pom.xml` | `mvn test` | Standard Maven convention |
| `mix.exs` | `mix test` | Standard Elixir convention |
| `Gemfile` | `bundle exec rake test` | Standard Ruby convention |

**Detection implementation notes:**
- For `Makefile`: use a simple regex scan for `^test:` or `^check:` (line starts with target name followed by colon)
- For `package.json`: parse JSON, check `scripts.test` exists and is not the default npm placeholder (`"echo \"Error: no test specified\" && exit 1"`)
- For `pyproject.toml`: scan for `[tool.pytest` string (avoid adding `tomllib` dependency just for detection)
- All other files: presence check only (no content parsing needed)

### Interactive Flow

#### When auto-detection finds a match:

```
🔍 Auto-detected check command: uv run pytest
   (from pyproject.toml with pytest configuration)

Use this command? [Y/n]:
```

- **Enter / Y / y** → persist to `.fix-die-repeat/config` and proceed
- **N / n** → fall through to interactive prompt (step 5)

#### When no detection or user declines:

```
No check command detected for this project.

What command should fix-die-repeat run to check your project?
Examples: pytest, npm test, cargo test, make test, ./scripts/ci.sh

Check command: █
```

- User types a command → persist to `.fix-die-repeat/config` and proceed
- Empty input → re-prompt (up to 3 times, then exit with error)

#### When no TTY (non-interactive):

```
Error: No check command configured.

fix-die-repeat needs a check command to run. Provide one via:
  • CLI flag:          fix-die-repeat -c "pytest"
  • Environment var:   FDR_CHECK_CMD="pytest" fix-die-repeat
  • Project config:    echo 'check_cmd = "pytest"' > .fix-die-repeat/config
  • Global config:     echo 'check_cmd = "pytest"' > ~/.config/fix-die-repeat/config
```

#### When system-wide config command not found:

```
⚠ Global check command 'pytest' not found in this project. Falling back to auto-detection...
```

Then continues to auto-detect (step 4).

---

## Implementation Plan

### Phase 1: Config Resolution Module

**New file: `fix_die_repeat/detection.py`**

This module handles the full resolution chain. Keep it separate from `config.py` to maintain single-responsibility (config.py handles Settings/Paths, detection.py handles the interactive resolution logic).

```python
# Public API
def resolve_check_cmd(
    cli_check_cmd: str | None,
    project_config_path: Path,
    system_config_path: Path,
    project_root: Path,
) -> str:
    """Resolve check command from the resolution chain.

    Returns the resolved command string.
    Raises SystemExit if no command can be resolved.
    """
```

**Key functions to implement:**

1. `read_config_file(path: Path) -> str | None` — parse a config file and return `check_cmd` value or `None`
2. `write_config_file(path: Path, check_cmd: str) -> None` — write/update a config file with the `check_cmd`
3. `auto_detect_check_cmd(project_root: Path) -> tuple[str, str] | None` — scan for project files, return `(command, reason)` or `None`
4. `validate_command_exists(command: str) -> bool` — check if the first token of the command is executable
5. `prompt_confirm_command(command: str, reason: str) -> bool` — ask Y/n (returns `True` on accept)
6. `prompt_check_command() -> str` — ask the user to type a command
7. `is_interactive() -> bool` — check if stdin is a TTY
8. `resolve_check_cmd(...)` — orchestrate the full chain

### Phase 2: Integrate into Config/CLI

**Modify: `fix_die_repeat/config.py`**

- Remove the default value `"./scripts/ci.sh"` from `Settings.check_cmd`
- Change type to `str | None` with `default=None`
- Add `config_file` path to `Paths` class (project-level)
- Add `system_config_path()` classmethod or standalone function for `~/.config/fix-die-repeat/config`

```python
class Settings(BaseSettings):
    check_cmd: str | None = pyd.Field(
        default=None,
        alias="FDR_CHECK_CMD",
        description="Command to run checks",
    )
```

**Modify: `fix_die_repeat/cli.py`**

- After building `Settings`, if `settings.check_cmd` is `None`, call `resolve_check_cmd()` to resolve it
- Set `settings.check_cmd` to the resolved value before creating `PiRunner`

The resolution should happen in `_run_main()` between `get_settings()` and `PiRunner()`:

```python
def _run_main(options: CliOptions) -> int:
    settings = get_settings(options)
    paths = Paths()

    if settings.check_cmd is None:
        settings.check_cmd = resolve_check_cmd(
            cli_check_cmd=None,
            project_config_path=paths.fdr_dir / "config",
            system_config_path=get_system_config_path(),
            project_root=paths.project_root,
        )

    # Pre-flight validation
    validate_check_cmd_or_exit(settings.check_cmd)

    runner = PiRunner(settings, paths)
    return runner.run()
```

### Phase 3: Pre-Flight Validation

**Add to `fix_die_repeat/detection.py`:**

```python
def validate_check_cmd_or_exit(check_cmd: str) -> None:
    """Validate the check command is executable. Exit with error if not."""
```

Logic:
1. Tokenize with `shlex.split()`
2. Get first token (the executable)
3. If first token is `bash`, `sh`, `zsh` — the command is a shell wrapper, skip deep validation (the shell exists)
4. If first token starts with `./` or `/` — check `Path(token).exists()` and `os.access(token, os.X_OK)`
5. Otherwise — check `shutil.which(token)` is not `None`
6. On failure: print clear error message and `sys.exit(1)`

### Phase 4: Messages

**Modify: `fix_die_repeat/messages.py`**

Add message functions for all user-facing strings in the detection flow:

- `auto_detect_found_message(command: str, reason: str) -> str`
- `auto_detect_confirm_prompt() -> str`
- `no_detection_prompt_message() -> str`
- `no_tty_error_message() -> str`
- `global_config_fallthrough_warning(command: str) -> str`
- `check_cmd_not_found_error(command: str) -> str`
- `check_cmd_persisted_message(config_path: str) -> str`

This keeps all user-facing text in `messages.py` consistent with the existing pattern.

### Phase 5: Tests

**New file: `tests/test_detection.py`**

Test cases organized by function:

#### `TestReadConfigFile`
- `test_reads_check_cmd_from_file` — basic key-value parsing
- `test_reads_quoted_value` — handles `check_cmd = "pytest"`
- `test_ignores_comments` — lines starting with `#`
- `test_ignores_empty_lines`
- `test_returns_none_for_missing_file`
- `test_returns_none_for_file_without_check_cmd`
- `test_returns_none_for_empty_file`

#### `TestWriteConfigFile`
- `test_creates_file_with_check_cmd`
- `test_creates_parent_directories`
- `test_overwrites_existing_check_cmd`
- `test_preserves_comments_and_other_keys`

#### `TestAutoDetect`
- `test_detects_scripts_ci_sh` — existing convention honored
- `test_detects_makefile_test_target`
- `test_detects_makefile_check_target`
- `test_detects_package_json_with_test_script`
- `test_ignores_package_json_default_test_script` — npm's placeholder
- `test_detects_cargo_toml`
- `test_detects_pyproject_with_pytest`
- `test_detects_pyproject_without_pytest`
- `test_detects_go_mod`
- `test_detects_mix_exs`
- `test_detects_gradle`
- `test_detects_pom_xml`
- `test_detects_gemfile`
- `test_returns_none_for_empty_directory`
- `test_priority_order` — when multiple files exist, first match wins

#### `TestValidateCommandExists`
- `test_valid_system_command` — e.g., `ls`
- `test_valid_path_command` — e.g., `./scripts/ci.sh` (create temp script)
- `test_invalid_command` — nonexistent binary
- `test_shell_wrapper_passes` — `bash -lc '...'` validates `bash`
- `test_path_command_not_executable` — exists but not +x

#### `TestPromptConfirmCommand` (with monkeypatched stdin)
- `test_accepts_y`
- `test_accepts_empty_enter`
- `test_declines_n`

#### `TestPromptCheckCommand` (with monkeypatched stdin)
- `test_returns_user_input`
- `test_retries_on_empty_input`
- `test_exits_after_max_retries`

#### `TestIsInteractive`
- `test_returns_true_for_tty` (mock `sys.stdin.isatty`)
- `test_returns_false_for_pipe`

#### `TestResolveCheckCmd` (integration-level)
- `test_cli_flag_takes_priority`
- `test_project_config_over_system_config`
- `test_system_config_used_when_no_project_config`
- `test_system_config_fallthrough_on_bad_command` — system config has `pytest` but it's not installed; falls through to auto-detect
- `test_auto_detect_with_confirmation` (mock stdin)
- `test_auto_detect_declined_falls_to_prompt` (mock stdin)
- `test_no_tty_exits_with_error`
- `test_persists_to_project_config_after_auto_detect`
- `test_persists_to_project_config_after_prompt`

**Modify: `tests/test_config.py`**
- Update existing tests that rely on `check_cmd` defaulting to `"./scripts/ci.sh"` — it will now default to `None`

**Modify: `tests/test_cli.py`**
- Add test for resolution being called when no `check_cmd` provided

### Phase 6: Update Documentation

**Modify: `README.md`**
- Update Quick Start section to show the first-run experience
- Remove references to `./scripts/ci.sh` as the default
- Add a "Configuration" section explaining the resolution chain
- Update the environment variables table (`FDR_CHECK_CMD` default changes from `./scripts/ci.sh` to `(auto-detected)`)
- Add examples of `.fix-die-repeat/config` and `~/.config/fix-die-repeat/config`

**Modify: `AGENTS.md`**
- Update `Settings` documentation (`check_cmd` default changes to `None`)
- Add `detection.py` to project structure
- Add `config` to the Paths file reference table
- Document the resolution chain in Key Design Decisions
- Update the "Adding a New Configuration Option" pattern if needed

---

## File Changes Summary

| File | Change Type | Description |
|------|-------------|-------------|
| `fix_die_repeat/detection.py` | **New** | Check command resolution chain, auto-detection, interactive prompts, config file I/O, pre-flight validation |
| `fix_die_repeat/config.py` | **Modify** | Change `check_cmd` default to `None`, add `config_file` path to `Paths`, add `system_config_path` helper |
| `fix_die_repeat/cli.py` | **Modify** | Call `resolve_check_cmd()` and `validate_check_cmd_or_exit()` in `_run_main()` |
| `fix_die_repeat/messages.py` | **Modify** | Add message functions for detection flow user-facing text |
| `tests/test_detection.py` | **New** | Comprehensive tests for all detection module functions |
| `tests/test_config.py` | **Modify** | Update tests for `check_cmd` default change (`None` instead of `./scripts/ci.sh`) |
| `tests/test_cli.py` | **Modify** | Add tests for resolution integration |
| `README.md` | **Modify** | Update Quick Start, config docs, env var table |
| `AGENTS.md` | **Modify** | Update architecture docs, add detection.py, update Settings docs |

---

## Constraints & Guidelines

### From AGENTS.md (must follow)

- **All Python commands via `uv run`** — never call `pytest`, `ruff`, etc. directly
- **Never modify test configuration** (coverage thresholds, pytest addopts) without explicit human approval
- **Ruff rules**: C901, PLR0913, PLR2004, PLC0415 must NEVER be ignored — refactor instead
- **Per-file ignores** require explanatory comments
- **All files under 2000 lines**
- **Run full checks after changes**: `uv run pytest && uv run ruff check --fix fix_die_repeat tests && uv run ruff format fix_die_repeat tests && uv run mypy fix_die_repeat`

### Design Constraints

- **No new dependencies** — use only stdlib + existing deps (click, rich, pydantic)
- **Language-agnostic** — this tool is used with any language, not just Python
- **Interactive prompts via `click.prompt()` and `click.confirm()`** — leverage the existing Click dependency for consistent terminal interaction instead of raw `input()`
- **Rich console for styled output** — use the existing `Console` instance for warnings/errors during the detection flow
- **Keep `detection.py` under 300 lines** — it's a focused module; if it grows, split into `detection.py` (orchestration) and `detection_rules.py` (auto-detect patterns)

### Backward Compatibility

- **Existing users with `-c` or `FDR_CHECK_CMD`**: Zero change in behavior — these take highest priority
- **Existing users with `./scripts/ci.sh`**: Auto-detection will find `scripts/ci.sh` as the first detection rule, so they get the same behavior (with a confirmation on first run, then it's persisted)
- **CI/CD environments (no TTY)**: Must set `FDR_CHECK_CMD` or `-c` explicitly — the hard error message tells them exactly how

---

## Open Questions (for implementer to decide)

1. **Should `write_config_file` preserve existing content?** Recommendation: Yes — read the file, update/add the `check_cmd` line, write back. This allows future keys to be added without losing data.

2. **Should auto-detection check if the detected command is actually executable?** Recommendation: No — auto-detection is about identifying the *likely* command from project structure. The pre-flight validation step (Phase 3) handles executability. This keeps concerns separated.

3. **XDG compliance for system config path**: Use `os.environ.get("XDG_CONFIG_HOME", "~/.config")` to respect the XDG Base Directory spec on Linux. On macOS, `~/.config` is also conventional for CLI tools.

---

## Success Criteria

- [ ] `fix-die-repeat` in a fresh directory with no config → shows interactive prompt, persists answer
- [ ] `fix-die-repeat` in a Python project with `pyproject.toml` → auto-detects `uv run pytest`, asks to confirm
- [ ] `fix-die-repeat` in a Rust project with `Cargo.toml` → auto-detects `cargo test`, asks to confirm
- [ ] `fix-die-repeat -c "make test"` → uses the command directly, no detection/prompts
- [ ] `FDR_CHECK_CMD=pytest fix-die-repeat` → uses env var directly
- [ ] Second run in same project (after first-run persist) → uses `.fix-die-repeat/config` silently
- [ ] System config with bad command → logs warning, falls through to auto-detect
- [ ] Non-existent final command → clear error before entering loop
- [ ] `echo "" | fix-die-repeat` (piped/no TTY, no config) → clear error with setup instructions
- [ ] All existing tests still pass (with updates for `check_cmd` default change)
- [ ] Coverage stays ≥80%
- [ ] `uv run ruff check`, `uv run mypy` pass clean
