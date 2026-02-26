"""Introspection management for fix-die-repeat runner.

This module handles PR review introspection, which analyzes real-world PR
feedback to improve future prompts.
"""

import json
import logging
import sys
import types
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import yaml

# Platform-specific file locking imports
if sys.platform == "win32":  # pragma: no cover
    import msvcrt
else:  # pragma: no cover
    import fcntl

from fix_die_repeat.config import Paths, Settings, get_introspection_file_path
from fix_die_repeat.prompts import render_prompt
from fix_die_repeat.utils import run_command

if TYPE_CHECKING:
    from typing import Self


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


class _FileLock:
    """Context manager for cross-platform file locking.

    Provides exclusive file locking to prevent concurrent writes from
    corrupting shared files like the global introspection.yaml.

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
            msvcrt.locking(self.file_handle.fileno(), msvcrt.LK_LOCK, 65535)
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
            # starting from the current file position. To unlock the same
            # region that was locked (starting at position 0), we must seek
            # back to position 0 before calling LK_UNLCK.
            self.file_handle.seek(0)
            msvcrt.locking(self.file_handle.fileno(), msvcrt.LK_UNLCK, 65535)
        else:  # pragma: no cover
            # Unix: use fcntl.flock with LOCK_UN
            fcntl.flock(self.file_handle.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True)
class IntrospectionYamlParams:
    """Parameters for building introspection YAML.

    Groups related parameters for _build_introspection_yaml to avoid
    violating PLR0913 (too many arguments).
    """

    pr_number: int | str
    pr_url: str
    in_scope_ids: list[str]
    resolved_set: set[str]
    pr_threads_content: str
    diff_content: str


@dataclass
class PrInfo:
    """PR information from GitHub (minimal for introspection)."""

    number: int
    url: str


class IntrospectionManager:
    """Manages PR review introspection for the fix-die-repeat runner.

    Handles:
    - Collecting PR thread data and agent outcomes
    - Running introspection analysis via pi
    - Appending results to global introspection file
    """

    def __init__(
        self,
        settings: Settings,
        paths: Paths,
        project_root: Path,
        logger: logging.Logger,
    ) -> None:
        """Initialize the introspection manager.

        Args:
            settings: Configuration settings
            paths: Path management
            project_root: Project root directory
            logger: Logger instance for output

        """
        self.settings = settings
        self.paths = paths
        self.project_root = project_root
        self.logger = logger

    def run_introspection(
        self,
        iteration: int,
        start_sha: str,
        run_pi_callback: Callable[..., tuple[int, str, str]],
    ) -> None:
        """Run prompt introspection analysis after PR review completion.

        This is a post-run step that:
        1. Collects PR thread data and agent outcomes
        2. Calls pi with the introspection prompt template
        3. Appends the result to the global introspection file

        This step does NOT block or fail the overall run. Any errors are
        logged as warnings and the main run result is preserved.

        Args:
            iteration: Current iteration number
            start_sha: Starting git commit SHA
            run_pi_callback: Function to run pi (from PiRunner)

        """
        self.logger.info("[Introspection] Running PR review introspection...")

        # Validate prerequisites and get PR info
        pr_info = self._validate_prerequisites_for_introspection()
        if pr_info is None:
            return

        try:
            # Collect input data
            self.collect_introspection_data(iteration, start_sha, pr_info)
            if not self.paths.introspection_data_file.exists():
                self.logger.warning(
                    "[Introspection] Failed to collect introspection data, skipping",
                )
                return

            # Render prompt and build pi command
            prompt = render_prompt(
                "introspect_pr_review.j2",
                run_date=datetime.now(tz=UTC).strftime("%Y-%m-%d"),
                project_name=self.project_root.name,
                pr_number=pr_info.number,
                pr_url=pr_info.url,
                output_path=str(self.paths.introspection_result_file),
            )
            pi_args = ["-p", "--tools", "read,write"]
            pi_args.append(f"@{self.paths.introspection_data_file}")

            # Run pi and validate result
            self.logger.info(
                "[Introspection] Calling pi to analyze PR threads...",
            )
            returncode, _stdout, _stderr = run_pi_callback(*pi_args, prompt)

            if returncode != 0:
                self.logger.warning(
                    "[Introspection] pi call failed (exit %s), skipping introspection",
                    returncode,
                )
                return

            # Validate result file and content
            result_content = self._validate_pi_result_file()
            if result_content is None:
                return

            # Validate YAML syntax
            try:
                yaml.safe_load(result_content)
            except yaml.YAMLError as e:
                self.logger.warning(
                    "[Introspection] Result is not valid YAML: %s",
                    e,
                )
                self.logger.debug(
                    "[Introspection] Invalid YAML content:\n%s",
                    result_content,
                )
                return

            # Append to global introspection file with file locking for atomicity
            global_introspection_file = get_introspection_file_path()
            separator = "\n---\n"

            if global_introspection_file.exists():
                # Open file with file lock to prevent concurrent write corruption
                with global_introspection_file.open("a") as f, _FileLock(f):
                    f.write(separator)
                    f.write(result_content)
            else:
                # For new file, write_text is atomic enough (no concurrent readers yet)
                global_introspection_file.write_text(result_content)

            self.logger.info(
                "[Introspection] Appended analysis to %s",
                global_introspection_file,
            )

        except Exception:
            self.logger.exception(
                "[Introspection] Unexpected error during introspection (non-blocking)",
            )
        finally:
            # Clean up temporary files
            self.paths.introspection_data_file.unlink(missing_ok=True)
            self.paths.introspection_result_file.unlink(missing_ok=True)

    def _validate_prerequisites_for_introspection(self) -> PrInfo | None:
        """Validate introspection prerequisites and get PR info.

        Returns:
            PrInfo if valid, None if introspection should skip

        """
        # Skip if no PR threads were processed (check cumulative file)
        if not self.paths.cumulative_in_scope_threads_file.exists():
            self.logger.info(
                "[Introspection] No PR threads were processed, skipping introspection",
            )
            return None

        # Get PR info - use a simple branch check
        returncode, branch, _ = run_command(
            "git branch --show-current",
            cwd=self.project_root,
        )
        if returncode != 0 or not branch.strip():
            self.logger.warning(
                "[Introspection] Not on a git branch, skipping introspection",
            )
            return None

        # Get PR info
        returncode, pr_json, _ = run_command(
            f"gh pr view {branch.strip()} --json number,url",
            cwd=self.project_root,
        )
        if returncode != 0:
            self.logger.warning(
                "[Introspection] No PR info available, skipping introspection",
            )
            return None

        try:
            pr_data = json.loads(pr_json)
            return PrInfo(
                number=pr_data.get("number"),
                url=pr_data.get("url"),
            )
        except (json.JSONDecodeError, KeyError):
            self.logger.warning("[Introspection] Failed to parse PR info")
            return None

    def _validate_pi_result_file(self) -> str | None:
        """Validate that pi created a valid result file.

        Returns:
            Result content if valid, None otherwise

        """
        # Check if result file was created
        if not self.paths.introspection_result_file.exists():
            self.logger.warning(
                "[Introspection] pi did not create result file, skipping",
            )
            return None

        # Read and validate result
        result_content = self.paths.introspection_result_file.read_text()
        if not result_content.strip():
            self.logger.warning(
                "[Introspection] Result file is empty, skipping append",
            )
            return None

        return result_content

    def collect_introspection_data(self, _iteration: int, start_sha: str, pr_info: PrInfo) -> None:
        """Collect input data for introspection analysis.

        Gathers PR thread comments, fix/won't-fix outcomes, and the diff
        of changes made by the agent during PR review mode.

        Writes the collected data to `self.paths.introspection_data_file`
        as YAML that the introspection LLM prompt can reference.

        Args:
            iteration: Current iteration number (unused, kept for compatibility)
            start_sha: Starting git commit SHA
            pr_info: PR information

        """
        self.logger.info("Collecting introspection data...")

        # Collect thread IDs and outcomes
        in_scope_ids, resolved_set = self._collect_thread_ids()
        pr_number = pr_info.number
        pr_url = pr_info.url

        # Read cached data
        pr_threads_content = self._read_pr_threads_cache()
        diff_content = self._read_diff_content(start_sha)

        # Build and write YAML structure
        yaml_params = IntrospectionYamlParams(
            pr_number=pr_number,
            pr_url=pr_url,
            in_scope_ids=in_scope_ids,
            resolved_set=resolved_set,
            pr_threads_content=pr_threads_content,
            diff_content=diff_content,
        )
        yaml_content = self._build_introspection_yaml(yaml_params)
        self.paths.introspection_data_file.write_text(yaml_content)
        self.logger.info(
            "Collected introspection data to %s",
            self.paths.introspection_data_file,
        )

    def _collect_thread_ids(self) -> tuple[list[str], set[str]]:
        """Collect in-scope and resolved thread IDs.

        Reads from cumulative files that persist across iterations,
        not the per-iteration files that may be deleted.

        Returns:
            Tuple of (in_scope_ids list, resolved_set)

        """
        # Read in-scope thread IDs from cumulative file
        in_scope_ids = []
        if self.paths.cumulative_in_scope_threads_file.exists():
            cumulative_content = self.paths.cumulative_in_scope_threads_file.read_text().strip()
            in_scope_ids = [thread_id for thread_id in cumulative_content.split("\n") if thread_id]

        # Read resolved thread IDs from cumulative file
        resolved_ids = []
        if self.paths.cumulative_resolved_threads_file.exists():
            resolved_content = self.paths.cumulative_resolved_threads_file.read_text().strip()
            resolved_ids = [thread_id for thread_id in resolved_content.split("\n") if thread_id]

        return in_scope_ids, set(resolved_ids)

    def _read_pr_threads_cache(self) -> str:
        """Read PR threads cache content from cumulative file.

        Reads from the cumulative file that contains all PR threads
        fetched across all iterations, not the per-iteration cache
        that is overwritten.

        Returns:
            PR threads cache content or empty string

        """
        if self.paths.cumulative_pr_threads_content_file.exists():
            return self.paths.cumulative_pr_threads_content_file.read_text()
        return ""

    def _read_diff_content(self, start_sha: str) -> str:
        """Read diff content from file or git.

        Args:
            start_sha: Starting git commit SHA

        Returns:
            Diff content or empty string

        """
        # Try to read from diff file
        if self.paths.diff_file.exists():
            return self.paths.diff_file.read_text()

        # Fall back to git diff if start_sha is available
        if not start_sha:
            return ""

        returncode, stdout, _ = run_command(
            f"git diff {start_sha}",
            cwd=self.project_root,
            check=False,
        )
        return stdout if returncode == 0 else ""

    def _build_introspection_yaml(self, params: IntrospectionYamlParams) -> str:
        """Build YAML structure for introspection data.

        Args:
            params: IntrospectionYamlParams with all required data

        Returns:
            YAML content as string

        """
        lines = [
            "# Introspection input data for PR review",
        ]

        # Use yaml.safe_dump for proper escaping and quoting
        header_data = {
            "pr_number": params.pr_number,
            "pr_url": params.pr_url,
            "in_scope_thread_ids": [
                {
                    "id": thread_id,
                    "outcome": ("fixed" if thread_id in params.resolved_set else "wont-fix"),
                }
                for thread_id in params.in_scope_ids
            ],
        }
        lines.append(yaml.safe_dump(header_data, default_flow_style=False, sort_keys=False).strip())

        # Add PR threads content
        lines.extend(
            [
                "",
                "# PR threads (from cache)",
                "pr_threads: |",
            ]
        )
        lines.extend([f"  {line}" for line in params.pr_threads_content.splitlines()])

        # Add diff content
        lines.extend(
            [
                "",
                "# Diff of changes made by agent",
                "changes_diff: |",
            ]
        )
        lines.extend([f"  {line}" for line in params.diff_content.splitlines()])

        return "\n".join(lines)
