"""Configuration management for fix-die-repeat."""

from pathlib import Path

import pydantic as pyd
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration settings for fix-die-repeat."""

    # Check configuration
    check_cmd: str = pyd.Field(
        default="./scripts/ci.sh",
        alias="FDR_CHECK_CMD",
        description="Command to run checks",
    )

    # Iteration limits
    max_iters: int = pyd.Field(
        default=10,
        alias="FDR_MAX_ITERS",
        description="Maximum loop iterations",
    )

    # Model configuration
    model: str | None = pyd.Field(
        default=None,
        alias="FDR_MODEL",
        description="Override model selection",
    )

    test_model: str | None = pyd.Field(
        default=None,
        alias="FDR_TEST_MODEL",
        description="Test model compatibility before running",
    )

    # PR review configuration
    max_pr_threads: int = pyd.Field(
        default=5,
        alias="FDR_MAX_PR_THREADS",
        description="Maximum PR threads to process per iteration",
    )

    # Artifact management
    archive_artifacts: bool = pyd.Field(
        default=False,
        alias="FDR_ARCHIVE_ARTIFACTS",
        description="Archive existing artifacts to a timestamped folder",
    )

    compact_artifacts: bool = pyd.Field(
        default=True,
        alias="FDR_COMPACT_ARTIFACTS",
        description="Automatically compact large artifacts",
    )

    # PR review mode
    pr_review: bool = pyd.Field(
        default=False,
        alias="FDR_PR_REVIEW",
        description="Enable PR review mode",
    )

    # Debug mode
    debug: bool = pyd.Field(
        default=False,
        alias="FDR_DEBUG",
        description="Enable debug mode with timestamped session logs",
    )

    # Notification settings
    ntfy_enabled: bool = pyd.Field(
        default=True,
        alias="FDR_NTFY_ENABLED",
        description="Enable ntfy notifications",
    )

    ntfy_url: str = pyd.Field(
        default="http://localhost:2586",
        alias="FDR_NTFY_URL",
        description="ntfy server URL",
    )

    # Thresholds
    auto_attach_threshold: int = pyd.Field(
        default=200 * 1024,
        description="Size threshold in bytes for auto-attaching file contents",
    )

    compact_threshold_lines: int = pyd.Field(
        default=150,
        description="Line count threshold for artifact compaction",
    )

    emergency_threshold_lines: int = pyd.Field(
        default=200,
        description="Emergency compaction threshold",
    )

    large_file_lines: int = pyd.Field(
        default=2000,
        description="Line count threshold for large file warnings",
    )

    pi_sequential_delay_seconds: int = pyd.Field(
        default=1,
        description="Minimum delay between sequential pi invocations",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="FDR_",
        extra="allow",
    )

    def validate_max_iters(self) -> None:
        """Validate max_iters is a positive integer."""
        if self.max_iters <= 0:
            raise ValueError(
                f"Invalid configuration: FDR_MAX_ITERS must be a positive integer (got '{self.max_iters}')"
            )


def get_settings(
    check_cmd: str | None = None,
    max_iters: int | None = None,
    model: str | None = None,
    archive_artifacts: bool | None = None,
    no_compact: bool = False,
    pr_review: bool = False,
    test_model: str | None = None,
    debug: bool = False,
) -> Settings:
    """Create Settings instance from command line args and environment.

    Args:
        check_cmd: Override check command
        max_iters: Override max iterations
        model: Override model selection
        archive_artifacts: Enable artifact archiving
        no_compact: Disable artifact compaction
        pr_review: Enable PR review mode
        test_model: Model to test compatibility
        debug: Enable debug mode

    Returns:
        Settings instance
    """
    # Get base settings from environment
    settings = Settings()

    # Apply CLI overrides
    if check_cmd is not None:
        settings.check_cmd = check_cmd
    if max_iters is not None:
        settings.max_iters = max_iters
    if model is not None:
        settings.model = model
    if archive_artifacts is not None:
        settings.archive_artifacts = archive_artifacts
    if no_compact:
        settings.compact_artifacts = False
    if pr_review:
        settings.pr_review = pr_review
    if test_model is not None:
        settings.test_model = test_model
    if debug:
        settings.debug = debug

    # Validate settings
    settings.validate_max_iters()

    return settings


class Paths:
    """Path management for fix-die-repeat."""

    def __init__(self, project_root: Path | None = None) -> None:
        """Initialize paths.

        Args:
            project_root: Project root directory (defaults to git root or cwd)
        """
        self.project_root = project_root or self._find_project_root()
        self.fdr_dir = self.project_root / ".fix-die-repeat"
        self.review_file = self.fdr_dir / "review.md"
        self.review_current_file = self.fdr_dir / "review_current.md"
        self.review_recent_file = self.fdr_dir / "review_recent.md"
        self.build_history_file = self.fdr_dir / "build_history.md"
        self.checks_log = self.fdr_dir / "checks.log"
        self.checks_filtered_log = self.fdr_dir / "checks_filtered.log"
        self.checks_hash_file = self.fdr_dir / ".checks_hashes"
        self.pi_log = self.fdr_dir / "pi.log"
        self.fdr_log = self.fdr_dir / "fdr.log"
        self.pr_threads_cache = self.fdr_dir / ".pr_threads_cache"
        self.pr_threads_hash_file = self.fdr_dir / ".pr_threads_hash"
        self.start_sha_file = self.fdr_dir / ".start_sha"
        self.pr_thread_ids_file = self.fdr_dir / ".pr_thread_ids_in_scope"
        self.pr_resolved_threads_file = self.fdr_dir / ".resolved_threads"
        self.diff_file = self.fdr_dir / "changes.diff"
        self.run_timestamps_file = self.fdr_dir / "run_timestamps.md"

    @staticmethod
    def _find_project_root() -> Path:
        """Find project root directory.

        Returns:
            Path to project root
        """
        # Try to get git root first
        import subprocess

        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                check=True,
            )
            return Path(result.stdout.strip())
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Fall back to current directory
            return Path.cwd()

    def ensure_fdr_dir(self) -> None:
        """Ensure .fix-die-repeat directory exists."""
        self.fdr_dir.mkdir(parents=True, exist_ok=True)

        # Add to .gitignore if not present
        gitignore = self.project_root / ".gitignore"
        if gitignore.exists():
            gitignore_content = gitignore.read_text()
            if ".fix-die-repeat/" not in gitignore_content:
                with gitignore.open("a") as f:
                    f.write("\n.fix-die-repeat/\n")
