"""Utility functions for fix-die-repeat."""

import contextlib
import fnmatch
import hashlib
import importlib.metadata
import io
import json
import logging
import os
import shlex
import subprocess
import sys
import tomllib
import types
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

import yaml
from rich.console import Console
from rich.logging import RichHandler

from fix_die_repeat.messages import build_large_file_warning

if TYPE_CHECKING:
    from typing import Self

# Platform-specific file locking imports
if sys.platform == "win32":  # pragma: no cover
    import msvcrt
else:  # pragma: no cover
    import fcntl

console = Console()
LOG_FORMAT = "[%(asctime)s] [fdr] [%(levelname)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOGGER_NAME = "fix_die_repeat"

# Prohibited ruff rules that must NEVER be ignored (see AGENTS.md)
PROHIBITED_RUFF_RULES = {"C901", "PLR0913", "PLR2004", "PLC0415"}

# Exit codes
EXIT_INVALID_COMMAND = 2
EXIT_COMMAND_NOT_FOUND = 127
EXIT_TIMEOUT = 124

# Rotation constants
DEFAULT_MAX_LINES = 2000
YAML_SUFFIXES = {".yaml", ".yml"}
YAML_SEPARATOR = "\n---\n"
DATE_FORMAT_MONTHLY = "%Y-%m"
WINDOWS_LOCK_LENGTH = 0xFFFF

# Constants for file handling and rotation to avoid magic values (PLR2004)
SEEK_BACK_ONE = -1
BINARY_NEWLINE = b"\n"
BINARY_EMPTY = b""


@runtime_checkable
class _FileHandle(Protocol):
    """Protocol for objects that support file descriptor access.

    Used for type-checking the file lock context manager.
    """

    def fileno(self) -> int:
        """Return the file descriptor for the file handle."""
        ...

    def seek(self, offset: int, whence: int = 0) -> int:
        """Move to a new file position."""
        ...

    def tell(self) -> int:
        """Return the current file position."""
        ...


