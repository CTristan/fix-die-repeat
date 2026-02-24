"""Main runner for fix-die-repeat."""

import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from fix_die_repeat.config import Paths, Settings
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
    Logger,
    detect_large_files,
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

        # Determine session log path
        if self.settings.debug:
            session_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.session_log = self.paths.fdr_dir / f"session_{session_timestamp}.log"
        else:
            self.session_log = self.paths.fdr_dir / "session.log"

        # Initialize logger
        self.logger = Logger(
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

        cmd = "pi " + " ".join(args)
        returncode, stdout, stderr = run_command(cmd, cwd=self.paths.project_root)

        # Log output
        if self.paths.pi_log:
            with self.paths.pi_log.open("a", encoding="utf-8") as f:
                f.write(f"Command: {cmd}\n")
                f.write(f"Exit code: {returncode}\n")
                if stdout:
                    f.write(f"STDOUT:\n{stdout}\n")
                if stderr:
                    f.write(f"STDERR:\n{stderr}\n")
                f.write("\n")

        if returncode != 0:
            self.logger.error(f"pi exited with code {returncode}")
            if self.paths.pi_log:
                self.logger.error(f"pi output logged to: {self.paths.pi_log}")

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
                self._emergency_compact()
                self.logger.info("Emergency compaction complete. Retrying...")

        self.logger.info(f"pi failed (exit {returncode}). Retrying once...")
        return self.run_pi(*args)

    def _emergency_compact(self) -> None:
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

        self.logger.info(f"Filtering checks.log ({total_lines} lines -> ~{max_lines} target)...")

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
        self.logger.info(f"Filtered checks.log: {total_lines} -> {filtered_count} lines")

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
                        f"Detected oscillation: iteration {self.iteration} "
                        f"matches iteration {prev_iter}",
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

    def check_and_compact_artifacts(self) -> bool:
        """Check and compact large persistent artifacts.

        Returns:
            True if compaction was performed, False otherwise

        """
        if not self.settings.compact_artifacts:
            return False

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

        if needs_emergency:
            self.logger.info(
                f"Emergency: artifacts exceed {self.settings.emergency_threshold_lines} lines. "
                "Truncating to last 100 lines...",
            )
            for f in [self.paths.review_file, self.paths.build_history_file]:
                if f.exists():
                    lines = f.read_text().splitlines()[-100:]
                    f.write_text("\n".join(lines))
            return True

        if not needs_compact:
            return False

        self.logger.info(
            f"Artifacts exceed {self.settings.compact_threshold_lines} lines. "
            "Compacting with pi...",
        )

        # TODO: Implement pi-based compaction
        # For now, do simple truncation
        for f in [self.paths.review_file, self.paths.build_history_file]:
            if f.exists():
                before = get_file_line_count(f)
                lines = f.read_text().splitlines()[-50:]
                f.write_text("\n".join(lines))
                after = get_file_line_count(f)
                self.logger.info(f"Compacted {f.name} from {before} to {after} lines")

        return True

    def test_model(self) -> None:
        """Test model compatibility before running full loop."""
        if not self.settings.test_model:
            self.logger.info("No --test-model specified, skipping test.")
            return

        test_file = self.paths.fdr_dir / ".model_test_result.txt"

        self.logger.info(f"===== Testing model compatibility: {self.settings.test_model} =====")
        self.logger.info("Running simple write test to verify model can use pi's tools...")

        # Create test prompt
        self.before_pi_call()
        returncode, _stdout, _stderr = run_command(
            f"pi -p --model {self.settings.test_model} "
            f"\"Write 'MODEL TEST OK' to file {test_file}. "
            'Do NOT use any other tools or generate pseudo-code."',
            cwd=self.paths.project_root,
        )

        if returncode != 0:
            self.logger.error(f"pi test invocation failed with code {returncode}")
            self.logger.error(f"Model {self.settings.test_model} failed basic invocation test.")
            test_file.unlink(missing_ok=True)
            sys.exit(1)

        # Check if model wrote the expected output
        if test_file.exists() and "MODEL TEST OK" in test_file.read_text():
            self.logger.info(f"Model {self.settings.test_model} PASSED tool test.")
            self.logger.info(f"Test output: {test_file.read_text().strip()}")
            test_file.unlink(missing_ok=True)

            self.logger.info("Model is compatible for code editing. Ready to proceed.")
            self.logger.info("")
            self.logger.info(
                f"To run with this model: fix-die-repeat --model {self.settings.test_model}",
            )
            self.logger.info(f"Or set via env var: export FDR_MODEL={self.settings.test_model}")
            sys.exit(0)
        else:
            test_output = test_file.read_text() if test_file.exists() else "(empty)"
            self.logger.info(f"Model {self.settings.test_model} FAILED tool test.")
            self.logger.info(f"Test output: {test_output}")

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
                f"Model {self.settings.test_model} is NOT suitable for code editing tasks.",
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
        self.logger.info(f"[Step 1] Running {self.settings.check_cmd} (output: checks.log)...")

        start_time = time.time()
        returncode, stdout, stderr = run_command(
            self.settings.check_cmd,
            cwd=self.paths.project_root,
        )

        # Write output to checks log
        output = f"{stdout}{stderr}"
        self.paths.checks_log.write_text(output)

        end_time = time.time()
        duration = int(end_time - start_time)
        self.logger.info(f"[Step 1] run_checks duration: {format_duration(duration)}")

        return (returncode, output)

    def run(self) -> int:
        """Run the main fix-die-repeat loop.

        Returns:
            Exit code (0 for success, non-zero for failure)

        """
        self.script_start_time = time.time()

        # Setup paths
        self.paths.ensure_fdr_dir()

        # Archive artifacts if requested
        if self.settings.archive_artifacts:
            archive_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            archive_dir = self.paths.fdr_dir / "archive" / archive_timestamp
            self.logger.info(f"Archiving existing artifacts to {archive_dir}")
            archive_dir.mkdir(parents=True, exist_ok=True)
            for file_path in self.paths.fdr_dir.glob("*"):
                if file_path.is_file():
                    file_path.rename(archive_dir / file_path.name)

        # Initialize logs
        self.paths.pi_log.write_text("")
        self.session_log.write_text("")
        self.paths.checks_hash_file.write_text("")
        self.logger.info(f"Logging full session output to: {self.session_log}")

        # Record starting commit SHA
        returncode, stdout, _ = run_command(
            "git rev-parse HEAD",
            cwd=self.paths.project_root,
            check=False,
        )
        if returncode == 0:
            self.start_sha = stdout.strip()
            self.paths.start_sha_file.write_text(self.start_sha)
            self.logger.info(f"Git checkpoint: {self.start_sha}")

        # Test model if requested
        self.test_model()

        # Compact large artifacts from previous runs
        self.check_and_compact_artifacts()

        # Main loop
        while True:
            self.iteration += 1
            self.logger.info(f"===== Iteration {self.iteration} of {self.settings.max_iters} =====")

            # Check and compact at start of each iteration
            self.check_and_compact_artifacts()

            if self.iteration > self.settings.max_iters:
                self.logger.error(
                    f"Maximum iterations ({self.settings.max_iters}) exceeded. "
                    "Could not resolve all issues.",
                )
                if self.start_sha:
                    self.logger.error(git_diff_instructions(self.start_sha))
                    self.logger.error(git_checkout_instructions(self.start_sha))
                return 1

            # Step 1: Run checks
            checks_status, _ = self.run_checks()

            # Step 2: Inner fix loop - if checks failed, keep fixing
            fix_attempt = 0
            while checks_status != 0:
                fix_attempt += 1

                if fix_attempt > self.settings.max_iters:
                    self.logger.error(
                        f"Maximum fix attempts ({self.settings.max_iters}) exhausted. "
                        "Could not resolve check failures.",
                    )
                    if self.start_sha:
                        self.logger.error(git_diff_instructions(self.start_sha))
                        self.logger.error(git_checkout_instructions(self.start_sha))
                    return 1

                # Check for oscillation
                oscillation_warning = None
                if checks_status != 0:
                    oscillation_warning = self.check_oscillation()

                self.logger.info(
                    f"[Step 2A] Checks failed (fix attempt {fix_attempt}/"
                    f"{self.settings.max_iters}). Running pi to fix errors...",
                )

                # Filter checks log
                self.filter_checks_log()

                # Get changed files
                changed_files = get_changed_files(self.paths.project_root)

                if not changed_files:
                    self.logger.info("No changed files found.")
                else:
                    self.logger.info(f"Found {len(changed_files)} changed file(s)")

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
                        f"Context size ({changed_size} bytes) exceeds threshold "
                        f"({self.settings.auto_attach_threshold}). Switching to PULL mode.",
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
                        f"Context size ({changed_size} bytes) is within limits. "
                        "Pushing file contents to prompt.",
                    )

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

                self.logger.info(f"Running pi to fix errors (attempt {fix_attempt})...")
                returncode, _, _ = self.run_pi_safe(*pi_args, prompt)

                if returncode != 0:
                    self.logger.info(f"pi could not produce a fix on attempt {fix_attempt}.")

                # Check if changes were made
                returncode, stdout, _ = run_command("git status --porcelain", check=False)
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
                    returncode, stat_output, _ = run_command("git diff --stat")
                    with self.paths.build_history_file.open("a") as f:
                        f.write(f"## Iteration {self.iteration} fix attempt {fix_attempt}\n")
                        f.write(f"{stat_output}\n\n")

                # Re-run checks
                self.logger.info(
                    f"[Step 2A] Re-running {self.settings.check_cmd} after "
                    f"fix attempt {fix_attempt}...",
                )
                checks_status, _ = self.run_checks()

            self.logger.info("[Step 2B] Checks passed. Proceeding to review.")

            # Step 3: Prepare review artifacts
            self.logger.info("[Step 3] Preparing review artifacts...")
            self.paths.review_current_file.unlink(missing_ok=True)

            # Step 3.5: Check PR threads if enabled
            if self.settings.pr_review:
                self._fetch_pr_threads()

            # Step 4: Collect files for review
            if (
                not self.paths.review_current_file.exists()
                or not self.paths.review_current_file.read_text()
            ):
                # Skip local review if we have PR threads to process
                self._run_local_review(changed_files)
            else:
                self.logger.info(
                    f"[Step 4] Using PR threads from {self.paths.review_current_file} for review.",
                )
                self.logger.info("[Step 5] Skipping local file review generation.")

            # Step 6: Process review results
            self._process_review_results()

        # Should not reach here
        return 0

    def _fetch_pr_threads(self) -> None:
        """Fetch PR threads for PR review mode."""
        self.logger.info("[Step 3.5] Checking for unresolved PR threads...")

        # Get branch name
        returncode, branch, _ = run_command("git branch --show-current")
        if returncode != 0 or not branch.strip():
            self.logger.error("Not on a git branch. Skipping PR review.")
            return

        branch = branch.strip()
        self.logger.info(f"Fetching PR info for branch: {branch}")

        # Check gh auth
        returncode, _, _ = run_command("gh auth status")
        if returncode != 0:
            self.logger.error("GitHub CLI not authenticated. Skipping PR review.")
            return

        # Get PR info
        returncode, pr_json, _ = run_command(
            f"gh pr view {branch} --json number,url,headRepository,headRepositoryOwner",
        )
        if returncode != 0:
            self.logger.info(
                f"No open PR found for {branch} or error fetching PR. Skipping PR review.",
            )
            return

        # Parse PR info
        import json

        pr_data = json.loads(pr_json)
        pr_number = pr_data.get("number")
        pr_url = pr_data.get("url")
        repo_owner = pr_data["headRepositoryOwner"]["login"]
        repo_name = pr_data["headRepository"]["name"]

        self.logger.info(f"Found PR #{pr_number} ({pr_url}). Checking for cached threads...")

        # Check cache
        cache_key = f"{repo_owner}/{repo_name}/{pr_number}"
        if (
            self.paths.pr_threads_cache.exists()
            and self.paths.pr_threads_hash_file.exists()
            and self.paths.pr_threads_hash_file.read_text() == cache_key
        ):
            self.logger.info("Using cached PR threads (unchanged)...")
            self.paths.review_current_file.write_text(self.paths.pr_threads_cache.read_text())
            thread_count = self.paths.review_current_file.read_text().count("--- Thread #")
            self.logger.info(f"Found {thread_count} unresolved threads from cache.")
            return

        self.logger.info("Cache miss or invalid. Fetching fresh threads...")

        # GraphQL query
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
                                comments(first: 10) {
                                    nodes {
                                        author { login }
                                        body
                                    }
                                }
                            }
                        }
                    }
                }
            }
        """

        returncode, gql_result, _ = run_command(
            f"gh api graphql -f query='{query}' -F owner={repo_owner} "
            f"-F repo={repo_name} -F number={pr_number}",
        )
        if returncode != 0:
            self.logger.error("Failed to fetch threads via GraphQL.")
            return

        # Parse and format threads
        import json

        try:
            data = json.loads(gql_result)
            threads = data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
            unresolved_threads = [t for t in threads if not t.get("isResolved", True)]

            if unresolved_threads:
                threads_output = []
                for i, thread in enumerate(unresolved_threads, 1):
                    threads_output.append(f"--- Thread #{i} ---")
                    threads_output.append(f"ID: {thread['id']}")
                    threads_output.append(f"File: {thread.get('path', 'N/A')}")
                    if thread.get("line"):
                        threads_output.append(f"Line: {thread['line']}")

                    for comment in thread["comments"]["nodes"]:
                        author = comment["author"]["login"] if comment.get("author") else "unknown"
                        body = comment["body"]
                        threads_output.append(f"[{author}]: {body}")

                    threads_output.append("")

                header = render_prompt(
                    "pr_threads_header.j2",
                    unresolved_count=len(unresolved_threads),
                    pr_number=pr_number,
                    pr_url=pr_url,
                )

                content = f"{header}\n\n" + "\n".join(threads_output)
                self.paths.review_current_file.write_text(content)
                self.logger.info(
                    f"Found {len(unresolved_threads)} unresolved threads. Added to review queue.",
                )

                # Cache
                self.paths.pr_threads_cache.write_text(content)
                self.paths.pr_threads_hash_file.write_text(cache_key)
            else:
                self.logger.info("No unresolved threads found.")
        except (json.JSONDecodeError, KeyError) as e:
            # Custom Logger doesn't support .exception() method
            self.logger.error(f"Failed to parse PR thread data: {e}")

    def _generate_diff(self) -> str:
        """Generate git diff for review.

        Returns:
            Diff content as string

        """
        diff_content = ""
        if self.start_sha:
            _returncode, diff_output, _ = run_command(f"git diff {self.start_sha}")
            diff_content += diff_output
        else:
            _returncode, diff_output, _ = run_command("git diff HEAD")
            diff_content += diff_output
        return diff_content

    def _add_untracked_files_diff(self, diff_content: str) -> str:
        """Add pseudo-diff for untracked files.

        Args:
            diff_content: Existing diff content

        Returns:
            Diff content with untracked files added

        """
        returncode, new_files, _ = run_command("git ls-files --others --exclude-standard")
        if returncode != 0:
            return diff_content

        for new_file in new_files.strip().split("\n"):
            if not new_file or not (self.paths.project_root / new_file).is_file():
                continue
            if new_file.startswith(".fix-die-repeat") or is_excluded_file(Path(new_file).name):
                continue

            diff_content += self._create_pseudo_diff(new_file)

        return diff_content

    def _create_pseudo_diff(self, filepath: str) -> str:
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
            _returncode, file_type, _ = run_command(f"file {file_path}")
            if "text" in file_type.lower():
                for line in file_path.open():
                    pseudo_diff += f"+{line}"
            else:
                pseudo_diff += f"Binary file {filepath} differs\n"
        except OSError:
            self.logger.debug_log(f"Failed to read file {filepath} for diff")

        return pseudo_diff + "\n"

    def _run_pi_review(self, diff_size: int) -> None:
        """Run pi to review changes.

        Args:
            diff_size: Size of the diff in bytes

        """
        self.logger.info("[Step 5] Running pi to review files...")

        pi_args = ["-p", "--tools", "read,write,grep,find,ls"]
        review_prompt_prefix = self._build_review_prompt(diff_size, pi_args)

        if self.paths.review_file.exists():
            pi_args.append(f"@{self.paths.review_file}")

        review_prompt = render_prompt(
            "local_review.j2",
            review_prompt_prefix=review_prompt_prefix,
        )

        returncode, _, _ = self.run_pi_safe(*pi_args, review_prompt)

        if returncode != 0:
            self.logger.info("pi review failed. Treating as no issues found.")
            self.paths.review_current_file.touch()

    def _build_review_prompt(self, diff_size: int, pi_args: list[str]) -> str:
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
            f"Review diff size ({diff_size} bytes) is within limits. Attaching changes.diff.",
        )
        pi_args.append(f"@{self.paths.diff_file}")
        return (
            "I have attached '.fix-die-repeat/changes.diff' which contains the changes "
            "made in this session.\n"
        )

    def _append_review_entry(self) -> None:
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

    def _run_local_review(self, changed_files: list[str]) -> None:
        """Run local file review.

        Args:
            changed_files: List of changed files

        """
        self.logger.info("[Step 4] Collecting changed and staged files...")

        changed_files = get_changed_files(self.paths.project_root)

        if not changed_files:
            self.logger.info("No changed or staged files found to review. Checks passed. Exiting.")
            sys.exit(0)

        self.logger.info(f"[Step 4] Found {len(changed_files)} file(s) to review")

        # Generate diff
        self.logger.info("[Step 5] Generating diff for review...")

        diff_content = self._generate_diff()
        diff_content = self._add_untracked_files_diff(diff_content)

        self.paths.diff_file.write_text(diff_content)
        diff_size = get_file_size(self.paths.diff_file)
        self.logger.info(f"Generated review diff size: {diff_size} bytes")

        # Run pi review
        self._run_pi_review(diff_size)

        # Append review entry
        self._append_review_entry()

    def _process_review_results(self) -> None:
        """Process review results and fix issues if needed."""
        self.logger.info("[Step 6] Processing review results...")

        if not self.paths.review_current_file.exists():
            self.logger.error(
                ".fix-die-repeat/review_current.md was not created by pi. This is unexpected.",
            )
            sys.exit(1)

        # Check if file is empty or only contains "no issues" messages
        review_content = (
            self.paths.review_current_file.read_text()
            if self.paths.review_current_file.exists()
            else ""
        )

        if not review_content.strip() or "no critical issues found" in review_content.lower():
            # Count actual content lines (excluding headers and empty lines)
            content_lines = [
                line for line in review_content.splitlines() if line and not line.startswith("#")
            ]

            if len(content_lines) <= 1:
                self.logger.info("[Step 6B] No issues found in .fix-die-repeat/review_current.md.")
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
                    f"[Step 7] Done! All checks passed and no review issues found. "
                    f".fix-die-repeat/review.md retained. Session log: {self.session_log}",
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

                sys.exit(0)

        # Issues found - fix them
        self.logger.info(
            "[Step 6A] Issues found in .fix-die-repeat/review_current.md. "
            "Running pi to fix them...",
        )

        fix_attempt = 1
        max_fix_attempts = 3

        while fix_attempt <= max_fix_attempts:
            self.logger.info(f"[Step 6A] Pi fix attempt {fix_attempt} of {max_fix_attempts}...")

            pi_args = ["-p"]

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
                self.logger.info(f"pi fix failed on attempt {fix_attempt}.")

            # Record resolution attempt
            timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            with self.paths.review_file.open("a") as f:
                f.write(
                    f"### Iteration {self.iteration} - Resolution ({timestamp})\n"
                    f"- Fixes applied for .fix-die-repeat/review_current.md "
                    f"(attempt {fix_attempt}); verification pending.\n\n",
                )

            # Check if changes were made
            returncode, stdout, _ = run_command("git status --porcelain")
            if not stdout.strip():
                self.consecutive_toolless_attempts += 1
                self.logger.error(
                    f"Pi reported success on attempt {fix_attempt} but NO files were modified. "
                    "This suggests 'edit' commands failed (e.g., text not found).",
                )
                with self.paths.build_history_file.open("a") as f:
                    f.write(
                        f"## Iteration {self.iteration} fix attempt {fix_attempt}: "
                        "FAILED to apply fixes (no files changed)\n\n",
                    )

                if fix_attempt < max_fix_attempts:
                    self.logger.info(f"Retrying fix (attempt {fix_attempt + 1})...")
                    fix_attempt += 1
                    continue
            else:
                self.consecutive_toolless_attempts = 0
                # Record history
                returncode, stat_output, _ = run_command("git diff --stat")
                with self.paths.build_history_file.open("a") as f:
                    f.write(f"## Iteration {self.iteration} Review Fixes (attempt {fix_attempt})\n")
                    f.write(f"{stat_output}\n\n")

                # Check if PR threads were resolved
                if self.settings.pr_review:
                    self._resolve_pr_threads()

                break  # Success!

            fix_attempt += 1

        # Continue to next iteration

    def _resolve_pr_threads(self) -> None:
        """Resolve PR threads that were fixed."""
        if not self.paths.pr_resolved_threads_file.exists():
            self.logger.info("No threads were reported as resolved. Continuing to next iteration.")
            return

        resolved_ids = self.paths.pr_resolved_threads_file.read_text().strip().split("\n")
        resolved_ids = [thread_id for thread_id in resolved_ids if thread_id]

        if not resolved_ids:
            self.logger.info("No threads were reported as resolved. Continuing to next iteration.")
            return

        self.logger.info(f"Model reported {len(resolved_ids)} resolved thread(s).")

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
            # Build JSON array
            ids_json = json.dumps(list(safe_resolved_ids))

            self.logger.info(f"Calling resolve_pr_threads on safe IDs: {ids_json}")
            self.before_pi_call()
            returncode, _stdout, _stderr = run_command(
                f"pi -p 'resolve_pr_threads(threadIds: {ids_json})'",
            )

            if returncode == 0:
                self.logger.info(f"Successfully resolved {len(safe_resolved_ids)} thread(s).")
                # Invalidate cache and refetch
                self.paths.pr_threads_hash_file.unlink(missing_ok=True)
                self._fetch_pr_threads()

                if not self.paths.review_current_file.read_text().strip():
                    self.logger.info("All PR threads have been resolved! Exiting successfully.")
                    play_completion_sound()
                    sys.exit(0)
                else:
                    remaining_count = self.paths.review_current_file.read_text().count(
                        "--- Thread #",
                    )
                    self.logger.info(
                        f"{remaining_count} PR threads remain. Continuing to next iteration.",
                    )
            else:
                self.logger.warning("Failed to resolve some threads. Continuing to next iteration.")
        else:
            self.logger.info(
                "No in-scope threads were reported as resolved. Continuing to next iteration.",
            )

        # Clear resolved threads file
        self.paths.pr_resolved_threads_file.unlink(missing_ok=True)
