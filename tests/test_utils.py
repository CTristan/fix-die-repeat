"""Tests for utils module."""

import hashlib
import importlib
import importlib.metadata
import sys
from pathlib import Path
from subprocess import CalledProcessError
from unittest.mock import MagicMock, patch

import pytest

from fix_die_repeat.utils import (
    ReviewScope,
    _collect_git_files,
    _should_exclude_file,
    configure_logger,
    detect_large_files,
    determine_review_scope,
    format_duration,
    get_all_tracked_files,
    get_branch_changed_files,
    get_changed_files,
    get_default_branch,
    get_file_line_count,
    get_file_size,
    get_git_revision_hash,
    is_excluded_file,
    is_running_in_dev_mode,
    play_completion_sound,
    run_command,
    sanitize_ntfy_topic,
    send_ntfy_notification,
)

# Constants for utils test values
HELLO_WORLD_SIZE = 13  # len("Hello, World!")
TEST_FILE_LINES = 3
COMMAND_NOT_FOUND_EXIT_CODE = 127
COMMAND_SYNTAX_EXIT_CODE = 2


class TestFormatDuration:
    """Tests for format_duration function."""

    def test_seconds_only(self) -> None:
        """Test formatting seconds only."""
        assert format_duration(45) == "45s"
        assert format_duration(59) == "59s"

    def test_minutes_and_seconds(self) -> None:
        """Test formatting minutes and seconds."""
        assert format_duration(60) == "1m 0s"
        assert format_duration(90) == "1m 30s"
        assert format_duration(125) == "2m 5s"
        assert format_duration(3599) == "59m 59s"

    def test_hours_minutes_seconds(self) -> None:
        """Test formatting hours, minutes, and seconds."""
        assert format_duration(3600) == "1h 0m 0s"
        assert format_duration(3661) == "1h 1m 1s"
        assert format_duration(7265) == "2h 1m 5s"


class TestIsRunningInDevMode:
    """Tests for is_running_in_dev_mode function."""

    def test_returns_bool(self) -> None:
        """Test that is_running_in_dev_mode returns a boolean."""
        result = is_running_in_dev_mode()
        assert isinstance(result, bool)
        assert result in (True, False)

    @patch("fix_die_repeat.utils.importlib.metadata.distribution")
    def test_package_not_found(self, mock_distribution: MagicMock) -> None:
        """Test when package metadata is not found."""
        mock_distribution.side_effect = importlib.metadata.PackageNotFoundError()

        result = is_running_in_dev_mode()

        # Should return False when package is not found
        assert result is False

    @patch("fix_die_repeat.utils.importlib.metadata.distribution")
    def test_editable_install_detection(self, mock_distribution: MagicMock, tmp_path: Path) -> None:
        """Test detection of editable install via direct_url.json."""
        # Create a mock distribution with direct_url.json
        dist_path = tmp_path / "dist"
        dist_path.mkdir()

        direct_url_file = dist_path / "direct_url.json"
        direct_url_file.write_text('{"dir_info": {"editable": true}, "url_info": {}}')

        mock_dist = MagicMock()
        mock_dist._path = str(dist_path)  # noqa: SLF001
        mock_distribution.return_value = mock_dist

        result = is_running_in_dev_mode()

        # Should detect editable install
        assert result is True

    @patch("fix_die_repeat.utils.importlib.metadata.distribution")
    def test_non_editable_install(self, mock_distribution: MagicMock, tmp_path: Path) -> None:
        """Test detection of non-editable install."""
        # Create a mock distribution without direct_url.json or with editable: false
        dist_path = tmp_path / "dist"
        dist_path.mkdir()

        # direct_url.json doesn't exist or has editable: false
        direct_url_file = dist_path / "direct_url.json"
        direct_url_file.write_text('{"dir_info": {"editable": false}, "url_info": {}}')

        mock_dist = MagicMock()
        mock_dist._path = str(dist_path)  # noqa: SLF001
        mock_distribution.return_value = mock_dist

        result = is_running_in_dev_mode()

        # Should detect non-editable (or handle gracefully)
        assert isinstance(result, bool)

    def test_exception_handling(self) -> None:
        """Test that exceptions are handled gracefully."""
        with patch(
            "fix_die_repeat.utils.importlib.metadata.distribution",
            side_effect=OSError("boom"),
        ):
            result = is_running_in_dev_mode()

            # Should return False on any error
            assert result is False


