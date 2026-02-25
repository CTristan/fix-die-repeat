"""Tests for runner review fix and PR thread resolution."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from fix_die_repeat.runner import PiRunner


class TestRunReviewFixAttempt:
    """Tests for run_review_fix_attempt method."""

    def test_run_review_fix_attempt_success(self, tmp_path: Path) -> None:
        """Test running a successful review fix attempt."""
        settings = MagicMock()
        settings.model = "test-model"
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.review_current_file = tmp_path / "review_current.md"
        paths.review_file = tmp_path / "review.md"
        paths.build_history_file = tmp_path / "build_history.md"
        paths.pr_resolved_threads_file = tmp_path / "pr_resolved_threads"
        paths.review_recent_file = tmp_path / "review_recent.md"
        paths.pi_log = tmp_path / "pi.log"

        runner = PiRunner.__new__(PiRunner)
        runner.settings = settings
        runner.paths = paths
        runner.iteration = 1
        runner.consecutive_toolless_attempts = 0
        runner.logger = MagicMock()
        runner.before_pi_call = MagicMock()  # type: ignore[method-assign]
        runner.run_pi_safe = MagicMock(return_value=(0, "", ""))  # type: ignore[method-assign]
        runner.resolve_pr_threads = MagicMock()  # type: ignore[method-assign]

        # Create review current file
        paths.review_current_file.write_text("[CRITICAL] Bug found")

        with patch("fix_die_repeat.runner.run_command") as mock_git:
            mock_git.return_value = (0, "M file1.py\n", "")

            result = runner.run_review_fix_attempt(1, 3)

            # Should return True on success
            assert result is True
            # Should have called run_pi_safe
            assert runner.run_pi_safe.called

    def test_run_review_fix_attempt_pi_fails(self, tmp_path: Path) -> None:
        """Test running review fix attempt when pi fails."""
        settings = MagicMock()
        settings.model = "test-model"
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.review_current_file = tmp_path / "review_current.md"
        paths.review_file = tmp_path / "review.md"
        paths.build_history_file = tmp_path / "build_history.md"
        paths.pr_resolved_threads_file = tmp_path / "pr_resolved_threads"
        paths.review_recent_file = tmp_path / "review_recent.md"
        paths.pi_log = tmp_path / "pi.log"

        runner = PiRunner.__new__(PiRunner)
        runner.settings = settings
        runner.paths = paths
        runner.iteration = 1
        runner.consecutive_toolless_attempts = 0
        runner.logger = MagicMock()
        runner.before_pi_call = MagicMock()  # type: ignore[method-assign]
        runner.run_pi_safe = MagicMock(return_value=(1, "", "error"))  # type: ignore[method-assign]

        # Create review current file
        paths.review_current_file.write_text("[CRITICAL] Bug found")

        with patch("fix_die_repeat.runner.run_command") as mock_git:
            # No files modified
            mock_git.return_value = (0, "", "")

            result = runner.run_review_fix_attempt(1, 3)

            # Should return False when no files changed
            assert result is False

    def test_run_review_fix_attempt_no_files_changed(self, tmp_path: Path) -> None:
        """Test running review fix attempt when no files change."""
        settings = MagicMock()
        settings.model = "test-model"
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.review_current_file = tmp_path / "review_current.md"
        paths.review_file = tmp_path / "review.md"
        paths.build_history_file = tmp_path / "build_history.md"
        paths.pr_resolved_threads_file = tmp_path / "pr_resolved_threads"
        paths.review_recent_file = tmp_path / "review_recent.md"
        paths.pi_log = tmp_path / "pi.log"

        runner = PiRunner.__new__(PiRunner)
        runner.settings = settings
        runner.paths = paths
        runner.iteration = 1
        runner.consecutive_toolless_attempts = 0
        runner.logger = MagicMock()
        runner.before_pi_call = MagicMock()  # type: ignore[method-assign]
        runner.run_pi_safe = MagicMock(return_value=(0, "", ""))  # type: ignore[method-assign]
        runner.resolve_pr_threads = MagicMock()  # type: ignore[method-assign]

        # Create review current file
        paths.review_current_file.write_text("[CRITICAL] Bug found")

        with patch("fix_die_repeat.runner.run_command") as mock_git:
            mock_git.return_value = (0, "", "")

            result = runner.run_review_fix_attempt(1, 3)

            # Should return False when no files changed
            assert result is False
            # Should increment consecutive_toolless_attempts
            assert runner.consecutive_toolless_attempts == 1


class TestResolvePrThreads:
    """Tests for resolve_pr_threads method."""

    def test_resolve_pr_threads_no_file(self, tmp_path: Path) -> None:
        """Test resolving PR threads when no resolved threads file."""
        settings = MagicMock()
        settings.pr_review = True
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.pr_resolved_threads_file = tmp_path / "pr_resolved_threads"
        paths.pr_thread_ids_file = tmp_path / "pr_thread_ids"
        paths.pi_log = tmp_path / "pi.log"

        runner = PiRunner.__new__(PiRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()
        runner.before_pi_call = MagicMock()  # type: ignore[method-assign]

        runner.resolve_pr_threads()

        # Should log about no threads reported as resolved
        assert runner.logger.info.called

    def test_resolve_pr_threads_empty_file(self, tmp_path: Path) -> None:
        """Test resolving PR threads with empty file."""
        settings = MagicMock()
        settings.pr_review = True
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.pr_resolved_threads_file = tmp_path / "pr_resolved_threads"
        paths.pr_thread_ids_file = tmp_path / "pr_thread_ids"
        paths.pi_log = tmp_path / "pi.log"

        runner = PiRunner.__new__(PiRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()
        runner.before_pi_call = MagicMock()  # type: ignore[method-assign]

        # Create empty file
        paths.pr_resolved_threads_file.write_text("")

        runner.resolve_pr_threads()

        # Should log about no threads reported as resolved
        assert runner.logger.info.called

    def test_resolve_pr_threads_no_in_scope(self, tmp_path: Path) -> None:
        """Test resolving PR threads when no threads are in scope."""
        settings = MagicMock()
        settings.pr_review = True
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.pr_resolved_threads_file = tmp_path / "pr_resolved_threads"
        paths.pr_thread_ids_file = tmp_path / "pr_thread_ids"
        paths.pi_log = tmp_path / "pi.log"

        runner = PiRunner.__new__(PiRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()
        runner.before_pi_call = MagicMock()  # type: ignore[method-assign]

        # Create resolved file with thread IDs
        paths.pr_resolved_threads_file.write_text("thread1\nthread2\n")
        # No in-scope file

        runner.resolve_pr_threads()

        # Should log about no in-scope threads
        assert runner.logger.info.called

    def test_resolve_pr_threads_some_safe(self, tmp_path: Path) -> None:
        """Test resolving PR threads with some safe IDs."""
        settings = MagicMock()
        settings.pr_review = True
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.pr_resolved_threads_file = tmp_path / "pr_resolved_threads"
        paths.pr_thread_ids_file = tmp_path / "pr_thread_ids"
        paths.pi_log = tmp_path / "pi.log"

        runner = PiRunner.__new__(PiRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()
        runner.before_pi_call = MagicMock()  # type: ignore[method-assign]

        # Create resolved file with thread IDs
        paths.pr_resolved_threads_file.write_text("thread1\nthread2\nthread3\n")
        # Create in-scope file with subset
        paths.pr_thread_ids_file.write_text("thread1\nthread2\n")

        with patch("fix_die_repeat.runner.run_command") as mock_run:
            mock_run.return_value = (0, "", "")

            runner.resolve_pr_threads()

            # Should log about safe resolution
            assert runner.logger.info.called

    def test_resolve_pr_threads_with_unsafe(self, tmp_path: Path) -> None:
        """Test resolving PR threads with some unsafe IDs."""
        settings = MagicMock()
        settings.pr_review = True
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.pr_resolved_threads_file = tmp_path / "pr_resolved_threads"
        paths.pr_thread_ids_file = tmp_path / "pr_thread_ids"
        paths.pi_log = tmp_path / "pi.log"

        runner = PiRunner.__new__(PiRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()
        runner.before_pi_call = MagicMock()  # type: ignore[method-assign]

        # Create resolved file with thread IDs (some not in scope)
        paths.pr_resolved_threads_file.write_text("thread1\nthread2\nthread3\n")
        # Create in-scope file with only thread1
        paths.pr_thread_ids_file.write_text("thread1\n")

        with patch("fix_die_repeat.runner.run_command") as mock_run:
            mock_run.return_value = (0, "", "")

            runner.resolve_pr_threads()

            # Should log warning about unsafe threads
            assert runner.logger.warning.called
