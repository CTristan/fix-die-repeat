"""Utility functions for fix-die-repeat."""

import fnmatch
import hashlib
import logging
import re
import shlex
import subprocess
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

from fix_die_repeat.messages import build_large_file_warning

console = Console()
LOG_FORMAT = "[%(asctime)s] [fdr] [%(levelname)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOGGER_NAME = "fix_die_repeat"


def configure_logger(
    fdr_log: Path | None = None,
    session_log: Path | None = None,
    *,
    debug: bool = False,
) -> logging.Logger:
    """Configure and return the project logger.

    Args:
        fdr_log: Path to fdr.log file
        session_log: Path to session log file
        debug: Enable debug mode

    Returns:
        Configured logger instance

    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    rich_handler = RichHandler(
        console=console,
        show_time=False,
        show_level=False,
        show_path=False,
        markup=False,
        rich_tracebacks=True,
    )
    rich_handler.setLevel(logging.DEBUG if debug else logging.INFO)
    rich_handler.setFormatter(formatter)
    logger.addHandler(rich_handler)

    for log_file in (fdr_log, session_log):
        if not log_file:
            continue
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG if debug else logging.INFO)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def format_duration(total_seconds: int) -> str:
    """Format duration in seconds to human-readable string.

    Args:
        total_seconds: Duration in seconds

    Returns:
        Formatted duration string

    """
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60

    if hours > 0:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes > 0:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def run_command(
    command: str | list[str],
    cwd: Path | None = None,
    *,
    capture_output: bool = True,
    check: bool = False,
) -> tuple[int, str, str]:
    """Run a command without invoking a shell.

    String commands are tokenized with ``shlex.split``. If shell features
    (pipes, redirection, ``&&``) are required, wrap explicitly via something
    like ``bash -lc '...'``.

    Args:
        command: Command to run as a string or argv list
        cwd: Working directory
        capture_output: Capture stdout and stderr
        check: Raise exception on non-zero exit code

    Returns:
        Tuple of (exit_code, stdout, stderr)

    """
    try:
        args = shlex.split(command) if isinstance(command, str) else command
    except ValueError as exc:
        return (2, "", f"Invalid command syntax: {exc}")

    if not args:
        return (2, "", "No command provided")

    try:
        result = subprocess.run(  # noqa: S603  # args are tokenized argv with shell disabled.
            args,
            cwd=cwd,
            capture_output=capture_output,
            text=True,
            check=check,
        )
        return (result.returncode, result.stdout or "", result.stderr or "")
    except FileNotFoundError:
        return (127, "", f"Command not found: {args[0]}")


def get_git_revision_hash(file_path: Path) -> str:
    """Get git hash-object of a file.

    Args:
        file_path: Path to file

    Returns:
        Git hash

    """
    if not file_path.exists():
        return f"no_file_{file_path.name}"

    try:
        git_returncode, stdout, _ = run_command(["git", "hash-object", str(file_path)], check=False)
        if git_returncode == 0:
            return stdout.strip()
    except OSError:
        pass

    # Fallback to sha256
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def _collect_git_files(project_root: Path) -> set[str]:
    """Collect all changed files from git (staged, unstaged, untracked).

    Args:
        project_root: Project root directory

    Returns:
        Set of file paths

    """
    files = set()

    # Staged and unstaged changes
    for cmd in ["git diff --name-only", "git diff --cached --name-only"]:
        returncode, stdout, _ = run_command(cmd, cwd=project_root, check=False)
        if returncode == 0:
            files.update(stdout.strip().split("\n"))

    # Untracked files
    returncode, stdout, _ = run_command(
        "git ls-files --others --exclude-standard",
        cwd=project_root,
        check=False,
    )
    if returncode == 0:
        files.update(stdout.strip().split("\n"))

    return files


def _should_exclude_file(basename: str, exclude_patterns: list[str]) -> bool:
    """Check if a file should be excluded based on patterns.

    Args:
        basename: Name of the file
        exclude_patterns: Patterns to check

    Returns:
        True if file should be excluded

    """
    for pattern in exclude_patterns:
        if basename.lower() == pattern.lower().replace("*", ""):
            if pattern.startswith("*"):
                suffix = pattern[1:]
                if basename.endswith(suffix):
                    return True
            elif basename == pattern:
                return True
    return False


def get_changed_files(
    project_root: Path,
    exclude_patterns: list[str] | None = None,
) -> list[str]:
    """Get changed files (staged + unstaged + untracked).

    Args:
        project_root: Project root directory
        exclude_patterns: Patterns to exclude

    Returns:
        List of changed file paths (relative to project root)

    """
    exclude_patterns = exclude_patterns or [
        "*.lock",
        "*-lock.json",
        "*-lock.yaml",
        "go.sum",
        "*.min.*",
    ]

    files = _collect_git_files(project_root)

    # Filter out non-existent files, .fix-die-repeat files, and excluded patterns
    result = []
    for f in sorted(files):
        if not f or f.startswith(".fix-die-repeat"):
            continue

        file_path = project_root / f
        if not file_path.is_file():
            continue

        if not _should_exclude_file(file_path.name, exclude_patterns):
            result.append(f)

    return result


def get_file_size(path: Path) -> int:
    """Get file size in bytes.

    Args:
        path: Path to file

    Returns:
        File size in bytes (0 if file doesn't exist)

    """
    try:
        return path.stat().st_size
    except OSError:
        return 0


def get_file_line_count(path: Path) -> int:
    """Get file line count.

    Args:
        path: Path to file

    Returns:
        Number of lines (0 if file doesn't exist)

    """
    try:
        return sum(1 for _ in path.open(encoding="utf-8", errors="ignore"))
    except OSError:
        return 0


def detect_large_files(
    files: list[str],
    project_root: Path,
    threshold_lines: int = 2000,
) -> str:
    """Detect large files and generate warning message.

    Args:
        files: List of file paths relative to project root
        project_root: Project root directory
        threshold_lines: Line count threshold

    Returns:
        Warning message (empty if no large files found)

    """
    large_files: list[tuple[str, int]] = []

    for f in files:
        file_path = project_root / f
        if file_path.exists():
            lines = get_file_line_count(file_path)
            if lines > threshold_lines:
                large_files.append((f, lines))

    return build_large_file_warning(large_files)


def is_excluded_file(filename: str, exclude_patterns: list[str] | None = None) -> bool:
    """Check if a file should be excluded from context.

    Args:
        filename: Name of the file
        exclude_patterns: Patterns to exclude

    Returns:
        True if file should be excluded

    """
    exclude_patterns = exclude_patterns or [
        "*.lock",
        "*-lock.json",
        "*-lock.yaml",
        "go.sum",
        "*.min.*",
    ]

    return any(fnmatch.fnmatch(filename.lower(), pattern.lower()) for pattern in exclude_patterns)


def play_completion_sound() -> None:
    """Play a completion sound (best-effort)."""
    # macOS
    for sound in ["Purr", "Tink", "Pop", "Glass"]:
        sound_file = Path(f"/System/Library/Sounds/{sound}.aiff")
        if sound_file.exists():
            run_command(["afplay", str(sound_file)], check=False)
            return

    # Linux - paplay
    for sound in ["complete.oga", "service-login.oga", "message.oga"]:
        sound_file = Path(f"/usr/share/sounds/freedesktop/stereo/{sound}")
        if sound_file.exists():
            run_command(["paplay", str(sound_file)], check=False)
            return

    # Linux - canberra-gtk-play
    run_command(["canberra-gtk-play", "-i", "complete", "-d", "fix-die-repeat"], check=False)

    # Last resort
    print("\a", end="", flush=True)


def sanitize_ntfy_topic(text: str) -> str:
    """Sanitize text for ntfy topic name.

    Args:
        text: Text to sanitize

    Returns:
        Sanitized topic name

    """
    # ntfy allows alphanumeric, hyphen, underscore, and dot
    return re.sub(r"[^a-z0-9._-]", "-", text.lower()).strip("-")


def send_ntfy_notification(
    exit_code: int,
    duration_str: str,
    repo_name: str,
    ntfy_url: str,
    logger: logging.Logger | None = None,
) -> None:
    """Send ntfy notification (best-effort).

    Args:
        exit_code: Process exit code
        duration_str: Duration string
        repo_name: Repository name
        ntfy_url: ntfy server URL
        logger: Logger instance for debug output

    """
    # Check if curl is available
    returncode, _, _ = run_command(["which", "curl"], check=False)
    if returncode != 0:
        return

    topic = sanitize_ntfy_topic(repo_name)

    if exit_code == 0:
        title = "✓ fix-die-repeat completed"
        tags = "white_check_mark,done"
        priority = "default"
    else:
        title = "✗ fix-die-repeat failed"
        tags = "warning,x"
        priority = "high"

    message = f"{title} ({duration_str}) in {topic}"

    # Send notification (ignore errors)
    run_command(
        [
            "curl",
            "-sS",
            "-X",
            "POST",
            f"{ntfy_url}/{topic}",
            "-H",
            f"Title: {title}",
            "-H",
            f"Tags: {tags}",
            "-H",
            f"Priority: {priority}",
            "-d",
            message,
        ],
        check=False,
    )

    if logger:
        logger.debug("Sent ntfy notification to %s/%s", ntfy_url, topic)