class TestGetFileSize:
    """Tests for get_file_size function."""

    def test_existing_file(self, tmp_path: Path) -> None:
        """Test getting size of existing file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!")
        assert get_file_size(test_file) == HELLO_WORLD_SIZE

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """Test getting size of non-existent file."""
        assert get_file_size(tmp_path / "nonexistent.txt") == 0


class TestGetFileLineCount:
    """Tests for get_file_line_count function."""

    def test_existing_file(self, tmp_path: Path) -> None:
        """Test counting lines in existing file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("line1\nline2\nline3")
        assert get_file_line_count(test_file) == TEST_FILE_LINES

    def test_empty_file(self, tmp_path: Path) -> None:
        """Test counting lines in empty file."""
        test_file = tmp_path / "empty.txt"
        test_file.touch()
        assert get_file_line_count(test_file) == 0

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """Test counting lines in non-existent file."""
        assert get_file_line_count(tmp_path / "nonexistent.txt") == 0

    def test_directory_path(self, tmp_path: Path) -> None:
        """Test counting lines when path is a directory (OSError case)."""
        # A directory will raise OSError when trying to open as a file
        assert get_file_line_count(tmp_path) == 0


class TestDetectLargeFiles:
    """Tests for detect_large_files function."""

    def test_no_large_files(self, tmp_path: Path) -> None:
        """Test when no large files are present."""
        # Create a file under the threshold
        test_file = tmp_path / "test.py"
        test_file.write_text("\n".join(["line"] * 100))

        files = ["test.py"]
        warning = detect_large_files(files, tmp_path, threshold_lines=2000)

        assert warning == ""

    def test_single_large_file(self, tmp_path: Path) -> None:
        """Test detecting a single large file."""
        # Create a file over the threshold
        test_file = tmp_path / "large.py"
        test_file.write_text("\n".join(["line"] * 2500))

        files = ["large.py"]
        warning = detect_large_files(files, tmp_path, threshold_lines=2000)

        assert "CRITICAL WARNING" in warning
        assert "large.py (2500 lines)" in warning

    def test_multiple_large_files(self, tmp_path: Path) -> None:
        """Test detecting multiple large files."""
        # Create multiple large files
        large1 = tmp_path / "large1.py"
        large2 = tmp_path / "large2.py"
        large1.write_text("\n".join(["line"] * 2200))
        large2.write_text("\n".join(["line"] * 3000))

        files = ["large1.py", "large2.py"]
        warning = detect_large_files(files, tmp_path, threshold_lines=2000)

        assert "CRITICAL WARNING" in warning
        assert "large1.py (2200 lines)" in warning
        assert "large2.py (3000 lines)" in warning


class TestSanitizeNtfyTopic:
    """Tests for sanitize_ntfy_topic function."""

    def test_alphanumeric(self) -> None:
        """Test sanitizing alphanumeric string."""
        assert sanitize_ntfy_topic("TestRepo123") == "testrepo123"

    def test_special_chars(self) -> None:
        """Test sanitizing string with special characters."""
        assert sanitize_ntfy_topic("test-repo_name") == "test-repo_name"
        assert sanitize_ntfy_topic("test.repo") == "test.repo"

    def test_spaces_and_specials(self) -> None:
        """Test sanitizing string with spaces and other specials."""
        assert sanitize_ntfy_topic("My Test Repo!") == "my-test-repo"
        assert sanitize_ntfy_topic("test@repo#123") == "test-repo-123"


