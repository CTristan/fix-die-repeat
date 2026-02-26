"""Main runner for fix-die-repeat."""

import json
import re
import shlex
import sys
import time
import types
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

if TYPE_CHECKING:
    from typing import Self

from fix_die_repeat.config import Paths, Settings, get_introspection_file_path
from fix_die_repeat.messages import (
    git_checkout_instructions,
    git_diff_instructions,
    model_recommendations_full,
    oscillation_warning,
    pr_threads_safe_only_message,
    pr_threads_unsafe_count_warning,
)
from fix_die_repeat.prompts import render_prompt
from fix_die_repeat.utils import (
    PROHIBITED_RUFF_RULES,
    RuffConfigParseError,
    configure_logger,
    detect_large_files,
    find_prohibited_ruff_ignores,
    format_duration,
    get_changed_files,
    get_file_line_count,
    get_file_size,
    get_git_revision_hash,
    is_excluded_file,
    play_completion_sound,
    run_command,
    send_ntfy_notification,
)


@runtime_checkable
class _FileHandle(Protocol):
    """Protocol for objects that support file descriptor access.

    Used for type-checking the file lock context manager.
    """

    def fileno(self) -> int:
        """Return the file descriptor for the file handle."""
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


class PiRunner:
    """Runner that orchestrates the fix-die-repeat loop."""

    def __init__(self, settings: Settings, paths: Paths) -> None:
        """Initialize the runner.

        Args:
            settings: Configuration settings
            paths: Path management

        """
        self.settings = settings
        self.paths = paths
        self.iteration = 0
        self.pi_invocation_count = 0
        self.script_start_time: float = 0.0
        self.start_sha = ""
        self.pr_review_no_progress_count = 0
        self.last_review_current_hash = ""
        self.last_git_state = ""
        self.consecutive_toolless_attempts = 0
        self._success_complete = False

        # Ensure .fix-die-repeat directory exists before creating logger
        self.paths.ensure_fdr_dir()

        # Determine session log path
        if self.settings.debug:
            session_timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
            self.session_log = self.paths.fdr_dir / f"session_{session_timestamp}.log"
        else:
            self.session_log = self.paths.fdr_dir / "session.log"

        # Initialize logger
        self.logger = configure_logger(
            fdr_log=self.paths.fdr_log,
            session_log=self.session_log,
            debug=self.settings.debug,
        )

    def before_pi_call(self) -> None:
        """Add delay between sequential pi calls to reduce lock contention."""
        if self.pi_invocation_count > 0:
            time.sleep(self.settings.pi_sequential_delay_seconds)
        self.pi_invocation_count += 1

    def run_pi(self, *args: str) -> tuple[int, str, str]:
        """Run pi command with logging.

        Args:
            *args: Arguments to pass to pi

        Returns:
            Tuple of (exit_code, stdout, stderr)

        """
        self.before_pi_call()

        cmd_args = ["pi", *args]
        returncode, stdout, stderr = run_command(cmd_args, cwd=self.paths.project_root)

        # Log output
        if self.paths.pi_log:
            with self.paths.pi_log.open("a", encoding="utf-8") as f:
                f.write(f"Command: {shlex.join(cmd_args)}\n")
                f.write(f"Exit code: {returncode}\n")
                if stdout:
                    f.write(f"STDOUT:\n{stdout}\n")
                if stderr:
                    f.write(f"STDERR:\n{stderr}\n")
                f.write("\n")

        if returncode != 0:
            self.logger.error("pi exited with code %s", returncode)
            if self.paths.pi_log:
                self.logger.error("pi output logged to: %s", self.paths.pi_log)

        return (returncode, stdout, stderr)

    def run_pi_safe(self, *args: str) -> tuple[int, str, str]:
        """Run pi with single retry on failure.

        Args:
            *args: Arguments to pass to pi

        Returns:
            Tuple of (exit_code, stdout, stderr)

        """
        returncode, stdout, stderr = self.run_pi(*args)

        if returncode == 0:
            return (returncode, stdout, stderr)

        # Detect capacity error (503)
        if self.paths.pi_log and self.paths.pi_log.exists():
            content = self.paths.pi_log.read_text()
            if "503" in content or "No capacity" in content:
                self.logger.info("Detected model capacity error (503). Skipping current model...")
                self.run_pi("-p", "/model-skip")  # Trigger model skip

        # Detect long context error (429)
        if self.paths.pi_log and self.paths.pi_log.exists():
            content = self.paths.pi_log.read_text()
            if "429" in content.lower() and "long context" in content.lower():
                self.logger.info(
                    "Detected long context rate limit (429). Forcing emergency compaction...",
                )
                self.emergency_compact()
                self.logger.info("Emergency compaction complete. Retrying...")

        self.logger.info("pi failed (exit %s). Retrying once...", returncode)
        return self.run_pi(*args)

    def emergency_compact(self) -> None:
        """Force emergency truncation of artifacts."""
        for f in [self.paths.review_file, self.paths.build_history_file]:
            if f.exists():
                lines = f.read_text().splitlines()[-100:]
                f.write_text("\n".join(lines))

    def filter_checks_log(self) -> None:
        """Filter checks.log to extract the most useful failure information."""
        max_lines = 300
        context_lines = 3
        tail_lines = 80

        if not self.paths.checks_log.exists():
            return

        total_lines = get_file_line_count(self.paths.checks_log)

        if total_lines <= max_lines:
            # Log is small enough, just copy it
            self.paths.checks_filtered_log.write_text(self.paths.checks_log.read_text())
            return

        self.logger.info(
            "Filtering checks.log (%s lines -> ~%s target)...",
            total_lines,
            max_lines,
        )

        # Extract error lines with context
        error_pattern = re.compile(
            r"(error[:\[ ]|ERROR[:\[ ]|fatal|FATAL|FAILED|panic|exception|undefined reference|"
            r"cannot find|no such file|not found|segfault|abort|compilation failed|build failed|"
            r"assert)",
            re.IGNORECASE,
        )

        lines = self.paths.checks_log.read_text().splitlines()
        filtered_lines = [
            "=== FILTERED CHECK OUTPUT (full log: .fix-die-repeat/checks.log, "
            f"{total_lines} lines) ===",
            "",
            "--- Error/failure lines with context ---",
        ]

        seen_indices = set()
        for i, line in enumerate(lines):
            if error_pattern.search(line):
                start = max(0, i - context_lines)
                end = min(len(lines), i + context_lines + 1)
                for j in range(start, end):
                    if j not in seen_indices:
                        filtered_lines.append(lines[j])
                        seen_indices.add(j)

        # Add tail
        filtered_lines.extend(["", f"--- Last {tail_lines} lines ---"])
        filtered_lines.extend(lines[-tail_lines:])

        # Limit to 200 error lines + tail
        if len(filtered_lines) > max_lines:
            filtered_lines = filtered_lines[: max_lines - tail_lines] + lines[-tail_lines:]

        self.paths.checks_filtered_log.write_text("\n".join(filtered_lines))

        filtered_count = get_file_line_count(self.paths.checks_filtered_log)
        self.logger.info("Filtered checks.log: %s -> %s lines", total_lines, filtered_count)

    def check_oscillation(self) -> str | None:
        """Check for oscillation by tracking check output hashes.

        Returns:
            Warning message if oscillation detected, None otherwise

        """
        current_hash = get_git_revision_hash(self.paths.checks_log)

        if self.paths.checks_hash_file.exists():
            hashes = self.paths.checks_hash_file.read_text().strip().split("\n")
            for entry in reversed(hashes):
                if entry.startswith(f"{current_hash}:"):
                    prev_iter = int(entry.split(":")[-1])
                    self.logger.info(
                        "Detected oscillation: iteration %s matches iteration %s",
                        self.iteration,
                        prev_iter,
                    )
                    warning = oscillation_warning(prev_iter)
                    # Record this hash
                    with self.paths.checks_hash_file.open("a") as f:
                        f.write(f"{current_hash}:{self.iteration}\n")
                    return warning

        # Record this hash
        with self.paths.checks_hash_file.open("a") as f:
            f.write(f"{current_hash}:{self.iteration}\n")

        return None

    def check_compaction_needed(self) -> tuple[bool, bool]:
        """Check if artifacts need compaction.

        Returns:
            Tuple of (needs_emergency, needs_compact)

        """
        needs_compact = False
        needs_emergency = False

        for f in [self.paths.review_file, self.paths.build_history_file]:
            if f.exists():
                line_count = get_file_line_count(f)
                if line_count > self.settings.emergency_threshold_lines:
                    needs_emergency = True
                    break
                if line_count > self.settings.compact_threshold_lines:
                    needs_compact = True

        return needs_emergency, needs_compact

    def perform_emergency_compaction(self) -> None:
        """Perform emergency compaction (truncate to 100 lines)."""
        self.logger.info(
            "Emergency: artifacts exceed %s lines. Truncating to last 100 lines...",
            self.settings.emergency_threshold_lines,
        )
        for f in [self.paths.review_file, self.paths.build_history_file]:
            if f.exists():
                lines = f.read_text().splitlines()[-100:]
                f.write_text("\n".join(lines))

    def perform_regular_compaction(self) -> None:
        """Perform regular compaction (truncate to 50 lines)."""
        self.logger.info(
            "Artifacts exceed %s lines. Compacting with pi...",
            self.settings.compact_threshold_lines,
        )

        # Use simple truncation; pi-based compaction needs a dedicated prompt flow.
        for f in [self.paths.review_file, self.paths.build_history_file]:
            if f.exists():
                before = get_file_line_count(f)
                lines = f.read_text().splitlines()[-50:]
                f.write_text("\n".join(lines))
                after = get_file_line_count(f)
                self.logger.info("Compacted %s from %s to %s lines", f.name, before, after)

    def check_and_compact_artifacts(self) -> bool:
        """Check and compact large persistent artifacts.

        Returns:
            True if compaction was performed, False otherwise

        """
        if not self.settings.compact_artifacts:
            return False

        needs_emergency, needs_compact = self.check_compaction_needed()

        if needs_emergency:
            self.perform_emergency_compaction()
            return True

        if needs_compact:
            self.perform_regular_compaction()
            return True

        return False

    def test_model(self) -> None:
        """Test model compatibility before running full loop."""
        if not self.settings.test_model:
            self.logger.info("No --test-model specified, skipping test.")
            return

        test_file = self.paths.fdr_dir / ".model_test_result.txt"

        self.logger.info(
            "===== Testing model compatibility: %s =====",
            self.settings.test_model,
        )
        self.logger.info("Running simple write test to verify model can use pi's tools...")

        # Create test prompt
        self.before_pi_call()
        returncode, _stdout, _stderr = run_command(
            [
                "pi",
                "-p",
                "--model",
                self.settings.test_model,
                (
                    f"Write 'MODEL TEST OK' to file {test_file}. "
                    "Do NOT use any other tools or generate pseudo-code."
                ),
            ],
            cwd=self.paths.project_root,
        )

        if returncode != 0:
            self.logger.error("pi test invocation failed with code %s", returncode)
            self.logger.error(
                "Model %s failed basic invocation test.",
                self.settings.test_model,
            )
            test_file.unlink(missing_ok=True)
            sys.exit(1)

        # Check if model wrote the expected output
        if test_file.exists() and "MODEL TEST OK" in test_file.read_text():
            self.logger.info("Model %s PASSED tool test.", self.settings.test_model)
            self.logger.info("Test output: %s", test_file.read_text().strip())
            test_file.unlink(missing_ok=True)

            self.logger.info("Model is compatible for code editing. Ready to proceed.")
            self.logger.info("")
            self.logger.info(
                "To run with this model: fix-die-repeat --model %s",
                self.settings.test_model,
            )
            self.logger.info(
                "Or set via env var: export FDR_MODEL=%s",
                self.settings.test_model,
            )
            sys.exit(0)
        else:
            test_output = test_file.read_text() if test_file.exists() else "(empty)"
            self.logger.info("Model %s FAILED tool test.", self.settings.test_model)
            self.logger.info("Test output: %s", test_output)

            # Check for pseudo-code
            if re.search(
                r"print|IO\.puts|System\.cmd|File\.write|defmodule",
                test_output,
                re.IGNORECASE,
            ):
                self.logger.warning(
                    "WARNING: Model generated pseudo-code instead of using pi's tools.",
                )
                self.logger.warning(
                    "This model appears incompatible with pi's tool-calling interface.",
                )

            test_file.unlink(missing_ok=True)

            self.logger.info(
                "Model %s is NOT suitable for code editing tasks.",
                self.settings.test_model,
            )
            self.logger.info("")
            self.logger.info(model_recommendations_full())
            self.logger.info("")
            self.logger.info("To override: fix-die-repeat --model <model>")
            sys.exit(1)

    def run_checks(self) -> tuple[int, str]:
        """Run the check command.

        Returns:
            Tuple of (exit_code, output)

        """
        check_cmd = self.settings.check_cmd
        if check_cmd is None:
            msg = (
                "Settings.check_cmd is None. This likely indicates PiRunner was "
                "constructed or used without running the CLI validation that sets "
                "a check command."
            )
            raise RuntimeError(msg)
        self.logger.info("[Step 1] Running %s (output: checks.log)...", check_cmd)

        start_time = time.time()
        returncode, stdout, stderr = run_command(
            check_cmd,
            cwd=self.paths.project_root,
        )

        # Write output to checks log
        output = f"{stdout}{stderr}"
        self.paths.checks_log.write_text(output)

        end_time = time.time()
        duration = int(end_time - start_time)
        self.logger.info("[Step 1] run_checks duration: %s", format_duration(duration))

        return (returncode, output)

    def setup_run(self) -> None:
        """Initialize run environment."""
        self.script_start_time = time.time()

        # Setup paths
        self.paths.ensure_fdr_dir()

        # Archive artifacts if requested
        if self.settings.archive_artifacts:
            archive_timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
            archive_dir = self.paths.fdr_dir / "archive" / archive_timestamp
            self.logger.info("Archiving existing artifacts to %s", archive_dir)
            archive_dir.mkdir(parents=True, exist_ok=True)
            for file_path in self.paths.fdr_dir.glob("*"):
                if file_path.is_file():
                    file_path.rename(archive_dir / file_path.name)

        # Initialize logs
        self.paths.pi_log.write_text("")
        self.session_log.write_text("")
        self.paths.checks_hash_file.write_text("")

        # Reset cumulative tracking files for introspection (fresh for each run)
        self.paths.cumulative_in_scope_threads_file.unlink(missing_ok=True)
        self.paths.cumulative_resolved_threads_file.unlink(missing_ok=True)
        self.paths.cumulative_pr_threads_content_file.unlink(missing_ok=True)
        self.logger.info("Logging full session output to: %s", self.session_log)

        # Record starting commit SHA
        returncode, stdout, _ = run_command(
            "git rev-parse HEAD",
            cwd=self.paths.project_root,
            check=False,
        )
        if returncode == 0:
            self.start_sha = stdout.strip()
            self.paths.start_sha_file.write_text(self.start_sha)
            self.logger.info("Git checkpoint: %s", self.start_sha)

        # Test model if requested
        self.test_model()

        # Compact large artifacts from previous runs
        self.check_and_compact_artifacts()

    def run_fix_attempt(
        self,
        fix_attempt: int,
        changed_files: list[str],
        context_mode: str,
        large_context_list: str,
        large_file_warning: str,
    ) -> int:
        """Run a single fix attempt.

        Args:
            fix_attempt: Current attempt number
            changed_files: List of changed files
            context_mode: Either "push" or "pull"
            large_context_list: Formatted list of large files for pull mode
            large_file_warning: Warning message for large files

        Returns:
            Exit code from pi command

        """
        oscillation_warning = self.check_oscillation()

        self.logger.info(
            "[Step 2A] Checks failed (fix attempt %s/%s). Running pi to fix errors...",
            fix_attempt,
            self.settings.max_iters,
        )

        # Filter checks log
        self.filter_checks_log()

        # Build pi command
        pi_args = ["-p", "--tools", "read,edit,write,bash,grep,find,ls"]
        pi_args.append(f"@{self.paths.checks_filtered_log}")

        # Add historical context
        if self.paths.review_file.exists():
            pi_args.append(f"@{self.paths.review_file}")
        if self.paths.build_history_file.exists():
            pi_args.append(f"@{self.paths.build_history_file}")

        # Attach changed files in push mode
        if context_mode == "push":
            pi_args.extend(f"@{filepath}" for filepath in changed_files)

        prompt = render_prompt(
            "fix_checks.j2",
            check_cmd=self.settings.check_cmd,
            oscillation_warning=oscillation_warning,
            include_review_history=self.paths.review_file.exists(),
            include_build_history=self.paths.build_history_file.exists(),
            context_mode=context_mode,
            large_context_list=large_context_list,
            large_file_warning=large_file_warning,
        )

        self.logger.info("Running pi to fix errors (attempt %s)...", fix_attempt)
        pi_returncode, _, _ = self.run_pi_safe(*pi_args, prompt)

        if pi_returncode != 0:
            self.logger.info("pi could not produce a fix on attempt %s.", fix_attempt)

        # Check if changes were made
        _git_returncode, stdout, _ = run_command(
            "git status --porcelain",
            cwd=self.paths.project_root,
            check=False,
        )
        if not stdout.strip():
            self.logger.error(
                "Pi reported success but NO files were modified. This suggests "
                "'edit' commands failed (e.g., text not found).",
            )
            with self.paths.build_history_file.open("a") as f:
                f.write(
                    f"## Iteration {self.iteration} fix attempt {fix_attempt}: "
                    "FAILED to apply fixes (no files changed)\n\n",
                )
        else:
            # Record history
            _git_returncode, stat_output, _ = run_command(
                "git diff --stat",
                cwd=self.paths.project_root,
                check=False,
            )
            with self.paths.build_history_file.open("a") as f:
                f.write(f"## Iteration {self.iteration} fix attempt {fix_attempt}\n")
                f.write(f"{stat_output}\n\n")

        return pi_returncode

    def prepare_fix_context(self) -> tuple[list[str], str, str, str]:
        """Prepare context for fix attempts.

        Returns:
            Tuple of (changed_files, context_mode, large_context_list, large_file_warning)

        """
        # Get changed files
        changed_files = get_changed_files(self.paths.project_root)

        if not changed_files:
            self.logger.info("No changed files found.")
        else:
            self.logger.info("Found %s changed file(s)", len(changed_files))

        # Check for large files
        large_file_warning = ""
        if changed_files:
            large_file_warning = detect_large_files(
                changed_files,
                self.paths.project_root,
                self.settings.large_file_lines,
            )

        # Calculate context size
        changed_size = 0
        for filepath in changed_files:
            changed_size += get_file_size(self.paths.project_root / filepath)

        context_mode = "push"
        large_context_list = ""

        if changed_size > self.settings.auto_attach_threshold:
            context_mode = "pull"
            self.logger.info(
                "Context size (%s bytes) exceeds threshold (%s). Switching to PULL mode.",
                changed_size,
                self.settings.auto_attach_threshold,
            )
            large_context_lines = [
                "The following files have changed but are too large to pre-load "
                f"automatically ({changed_size} bytes total). You MUST use the "
                "'read' tool to inspect the ones relevant to the error:",
            ]
            large_context_lines.extend(f"- {filepath}" for filepath in changed_files)
            large_context_list = "\n".join(large_context_lines)
        else:
            self.logger.info(
                "Context size (%s bytes) is within limits. Pushing file contents to prompt.",
                changed_size,
            )

        return changed_files, context_mode, large_context_list, large_file_warning

    def run_fix_loop(self) -> int:
        """Run the inner fix loop until checks pass or max attempts reached.

        Returns:
            Exit code (0 for success, non-zero for failure)

        """
        # Step 1: Run checks
        checks_status, _ = self.run_checks()

        # Step 2: Inner fix loop - if checks failed, keep fixing
        fix_attempt = 0
        while checks_status != 0:
            fix_attempt += 1

            if fix_attempt > self.settings.max_iters:
                self.logger.error(
                    "Maximum fix attempts (%s) exhausted. Could not resolve check failures.",
                    self.settings.max_iters,
                )
                if self.start_sha:
                    self.logger.error(git_diff_instructions(self.start_sha))
                    self.logger.error(git_checkout_instructions(self.start_sha))
                return 1

            # Prepare context
            changed_files, context_mode, large_context_list, large_file_warning = (
                self.prepare_fix_context()
            )

            # Run fix attempt
            self.run_fix_attempt(
                fix_attempt,
                changed_files,
                context_mode,
                large_context_list,
                large_file_warning,
            )

            # Re-run checks
            self.logger.info(
                "[Step 2A] Re-running %s after fix attempt %s...",
                self.settings.check_cmd,
                fix_attempt,
            )
            checks_status, _ = self.run_checks()

        self.logger.info("[Step 2B] Checks passed. Proceeding to review.")
        return 0

    def run_review_phase(self, changed_files: list[str]) -> None:
        """Run the review phase.

        Args:
            changed_files: List of changed files

        """
        # Step 3: Prepare review artifacts
        self.logger.info("[Step 3] Preparing review artifacts...")
        self.paths.review_current_file.unlink(missing_ok=True)

        # Step 3.5: Check PR threads if enabled
        if self.settings.pr_review:
            self.fetch_pr_threads()

        # Step 4: Collect files for review
        if (
            not self.paths.review_current_file.exists()
            or not self.paths.review_current_file.read_text()
        ):
            # Skip local review if we have PR threads to process
            self.run_local_review(changed_files)
        else:
            self.logger.info(
                "[Step 4] Using PR threads from %s for review.",
                self.paths.review_current_file,
            )
            self.logger.info("[Step 5] Skipping local file review generation.")

        # Step 6: Process review results
        self.process_review_results()

    def run(self) -> int:
        """Run the main fix-die-repeat loop.

        Returns:
            Exit code (0 for success, non-zero for failure)

        """
        self.setup_run()

        while True:
            self.iteration += 1
            self.logger.info(
                "===== Iteration %s of %s =====",
                self.iteration,
                self.settings.max_iters,
            )

            self.check_and_compact_artifacts()

            if self.iteration > self.settings.max_iters:
                return self._handle_max_iterations_exceeded()

            exit_code = self.run_fix_loop()
            if exit_code != 0:
                return self._handle_fix_loop_failure(exit_code)

            changed_files = get_changed_files(self.paths.project_root)
            self.run_review_phase(changed_files)

            if self._success_complete:
                self._run_post_run_introspection()
                self.complete_success()
                return 0

        return 0

    def _run_post_run_introspection(self) -> None:
        """Run post-run introspection if enabled.

        Introspection is a non-blocking operation. Errors are logged but do not
        affect the main flow.

        """
        if self.settings.pr_review_introspect:
            try:
                self.run_introspection()
            except Exception:
                self.logger.exception(
                    "Introspection step failed (non-blocking)",
                )

    def _handle_max_iterations_exceeded(self) -> int:
        """Handle the case when maximum iterations is exceeded.

        Returns:
            Exit code 1 (failure)

        """
        self.logger.error(
            "Maximum iterations (%s) exceeded. Could not resolve all issues.",
            self.settings.max_iters,
        )
        if self.start_sha:
            self.logger.error(git_diff_instructions(self.start_sha))
            self.logger.error(git_checkout_instructions(self.start_sha))
        self._run_post_run_introspection()
        return 1

    def _handle_fix_loop_failure(self, exit_code: int) -> int:
        """Handle fix loop failure.

        Args:
            exit_code: Exit code from the fix loop

        Returns:
            The provided exit code

        """
        self._run_post_run_introspection()
        return exit_code

    def get_branch_name(self) -> str | None:
        """Get the current git branch name.

        Returns:
            Branch name or None if not on a branch

        """
        returncode, branch, _ = run_command(
            "git branch --show-current",
            cwd=self.paths.project_root,
        )
        if returncode != 0 or not branch.strip():
            return None
        return branch.strip()

    def get_pr_info(self, branch: str) -> dict | None:
        """Get PR information for a branch.

        Args:
            branch: Branch name

        Returns:
            PR data dict or None if not found

        """
        returncode, pr_json, _ = run_command(
            f"gh pr view {branch} --json number,url,headRepository,headRepositoryOwner",
            cwd=self.paths.project_root,
        )
        if returncode != 0:
            return None

        pr_data = json.loads(pr_json)
        return {
            "number": pr_data.get("number"),
            "url": pr_data.get("url"),
            "repo_owner": pr_data["headRepositoryOwner"]["login"],
            "repo_name": pr_data["headRepository"]["name"],
        }

    def check_pr_threads_cache(self, cache_key: str) -> bool:
        """Check if cached PR threads are valid and use them.

        Args:
            cache_key: Cache key to validate

        Returns:
            True if cache was used, False otherwise

        """
        has_valid_cache = (
            self.paths.pr_threads_cache.exists()
            and self.paths.pr_threads_hash_file.exists()
            and self.paths.pr_threads_hash_file.read_text() == cache_key
        )
        if not has_valid_cache:
            return False

        if not self.paths.pr_thread_ids_file.exists():
            self.logger.warning(
                "PR thread cache exists but in-scope ID file is missing. Refetching threads.",
            )
            return False

        in_scope_ids = [
            thread_id.strip()
            for thread_id in self.paths.pr_thread_ids_file.read_text().splitlines()
            if thread_id.strip()
        ]
        if not in_scope_ids:
            self.logger.warning(
                "PR thread cache exists but in-scope ID file is empty. Refetching threads.",
            )
            return False

        self.logger.info("Using cached PR threads (unchanged)...")
        cached_content = self.paths.pr_threads_cache.read_text()
        self.paths.review_current_file.write_text(cached_content)
        self._persist_in_scope_thread_ids(in_scope_ids)
        self.logger.info("Found %s unresolved threads from cache.", len(in_scope_ids))
        return True

    @staticmethod
    def _extract_thread_ids(threads: list[dict]) -> list[str]:
        """Extract GraphQL thread IDs from thread dictionaries.

        Args:
            threads: Thread dictionaries

        Returns:
            Thread IDs in list order

        """
        return [str(thread["id"]) for thread in threads if thread.get("id")]

    def _persist_in_scope_thread_ids(self, thread_ids: list[str]) -> None:
        """Persist in-scope PR thread IDs for safe resolution.

        Args:
            thread_ids: Thread IDs to persist

        """
        unique_ids = list(dict.fromkeys(thread_id for thread_id in thread_ids if thread_id))

        if unique_ids:
            self.paths.pr_thread_ids_file.write_text("\n".join(unique_ids) + "\n")

            # Also append to cumulative file for introspection
            existing_cumulative: set[str] = set()
            if self.paths.cumulative_in_scope_threads_file.exists():
                existing_cumulative = {
                    line.strip()
                    for line in self.paths.cumulative_in_scope_threads_file.read_text().splitlines()
                    if line.strip()
                }
            new_ids = set(unique_ids) - existing_cumulative
            if new_ids:
                with self.paths.cumulative_in_scope_threads_file.open("a") as f:
                    for thread_id in sorted(new_ids):
                        f.write(f"{thread_id}\n")
            return

        self.paths.pr_thread_ids_file.unlink(missing_ok=True)

    @staticmethod
    def _latest_thread_comment_timestamp(thread: dict) -> str:
        """Get the latest comment timestamp available for a review thread.

        Args:
            thread: Review thread payload from GraphQL

        Returns:
            Latest ``createdAt`` timestamp as an ISO string, or empty string

        """
        comments = thread.get("comments")
        if not isinstance(comments, dict):
            return ""

        nodes = comments.get("nodes")
        if not isinstance(nodes, list):
            return ""

        timestamps = [
            str(node.get("createdAt"))
            for node in nodes
            if isinstance(node, dict) and node.get("createdAt")
        ]
        if not timestamps:
            return ""

        return max(timestamps)

    def _limit_unresolved_threads(self, unresolved_threads: list[dict]) -> list[dict]:
        """Limit unresolved PR threads based on settings.max_pr_threads.

        Args:
            unresolved_threads: All unresolved PR threads

        Returns:
            Limited unresolved threads suitable for the prompt

        """
        total_unresolved = len(unresolved_threads)
        max_threads = self.settings.max_pr_threads

        if not isinstance(max_threads, int) or max_threads <= 0 or total_unresolved <= max_threads:
            return unresolved_threads

        unresolved_threads = sorted(
            unresolved_threads,
            key=lambda thread: (
                self._latest_thread_comment_timestamp(thread),
                str(thread.get("id") or ""),
            ),
            reverse=True,
        )
        limited_threads = unresolved_threads[:max_threads]
        skipped_threads = unresolved_threads[max_threads:]
        skipped_ids = self._extract_thread_ids(skipped_threads)

        self.logger.warning(
            pr_threads_unsafe_count_warning(len(skipped_threads), skipped_ids),
        )
        self.logger.info(pr_threads_safe_only_message(len(limited_threads)))

        return limited_threads

    def fetch_pr_threads_gql(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
    ) -> list[dict] | None:
        """Fetch PR threads via GraphQL.

        Args:
            repo_owner: Repository owner
            repo_name: Repository name
            pr_number: PR number

        Returns:
            Thread data list or None on failure

        """
        query = """
            query($owner: String!, $repo: String!, $number: Int!) {
                repository(owner: $owner, name: $repo) {
                    pullRequest(number: $number) {
                        reviewThreads(first: 100) {
                            nodes {
                                isResolved
                                id
                                path
                                line
                                comments(last: 10) {
                                    nodes {
                                        author { login }
                                        body
                                        createdAt
                                    }
                                }
                            }
                        }
                    }
                }
            }
        """

        query_single_line = " ".join(line.strip() for line in query.splitlines() if line.strip())
        cmd = [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={query_single_line}",
            "-F",
            f"owner={repo_owner}",
            "-F",
            f"repo={repo_name}",
            "-F",
            f"number={pr_number}",
        ]

        returncode, gql_result, _ = run_command(cmd, cwd=self.paths.project_root)
        if returncode != 0:
            return None

        try:
            data = json.loads(gql_result)
            return data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
        except (json.JSONDecodeError, KeyError):
            self.logger.exception("Failed to parse PR thread data")
            return None

    @staticmethod
    def _format_thread_comment(author: str, body: str) -> list[str]:
        """Format a thread comment without exposing parser-sensitive prefixes.

        Args:
            author: Comment author login
            body: Raw comment body

        Returns:
            Formatted comment lines safe to embed in cached markdown

        """
        comment_lines = body.splitlines() or [""]
        formatted_lines = [f"[{author}]: {comment_lines[0]}"]
        formatted_lines.extend(f"    {line}" for line in comment_lines[1:])
        return formatted_lines

    def format_pr_threads(self, threads: list, pr_number: int, pr_url: str) -> str:
        """Format PR threads for display.

        Args:
            threads: List of thread data
            pr_number: PR number
            pr_url: PR URL

        Returns:
            Formatted thread content

        """
        threads_output = []
        for i, thread in enumerate(threads, 1):
            threads_output.append(f"--- Thread #{i} ---")
            threads_output.append(f"ID: {thread['id']}")
            threads_output.append(f"File: {thread.get('path', 'N/A')}")
            if thread.get("line"):
                threads_output.append(f"Line: {thread['line']}")

            for comment in thread["comments"]["nodes"]:
                author = comment["author"]["login"] if comment.get("author") else "unknown"
                body = str(comment.get("body") or "")
                threads_output.extend(self._format_thread_comment(author, body))

            threads_output.append("")

        header = render_prompt(
            "pr_threads_header.j2",
            unresolved_count=len(threads),
            pr_number=pr_number,
            pr_url=pr_url,
        )

        return f"{header}\n\n" + "\n".join(threads_output)

    def fetch_pr_threads(self) -> None:
        """Fetch PR threads for PR review mode."""
        self.logger.info("[Step 3.5] Checking for unresolved PR threads...")

        branch = self.get_branch_name()
        if not branch:
            self.logger.error("Not on a git branch. Skipping PR review.")
            return

        self.logger.info("Fetching PR info for branch: %s", branch)

        # Check gh auth
        returncode, _, _ = run_command("gh auth status", cwd=self.paths.project_root)
        if returncode != 0:
            self.logger.error("GitHub CLI not authenticated. Skipping PR review.")
            return

        pr_info = self.get_pr_info(branch)
        if not pr_info:
            self.logger.info(
                "No open PR found for %s or error fetching PR. Skipping PR review.",
                branch,
            )
            return

        pr_number = pr_info["number"]
        pr_url = pr_info["url"]
        repo_owner = pr_info["repo_owner"]
        repo_name = pr_info["repo_name"]

        self.logger.info(
            "Found PR #%s (%s). Checking for cached threads...",
            pr_number,
            pr_url,
        )

        cache_key = f"{repo_owner}/{repo_name}/{pr_number}"
        if self.check_pr_threads_cache(cache_key):
            return

        self.logger.info("Cache miss or invalid. Fetching fresh threads...")

        threads = self.fetch_pr_threads_gql(repo_owner, repo_name, pr_number)
        if threads is None:
            return

        unresolved_threads = [t for t in threads if not t.get("isResolved", True)]
        unresolved_threads = self._limit_unresolved_threads(unresolved_threads)

        if unresolved_threads:
            content = self.format_pr_threads(unresolved_threads, pr_number, pr_url)
            self.paths.review_current_file.write_text(content)
            thread_ids = self._extract_thread_ids(unresolved_threads)
            self._persist_in_scope_thread_ids(thread_ids)
            self.logger.info(
                "Found %s unresolved threads. Added to review queue.",
                len(unresolved_threads),
            )

            # Cache
            self.paths.pr_threads_cache.write_text(content)
            self.paths.pr_threads_hash_file.write_text(cache_key)

            # Append to cumulative content file for introspection (with iteration marker)
            iteration_num = getattr(self, "iteration", 0)
            thread_count = len(unresolved_threads)
            with self.paths.cumulative_pr_threads_content_file.open("a") as f:
                f.write(f"# Iteration {iteration_num} - Fetched {thread_count} threads\n")
                f.write(content)
                f.write("\n")
        else:
            self.logger.info("No unresolved threads found.")
            self.paths.review_current_file.write_text("")
            self._persist_in_scope_thread_ids([])

    def generate_diff(self) -> str:
        """Generate git diff for review.

        Returns:
            Diff content as string

        """
        diff_content = ""
        if self.start_sha:
            _returncode, diff_output, _ = run_command(
                f"git diff {self.start_sha}",
                cwd=self.paths.project_root,
            )
            diff_content += diff_output
        else:
            _returncode, diff_output, _ = run_command(
                "git diff HEAD",
                cwd=self.paths.project_root,
            )
            diff_content += diff_output
        return diff_content

    def add_untracked_files_diff(self, diff_content: str) -> str:
        """Add pseudo-diff for untracked files.

        Args:
            diff_content: Existing diff content

        Returns:
            Diff content with untracked files added

        """
        returncode, new_files, _ = run_command(
            "git ls-files --others --exclude-standard",
            cwd=self.paths.project_root,
            check=False,
        )
        if returncode != 0:
            return diff_content

        for new_file in new_files.strip().split("\n"):
            if not new_file or not (self.paths.project_root / new_file).is_file():
                continue
            if new_file.startswith(".fix-die-repeat") or is_excluded_file(Path(new_file).name):
                continue

            diff_content += self.create_pseudo_diff(new_file)

        return diff_content

    def create_pseudo_diff(self, filepath: str) -> str:
        """Create pseudo-diff for a single untracked file.

        Args:
            filepath: Path to the untracked file

        Returns:
            Pseudo-diff content

        """
        file_path = self.paths.project_root / filepath
        pseudo_diff = (
            f"diff --git a/{filepath} b/{filepath}\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            f"+++ b/{filepath}\n"
        )

        try:
            returncode, file_type, _ = run_command(["file", str(file_path)], check=False)
            if returncode == 0 and "text" in file_type.lower():
                with file_path.open(encoding="utf-8", errors="replace") as file_handle:
                    for line in file_handle:
                        pseudo_diff += f"+{line}"
            else:
                pseudo_diff += f"Binary file {filepath} differs\n"
        except OSError:
            self.logger.debug("Failed to read file %s for diff", filepath)

        return pseudo_diff + "\n"

    def run_pi_review(self, diff_size: int) -> None:
        """Run pi to review changes.

        Args:
            diff_size: Size of the diff in bytes

        """
        self.logger.info("[Step 5] Running pi to review files...")

        pi_args = ["-p", "--tools", "read,write,grep,find,ls"]
        review_prompt_prefix = self.build_review_prompt(diff_size, pi_args)

        if self.paths.review_file.exists():
            pi_args.append(f"@{self.paths.review_file}")

        review_prompt = render_prompt(
            "local_review.j2",
            review_prompt_prefix=review_prompt_prefix,
            has_agents_file=(self.paths.project_root / "AGENTS.md").exists(),
        )

        returncode, _, _ = self.run_pi_safe(*pi_args, review_prompt)

        if returncode != 0:
            self.logger.info("pi review failed. Treating as no issues found.")
            self.paths.review_current_file.write_text("NO_ISSUES")

    def build_review_prompt(self, diff_size: int, pi_args: list[str]) -> str:
        """Build review prompt based on diff size.

        Args:
            diff_size: Size of the diff in bytes
            pi_args: List to append diff file to if within threshold

        Returns:
            Review prompt prefix

        """
        if diff_size > self.settings.auto_attach_threshold:
            self.logger.info("Review diff size exceeds threshold. Switching to PULL mode.")
            return (
                f"The changes are too large to attach automatically ({diff_size} bytes). "
                "You MUST use the 'read' tool to inspect '.fix-die-repeat/changes.diff'.\n"
            )

        self.logger.info(
            "Review diff size (%s bytes) is within limits. Attaching changes.diff.",
            diff_size,
        )
        pi_args.append(f"@{self.paths.diff_file}")
        return (
            "I have attached '.fix-die-repeat/changes.diff' which contains the changes "
            "made in this session.\n"
        )

    def append_review_entry(self) -> None:
        """Append review entry to review file."""
        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.paths.review_file.open("a") as f:
            f.write(f"## Iteration {self.iteration} - Review ({timestamp})\n")
            if (
                self.paths.review_current_file.exists()
                and self.paths.review_current_file.read_text()
            ):
                f.write(self.paths.review_current_file.read_text())
            else:
                f.write("_No issues found._\n")
            f.write("\n")

    def run_local_review(self, changed_files: list[str]) -> None:
        """Run local file review.

        Args:
            changed_files: List of changed files

        """
        self.logger.info("[Step 4] Collecting changed and staged files...")

        changed_files = get_changed_files(self.paths.project_root)

        if not changed_files:
            self.logger.info("No changed or staged files found to review. Checks passed. Exiting.")
            sys.exit(0)

        self.logger.info("[Step 4] Found %s file(s) to review", len(changed_files))

        # Generate diff
        self.logger.info("[Step 5] Generating diff for review...")

        diff_content = self.generate_diff()
        diff_content = self.add_untracked_files_diff(diff_content)

        self.paths.diff_file.write_text(diff_content)
        diff_size = get_file_size(self.paths.diff_file)
        self.logger.info("Generated review diff size: %s bytes", diff_size)

        # Run pi review
        self.run_pi_review(diff_size)

        # Append review entry
        self.append_review_entry()

    def has_no_review_issues(self, review_content: str) -> bool:
        """Check if review content indicates no issues.

        Args:
            review_content: Review file content

        Returns:
            True if no issues found

        """
        stripped = review_content.strip()

        # Explicit marker for no issues
        if stripped == "NO_ISSUES":
            return True

        # Empty file — treat as no issues but warn (ambiguous state)
        if not stripped:
            self.logger.warning(
                "review_current.md is empty — expected 'NO_ISSUES' marker. "
                "Treating as no issues, but this may indicate a problem.",
            )
            return True

        # Legacy fallback for LLMs that ignored the instruction
        if "no critical issues found" in stripped.lower():
            # Count actual content lines (excluding headers and empty lines)
            content_lines = [
                line for line in review_content.splitlines() if line and not line.startswith("#")
            ]
            return len(content_lines) <= 1

        return False

    def complete_success(self) -> int:
        """Complete the run successfully.

        Returns:
            Exit code 0 for success

        """
        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.paths.review_file.open("a") as f:
            f.write(f"### Iteration {self.iteration} - Resolution ({timestamp})\n")
            f.write("- No issues found.\n\n")

        # Done!
        self.paths.review_current_file.unlink(missing_ok=True)
        self.paths.start_sha_file.unlink(missing_ok=True)

        end_time = int(time.time())
        duration = int(end_time - self.script_start_time)
        self.logger.info(
            "[Step 7] Done! All checks passed and no review issues found. "
            ".fix-die-repeat/review.md retained. Session log: %s",
            self.session_log,
        )

        play_completion_sound()

        # Send notification
        if self.settings.ntfy_enabled:
            send_ntfy_notification(
                exit_code=0,
                duration_str=format_duration(duration),
                repo_name=self.paths.project_root.name,
                ntfy_url=self.settings.ntfy_url,
                logger=self.logger,
            )

        return 0

    def run_review_fix_attempt(self, fix_attempt: int, max_fix_attempts: int) -> bool:
        """Run a single review fix attempt.

        Args:
            fix_attempt: Current attempt number
            max_fix_attempts: Maximum attempts

        Returns:
            True if fix was successful, False to retry

        """
        self.logger.info(
            "[Step 6A] Pi fix attempt %s of %s...",
            fix_attempt,
            max_fix_attempts,
        )

        pi_args = ["-p", "--tools", "read,edit,write,bash,grep,find,ls"]

        if self.settings.model:
            pi_args.extend(["--model", self.settings.model])

        pi_args.append(f"@{self.paths.review_current_file}")

        # Attach recent history
        if self.paths.review_file.exists():
            lines = self.paths.review_file.read_text().splitlines()[-50:]
            self.paths.review_recent_file.write_text("\n".join(lines))
            pi_args.append(f"@{self.paths.review_recent_file}")

        # Build fix prompt
        fix_prompt = render_prompt("resolve_review_issues.j2")

        returncode, _, _ = self.run_pi_safe(*pi_args, fix_prompt)

        if returncode != 0:
            self.logger.info("pi fix failed on attempt %s.", fix_attempt)

        # Record resolution attempt
        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.paths.review_file.open("a") as f:
            f.write(
                f"### Iteration {self.iteration} - Resolution ({timestamp})\n"
                f"- Fixes applied for .fix-die-repeat/review_current.md "
                f"(attempt {fix_attempt}); verification pending.\n\n",
            )

        # Check if changes were made
        returncode, stdout, _ = run_command(
            "git status --porcelain",
            cwd=self.paths.project_root,
            check=False,
        )
        if not stdout.strip():
            self.consecutive_toolless_attempts += 1
            self.logger.error(
                "Pi reported success on attempt %s but NO files were modified. "
                "This suggests 'edit' commands failed (e.g., text not found).",
                fix_attempt,
            )
            with self.paths.build_history_file.open("a") as f:
                f.write(
                    f"## Iteration {self.iteration} fix attempt {fix_attempt}: "
                    "FAILED to apply fixes (no files changed)\n\n",
                )
            return False

        self.consecutive_toolless_attempts = 0
        # Record history
        returncode, stat_output, _ = run_command(
            "git diff --stat",
            cwd=self.paths.project_root,
            check=False,
        )
        with self.paths.build_history_file.open("a") as f:
            f.write(f"## Iteration {self.iteration} Review Fixes (attempt {fix_attempt})\n")
            f.write(f"{stat_output}\n\n")

        # Check if PR threads were resolved
        if self.settings.pr_review:
            self.resolve_pr_threads()

        return True

    def check_prohibited_ruff_ignores(self) -> None:
        """Check for prohibited ruff rules in per-file-ignores.

        Enforces the NEVER-IGNORE policy for C901, PLR0913, PLR2004, PLC0415.

        Raises:
            SystemExit: If prohibited ignores are found or config cannot be parsed

        """
        pyproject_path = self.paths.project_root / "pyproject.toml"

        if not pyproject_path.exists():
            return

        # Prohibited rules (see AGENTS.md)
        try:
            violations = find_prohibited_ruff_ignores(pyproject_path, PROHIBITED_RUFF_RULES)
        except RuffConfigParseError as e:
            self.logger.exception("CRITICAL: Failed to parse ruff config!")
            self.logger.error("=" * 70)
            self.logger.error("%s", e)
            self.logger.error("")
            self.logger.error("This is a CRITICAL policy violation. The build cannot continue.")
            sys.exit(1)

        if violations:
            self.logger.error("=" * 70)
            self.logger.error("CRITICAL: Prohibited ruff rules found in per-file-ignores!")
            self.logger.error("=" * 70)
            self.logger.error("The following rules MUST NEVER be ignored (see AGENTS.md):")
            for rule in sorted(PROHIBITED_RUFF_RULES):
                self.logger.error("  - %s: NEVER IGNORE", rule)
            self.logger.error("")
            self.logger.error("Violations found:")
            for file_pattern, rules in sorted(violations.items()):
                self.logger.error("  %s:", file_pattern)
                for rule in sorted(rules):
                    self.logger.error("    - %s", rule)
            self.logger.error("")
            self.logger.error("To fix:")
            self.logger.error("  1. Remove the ignore from pyproject.toml")
            self.logger.error("  2. Refactor the code to address the underlying issue")
            self.logger.error("")
            self.logger.error("This is a CRITICAL policy violation. The build cannot continue.")
            sys.exit(1)

    def process_review_results(self) -> None:
        """Process review results and fix issues if needed."""
        self.logger.info("[Step 6] Processing review results...")

        # CRITICAL: Check for prohibited ruff ignores (NEVER-IGNORE policy)
        self.check_prohibited_ruff_ignores()

        if not self.paths.review_current_file.exists():
            self.logger.error(
                ".fix-die-repeat/review_current.md was not created by pi. This is unexpected.",
            )
            sys.exit(1)

        # Check if file is empty or only contains "no issues" messages
        review_content = self.paths.review_current_file.read_text()

        if self.has_no_review_issues(review_content):
            self.logger.info("[Step 6B] No issues found in .fix-die-repeat/review_current.md.")
            # Set a flag to call complete_success after the loop
            self._success_complete = True
            return

        # Issues found - fix them
        self.logger.info(
            "[Step 6A] Issues found in .fix-die-repeat/review_current.md. "
            "Running pi to fix them...",
        )

        fix_attempt = 1
        max_fix_attempts = 3

        while fix_attempt <= max_fix_attempts:
            success = self.run_review_fix_attempt(fix_attempt, max_fix_attempts)
            if success:
                break
            if fix_attempt < max_fix_attempts:
                self.logger.info("Retrying fix (attempt %s)...", fix_attempt + 1)
                fix_attempt += 1
            else:
                fix_attempt += 1

        # Continue to next iteration

    def resolve_pr_threads(self) -> None:
        """Resolve PR threads that were fixed."""
        if not self.paths.pr_resolved_threads_file.exists():
            self.logger.info("No threads were reported as resolved. Continuing to next iteration.")
            return

        resolved_ids = self.paths.pr_resolved_threads_file.read_text().strip().split("\n")
        resolved_ids = [thread_id for thread_id in resolved_ids if thread_id]

        if not resolved_ids:
            self.logger.info("No threads were reported as resolved. Continuing to next iteration.")
            return

        self.logger.info("Model reported %s resolved thread(s).", len(resolved_ids))

        # Verify all resolved IDs were in scope
        in_scope_ids = []
        if self.paths.pr_thread_ids_file.exists():
            in_scope_ids = self.paths.pr_thread_ids_file.read_text().strip().split("\n")

        safe_resolved_ids = set(resolved_ids) & set(in_scope_ids)

        if len(safe_resolved_ids) < len(resolved_ids):
            unsafe_ids = set(resolved_ids) - set(in_scope_ids)
            self.logger.warning(
                pr_threads_unsafe_count_warning(len(unsafe_ids), list(unsafe_ids)),
            )
            self.logger.info(pr_threads_safe_only_message(len(safe_resolved_ids)))

        if safe_resolved_ids:
            # Build GraphQL mutation to resolve a single review thread
            # Note: resolveReviewThread only accepts one threadId at a time
            mutation = """
                mutation($threadId: ID!) {
                    resolveReviewThread(input: {threadId: $threadId}) {
                        thread {
                            id
                        }
                    }
                }
            """
            mutation_single_line = " ".join(
                line.strip() for line in mutation.splitlines() if line.strip()
            )

            # Resolve each thread individually (GitHub API limitation)
            resolved_count = 0
            for thread_id in safe_resolved_ids:
                self.logger.info(
                    "Resolving PR thread %s via gh GraphQL",
                    thread_id,
                )
                returncode, _gql_result, _ = run_command(
                    [
                        "gh",
                        "api",
                        "graphql",
                        "-f",
                        f"query={mutation_single_line}",
                        "-F",
                        f"threadId={thread_id}",
                    ],
                    cwd=self.paths.project_root,
                )

                if returncode == 0:
                    resolved_count += 1
                    # Append to cumulative resolved threads file for introspection
                    with self.paths.cumulative_resolved_threads_file.open("a") as f:
                        f.write(f"{thread_id}\n")
                else:
                    self.logger.warning(
                        "Failed to resolve thread %s (exit code: %s)",
                        thread_id,
                        returncode,
                    )

            self.logger.info(
                "Successfully resolved %s of %s thread(s).",
                resolved_count,
                len(safe_resolved_ids),
            )
            # Invalidate cache and refetch
            self.paths.pr_threads_hash_file.unlink(missing_ok=True)
            self.fetch_pr_threads()

            if (
                not self.paths.review_current_file.exists()
                or not self.paths.review_current_file.read_text().strip()
            ):
                self.logger.info(
                    "All PR threads have been resolved! "
                    "Running final local diff review to catch any remaining issues.",
                )
                return

            remaining_count = self.paths.review_current_file.read_text().count(
                "--- Thread #",
            )
            self.logger.info(
                "%s PR threads remain. Continuing to next iteration.",
                remaining_count,
            )
        else:
            self.logger.info(
                "No in-scope threads were reported as resolved. Continuing to next iteration.",
            )

        # Clear resolved threads file
        self.paths.pr_resolved_threads_file.unlink(missing_ok=True)

    def collect_introspection_data(self) -> None:
        """Collect input data for introspection analysis.

        Gathers PR thread comments, fix/won't-fix outcomes, and the diff
        of changes made by the agent during PR review mode.

        Writes the collected data to `self.paths.introspection_data_file`
        as YAML that the introspection LLM prompt can reference.

        """
        self.logger.info("Collecting introspection data...")

        # Get PR info
        pr_info = self._get_pr_info_for_introspection()
        if not pr_info:
            self.logger.warning("No PR info available for introspection")
            return

        # Collect thread IDs and outcomes
        in_scope_ids, resolved_set = self._collect_thread_ids()
        pr_number, pr_url = self._extract_pr_info(pr_info)

        # Read cached data
        pr_threads_content = self._read_pr_threads_cache()
        diff_content = self._read_diff_content()

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

    def _get_pr_info_for_introspection(self) -> dict | None:
        """Get PR information for introspection.

        Returns:
            PR info dict or None if not available

        """
        branch = self.get_branch_name()
        if not branch:
            return None
        return self.get_pr_info(branch)

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

    def _extract_pr_info(self, pr_info: dict) -> tuple[int | str, str]:
        """Extract PR number and URL from PR info.

        Args:
            pr_info: PR info dict

        Returns:
            Tuple of (pr_number, pr_url)

        """
        raw_number = pr_info.get("number")
        raw_url = pr_info.get("url")

        # Preserve integer type for YAML output (will be unquoted in YAML)
        pr_number = "unknown" if raw_number is None else raw_number
        pr_url = "unknown" if raw_url is None else str(raw_url)
        return pr_number, pr_url

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

    def _read_diff_content(self) -> str:
        """Read diff content from file or git.

        Returns:
            Diff content or empty string

        """
        # Try to read from diff file
        if self.paths.diff_file.exists():
            return self.paths.diff_file.read_text()

        # Fall back to git diff if start_sha is available
        if not self.start_sha:
            return ""

        returncode, stdout, _ = run_command(
            f"git diff {self.start_sha}",
            cwd=self.paths.project_root,
            check=False,
        )
        return stdout if returncode == 0 else ""

    def _build_introspection_yaml(
        self,
        params: IntrospectionYamlParams,
    ) -> str:
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

    def run_introspection(self) -> None:
        """Run prompt introspection analysis after PR review completion.

        This is a post-run step that:
        1. Collects PR thread data and agent outcomes
        2. Calls pi with the introspection prompt template
        3. Appends the result to the global introspection file

        This step does NOT block or fail the overall run. Any errors are
        logged as warnings and the main run result is preserved.

        """
        self.logger.info("[Introspection] Running PR review introspection...")

        # Validate prerequisites and get PR info
        pr_info = self._validate_prerequisites_for_introspection()
        if pr_info is None:
            return

        try:
            # Collect input data
            self.collect_introspection_data()
            if not self.paths.introspection_data_file.exists():
                self.logger.warning(
                    "[Introspection] Failed to collect introspection data, skipping",
                )
                return

            # Prepare prompt template variables
            template_vars = {
                "run_date": datetime.now(tz=UTC).strftime("%Y-%m-%d"),
                "project_name": self.paths.project_root.name,
                "pr_number": pr_info.get("number", "unknown"),
                "pr_url": pr_info.get("url", "unknown"),
                "output_path": str(self.paths.introspection_result_file),
            }

            # Render prompt and build pi command
            prompt = render_prompt("introspect_pr_review.j2", **template_vars)
            pi_args = ["-p", "--tools", "read,write"]
            pi_args.append(f"@{self.paths.introspection_data_file}")

            # Run pi and validate result
            self.logger.info(
                "[Introspection] Calling pi to analyze PR threads...",
            )
            returncode, _stdout, _stderr = self.run_pi_safe(*pi_args, prompt)

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

    def _validate_prerequisites_for_introspection(self) -> dict | None:
        """Validate introspection prerequisites and get PR info.

        Returns:
            PR info dict if valid, None if introspection should skip

        """
        # Skip if no PR threads were processed (check cumulative file)
        if not self.paths.cumulative_in_scope_threads_file.exists():
            self.logger.info(
                "[Introspection] No PR threads were processed, skipping introspection",
            )
            return None

        # Get PR info
        branch = self.get_branch_name()
        pr_info = self.get_pr_info(branch) if branch else None
        if not pr_info:
            self.logger.warning(
                "[Introspection] No PR info available, skipping introspection",
            )
            return None

        return pr_info

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
