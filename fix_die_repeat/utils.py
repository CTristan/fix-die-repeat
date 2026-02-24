"""Utility functions for fix-die-repeat."""

import fnmatch
import hashlib
import subprocess
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.text import Text

console = Console()


class Logger:
    """Thread-safe logger with file and console output."""

    def __init__(
        self,
        fdr_log: Path | None = None,
        session_log: Path | None = None,
        debug: bool = False,
    ) -> None:
        """Initialize logger.

        Args:
            fdr_log: Path to fdr.log file
            session_log: Path to session log file
            debug: Enable debug mode

        """
        self.fdr_log = fdr_log
        self.session_log = session_log
        self.debug = debug

    def log(self, message: str, level: str = "INFO") -> None:
        """Log a message.

        Args:
            message: Message to log
            level: Log level (INFO, DEBUG, ERROR, WARNING)

        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_message = f"[{timestamp}] [fdr] [{level}] {message}"

        # Console output
        if level == "ERROR":
            console.print(Text(log_message, style="bold red"))
        elif level == "WARNING":
            console.print(Text(log_message, style="bold yellow"))
        elif level == "DEBUG" and self.debug:
            console.print(Text(log_message, style="dim"))
        else:
            console.print(log_message)

        # File output
        for log_file in [self.fdr_log, self.session_log]:
            if log_file:
                try:
                    with log_file.open("a", encoding="utf-8") as f:
                        f.write(log_message + "\n")
                except OSError:
                    pass

    def debug_log(self, message: str) -> None:
        """Log a debug message (only if debug mode is enabled)."""
        if self.debug:
            self.log(message, level="DEBUG")

    def error(self, message: str) -> None:
        """Log an error message."""
        self.log(message, level="ERROR")

    def warning(self, message: str) -> None:
        """Log a warning message."""
        self.log(message, level="WARNING")

    def info(self, message: str) -> None:
        """Log an info message."""
        self.log(message, level="INFO")


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
    command: str,
    cwd: Path | None = None,
    capture_output: bool = True,
    check: bool = False,
) -> tuple[int, str, str]:
    """Run a shell command.

    Args:
        command: Command to run
        cwd: Working directory
        capture_output: Capture stdout and stderr
        check: Raise exception on non-zero exit code

    Returns:
        Tuple of (exit_code, stdout, stderr)

    """
    kwargs: dict[str, str | bytes | Path | int | None | bool] = {"cwd": cwd}  # type: ignore[assignment]
    if capture_output:
        kwargs.update({"stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "text": True})  # type: ignore[dict-item]

    try:
        result = subprocess.run(command, shell=True, **kwargs)  # type: ignore[arg-type]
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, command)
        return (result.returncode, result.stdout or "", result.stderr or "")
    except FileNotFoundError:
        return (127, "", f"Command not found: {command.split()[0]}")


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
        returncode, stdout, _ = run_command(f"git hash-object {file_path}", check=False)
        if returncode == 0:
            return stdout.strip()
    except Exception:
        pass

    # Fallback to sha256
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


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

    # Get all changed files
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

    # Filter out non-existent files, .fix-die-repeat files, and excluded patterns
    result = []
    for f in sorted(files):
        if not f or f.startswith(".fix-die-repeat"):
            continue

        file_path = project_root / f
        if not file_path.is_file():
            continue

        # Check exclude patterns
        basename = file_path.name
        excluded = False
        for pattern in exclude_patterns:
            if basename.lower() == pattern.lower().replace("*", ""):
                # Simple match for patterns like "*.lock"
                if pattern.startswith("*"):
                    suffix = pattern[1:]
                    if basename.endswith(suffix):
                        excluded = True
                        break
                elif basename == pattern:
                    excluded = True
                    break

        if not excluded:
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
    warning_parts: list[str] = []

    for f in files:
        file_path = project_root / f
        if file_path.exists():
            lines = get_file_line_count(file_path)
            if lines > threshold_lines:
                if not warning_parts:
                    warning_parts.append(
                        "CRITICAL WARNING: The following files are >2000 lines and will be TRUNCATED by the 'read' tool:",
                    )
                warning_parts.append(f"- {f} ({lines} lines)")

    if warning_parts:
        warning_parts.extend(
            [
                "",
                "[CRITICAL]: You CANNOT see the bottom of these files. If errors occur there, you are flying blind.",
                "STRONGLY RECOMMENDED: Split these files into smaller files or modules to bring them under the 2000-line limit.",
                "  - If the file contains tests at the bottom, move them to a separate test file (e.g., tests.rs, test_file.py, file.test.js).",
                "  - If it is a large logic file, extract cohesive functionality into separate source files or subfolders.",
            ],
        )

    return "\n".join(warning_parts)


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

    for pattern in exclude_patterns:
        if fnmatch.fnmatch(filename.lower(), pattern.lower()):
            return True

    return False


def play_completion_sound() -> None:
    """Play a completion sound (best-effort)."""
    # macOS
    for sound in ["Purr", "Tink", "Pop", "Glass"]:
        sound_file = Path(f"/System/Library/Sounds/{sound}.aiff")
        if sound_file.exists():
            run_command(f"afplay {sound_file}", check=False)
            return

    # Linux - paplay
    for sound in ["complete.oga", "service-login.oga", "message.oga"]:
        sound_file = Path(f"/usr/share/sounds/freedesktop/stereo/{sound}")
        if sound_file.exists():
            run_command(f"paplay {sound_file}", check=False)
            return

    # Linux - canberra-gtk-play
    run_command("canberra-gtk-play -i complete -d 'fix-die-repeat'", check=False)

    # Last resort
    print("\a", end="", flush=True)


def sanitize_ntfy_topic(text: str) -> str:
    """Sanitize text for ntfy topic name.

    Args:
        text: Text to sanitize

    Returns:
        Sanitized topic name

    """
    import re

    # ntfy allows alphanumeric, hyphen, underscore, and dot
    return re.sub(r"[^a-z0-9._-]", "-", text.lower()).strip("-")


def send_ntfy_notification(
    exit_code: int,
    duration_str: str,
    repo_name: str,
    ntfy_url: str,
    logger: Logger | None = None,
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
    returncode, _, _ = run_command("which curl", check=False)
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
        f"curl -sS -X POST '{ntfy_url}/{topic}' "
        f"-H 'Title: {title}' "
        f"-H 'Tags: {tags}' "
        f"-H 'Priority: {priority}' "
        f"-d '{message}'",
        check=False,
    )

    if logger:
        logger.debug_log(f"Sent ntfy notification to {ntfy_url}/{topic}")