class TestConfigureLogger:
    """Tests for configure_logger function."""

    def test_log_to_file(self, tmp_path: Path) -> None:
        """Test logging to file."""
        log_file = tmp_path / "test.log"
        logger = configure_logger(fdr_log=log_file, session_log=None, debug=False)

        logger.info("Test message")

        assert log_file.exists()
        content = log_file.read_text()
        assert "Test message" in content

    def test_debug_logging_enabled(self, tmp_path: Path) -> None:
        """Test that debug messages appear when debug is enabled."""
        log_file = tmp_path / "test.log"
        logger = configure_logger(fdr_log=log_file, session_log=None, debug=True)

        logger.debug("Debug message")

        content = log_file.read_text()
        assert "Debug message" in content

    def test_debug_logging_disabled(self, tmp_path: Path) -> None:
        """Test that debug messages are hidden when debug is disabled."""
        log_file = tmp_path / "test.log"
        logger = configure_logger(fdr_log=log_file, session_log=None, debug=False)

        logger.debug("Normal debug message")

        content = log_file.read_text()
        assert "Normal debug message" not in content

    def test_all_log_levels(self, tmp_path: Path) -> None:
        """Test all logging levels (info, warning, error, debug)."""
        log_file = tmp_path / "test.log"
        logger = configure_logger(fdr_log=log_file, session_log=None, debug=True)

        logger.info("Info message")
        logger.warning("Warning message")
        logger.error("Error message")
        logger.debug("Debug message")

        content = log_file.read_text()
        assert "Info message" in content
        assert "Warning message" in content
        assert "Error message" in content
        assert "Debug message" in content

    def test_session_log(self, tmp_path: Path) -> None:
        """Test that session_log also receives messages."""
        fdr_log = tmp_path / "fdr.log"
        session_log = tmp_path / "session.log"
        logger = configure_logger(fdr_log=fdr_log, session_log=session_log, debug=False)

        logger.info("Test message")

        assert fdr_log.exists()
        assert session_log.exists()
        fdr_content = fdr_log.read_text()
        session_content = session_log.read_text()
        assert "Test message" in fdr_content
        assert "Test message" in session_content

    def test_lazy_formatting_args(self, tmp_path: Path) -> None:
        """Test lazy formatting arguments for standard logger compatibility."""
        log_file = tmp_path / "test.log"
        logger = configure_logger(fdr_log=log_file, session_log=None, debug=False)

        logger.info("Value %s", "42")

        content = log_file.read_text()
        assert "Value 42" in content


class TestGetGitRevisionHash:
    """Tests for get_git_revision_hash function."""

    def test_nonexistent_file(self) -> None:
        """Test getting hash of non-existent file."""
        result = get_git_revision_hash(Path("/nonexistent/file.txt"))
        # Should return a fallback hash string
        assert result is not None
        assert isinstance(result, str)
        assert "no_file_" in result

    def test_existing_file(self, tmp_path: Path) -> None:
        """Test getting hash of existing file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!")

        # This will use git hash-object if available, or sha256 fallback
        result = get_git_revision_hash(test_file)
        assert result is not None
        assert isinstance(result, str)
        # Git hash is 40 chars (SHA-1), sha256 is 64 chars
        assert len(result) in (40, 64)

    def test_file_content_affects_hash(self, tmp_path: Path) -> None:
        """Test that different file content produces different hashes."""
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        file1.write_text("content 1")
        file2.write_text("content 2")

        hash1 = get_git_revision_hash(file1)
        hash2 = get_git_revision_hash(file2)

        assert hash1 != hash2


class TestGetChangedFiles:
    """Tests for get_changed_files function."""

    def test_no_git_repo(self, tmp_path: Path) -> None:
        """Test getting changed files when not in a git repo."""
        files = get_changed_files(tmp_path)
        # Should return empty list or handle gracefully
        assert isinstance(files, list)

    def test_excluded_patterns(self) -> None:
        """Test that excluded patterns are filtered out."""
        assert is_excluded_file("yarn.lock")
        assert is_excluded_file("package-lock.json")
        assert is_excluded_file("main.min.js")
        assert not is_excluded_file("main.py")
        assert not is_excluded_file("Cargo.toml")


class TestRunCommand:
    """Tests for run_command function."""

    def test_run_command_success(self) -> None:
        """Test successful command execution."""
        returncode, stdout, _stderr = run_command("echo hello", check=False)

        assert returncode == 0
        assert "hello" in stdout

    def test_run_command_success_with_argv(self) -> None:
        """Test successful command execution with argv list input."""
        returncode, stdout, _stderr = run_command(
            [sys.executable, "-c", "print('hello')"],
            check=False,
        )

        assert returncode == 0
        assert "hello" in stdout

    def test_run_command_failure(self) -> None:
        """Test failed command execution."""
        returncode, _stdout, _stderr = run_command(
            [sys.executable, "-c", "import sys; sys.exit(1)"],
            check=False,
        )

        assert returncode == 1

    def test_run_command_with_check(self) -> None:
        """Test command with check=True raises on failure."""
        with pytest.raises(CalledProcessError):
            run_command([sys.executable, "-c", "import sys; sys.exit(1)"], check=True)

    def test_run_command_not_found(self) -> None:
        """Test command not found error."""
        returncode, _stdout, stderr = run_command("nonexistent-command-xyz-123", check=False)

        assert returncode == COMMAND_NOT_FOUND_EXIT_CODE
        # The error message should contain "not found" or "command not found"
        assert "not found" in stderr.lower() or "command not found" in stderr.lower()

    def test_run_command_invalid_syntax(self) -> None:
        """Test invalid command syntax is returned as a command error."""
        returncode, _stdout, stderr = run_command('echo "unclosed', check=False)

        assert returncode == COMMAND_SYNTAX_EXIT_CODE
        assert "invalid command syntax" in stderr.lower()

    def test_run_command_with_cwd(self, tmp_path: Path) -> None:
        """Test command with custom working directory."""
        # Create a test file in tmp_path
        (tmp_path / "test.txt").write_text("content")

        returncode, stdout, _ = run_command("ls test.txt", cwd=tmp_path, check=False)

        assert returncode == 0
        assert "test.txt" in stdout


class TestPlayCompletionSound:
    """Tests for play_completion_sound function."""

    @patch("fix_die_repeat.utils.run_command")
    def test_no_exception(self, mock_run: MagicMock) -> None:
        """Test that play_completion_sound doesn't raise exceptions."""
        # Mock run_command to prevent actual sound playback
        mock_run.return_value = (0, "", "")

        # Should not raise any exceptions (best-effort function)
        play_completion_sound()


