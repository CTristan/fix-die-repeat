"""Check command resolution and auto-detection for fix-die-repeat."""

import contextlib
import json
import os
import re
import shlex
import shutil
import sys
from pathlib import Path

import click
from rich.console import Console

from fix_die_repeat.messages import (
    auto_detect_confirm_prompt,
    auto_detect_found_message,
    check_cmd_not_found_error,
    check_cmd_persisted_message,
    global_config_fallthrough_warning,
    no_detection_prompt_message,
    no_tty_error_message,
)

console = Console()


def _parse_config_value(value: str) -> str:
    """Parse a config value, removing surrounding quotes if present.

    Args:
        value: Raw value string from config file

    Returns:
        Parsed value string

    """
    # Remove surrounding quotes if present
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    return value


def read_config_file(path: object) -> str | None:
    """Read and parse a simple key-value config file.

    Args:
        path: Path to config file

    Returns:
        check_cmd value if found, None otherwise

    """
    if not isinstance(path, str | os.PathLike):
        return None

    try:
        config_path = Path(path)
        with config_path.open(encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                # Skip comments and empty lines
                if not line or line.startswith("#"):
                    continue
                # Parse key = value or key = "value"
                if line.startswith("check_cmd ="):
                    value = line.split("=", 1)[1].strip()
                    return _parse_config_value(value)
    except OSError:
        pass

    return None


def write_config_file(path: object, check_cmd: str) -> None:
    """Write or update a config file with the check_cmd.

    Preserves existing content and comments.

    Args:
        path: Path to config file
        check_cmd: Check command to write

    """
    if not isinstance(path, str | os.PathLike):
        error_msg = "path must be a string or Path-like object"
        raise TypeError(error_msg)

    config_path = Path(path)

    # Create parent directory if needed
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Try to read existing content
    existing_lines = []
    check_cmd_found = False

    with contextlib.suppress(OSError):
        existing_lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)

    # Write back, updating check_cmd if present
    try:
        with config_path.open("w", encoding="utf-8") as f:
            for _i, line in enumerate(existing_lines):
                stripped = line.strip()
                if stripped.startswith("check_cmd ="):
                    f.write(f'check_cmd = "{check_cmd}"\n')
                    check_cmd_found = True
                else:
                    f.write(line)

            # If check_cmd wasn't found, append it
            if not check_cmd_found:
                if existing_lines and not existing_lines[-1].endswith("\n"):
                    f.write("\n")
                f.write(f'check_cmd = "{check_cmd}"\n')
    except OSError as e:
        error_msg = f"Failed to write config file {config_path}: {e}"
        raise OSError(error_msg) from e


def _check_makefile_targets(root: Path) -> tuple[str, str] | None:
    """Check Makefile for test or check targets.

    Args:
        root: Project root directory

    Returns:
        Tuple of (command, reason) if found, None otherwise

    """
    makefile_path = root / "Makefile"
    if not makefile_path.is_file():
        return None

    try:
        makefile_content = makefile_path.read_text(encoding="utf-8")
        if re.search(r"^test:\s*$", makefile_content, re.MULTILINE):
            return "make test", "from Makefile test target"
        if re.search(r"^check:\s*$", makefile_content, re.MULTILINE):
            return "make check", "from Makefile check target"
    except OSError:
        pass

    return None


def _check_package_json(root: Path) -> tuple[str, str] | None:
    """Check package.json for test script.

    Args:
        root: Project root directory

    Returns:
        Tuple of (command, reason) if found, None otherwise

    """
    package_json_path = root / "package.json"
    if not package_json_path.is_file():
        return None

    try:
        package_data = json.loads(package_json_path.read_text(encoding="utf-8"))
        test_script = package_data.get("scripts", {}).get("test", "")
        # Skip npm's default placeholder
        if (
            test_script
            and 'echo "Error: no test specified"' not in test_script
            and "exit 1" not in test_script
        ):
            return "npm test", "from package.json scripts.test"
    except (OSError, json.JSONDecodeError):
        pass

    return None


