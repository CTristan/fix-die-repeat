"""Tests for config module."""

import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from fix_die_repeat.config import (
    CliOptions,
    Paths,
    Settings,
    get_introspection_file_path,
    get_settings,
)
from fix_die_repeat.utils import run_command

# Constants for default settings values
DEFAULT_MAX_ITERS = 10
DEFAULT_MAX_PR_THREADS = 5
TEST_MAX_ITERS = 5
TEST_MAX_PR_THREADS = 10


def _git_executable() -> str:
    """Return the absolute path to the git executable, or skip if unavailable."""
    git_path = shutil.which("git")
    if git_path is None:
        pytest.skip("git executable is required for this test")
    assert git_path is not None  # pytest.skip() raises, so this is never None
    return git_path


def _run_git_command(project_dir: Path, *args: str) -> None:
    """Run a git command and assert success."""
    git_executable = _git_executable()
    returncode, _stdout, stderr = run_command([git_executable, *args], cwd=project_dir, check=False)
    assert returncode == 0, stderr


def _init_git_repo(project_dir: Path) -> None:
    """Initialize a git repository for path-discovery tests."""
    _run_git_command(project_dir, "init")
    _run_git_command(project_dir, "config", "user.email", "test@example.com")
    _run_git_command(project_dir, "config", "user.name", "Test User")