class TestSendNtfyNotification:
    """Tests for send_ntfy_notification function."""

    def test_send_ntfy_success(self, tmp_path: Path) -> None:
        """Test sending notification successfully."""
        log_file = tmp_path / "test.log"
        logger = configure_logger(fdr_log=log_file, session_log=None, debug=False)

        # Mock curl to succeed
        with patch("fix_die_repeat.utils.run_command") as mock_run:
            mock_run.return_value = (0, "", "")

            send_ntfy_notification(
                exit_code=0,
                duration_str="5m 30s",
                repo_name="test-repo",
                ntfy_url="http://localhost:2586",
                logger=logger,
            )

            # Check that curl was called (at least for the 'which curl' check)
            assert mock_run.called

    def test_send_ntfy_curl_not_available(self, tmp_path: Path) -> None:
        """Test notification when curl is not available."""
        log_file = tmp_path / "test.log"
        logger = configure_logger(fdr_log=log_file, session_log=None, debug=False)

        # Mock curl check to fail
        with patch("fix_die_repeat.utils.run_command") as mock_run:
            mock_run.return_value = (127, "", "")

            send_ntfy_notification(
                exit_code=0,
                duration_str="5m 30s",
                repo_name="test-repo",
                ntfy_url="http://localhost:2586",
                logger=logger,
            )

            # Should return early without trying to send
            # Only the 'which curl' call
            assert mock_run.call_count == 1

    def test_send_ntfy_failure_exit_code(self, tmp_path: Path) -> None:
        """Test notification for failed exit code."""
        log_file = tmp_path / "test.log"
        logger = configure_logger(fdr_log=log_file, session_log=None, debug=False)

        with patch("fix_die_repeat.utils.run_command") as mock_run:
            mock_run.return_value = (0, "", "")

            send_ntfy_notification(
                exit_code=1,
                duration_str="1m 0s",
                repo_name="test-repo",
                ntfy_url="http://localhost:2586",
                logger=logger,
            )

            # Check that curl was called
            assert mock_run.called


class TestCollectGitFiles:
    """Tests for _collect_git_files function."""

    def test_collect_git_files_no_git(self, tmp_path: Path) -> None:
        """Test collecting git files outside of git repo."""
        files = _collect_git_files(tmp_path)

        # Should return empty set for non-git directory
        assert isinstance(files, set)