def _check_pyproject_toml(root: Path) -> tuple[str, str] | None:
    """Check pyproject.toml for pytest configuration.

    Args:
        root: Project root directory

    Returns:
        Tuple of (command, reason) if found, None otherwise

    """
    pyproject_path = root / "pyproject.toml"
    if not pyproject_path.is_file():
        return None

    try:
        content = pyproject_path.read_text(encoding="utf-8")
    except OSError:
        return None

    # Check for pytest configuration
    if "[tool.pytest" in content:
        return "uv run pytest", "from pyproject.toml with pytest configuration"
    return "uv run python -m pytest", "from pyproject.toml"


def _validate_project_root(project_root: object) -> Path | None:
    """Validate and convert project_root to Path.

    Args:
        project_root: Project root directory

    Returns:
        Path object if valid, None otherwise

    """
    if not isinstance(project_root, str | os.PathLike):
        return None

    try:
        return Path(project_root)
    except (TypeError, AttributeError):
        return None


def auto_detect_check_cmd(project_root: object) -> tuple[str, str] | None:
    """Auto-detect check command from project files.

    Detection rules are checked in priority order. The first match wins.

    Args:
        project_root: Project root directory

    Returns:
        Tuple of (command, reason) if detected, None otherwise

    """
    root = _validate_project_root(project_root)
    if root is None:
        return None

    def _check_ci_sh(path: Path) -> tuple[str, str] | None:
        """Check for scripts/ci.sh (existing FDR convention)."""
        ci_sh_path = path / "scripts" / "ci.sh"
        if ci_sh_path.is_file():
            return "./scripts/ci.sh", "from scripts/ci.sh"
        return None

    def _check_simple_files(path: Path) -> tuple[str, str] | None:
        """Check for simple file-based detections."""
        simple_detections = [
            ("Cargo.toml", "cargo test", "from Cargo.toml"),
            ("go.mod", "go test ./...", "from go.mod"),
            ("pom.xml", "mvn test", "from pom.xml"),
            ("mix.exs", "mix test", "from mix.exs"),
            ("Gemfile", "bundle exec rake test", "from Gemfile"),
            ("build.gradle", "./gradlew test", "from Gradle build files"),
            ("build.gradle.kts", "./gradlew test", "from Gradle build files"),
        ]
        for filename, command, reason in simple_detections:
            if (path / filename).is_file():
                return command, reason
        return None

    # Chain detection rules in priority order using short-circuit evaluation
    return (
        _check_ci_sh(root)
        or _check_makefile_targets(root)
        or _check_package_json(root)
        or _check_pyproject_toml(root)
        or _check_simple_files(root)
    )


