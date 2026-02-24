"""Tests for config module."""

import os
import subprocess
from pathlib import Path

import pytest

from fix_die_repeat.config import Paths, Settings, get_settings


class TestSettings:
    """Tests for Settings class."""

    def test_default_settings(self) -> None:
        """Test default settings values."""
        settings = Settings()
        assert settings.check_cmd == "./scripts/ci.sh"
        assert settings.max_iters == 10
        assert settings.model is None
        assert settings.test_model is None
        assert settings.max_pr_threads == 5
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
        assert settings.max_iters == 5
        assert settings.model == "anthropic/claude-sonnet-4-5"

    def test_invalid_max_iters(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that invalid max_iters raises ValueError."""
        monkeypatch.setenv("FDR_MAX_ITERS", "0")

        settings = Settings()
        with pytest.raises(ValueError, match="must be a positive integer"):
            settings.validate_max_iters()

    def test_get_settings_with_cli_overrides(self) -> None:
        """Test get_settings with CLI parameter overrides."""
        settings = get_settings(
            check_cmd="pytest",
            max_iters=5,
            model="test-model",
            archive_artifacts=True,
            no_compact=True,
            pr_review=True,
            debug=True,
        )

        assert settings.check_cmd == "pytest"
        assert settings.max_iters == 5
        assert settings.model == "test-model"
        assert settings.archive_artifacts
        assert not settings.compact_artifacts
        assert settings.pr_review
        assert settings.debug

    def test_get_settings_with_test_model(self) -> None:
        """Test get_settings with test_model parameter."""
        settings = get_settings(test_model="anthropic/claude-sonnet-4-5")

        assert settings.test_model == "anthropic/claude-sonnet-4-5"


class TestPaths:
    """Tests for Paths class."""

    def test_default_project_root(self, tmp_path: Path) -> None:
        """Test that project root defaults to git root or cwd."""
        # Create a temporary directory with a git repo
        project_dir = tmp_path / "test_project"
        project_dir.mkdir()

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=project_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=project_dir,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=project_dir,
            check=True,
            capture_output=True,
        )

        paths = Paths(project_root=project_dir)
        assert paths.project_root == project_dir
        assert paths.fdr_dir == project_dir / ".fix-die-repeat"

    def test_project_root_from_git(self, tmp_path: Path) -> None:
        """Test that project root is found from git when not specified."""
        # Create a temporary directory with a git repo
        project_dir = tmp_path / "git_project"
        project_dir.mkdir()

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=project_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=project_dir,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=project_dir,
            check=True,
            capture_output=True,
        )

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