class TestIsExcludedFile:
    """Tests for is_excluded_file function."""

    def test_excluded_lock_file(self) -> None:
        """Test that .lock files are excluded."""
        assert is_excluded_file("package.lock") is True
        assert is_excluded_file("file.lock") is True

    def test_excluded_lock_json(self) -> None:
        """Test that -lock.json files are excluded."""
        assert is_excluded_file("package-lock.json") is True
        assert is_excluded_file("npm-lock.json") is True

    def test_excluded_lock_yaml(self) -> None:
        """Test that -lock.yaml files are excluded."""
        assert is_excluded_file("composer-lock.yaml") is True
        assert is_excluded_file("yarn-lock.yaml") is True

    def test_excluded_go_sum(self) -> None:
        """Test that go.sum is excluded."""
        assert is_excluded_file("go.sum") is True

    def test_excluded_min_files(self) -> None:
        """Test that .min.* files are excluded."""
        assert is_excluded_file("script.min.js") is True
        assert is_excluded_file("style.min.css") is True

    def test_not_excluded_normal_files(self) -> None:
        """Test that normal files are not excluded."""
        assert is_excluded_file("package.json") is False
        assert is_excluded_file("script.js") is False
        assert is_excluded_file("style.css") is False
        assert is_excluded_file("test.py") is False

    def test_custom_exclude_patterns(self) -> None:
        """Test with custom exclude patterns."""
        assert is_excluded_file("test.log", exclude_patterns=["*.log"]) is True
        assert is_excluded_file("test.py", exclude_patterns=["*.log"]) is False

    def test_empty_list_disables_exclusion(self) -> None:
        """Empty list must mean 'no exclusions', not 'fall back to defaults'."""
        # package-lock.json is in DEFAULT_EXCLUDE_PATTERNS; passing [] must NOT exclude it.
        assert is_excluded_file("package-lock.json", exclude_patterns=[]) is False

    def test_case_insensitive_matching(self) -> None:
        """Test that matching is case-insensitive."""
        assert is_excluded_file("PACKAGE.LOCK") is True
        assert is_excluded_file("Package-Lock.Json") is True
        assert is_excluded_file("SCRIPT.MIN.JS") is True


class TestRunCommandErrorHandling:
    """Tests for run_command error handling."""

    def test_run_command_file_not_found_exception(self) -> None:
        """Test run_command handles FileNotFoundError."""
        with patch("fix_die_repeat.utils.subprocess.run", side_effect=FileNotFoundError):
            returncode, _stdout, stderr = run_command("missing-command", check=False)

        assert returncode == COMMAND_NOT_FOUND_EXIT_CODE
        assert "Command not found" in stderr


class TestGetGitRevisionHashFallback:
    """Tests for get_git_revision_hash fallback behavior."""

    def test_hash_fallback_on_oserror(self, tmp_path: Path) -> None:
        """Test fallback to sha256 when git hash-object fails."""
        test_file = tmp_path / "file.txt"
        test_file.write_text("content")
        expected = hashlib.sha256(test_file.read_bytes()).hexdigest()

        with patch("fix_die_repeat.utils.run_command", side_effect=OSError("boom")):
            result = get_git_revision_hash(test_file)

        assert result == expected


class TestCollectGitFilesWithChanges:
    """Tests for _collect_git_files with mocked git output."""

    def test_collect_git_files_with_changes(self, tmp_path: Path) -> None:
        """Test collecting git files when git outputs file names."""
        with patch("fix_die_repeat.utils.run_command") as mock_run:
            mock_run.side_effect = [
                (0, "file1.py\n", ""),
                (0, "file2.py\n", ""),
                (0, "file3.py\n", ""),
            ]

            files = _collect_git_files(tmp_path)

        assert files == {"file1.py", "file2.py", "file3.py"}


class TestShouldExcludeFile:
    """Tests for _should_exclude_file helper."""

    def test_should_exclude_file_glob_suffix(self) -> None:
        """Test excluding files with suffix glob patterns."""
        assert _should_exclude_file("package.lock", ["*.lock"]) is True
        assert _should_exclude_file("package-lock.json", ["*-lock.json"]) is True

    def test_should_exclude_file_glob_substring(self) -> None:
        """Test excluding files with mid-pattern globs."""
        assert _should_exclude_file("script.min.js", ["*.min.*"]) is True
        assert _should_exclude_file("script.js", ["*.min.*"]) is False

    def test_should_exclude_file_exact(self) -> None:
        """Test excluding files with exact patterns."""
        assert _should_exclude_file("skip.log", ["skip.log"]) is True
        assert _should_exclude_file("keep.log", ["skip.log"]) is False

    def test_should_exclude_file_case_insensitive(self) -> None:
        """Test case-insensitive pattern matching."""
        assert _should_exclude_file("PACKAGE.LOCK", ["*.lock"]) is True


