"""Tests for runner review fix and PR thread resolution."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from fix_die_repeat.runner import PiRunner

# Constants for test assertions
EXPECTED_THREAD_COUNT = 2


class TestRunReviewFixAttempt:
    """Tests for run_review_fix_attempt method."""

    def test_run_review_fix_attempt_success(self, tmp_path: Path) -> None:
        """Test running a successful review fix attempt."""
        settings = MagicMock()
        settings.model = "test-model"
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.project_root = tmp_path
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

        paths.review_current_file.write_text("[CRITICAL] Bug found")

        with patch("fix_die_repeat.runner.run_command") as mock_git:
            mock_git.side_effect = [
                (0, "M file1.py\n", ""),
                (0, " file1.py | 1 +\n", ""),
            ]

            result = runner.run_review_fix_attempt(1, 3)

        assert result is True
        assert runner.run_pi_safe.called
        pi_args = runner.run_pi_safe.call_args.args
        assert "--tools" in pi_args
        assert "read,edit,write,bash,grep,find,ls" in pi_args
        assert mock_git.call_args_list[0].kwargs["cwd"] == tmp_path
        assert mock_git.call_args_list[1].kwargs["cwd"] == tmp_path

    def test_run_review_fix_attempt_pi_fails(self, tmp_path: Path) -> None:
        """Test running review fix attempt when pi fails."""
        settings = MagicMock()
        settings.model = "test-model"
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.project_root = tmp_path
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

        paths.review_current_file.write_text("[CRITICAL] Bug found")

        with patch("fix_die_repeat.runner.run_command") as mock_git:
            mock_git.return_value = (0, "", "")

            result = runner.run_review_fix_attempt(1, 3)

        assert result is False
        assert mock_git.call_args.kwargs["cwd"] == tmp_path

    def test_run_review_fix_attempt_no_files_changed(self, tmp_path: Path) -> None:
        """Test running review fix attempt when no files change."""
        settings = MagicMock()
        settings.model = "test-model"
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.project_root = tmp_path
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

        paths.review_current_file.write_text("[CRITICAL] Bug found")

        with patch("fix_die_repeat.runner.run_command") as mock_git:
            mock_git.return_value = (0, "", "")

            result = runner.run_review_fix_attempt(1, 3)

        assert result is False
        assert runner.consecutive_toolless_attempts == 1
        assert mock_git.call_args.kwargs["cwd"] == tmp_path


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

    def test_resolve_pr_threads_correct_mutation_and_variables(self, tmp_path: Path) -> None:
        """Test that correct GraphQL mutation and variables are passed to gh api."""
        settings = MagicMock()
        settings.pr_review = True
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.pr_resolved_threads_file = tmp_path / "pr_resolved_threads"
        paths.pr_thread_ids_file = tmp_path / "pr_thread_ids"
        paths.pr_threads_hash_file = tmp_path / "pr_threads_hash"
        paths.review_current_file = tmp_path / "review_current.md"
        paths.pi_log = tmp_path / "pi.log"
        paths.project_root = tmp_path

        runner = PiRunner.__new__(PiRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()
        runner.before_pi_call = MagicMock()  # type: ignore[method-assign]

        # Create resolved file with thread IDs
        thread_id_1 = "PRRT_kwDORYGRb85wxpuZ"
        thread_id_2 = "PRRT_kwDORYGRb85wxpun"
        paths.pr_resolved_threads_file.write_text(f"{thread_id_1}\n{thread_id_2}\n")
        # Create in-scope file with both threads
        paths.pr_thread_ids_file.write_text(f"{thread_id_1}\n{thread_id_2}\n")

        with patch("fix_die_repeat.runner.run_command") as mock_run:
            # Mock fetch_pr_threads to return threads (not all resolved)
            paths.review_current_file.write_text("--- Thread #1 ---\nRemaining issue\n")
            mock_run.return_value = (0, "", "")

            runner.resolve_pr_threads()

            # Verify gh api was called for each thread
            assert mock_run.call_count >= EXPECTED_THREAD_COUNT

            # Collect all thread IDs that were passed to gh api
            thread_ids_found = []
            for call in mock_run.call_args_list[:EXPECTED_THREAD_COUNT]:
                call_args = call[0][0]
                if call_args[0] == "gh" and call_args[1] == "api":
                    thread_id = call_args[6].split("=")[1]
                    thread_ids_found.append(thread_id)

            # Verify both thread IDs were called (order may vary)
            assert thread_id_1 in thread_ids_found
            assert thread_id_2 in thread_ids_found
            assert len(thread_ids_found) == EXPECTED_THREAD_COUNT

            # Verify the command structure is correct for each call
            for call in mock_run.call_args_list[:2]:
                call_args = call[0][0]
                assert call_args[0] == "gh"
                assert call_args[1] == "api"
                assert call_args[2] == "graphql"
                assert call_args[3] == "-f"
                assert "query=" in call_args[4]
                assert call_args[5] == "-F"
                assert call_args[6].startswith("threadId=")

    def test_resolve_pr_threads_partial_success_and_failure(self, tmp_path: Path) -> None:
        """Test behavior when some threads succeed and others fail."""
        settings = MagicMock()
        settings.pr_review = True
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.pr_resolved_threads_file = tmp_path / "pr_resolved_threads"
        paths.pr_thread_ids_file = tmp_path / "pr_thread_ids"
        paths.pr_threads_hash_file = tmp_path / "pr_threads_hash"
        paths.review_current_file = tmp_path / "review_current.md"
        paths.pi_log = tmp_path / "pi.log"
        paths.project_root = tmp_path

        runner = PiRunner.__new__(PiRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()
        runner.before_pi_call = MagicMock()  # type: ignore[method-assign]

        # Create resolved file with thread IDs
        thread_id_1 = "PRRT_kwDORYGRb85wxpuZ"
        thread_id_2 = "PRRT_kwDORYGRb85wxpun"
        paths.pr_resolved_threads_file.write_text(f"{thread_id_1}\n{thread_id_2}\n")
        paths.pr_thread_ids_file.write_text(f"{thread_id_1}\n{thread_id_2}\n")

        call_count = [0]

        def side_effect_run_command(
            _cmd: str | list[str],
            **_kwargs: object,
        ) -> tuple[int, str, str]:
            """Mock run_command that alternates success/failure."""
            call_count[0] += 1
            # First thread succeeds, second fails
            if call_count[0] == 1:
                return (0, "", "")
            return (1, "", "error")

        with patch("fix_die_repeat.runner.run_command") as mock_run:
            mock_run.side_effect = side_effect_run_command
            paths.review_current_file.write_text("--- Thread #1 ---\nRemaining\n")

            runner.resolve_pr_threads()

            # Should log about partial success
            assert runner.logger.info.called
            # Should log warning for failed thread
            assert runner.logger.warning.called

    def test_resolve_pr_threads_cache_invalidation_and_refetch(self, tmp_path: Path) -> None:
        """Test cache invalidation and refetch logic."""
        settings = MagicMock()
        settings.pr_review = True
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.pr_resolved_threads_file = tmp_path / "pr_resolved_threads"
        paths.pr_thread_ids_file = tmp_path / "pr_thread_ids"
        paths.pr_threads_hash_file = tmp_path / "pr_threads_hash"
        paths.review_current_file = tmp_path / "review_current.md"
        paths.pi_log = tmp_path / "pi.log"
        paths.project_root = tmp_path

        runner = PiRunner.__new__(PiRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()
        runner.before_pi_call = MagicMock()  # type: ignore[method-assign]
        runner.fetch_pr_threads = MagicMock()  # type: ignore[method-assign]

        # Create resolved file with thread IDs
        thread_id = "PRRT_kwDORYGRb85wxpuZ"
        paths.pr_resolved_threads_file.write_text(f"{thread_id}\n")
        paths.pr_thread_ids_file.write_text(f"{thread_id}\n")
        # Create cache hash file
        paths.pr_threads_hash_file.write_text("owner/repo/1")

        with patch("fix_die_repeat.runner.run_command") as mock_run:
            mock_run.return_value = (0, "", "")
            paths.review_current_file.write_text("--- Thread #1 ---\nRemaining\n")

            runner.resolve_pr_threads()

            # Verify cache hash file was deleted (cache invalidation)
            assert not paths.pr_threads_hash_file.exists()
            # Verify fetch_pr_threads was called (refetch)
            assert runner.fetch_pr_threads.called

    def test_resolve_pr_threads_early_exit_all_resolved(self, tmp_path: Path) -> None:
        """Test early exit logic when all threads are resolved."""
        settings = MagicMock()
        settings.pr_review = True
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.pr_resolved_threads_file = tmp_path / "pr_resolved_threads"
        paths.pr_thread_ids_file = tmp_path / "pr_thread_ids"
        paths.pr_threads_hash_file = tmp_path / "pr_threads_hash"
        paths.review_current_file = tmp_path / "review_current.md"
        paths.pi_log = tmp_path / "pi.log"
        paths.project_root = tmp_path

        runner = PiRunner.__new__(PiRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()
        runner.before_pi_call = MagicMock()  # type: ignore[method-assign]

        # Create resolved file with thread IDs
        thread_id = "PRRT_kwDORYGRb85wxpuZ"
        paths.pr_resolved_threads_file.write_text(f"{thread_id}\n")
        paths.pr_thread_ids_file.write_text(f"{thread_id}\n")

        with (
            patch("fix_die_repeat.runner.run_command") as mock_run,
            patch("fix_die_repeat.runner.play_completion_sound") as mock_sound,
            patch("sys.exit") as mock_exit,
        ):
            mock_run.return_value = (0, "", "")
            # Empty review_current means all threads resolved
            paths.review_current_file.write_text("")

            runner.resolve_pr_threads()

            # Verify early exit was triggered
            mock_exit.assert_called_once_with(0)
            mock_sound.assert_called_once()

    def test_resolve_pr_threads_error_logging_non_zero_exit(self, tmp_path: Path) -> None:
        """Test error logging when GraphQL returns non-zero exit codes."""
        settings = MagicMock()
        settings.pr_review = True
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.pr_resolved_threads_file = tmp_path / "pr_resolved_threads"
        paths.pr_thread_ids_file = tmp_path / "pr_thread_ids"
        paths.pr_threads_hash_file = tmp_path / "pr_threads_hash"
        paths.review_current_file = tmp_path / "review_current.md"
        paths.pi_log = tmp_path / "pi.log"
        paths.project_root = tmp_path

        runner = PiRunner.__new__(PiRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()
        runner.before_pi_call = MagicMock()  # type: ignore[method-assign]

        # Create resolved file with thread IDs
        thread_id = "PRRT_kwDORYGRb85wxpuZ"
        paths.pr_resolved_threads_file.write_text(f"{thread_id}\n")
        paths.pr_thread_ids_file.write_text(f"{thread_id}\n")

        with patch("fix_die_repeat.runner.run_command") as mock_run:
            # GraphQL returns error
            mock_run.return_value = (1, "", "GraphQL error: thread not found")
            paths.review_current_file.write_text("--- Thread #1 ---\nRemaining\n")

            runner.resolve_pr_threads()

            # Verify warning was logged for failed thread
            warning_calls = [
                call
                for call in runner.logger.warning.call_args_list
                if "Failed to resolve thread" in str(call)
            ]
            assert len(warning_calls) > 0