class TestSettings:
    """Tests for Settings class."""

    def test_default_settings(self) -> None:
        """Test default settings values."""
        settings = Settings()
        assert settings.check_cmd is None
        assert settings.max_iters == DEFAULT_MAX_ITERS
        assert settings.model is None
        assert settings.test_model is None
        assert settings.max_pr_threads == DEFAULT_MAX_PR_THREADS
        assert not settings.archive_artifacts
        assert settings.compact_artifacts
        assert not settings.pr_review
        assert not settings.debug
        assert settings.ntfy_enabled
        assert settings.ntfy_url == "http://localhost:2586"

    def test_settings_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test settings from environment variables."""
        monkeypatch.setenv("FDR_CHECK_CMD", "make test")
        monkeypatch.setenv("FDR_MAX_ITERS", "5")
        monkeypatch.setenv("FDR_MODEL", "anthropic/claude-sonnet-4-5")

        settings = Settings()
        assert settings.check_cmd == "make test"
        assert settings.max_iters == TEST_MAX_ITERS
        assert settings.model == "anthropic/claude-sonnet-4-5"

    def test_pr_review_introspect_default(self) -> None:
        """Test that pr_review_introspect defaults to False."""
        settings = Settings()
        assert not settings.pr_review_introspect

    def test_pr_review_introspect_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that pr_review_introspect can be set via environment variable."""
        monkeypatch.setenv("FDR_PR_REVIEW_INTROSPECT", "1")
        settings = Settings()
        assert settings.pr_review_introspect

    def test_pr_review_introspect_implies_pr_review(self) -> None:
        """Test that pr_review_introspect=True also sets pr_review=True."""
        options = CliOptions(pr_review_introspect=True)
        settings = get_settings(options)

        assert settings.pr_review_introspect
        assert settings.pr_review, "pr_review should be True when pr_review_introspect is True"

    def test_pr_review_flag_standalone(self) -> None:
        """Test that --pr-review flag can be used without --pr-review-introspect."""
        options = CliOptions(pr_review=True, pr_review_introspect=False)
        settings = get_settings(options)

        assert settings.pr_review
        assert not settings.pr_review_introspect

    def test_invalid_max_iters(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that invalid max_iters raises ValueError."""
        monkeypatch.setenv("FDR_MAX_ITERS", "0")

        settings = Settings()
        with pytest.raises(ValueError, match="must be a positive integer"):
            settings.validate_max_iters()

    def test_get_settings_with_cli_overrides(self) -> None:
        """Test get_settings with CLI parameter overrides."""
        options = CliOptions(
            check_cmd="pytest",
            max_iters=5,
            model="test-model",
            archive_artifacts=True,
            no_compact=True,
            pr_review=True,
            debug=True,
        )
        settings = get_settings(options)

        assert settings.check_cmd == "pytest"
        assert settings.max_iters == TEST_MAX_ITERS
        assert settings.model == "test-model"
        assert settings.archive_artifacts
        assert not settings.compact_artifacts
        assert settings.pr_review
        assert settings.debug

    def test_get_settings_with_test_model(self) -> None:
        """Test get_settings with test_model parameter."""
        options = CliOptions(test_model="anthropic/claude-sonnet-4-5")
        settings = get_settings(options)

        assert settings.test_model == "anthropic/claude-sonnet-4-5"

    def test_get_settings_with_max_pr_threads(self) -> None:
        """Test get_settings with max_pr_threads parameter (line 190)."""
        options = CliOptions(max_pr_threads=10)
        settings = get_settings(options)

        assert settings.max_pr_threads == TEST_MAX_PR_THREADS


class TestPaths:
    """Tests for Paths class."""

    def test_default_project_root(self, tmp_path: Path) -> None:
        """Test that project root defaults to git root or cwd."""
        # Create a temporary directory with a git repo
        project_dir = tmp_path / "test_project"
        project_dir.mkdir()

        # Initialize git repo
        _init_git_repo(project_dir)

        paths = Paths(project_root=project_dir)
        assert paths.project_root == project_dir
        assert paths.fdr_dir == project_dir / ".fix-die-repeat"

    def test_project_root_from_git(self, tmp_path: Path) -> None:
        """Test that project root is found from git when not specified."""
        # Create a temporary directory with a git repo
        project_dir = tmp_path / "git_project"
        project_dir.mkdir()

        # Initialize git repo
        _init_git_repo(project_dir)

        # Change to git directory and create Paths without project_root
        original_cwd = Path.cwd()
        try:
            os.chdir(project_dir)
            paths = Paths()  # No project_root argument - should find git root
            assert paths.project_root == project_dir
            assert paths.fdr_dir == project_dir / ".fix-die-repeat"
        finally:
            os.chdir(original_cwd)

    def test_project_root_without_git(self, tmp_path: Path) -> None:
        """Test that project root falls back to cwd when not in a git repo."""
        # Create a directory without git
        no_git_dir = tmp_path / "no_git_project"
        no_git_dir.mkdir()

        # Change to this directory and create Paths without project_root
        original_cwd = Path.cwd()
        try:
            os.chdir(no_git_dir)
            # Paths should find the project root (which falls back to cwd)
            paths = Paths()  # No project_root argument
            assert paths.project_root == no_git_dir
            assert paths.fdr_dir == no_git_dir / ".fix-die-repeat"
        finally:
            os.chdir(original_cwd)

    def test_ensure_fdr_dir(self, tmp_path: Path) -> None:
        """Test that ensure_fdr_dir creates the directory."""
        paths = Paths(project_root=tmp_path)
        paths.ensure_fdr_dir()

        assert paths.fdr_dir.exists()
        assert paths.fdr_dir.is_dir()

        # Check .gitignore is updated
        gitignore = tmp_path / ".gitignore"
        if gitignore.exists():
            content = gitignore.read_text()
            assert ".fix-die-repeat/" in content

    def test_ensure_fdr_dir_updates_gitignore(self, tmp_path: Path) -> None:
        """Test that ensure_fdr_dir adds .fix-die-repeat/ to existing gitignore."""
        # Create a gitignore without .fix-die-repeat/
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("*.pyc\n__pycache__/\n")

        paths = Paths(project_root=tmp_path)
        paths.ensure_fdr_dir()

        # Verify .fix-die-repeat/ was added
        content = gitignore.read_text()
        assert "*.pyc" in content
        assert "__pycache__/" in content
        assert ".fix-die-repeat/" in content

    def test_ensure_fdr_dir_does_not_duplicate_gitignore(self, tmp_path: Path) -> None:
        """Test that ensure_fdr_dir doesn't add .fix-die-repeat/ if already present."""
        # Create a gitignore with .fix-die-repeat/ already present
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("*.pyc\n.fix-die-repeat/\n__pycache__/\n")

        paths = Paths(project_root=tmp_path)
        paths.ensure_fdr_dir()

        # Verify .fix-die-repeat/ was not duplicated
        content = gitignore.read_text()
        count = content.count(".fix-die-repeat/")
        assert count == 1, f".fix-die-repeat/ appears {count} times, expected 1"

    def test_path_properties(self, tmp_path: Path) -> None:
        """Test that all path properties are correctly set."""
        paths = Paths(project_root=tmp_path)

        assert paths.review_file == paths.fdr_dir / "review.md"
        assert paths.review_current_file == paths.fdr_dir / "review_current.md"
        assert paths.review_recent_file == paths.fdr_dir / "review_recent.md"
        assert paths.build_history_file == paths.fdr_dir / "build_history.md"
        assert paths.checks_log == paths.fdr_dir / "checks.log"
        assert paths.checks_filtered_log == paths.fdr_dir / "checks_filtered.log"
        assert paths.checks_hash_file == paths.fdr_dir / ".checks_hashes"
        assert paths.pi_log == paths.fdr_dir / "pi.log"
        assert paths.fdr_log == paths.fdr_dir / "fdr.log"
        assert paths.pr_threads_cache == paths.fdr_dir / ".pr_threads_cache"
        assert paths.pr_threads_hash_file == paths.fdr_dir / ".pr_threads_hash"
        assert paths.start_sha_file == paths.fdr_dir / ".start_sha"
        assert paths.pr_thread_ids_file == paths.fdr_dir / ".pr_thread_ids_in_scope"
        assert paths.pr_resolved_threads_file == paths.fdr_dir / ".resolved_threads"
        assert paths.diff_file == paths.fdr_dir / "changes.diff"
        assert paths.run_timestamps_file == paths.fdr_dir / "run_timestamps.md"
        assert paths.introspection_data_file == paths.fdr_dir / ".introspection_data.yaml"
        assert paths.introspection_result_file == paths.fdr_dir / ".introspection_result.yaml"

    def test_find_project_root_git_fallback_to_cwd(self, tmp_path: Path) -> None:
        """Test _find_project_root falls back to cwd when git lookup fails."""
        no_git_dir = tmp_path / "no_git"
        no_git_dir.mkdir()

        original_cwd = Path.cwd()
        try:
            os.chdir(no_git_dir)
            with patch("fix_die_repeat.config.run_command", return_value=(1, "", "git failed")):
                # Create Paths - should fall back to cwd when git fails
                paths = Paths()

                # Should return current working directory when git fails
                assert paths.project_root == no_git_dir
        finally:
            os.chdir(original_cwd)


class TestGetIntrospectionFilePath:
    """Tests for get_introspection_file_path function."""

    def test_returns_path_object(self) -> None:
        """Test that get_introspection_file_path returns a Path object."""
        path = get_introspection_file_path()
        assert isinstance(path, Path)

    def test_default_location(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Test default location is ~/.config/fix-die-repeat/introspection.yaml."""
        # Set HOME to tmp_path for test isolation
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

        path = get_introspection_file_path()
        expected = tmp_path / ".config" / "fix-die-repeat" / "introspection.yaml"
        assert path == expected

    def test_xdg_config_home_respected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Test that XDG_CONFIG_HOME is respected when set."""
        xdg_config = tmp_path / "custom_config"
        xdg_config.mkdir()

        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))

        path = get_introspection_file_path()
        expected = xdg_config / "fix-die-repeat" / "introspection.yaml"
        assert path == expected

    def test_creates_parent_directories(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Test that parent directories are created."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

        # Remove any existing config directory
        config_dir = tmp_path / ".config" / "fix-die-repeat"
        if config_dir.exists():
            shutil.rmtree(config_dir.parent)

        # Call the function - should create directories
        path = get_introspection_file_path()

        # Verify parent directories were created
        assert path.parent.exists()
        assert path.parent.is_dir()