class TestGetChangedFilesFiltering:
    """Tests for get_changed_files filtering behavior."""

    def test_get_changed_files_filters_excluded(self, tmp_path: Path) -> None:
        """Test get_changed_files filters missing and excluded files."""
        (tmp_path / "keep.txt").write_text("ok")
        (tmp_path / "skip.log").write_text("skip")

        with patch(
            "fix_die_repeat.utils._collect_git_files",
            return_value={
                "keep.txt",
                "skip.log",
                "missing.txt",
                ".fix-die-repeat/skip.txt",
            },
        ):
            result = get_changed_files(tmp_path, exclude_patterns=["skip.log"])

        assert result == ["keep.txt"]


class TestGetAllTrackedFiles:
    """Tests for get_all_tracked_files function."""

    def test_git_failure_returns_empty(self, tmp_path: Path) -> None:
        """Non-zero git ls-files returncode yields an empty list."""
        with patch(
            "fix_die_repeat.utils.run_command",
            return_value=(1, "", "fatal: not a git repository"),
        ):
            assert get_all_tracked_files(tmp_path) == []

    def test_excludes_fix_die_repeat_prefix(self, tmp_path: Path) -> None:
        """Files under .fix-die-repeat/ are filtered out."""
        (tmp_path / "keep.py").write_text("ok")
        (tmp_path / ".fix-die-repeat").mkdir()
        (tmp_path / ".fix-die-repeat" / "state.txt").write_text("state")

        with patch(
            "fix_die_repeat.utils.run_command",
            return_value=(0, "keep.py\n.fix-die-repeat/state.txt\n", ""),
        ):
            assert get_all_tracked_files(tmp_path) == ["keep.py"]

    def test_filters_missing_on_disk(self, tmp_path: Path) -> None:
        """Files listed by git but missing on disk are skipped."""
        (tmp_path / "present.py").write_text("ok")

        with patch(
            "fix_die_repeat.utils.run_command",
            return_value=(0, "present.py\nghost.py\n", ""),
        ):
            assert get_all_tracked_files(tmp_path) == ["present.py"]

    def test_applies_exclude_patterns(self, tmp_path: Path) -> None:
        """Exclude patterns are applied to the tracked file list."""
        (tmp_path / "keep.txt").write_text("ok")
        (tmp_path / "skip.log").write_text("skip")

        with patch(
            "fix_die_repeat.utils.run_command",
            return_value=(0, "keep.txt\nskip.log\n", ""),
        ):
            result = get_all_tracked_files(tmp_path, exclude_patterns=["*.log"])

        assert result == ["keep.txt"]

    def test_empty_output(self, tmp_path: Path) -> None:
        """Empty git output yields an empty list without errors."""
        with patch(
            "fix_die_repeat.utils.run_command",
            return_value=(0, "\n", ""),
        ):
            assert get_all_tracked_files(tmp_path) == []


class TestGetDefaultBranch:
    """Tests for get_default_branch."""

    def test_symbolic_ref_success(self, tmp_path: Path) -> None:
        """Extracts default branch from symbolic-ref."""
        with patch("fix_die_repeat.utils.run_command") as mock_run:
            mock_run.return_value = (0, "refs/remotes/origin/main\n", "")
            result = get_default_branch(tmp_path)
        assert result == "origin/main"

    def test_symbolic_ref_master(self, tmp_path: Path) -> None:
        """Handles master as default branch."""
        with patch("fix_die_repeat.utils.run_command") as mock_run:
            mock_run.return_value = (0, "refs/remotes/origin/master\n", "")
            result = get_default_branch(tmp_path)
        assert result == "origin/master"

    def test_symbolic_ref_fails_fallback_main(self, tmp_path: Path) -> None:
        """Falls back to local main when symbolic-ref fails."""
        with patch("fix_die_repeat.utils.run_command") as mock_run:
            mock_run.side_effect = [
                (1, "", "fatal: ref not found"),  # symbolic-ref fails
                (0, "abc123\n", ""),  # rev-parse --verify main succeeds
            ]
            result = get_default_branch(tmp_path)
        assert result == "main"

    def test_symbolic_ref_fails_fallback_master(self, tmp_path: Path) -> None:
        """Falls back to local master when symbolic-ref and main fail."""
        with patch("fix_die_repeat.utils.run_command") as mock_run:
            mock_run.side_effect = [
                (1, "", "fatal: ref not found"),  # symbolic-ref fails
                (1, "", "fatal: not a valid ref"),  # main doesn't exist
                (0, "abc123\n", ""),  # rev-parse --verify master succeeds
            ]
            result = get_default_branch(tmp_path)
        assert result == "master"

    def test_no_default_branch_found(self, tmp_path: Path) -> None:
        """Returns None when no default branch can be determined."""
        with patch("fix_die_repeat.utils.run_command") as mock_run:
            mock_run.side_effect = [
                (1, "", "fatal: ref not found"),
                (1, "", "fatal: not a valid ref"),
                (1, "", "fatal: not a valid ref"),
            ]
            result = get_default_branch(tmp_path)
        assert result is None