def validate_command_exists(command: str) -> bool:
    """Check if the first token of a command is executable.

    Args:
        command: Command string to validate

    Returns:
        True if command is executable, False otherwise

    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False

    if not tokens:
        return False

    first_token = tokens[0]

    # Shell wrapper commands - ensure the wrapper itself exists
    if first_token in ("bash", "sh", "zsh", "fish"):
        return shutil.which(first_token) is not None

    # Check for relative path (./something)
    if first_token.startswith(("./", "/")):
        return Path(first_token).is_file() and os.access(first_token, os.X_OK)

    # Check system PATH
    return shutil.which(first_token) is not None


def validate_check_cmd_or_exit(check_cmd: str) -> None:
    """Validate the check command is executable. Exit with error if not.

    Args:
        check_cmd: Check command to validate

    Raises:
        SystemExit: If command is not executable

    """
    if not validate_command_exists(check_cmd):
        console.print(f"[red]{check_cmd_not_found_error(check_cmd)}[/red]")
        sys.exit(1)


def prompt_confirm_command(command: str, reason: str) -> bool:
    """Ask user to confirm auto-detected command.

    Args:
        command: Auto-detected command
        reason: Reason for detection

    Returns:
        True if user confirms, False otherwise

    """
    console.print(auto_detect_found_message(command, reason))
    return click.confirm(auto_detect_confirm_prompt(), default=True)


def prompt_check_command() -> str:
    """Ask user to type a check command.

    Returns:
        User-provided command string

    Raises:
        SystemExit: If user enters empty input 3 times

    """
    max_retries = 3
    for attempt in range(max_retries):
        command = click.prompt(
            no_detection_prompt_message(),
            default="",
            show_default=False,
        ).strip()

        if command:
            return command

        remaining = max_retries - attempt - 1
        if remaining > 0:
            msg = f"Please enter a command. {remaining} attempt(s) remaining."
            console.print(f"[yellow]{msg}[/yellow]")
        else:
            msg = f"No command provided after {max_retries} attempts. Exiting."
            console.print(f"[red]{msg}[/red]")
            sys.exit(1)

    # This should never be reached due to sys.exit above, but mypy needs it
    return ""


def is_interactive() -> bool:
    """Check if stdin is a TTY (interactive terminal).

    Returns:
        True if interactive, False otherwise

    """
    return sys.stdin.isatty()


def get_system_config_path() -> str:
    """Get the path to the system-wide config file.

    Respects XDG_CONFIG_HOME on Linux, uses ~/.config on macOS and other systems.

    Returns:
        Path to system config file

    """
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return str(config_home / "fix-die-repeat" / "config")


def _persist_command(project_config_path: str | os.PathLike[str], command: str) -> None:
    """Persist the check command to project config.

    Args:
        project_config_path: Path to project config file
        command: Command to persist

    """
    try:
        write_config_file(project_config_path, command)
        console.print(check_cmd_persisted_message(str(project_config_path)))
    except OSError as e:
        console.print(f"[yellow]Warning: Could not save config: {e}[/yellow]")


def _handle_auto_detect(
    project_config_path: str | os.PathLike[str],
    project_root: str | os.PathLike[str],
) -> str | None:
    """Handle auto-detection of check command.

    Args:
        project_config_path: Path to project config file
        project_root: Project root directory

    Returns:
        Resolved command if successful, None otherwise

    """
    detected = auto_detect_check_cmd(project_root)
    if not detected:
        return None

    command, reason = detected

    if is_interactive():
        if prompt_confirm_command(command, reason):
            _persist_command(project_config_path, command)
            return command
        # User declined - fall through to prompt
        return None

    # Non-interactive, use detected command
    return command


def _handle_prompt(project_config_path: str | os.PathLike[str]) -> str:
    """Handle interactive prompt for check command.

    Args:
        project_config_path: Path to project config file

    Returns:
        Command from user prompt

    """
    command = prompt_check_command()
    _persist_command(project_config_path, command)
    return command


def resolve_check_cmd(
    cli_check_cmd: str | None,
    project_config_path: str | os.PathLike[str],
    system_config_path: str,
    project_root: str | os.PathLike[str],
) -> str:
    """Resolve check command from the resolution chain.

    Priority order:
    1. CLI flag/env var (cli_check_cmd) - already set in Settings
    2. Project config (.fix-die-repeat/config)
    3. System config (~/.config/fix-die-repeat/config) - with validation
    4. Auto-detect from project files
    5. Interactive prompt
    6. No TTY - hard error

    Args:
        cli_check_cmd: Check command from CLI/env var (should be None if we're calling this)
        project_config_path: Path to project-level config
        system_config_path: Path to system-wide config
        project_root: Project root directory

    Returns:
        Resolved check command string

    Raises:
        SystemExit: If no command can be resolved

    """
    # Priority 1: CLI/env var (should already be handled, but double-check)
    if cli_check_cmd:
        return cli_check_cmd

    # Priority 2: Project config
    project_cmd = read_config_file(project_config_path)
    if project_cmd:
        return project_cmd

    # Priority 3: System config (with validation)
    system_cmd = read_config_file(system_config_path)
    if system_cmd:
        if validate_command_exists(system_cmd):
            return system_cmd
        console.print(global_config_fallthrough_warning(system_cmd))
    # Fall through to auto-detect

    # Priority 4: Auto-detect
    auto_result = _handle_auto_detect(project_config_path, project_root)
    if auto_result:
        return auto_result

    # Priority 5: Interactive prompt
    if is_interactive():
        return _handle_prompt(project_config_path)

    # Priority 6: No TTY - hard error
    console.print(f"[red]{no_tty_error_message()}[/red]")
    sys.exit(1)
