"""Review phase management for fix-die-repeat runner.

This module handles the local code review phase including:
- Generating and reviewing git diffs
- Running pi review prompts
- Processing review results and applying fixes
"""

import logging
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from fix_die_repeat.config import Paths, Settings
from fix_die_repeat.prompts import render_prompt
from fix_die_repeat.utils import (
    PROHIBITED_RUFF_RULES,
    RuffConfigParseError,
    find_prohibited_ruff_ignores,
    get_changed_files,
    get_file_size,
    is_excluded_file,
    run_command,
)


class ReviewManager:
    """Manages the review phase for the fix-die-repeat runner.

    Handles:
    - Generating diffs for review
    - Running pi to review changes
    - Processing review results and applying fixes
    - Checking for prohibited ruff rule ignores
    """

    def __init__(
        self,
        settings: Settings,
        paths: Paths,
        project_root: Path,
        logger: logging.Logger,
    ) -> None:
        """Initialize the review manager.

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

    def generate_diff(self, start_sha: str) -> str:
        """Generate git diff for review.

        Args:
            start_sha: Starting git commit SHA

        Returns:
            Diff content as string

        """
        diff_content = ""
        if start_sha:
            _returncode, diff_output, _ = run_command(
                f"git diff {start_sha}",
                cwd=self.project_root,
            )
            diff_content += diff_output
        else:
            _returncode, diff_output, _ = run_command(
                "git diff HEAD",
                cwd=self.project_root,
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
            cwd=self.project_root,
            check=False,
        )
        if returncode != 0:
            return diff_content

        for new_file in new_files.strip().split("\n"):
            if not new_file or not (self.project_root / new_file).is_file():
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
        file_path = self.project_root / filepath
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

    def run_pi_review(
        self,
        diff_size: int,
        run_pi_callback: Callable[..., tuple[int, str, str]],
    ) -> None:
        """Run pi to review changes.

        Args:
            diff_size: Size of the diff in bytes
            run_pi_callback: Function to run pi (from PiRunner)

        """
        self.logger.info("[Step 5] Running pi to review files...")

        pi_args = ["-p", "--tools", "read,write,grep,find,ls"]
        review_prompt_prefix = self.build_review_prompt(diff_size, pi_args)

        if self.paths.review_file.exists():
            pi_args.append(f"@{self.paths.review_file}")

        review_prompt = render_prompt(
            "local_review.j2",
            review_prompt_prefix=review_prompt_prefix,
            has_agents_file=(self.project_root / "AGENTS.md").exists(),
        )

        returncode, _, _ = run_pi_callback(*pi_args, review_prompt)

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

    def append_review_entry(self, iteration: int) -> None:
        """Append review entry to review file.

        Args:
            iteration: Current iteration number

        """
        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.paths.review_file.open("a") as f:
            f.write(f"## Iteration {iteration} - Review ({timestamp})\n")
            if (
                self.paths.review_current_file.exists()
                and self.paths.review_current_file.read_text()
            ):
                f.write(self.paths.review_current_file.read_text())
            else:
                f.write("_No issues found._\n")
            f.write("\n")

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

    def run_local_review(
        self,
        iteration: int,
        start_sha: str,
        run_pi_callback: Callable[..., tuple[int, str, str]],
    ) -> None:
        """Run local file review.

        Args:
            iteration: Current iteration number
            start_sha: Starting git commit SHA
            run_pi_callback: Function to run pi (from PiRunner)

        """
        self.logger.info("[Step 4] Collecting changed and staged files...")

        changed_files = get_changed_files(self.project_root)

        if not changed_files:
            self.logger.info(
                "No changed or staged files found to review. Checks passed. Skipping review.",
            )
            self.paths.review_current_file.write_text("NO_ISSUES")
            self.paths.diff_file.write_text("")
            self.append_review_entry(iteration)
            return

        self.logger.info("[Step 4] Found %s file(s) to review", len(changed_files))

        # Generate diff
        self.logger.info("[Step 5] Generating diff for review...")

        diff_content = self.generate_diff(start_sha)
        diff_content = self.add_untracked_files_diff(diff_content)

        self.paths.diff_file.write_text(diff_content)
        diff_size = get_file_size(self.paths.diff_file)
        self.logger.info("Generated review diff size: %s bytes", diff_size)

        # Run pi review
        self.run_pi_review(diff_size, run_pi_callback)

        # Append review entry
        self.append_review_entry(iteration)

    def check_prohibited_ruff_ignores(self) -> None:
        """Check for prohibited ruff rules in per-file-ignores.

        Enforces the NEVER-IGNORE policy for C901, PLR0913, PLR2004, PLC0415.

        Raises:
            SystemExit: If prohibited ignores are found or config cannot be parsed

        """
        pyproject_path = self.project_root / "pyproject.toml"

        if not pyproject_path.exists():
            return

        # Prohibited rules (see AGENTS.md)
        try:
            violations = find_prohibited_ruff_ignores(pyproject_path, PROHIBITED_RUFF_RULES)
        except RuffConfigParseError as exc:
            self.logger.exception("CRITICAL: Failed to parse ruff config!")
            self._log_ruff_config_parse_error(exc)
            sys.exit(1)

        if violations:
            separator = "=" * 70
            self.logger.error(separator)
            self.logger.error("CRITICAL: Prohibited ruff rules found in per-file-ignores!")
            self.logger.error(separator)
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

    def _log_ruff_config_parse_error(self, error: RuffConfigParseError) -> None:
        """Log details when the ruff config cannot be parsed.

        Args:
            error: Parsing error raised by ``find_prohibited_ruff_ignores``

        """
        separator = "=" * 70
        self.logger.error(separator)
        self.logger.error("%s", error)
        self.logger.error("")
        self.logger.error("This is a CRITICAL policy violation. The build cannot continue.")