class TestGetBranchChangedFiles:
    """Tests for get_branch_changed_files."""

    def test_normal_case(self, tmp_path: Path) -> None:
        """Returns files changed between merge-base and HEAD."""
        (tmp_path / "changed.py").write_text("code")
        (tmp_path / "also_changed.py").write_text("code")

        with patch("fix_die_repeat.utils.run_command") as mock_run:
            mock_run.side_effect = [
                (0, "abc123\n", ""),  # merge-base
                (0, "changed.py\nalso_changed.py\n", ""),  # diff --name-only
            ]
            result = get_branch_changed_files(tmp_path, "main")

        assert result == ["also_changed.py", "changed.py"]

    def test_merge_base_failure(self, tmp_path: Path) -> None:
        """Returns empty list when merge-base fails."""
        with patch("fix_die_repeat.utils.run_command") as mock_run:
            mock_run.return_value = (1, "", "fatal: not a valid commit")
            result = get_branch_changed_files(tmp_path, "main")
        assert result == []

    def test_excludes_lock_files(self, tmp_path: Path) -> None:
        """Applies standard exclude patterns."""
        (tmp_path / "changed.py").write_text("code")
        (tmp_path / "package.lock").write_text("lock")

        with patch("fix_die_repeat.utils.run_command") as mock_run:
            mock_run.side_effect = [
                (0, "abc123\n", ""),
                (0, "changed.py\npackage.lock\n", ""),
            ]
            result = get_branch_changed_files(tmp_path, "main")

        assert result == ["changed.py"]

    def test_excludes_missing_files(self, tmp_path: Path) -> None:
        """Filters out files that no longer exist on disk."""
        (tmp_path / "exists.py").write_text("code")

        with patch("fix_die_repeat.utils.run_command") as mock_run:
            mock_run.side_effect = [
                (0, "abc123\n", ""),
                (0, "exists.py\ndeleted.py\n", ""),
            ]
            result = get_branch_changed_files(tmp_path, "main")

        assert result == ["exists.py"]