class _FileLock:
    """Context manager for cross-platform file locking.

    Provides exclusive file locking to prevent concurrent writes from
    corrupting shared files.

    Uses fcntl on Unix and msvcrt on Windows.
    """

    def __init__(self, file_handle: _FileHandle) -> None:
        """Initialize the file lock.

        Args:
            file_handle: Open file handle to lock

        """
        self.file_handle = file_handle

    def __enter__(self) -> "Self":
        """Acquire the lock."""
        if sys.platform == "win32":  # pragma: no cover
            # Windows: use msvcrt.locking
            # Seek to start of file because msvcrt.locking locks a region
            # starting from the current file position. We lock a large region
            # (WINDOWS_LOCK_LENGTH bytes) so all processes contend for the same range even
            # as the file grows.
            self.file_handle.seek(0)
            msvcrt.locking(self.file_handle.fileno(), msvcrt.LK_LOCK, WINDOWS_LOCK_LENGTH)
        else:  # pragma: no cover
            # Unix: use fcntl.flock with LOCK_EX
            fcntl.flock(self.file_handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        """Release the lock."""
        if sys.platform == "win32":  # pragma: no cover
            # Windows: use msvcrt.locking with LK_UNLCK
            # Seek to start of file because msvcrt.locking locks a region
            # starting from the current file position. We must unlock the
            # same WINDOWS_LOCK_LENGTH-byte region that was locked in __enter__.
            self.file_handle.seek(0)
            msvcrt.locking(self.file_handle.fileno(), msvcrt.LK_UNLCK, WINDOWS_LOCK_LENGTH)
        else:  # pragma: no cover
            # Unix: use fcntl.flock with LOCK_UN
            fcntl.flock(self.file_handle.fileno(), fcntl.LOCK_UN)


class RuffConfigParseError(Exception):
    """Raised when pyproject.toml cannot be parsed for ruff config validation."""

    def __init__(self, path: Path, original_error: Exception) -> None:
        """Initialize the exception.

        Args:
            path: Path to the config file that failed to parse
            original_error: The original exception that caused the parse failure

        """
        self.path = path
        self.original_error = original_error
        super().__init__(f"Failed to parse ruff config from {path}: {original_error}")


def is_running_in_dev_mode() -> bool:
    """Check if fix-die-repeat is running from an editable install.

    An editable install is typically used during development where changes
    to the source code are immediately reflected without reinstallation.

    Returns:
        True if running from an editable install, False otherwise

    """
    # Get the package distribution metadata
    try:
        dist = importlib.metadata.distribution("fix-die-repeat")
    except importlib.metadata.PackageNotFoundError:
        # Package not installed via package manager - likely running directly
        return False
    except (OSError, ValueError):
        # Metadata access errors - conservatively return False
        return False

    # Check for direct_url.json which indicates editable install
    try:
        content = dist.read_text("direct_url.json")
        if content:
            data = json.loads(content)
            # Editable installs have "dir_info": {"editable": true}
            if data.get("dir_info", {}).get("editable") is True:
                return True
    except (json.JSONDecodeError, OSError):
        pass

    # Fallback: check if __file__ contains "site-packages"
    # Editable installs typically don't use site-packages
    # Use sys.modules to avoid triggering a new import (module is already loaded)
    try:
        module = sys.modules.get("fix_die_repeat")
        if module and hasattr(module, "__file__") and module.__file__:
            package_file = Path(module.__file__).resolve()
            package_path_str = str(package_file)
            # If the path doesn't contain "site-packages" or "dist-packages", it's likely editable
            if "site-packages" not in package_path_str and "dist-packages" not in package_path_str:
                return True
    except (AttributeError, OSError):
        pass

    return False


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
    timeout: float | None = None,
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
        timeout: Command timeout in seconds

    Returns:
        Tuple of (exit_code, stdout, stderr)

    """
    try:
        args = shlex.split(command) if isinstance(command, str) else command
    except ValueError as exc:
        return (EXIT_INVALID_COMMAND, "", f"Invalid command syntax: {exc}")

    if not args:
        return (EXIT_INVALID_COMMAND, "", "No command provided")

    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            capture_output=capture_output,
            text=True,
            check=check,
            timeout=timeout,
        )
    except FileNotFoundError:
        return (EXIT_COMMAND_NOT_FOUND, "", f"Command not found: {args[0]}")
    except subprocess.TimeoutExpired as exc:
        # exc.stdout is bytes | str | None (exception type is shared)
        if isinstance(exc.stdout, bytes):
            stdout_str = exc.stdout.decode("utf-8", errors="replace")
        else:
            stdout_str = exc.stdout or ""
        return (EXIT_TIMEOUT, stdout_str, f"Command timed out after {timeout}s")
    else:
        return (result.returncode, result.stdout or "", result.stderr or "")


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
    basename_lower = basename.lower()
    return any(fnmatch.fnmatchcase(basename_lower, pattern.lower()) for pattern in exclude_patterns)


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
    sys.stdout.write("\a")
    sys.stdout.flush()


def append_to_file(
    path: Path,
    content: str,
    *,
    use_yaml_separator: bool = False,
    use_safe_serializer: bool = False,
) -> None:
    """Safely append content to a file with locking.

    Args:
        path: Path to the file
        content: Content to append
        use_yaml_separator: Whether to prepend YAML document separator
        use_safe_serializer: Whether to use safe YAML serializer for appending

    """
    with path.open("a+", encoding="utf-8") as f, _FileLock(f):
        # Ensure file ends with newline
        f.seek(0, os.SEEK_END)
        if f.tell() > 0 and not (use_yaml_separator or use_safe_serializer):
            # For non-empty files without YAML separator, ensure a newline before appending.
            # Avoid arithmetic on text-mode tell() cookies by unconditionally writing a newline.
            # Note: YAML_SEPARATOR already begins with a newline, so we skip the extra newline.
            f.write("\n")

        if use_yaml_separator or use_safe_serializer:
            f.write(YAML_SEPARATOR)

        if use_safe_serializer:
            try:
                # Use safe serializer as per policy
                docs = list(yaml.safe_load_all(content))
                yaml.safe_dump_all(
                    docs,
                    f,
                    default_flow_style=False,
                    sort_keys=False,
                )
            except yaml.YAMLError:
                # Fallback to raw content if malformed to avoid data loss
                f.write(content)
        else:
            f.write(content)

        # Ensure trailing newline
        if not content.endswith("\n"):
            f.write("\n")


def rotate_file(
    path: Path,
    max_lines: int = DEFAULT_MAX_LINES,
    date_suffix: str | None = None,
) -> Path | None:
    """Rotate a file if it exceeds a line count threshold.

    Uses atomic operations to prevent data duplication or loss across
    multiple processes and crashes.

    Args:
        path: Path to the file to rotate
        max_lines: Maximum line count before rotation
        date_suffix: Optional date suffix (defaults to YYYY-MM).

    Returns:
        The path to the rotated file, or None if no rotation occurred.

    """
    if not path.is_file():
        return None

    if date_suffix is None:
        date_suffix = datetime.now(tz=UTC).strftime(DATE_FORMAT_MONTHLY)

    # Construct rotated filename
    rotated_path = path.parent / f"{path.stem}-{date_suffix}{path.suffix}"

    # Use a sidecar lock file to coordinate rotation.
    # This allows us to rename the source file safely.
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file, _FileLock(lock_file):
        # Check if rotation is still needed inside the lock
        if not path.is_file() or get_file_line_count(path) <= max_lines:
            return None

        result: Path | None = None

        # Determine if we append or perform initial rotation
        if rotated_path.exists():
            # Safety check: if path and rotated_path are hard links to the same inode
            # (can happen if previous rotation failed after os.link() succeeded but
            # path.unlink() failed), complete the rotation by unlinking path so only
            # rotated_path remains.
            try:
                if path.stat().st_ino == rotated_path.stat().st_ino:
                    # Both are hard links to the same file - complete the rotation
                    # by removing path, leaving only rotated_path
                    with contextlib.suppress(OSError):
                        path.unlink()
                    return rotated_path
            except OSError:
                # If we can't stat either file, proceed with normal rotation logic
                pass

            result = _rotate_with_existing_destination(path, rotated_path)
        elif _try_initial_rotation(path, rotated_path):
            # Initial rotation for this period
            result = rotated_path

    return result


def _try_initial_rotation(path: Path, rotated_path: Path) -> bool:
    """Try to rename the file to the rotated path.

    Returns:
        True if successful, False if rotation should be retried.

    """
    # Use os.link() as an atomic check-and-reserve for the rotated path.
    # If rotated_path already exists, this atomically raises FileExistsError
    # without overwriting, preventing race conditions in concurrent environments.
    try:
        os.link(path, rotated_path)
    except FileExistsError:
        # Destination already exists, cannot rotate safely without overwriting
        return False
    except OSError:
        # Hardlink failed (e.g., cross-device, permissions, filesystem doesn't support it).
        # Fall back to rename which works on same filesystem but isn't atomic for
        # the existence check - we rely on the lock file for coordination.
        try:
            # Check if destination exists before rename to avoid silent overwrite
            if rotated_path.exists():
                return False
            path.rename(rotated_path)
        except OSError:
            # Rename failed (e.g., cross-device), give up on initial rotation
            return False
    else:
        # Successfully created hard link; remove original to complete rotation
        try:
            path.unlink()
        except OSError:
            # If we failed to unlink, we can't claim success because the original
            # file still exists. The next run would try to rotate it again,
            # leading to duplication since rotated_path now also exists.
            # Best-effort cleanup: remove the rotated copy to prevent content
            # duplication on retry.
            with contextlib.suppress(OSError):
                rotated_path.unlink()
            return False

    return True


def _rotate_with_existing_destination(
    path: Path,
    rotated_path: Path,
) -> Path | None:
    """Rotate a file when the rotated destination already exists.

    This function handles the append-to-rotated case, ensuring that if the
    append operation fails, the original file is restored to prevent data loss.

    Args:
        path: The source file to rotate
        rotated_path: The existing rotated file to append to

    Returns:
        The path to the rotated file if successful, None otherwise

    """
    result: Path | None = None

    # Atomic move to temporary file on same partition
    tmp_rotating = path.with_suffix(path.suffix + f".{os.getpid()}.rotating")
    try:
        path.rename(tmp_rotating)
    except OSError:
        # Source vanished or was renamed by another process
        return rotated_path if rotated_path.exists() else None

    try:
        # Append content from temporary file to rotated file
        with tmp_rotating.open("r", encoding="utf-8") as src:
            _append_to_rotated_file(src, rotated_path)
    except Exception:
        # If append fails, attempt to restore the original path
        with contextlib.suppress(OSError):
            tmp_rotating.rename(path)
        # Re-raise so callers are aware of the failure
        raise
    else:
        # Cleanup temporary file only on successful append
        with contextlib.suppress(OSError):
            tmp_rotating.unlink()
        result = rotated_path

    return result


def _append_to_rotated_file(src: IO[str], rotated_path: Path) -> None:
    """Append content of source file to an existing rotated file."""
    with rotated_path.open("a+", encoding="utf-8") as target, _FileLock(target):
        # Ensure target ends with newline to avoid malformed appends.
        # Use the existing handle's binary buffer to safely inspect the last byte
        # to avoid reopening the file and potential sharing issues.
        last_byte = BINARY_EMPTY
        try:
            # Use cast to Any to avoid mypy confusion with internal _WrappedBuffer
            # and to handle potential Buffer return type in Python 3.12+
            buffer = cast("Any", target.buffer)
            buffer.seek(0, os.SEEK_END)
            if buffer.tell() > 0:
                buffer.seek(SEEK_BACK_ONE, os.SEEK_END)
                last_byte = bytes(buffer.read(1))
            # Re-sync TextIOWrapper after buffer manipulation
            target.seek(0, os.SEEK_END)
        except (OSError, io.UnsupportedOperation):
            # If we can't inspect the file safely, fall back to not
            # inserting a pre-append newline.
            last_byte = BINARY_EMPTY

        if last_byte not in (BINARY_EMPTY, BINARY_NEWLINE):
            target.write("\n")

        src.seek(0)
        content = src.read()

        if rotated_path.suffix in YAML_SUFFIXES:
            _append_yaml_to_rotated(content, target)
        else:
            target.write(content)

        # Ensure rotated file always ends with newline
        if not content.endswith("\n"):
            target.write("\n")


def _append_yaml_to_rotated(content: str, target: IO[str]) -> None:
    """Append YAML content with document separator and safe serialization."""
    try:
        # Use safe serializer for YAML rotation as per policy
        docs = list(yaml.safe_load_all(content))
        target.write(YAML_SEPARATOR)
        yaml.safe_dump_all(
            docs,
            target,
            default_flow_style=False,
            sort_keys=False,
        )
    except yaml.YAMLError:
        # Fallback for malformed YAML to avoid data loss
        target.write(YAML_SEPARATOR)
        target.write(content)


def find_prohibited_ruff_ignores(
    pyproject_path: Path,
    prohibited_rules: set[str] | None = None,
) -> dict[str, set[str]]:
    """Find prohibited ruff rule ignores in pyproject.toml.

    This is a shared utility used by both runner.py and validate_ruff_rules.py
    to enforce the NEVER-IGNORE policy for specific ruff rules.

    Args:
        pyproject_path: Path to pyproject.toml file
        prohibited_rules: Set of prohibited rule codes (defaults to PROHIBITED_RUFF_RULES)

    Returns:
        Dict mapping file patterns to sets of prohibited rule codes found

    """
    if prohibited_rules is None:
        prohibited_rules = PROHIBITED_RUFF_RULES

    violations: dict[str, set[str]] = {}

    try:
        with pyproject_path.open("rb") as f:
            config = tomllib.load(f)
    except OSError as e:
        raise RuffConfigParseError(pyproject_path, e) from e
    except tomllib.TOMLDecodeError as e:
        raise RuffConfigParseError(pyproject_path, e) from e

    # Navigate to tool.ruff.lint.per-file-ignores
    per_file_ignores = (
        config.get("tool", {}).get("ruff", {}).get("lint", {}).get("per-file-ignores", {})
    )

    if not per_file_ignores:
        return violations

    # Check each file pattern for prohibited rules
    for pattern, rules_list in per_file_ignores.items():
        if not isinstance(rules_list, list):
            continue

        for rule in rules_list:
            if rule in prohibited_rules:
                if pattern not in violations:
                    violations[pattern] = set()
                violations[pattern].add(rule)

    return violations