class TestDetermineReviewScope:
    """Tests for determine_review_scope."""

    def test_uncommitted_changes(self, tmp_path: Path) -> None:
        """Returns UNCOMMITTED when there are dirty files."""
        with patch(
            "fix_die_repeat.utils.get_changed_files",
            return_value=["dirty.py"],
        ):
            scope, files = determine_review_scope(tmp_path)

        assert scope == ReviewScope.UNCOMMITTED
        assert files == ["dirty.py"]

    def test_branch_changes(self, tmp_path: Path) -> None:
        """Returns BRANCH when on a non-default branch with no uncommitted changes."""
        with (
            patch("fix_die_repeat.utils.get_changed_files", return_value=[]),
            patch("fix_die_repeat.utils.run_command") as mock_run,
            patch(
                "fix_die_repeat.utils.get_default_branch",
                return_value="main",
            ),
            patch(
                "fix_die_repeat.utils.get_branch_changed_files",
                return_value=["feature.py"],
            ),
        ):
            mock_run.return_value = (0, "feat/my-branch\n", "")  # branch --show-current
            scope, files = determine_review_scope(tmp_path)

        assert scope == ReviewScope.BRANCH
        assert files == ["feature.py"]

    def test_full_fallback(self, tmp_path: Path) -> None:
        """Returns FULL when no uncommitted or branch changes."""
        with (
            patch("fix_die_repeat.utils.get_changed_files", return_value=[]),
            patch("fix_die_repeat.utils.run_command") as mock_run,
            patch("fix_die_repeat.utils.get_default_branch", return_value="main"),
            patch("fix_die_repeat.utils.get_branch_changed_files", return_value=[]),
        ):
            mock_run.return_value = (0, "main\n", "")  # on default branch
            scope, files = determine_review_scope(tmp_path)

        assert scope == ReviewScope.FULL
        assert files == []

    def test_detached_head(self, tmp_path: Path) -> None:
        """Falls through to FULL when in detached HEAD state."""
        with (
            patch("fix_die_repeat.utils.get_changed_files", return_value=[]),
            patch("fix_die_repeat.utils.run_command") as mock_run,
        ):
            mock_run.return_value = (0, "\n", "")  # empty branch name
            scope, files = determine_review_scope(tmp_path)

        assert scope == ReviewScope.FULL
        assert files == []

    def test_on_default_branch_no_changes(self, tmp_path: Path) -> None:
        """Returns FULL when on the default branch with no uncommitted changes."""
        with (
            patch("fix_die_repeat.utils.get_changed_files", return_value=[]),
            patch("fix_die_repeat.utils.run_command") as mock_run,
            patch("fix_die_repeat.utils.get_default_branch", return_value="main"),
        ):
            mock_run.return_value = (0, "main\n", "")  # on default branch
            scope, files = determine_review_scope(tmp_path)

        assert scope == ReviewScope.FULL
        assert files == []

    def test_excluded_only_uncommitted_still_scoped(self, tmp_path: Path) -> None:
        """Excluded-only uncommitted changes (e.g., lockfiles) stay UNCOMMITTED, not FULL.

        Regression: previously, if the only dirty files matched DEFAULT_EXCLUDE_PATTERNS,
        get_changed_files() returned [] and determine_review_scope() fell through to FULL,
        causing contextual review to run a full-codebase audit instead of reviewing the diff.
        """

        def fake_changed(_root: Path, exclude_patterns: list[str] | None = None) -> list[str]:
            if exclude_patterns == []:
                return ["package-lock.json"]
            return []

        with patch("fix_die_repeat.utils.get_changed_files", side_effect=fake_changed):
            scope, files = determine_review_scope(tmp_path)

        assert scope == ReviewScope.UNCOMMITTED
        assert files == []

    def test_excluded_only_branch_still_scoped(self, tmp_path: Path) -> None:
        """Excluded-only branch changes stay BRANCH, not FULL."""

        def fake_branch(
            _root: Path,
            _default: str,
            exclude_patterns: list[str] | None = None,
        ) -> list[str]:
            if exclude_patterns == []:
                return ["go.sum"]
            return []

        with (
            patch("fix_die_repeat.utils.get_changed_files", return_value=[]),
            patch("fix_die_repeat.utils.run_command") as mock_run,
            patch("fix_die_repeat.utils.get_default_branch", return_value="main"),
            patch("fix_die_repeat.utils.get_branch_changed_files", side_effect=fake_branch),
        ):
            mock_run.return_value = (0, "feat/my-branch\n", "")
            scope, files = determine_review_scope(tmp_path)

        assert scope == ReviewScope.BRANCH
        assert files == []

    def test_no_default_branch_found(self, tmp_path: Path) -> None:
        """Falls through to FULL when default branch can't be determined."""
        with (
            patch("fix_die_repeat.utils.get_changed_files", return_value=[]),
            patch("fix_die_repeat.utils.run_command") as mock_run,
            patch("fix_die_repeat.utils.get_default_branch", return_value=None),
        ):
            mock_run.return_value = (0, "feat/branch\n", "")
            scope, files = determine_review_scope(tmp_path)

        assert scope == ReviewScope.FULL
        assert files == []


class TestPlayCompletionSoundFallback:
    """Tests for play_completion_sound fallback behavior."""

    def test_play_completion_sound_fallback(self) -> None:
        """Test that fallback command runs when no sound files exist."""
        with (
            patch("fix_die_repeat.utils.Path.exists", return_value=False),
            patch("fix_die_repeat.utils.run_command") as mock_run,
            patch("fix_die_repeat.utils.sys.stdout.write") as mock_write,
            patch("fix_die_repeat.utils.sys.stdout.flush") as mock_flush,
        ):
            play_completion_sound()

        mock_run.assert_called_with(
            ["canberra-gtk-play", "-i", "complete", "-d", "fix-die-repeat"],
            check=False,
        )
        mock_write.assert_called_once_with("\a")
        mock_flush.assert_called_once()
