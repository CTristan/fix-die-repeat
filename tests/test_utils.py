"""Tests for utils module."""

from pathlib import Path
from unittest.mock import patch

from fix_die_repeat.utils import (
    Logger,
    detect_large_files,
    format_duration,
    get_file_line_count,
    get_file_size,
    sanitize_ntfy_topic,
)


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


class TestGetFileSize:
    """Tests for get_file_size function."""

    def test_existing_file(self, tmp_path: Path) -> None:
        """Test getting size of existing file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!")
        assert get_file_size(test_file) == 13

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """Test getting size of non-existent file."""
        assert get_file_size(tmp_path / "nonexistent.txt") == 0


class TestGetFileLineCount:
    """Tests for get_file_line_count function."""

    def test_existing_file(self, tmp_path: Path) -> None:
        """Test counting lines in existing file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("line1\nline2\nline3")
        assert get_file_line_count(test_file) == 3

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


class TestLogger:
    """Tests for Logger class."""

    def test_log_to_file(self, tmp_path: Path) -> None:
        """Test logging to file."""
        log_file = tmp_path / "test.log"
        logger = Logger(fdr_log=log_file, session_log=None, debug=False)

        logger.info("Test message")

        assert log_file.exists()
        content = log_file.read_text()
        assert "Test message" in content

    def test_debug_logging(self, tmp_path: Path) -> None:
        """Test that debug messages only appear when debug is enabled."""
        log_file = tmp_path / "test.log"
        logger_debug = Logger(fdr_log=log_file, session_log=None, debug=True)
        logger_normal = Logger(fdr_log=log_file, session_log=None, debug=False)

        logger_debug.debug_log("Debug message")
        logger_normal.debug_log("Normal debug message")

        content = log_file.read_text()
        assert "Debug message" in content
        assert "Normal debug message" not in content

    def test_all_log_levels(self, tmp_path: Path) -> None:
        """Test all logging levels (info, warning, error, debug)."""
        log_file = tmp_path / "test.log"
        logger = Logger(fdr_log=log_file, session_log=None, debug=True)

        logger.info("Info message")
        logger.warning("Warning message")
        logger.error("Error message")
        logger.debug_log("Debug message")

        content = log_file.read_text()
        assert "Info message" in content
        assert "Warning message" in content
        assert "Error message" in content
        assert "Debug message" in content

    def test_session_log(self, tmp_path: Path) -> None:
        """Test that session_log also receives messages."""
        fdr_log = tmp_path / "fdr.log"
        session_log = tmp_path / "session.log"
        logger = Logger(fdr_log=fdr_log, session_log=session_log, debug=False)

        logger.info("Test message")

        assert fdr_log.exists()
        assert session_log.exists()
        fdr_content = fdr_log.read_text()
        session_content = session_log.read_text()
        assert "Test message" in fdr_content
        assert "Test message" in session_content

    def test_file_write_error_handling(self, tmp_path: Path) -> None:
        """Test that logger handles file write errors gracefully."""
        from unittest.mock import MagicMock, patch

        log_file = tmp_path / "test.log"

        # Mock the file open to raise OSError
        mock_open = MagicMock()
        mock_open.return_value.__enter__.side_effect = OSError("Permission denied")

        with patch.object(Path, "open", mock_open):
            logger = Logger(fdr_log=log_file, session_log=None, debug=False)
            # Should not raise exception despite file write error
            logger.info("Test message")

        # No exception means the OSError was caught and handled


class TestGetGitRevisionHash:
    """Tests for get_git_revision_hash function."""

    def test_nonexistent_file(self) -> None:
        """Test getting hash of non-existent file."""
        from fix_die_repeat.utils import get_git_revision_hash

        result = get_git_revision_hash(Path("/nonexistent/file.txt"))
        # Should return a fallback hash string
        assert result is not None
        assert isinstance(result, str)
        assert "no_file_" in result

    def test_existing_file(self, tmp_path: Path) -> None:
        """Test getting hash of existing file."""
        from fix_die_repeat.utils import get_git_revision_hash

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
        from fix_die_repeat.utils import get_git_revision_hash

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
        from fix_die_repeat.utils import get_changed_files

        files = get_changed_files(tmp_path)
        # Should return empty list or handle gracefully
        assert isinstance(files, list)

    def test_excluded_patterns(self) -> None:
        """Test that excluded patterns are filtered out."""
        from fix_die_repeat.utils import is_excluded_file

        assert is_excluded_file("yarn.lock")
        assert is_excluded_file("package-lock.json")
        assert is_excluded_file("main.min.js")
        assert not is_excluded_file("main.py")
        assert not is_excluded_file("Cargo.toml")


class TestRunCommand:
    """Tests for run_command function."""

    def test_run_command_success(self) -> None:
        """Test successful command execution."""
        from fix_die_repeat.utils import run_command

        returncode, stdout, _stderr = run_command("echo hello", check=False)

        assert returncode == 0
        assert "hello" in stdout

    def test_run_command_failure(self) -> None:
        """Test failed command execution."""
        from fix_die_repeat.utils import run_command

        returncode, _stdout, _stderr = run_command("exit 1", check=False)

        assert returncode == 1

    def test_run_command_with_check(self) -> None:
        """Test command with check=True raises on failure."""
        from subprocess import CalledProcessError

        import pytest

        from fix_die_repeat.utils import run_command

        with pytest.raises(CalledProcessError):
            run_command("exit 1", check=True)

    def test_run_command_not_found(self) -> None:
        """Test command not found error."""
        from fix_die_repeat.utils import run_command

        returncode, _stdout, stderr = run_command("nonexistent-command-xyz-123", check=False)

        assert returncode == 127
        # The error message should contain "not found" or "command not found"
        assert "not found" in stderr.lower() or "command not found" in stderr.lower()

    def test_run_command_with_cwd(self, tmp_path: Path) -> None:
        """Test command with custom working directory."""
        from fix_die_repeat.utils import run_command

        # Create a test file in tmp_path
        (tmp_path / "test.txt").write_text("content")

        returncode, stdout, _ = run_command("ls test.txt", cwd=tmp_path, check=False)

        assert returncode == 0
        assert "test.txt" in stdout


class TestPlayCompletionSound:
    """Tests for play_completion_sound function."""

    def test_no_exception(self) -> None:
        """Test that play_completion_sound doesn't raise exceptions."""
        from fix_die_repeat.utils import play_completion_sound

        # Should not raise any exceptions (best-effort function)
        play_completion_sound()


class TestSendNtfyNotification:
    """Tests for send_ntfy_notification function."""

    def test_send_ntfy_success(self, tmp_path: Path) -> None:
        """Test sending notification successfully."""
        from fix_die_repeat.utils import send_ntfy_notification

        log_file = tmp_path / "test.log"
        logger = Logger(fdr_log=log_file, session_log=None, debug=False)

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
        from fix_die_repeat.utils import send_ntfy_notification

        log_file = tmp_path / "test.log"
        logger = Logger(fdr_log=log_file, session_log=None, debug=False)

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
        from fix_die_repeat.utils import send_ntfy_notification

        log_file = tmp_path / "test.log"
        logger = Logger(fdr_log=log_file, session_log=None, debug=False)

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
        from fix_die_repeat.utils import _collect_git_files

        files = _collect_git_files(tmp_path)

        # Should return empty set for non-git directory
        assert isinstance(files, set)


class TestIsExcludedFile:
    """Tests for is_excluded_file function."""

    def test_excluded_lock_file(self) -> None:
        """Test that .lock files are excluded."""
        from fix_die_repeat.utils import is_excluded_file

        assert is_excluded_file("package.lock") is True
        assert is_excluded_file("file.lock") is True

    def test_excluded_lock_json(self) -> None:
        """Test that -lock.json files are excluded."""
        from fix_die_repeat.utils import is_excluded_file

        assert is_excluded_file("package-lock.json") is True
        assert is_excluded_file("npm-lock.json") is True

    def test_excluded_lock_yaml(self) -> None:
        """Test that -lock.yaml files are excluded."""
        from fix_die_repeat.utils import is_excluded_file

        assert is_excluded_file("composer-lock.yaml") is True
        assert is_excluded_file("yarn-lock.yaml") is True

    def test_excluded_go_sum(self) -> None:
        """Test that go.sum is excluded."""
        from fix_die_repeat.utils import is_excluded_file

        assert is_excluded_file("go.sum") is True

    def test_excluded_min_files(self) -> None:
        """Test that .min.* files are excluded."""
        from fix_die_repeat.utils import is_excluded_file

        assert is_excluded_file("script.min.js") is True
        assert is_excluded_file("style.min.css") is True

    def test_not_excluded_normal_files(self) -> None:
        """Test that normal files are not excluded."""
        from fix_die_repeat.utils import is_excluded_file

        assert is_excluded_file("package.json") is False
        assert is_excluded_file("script.js") is False
        assert is_excluded_file("style.css") is False
        assert is_excluded_file("test.py") is False

    def test_custom_exclude_patterns(self) -> None:
        """Test with custom exclude patterns."""
        from fix_die_repeat.utils import is_excluded_file

        assert is_excluded_file("test.log", exclude_patterns=["*.log"]) is True
        assert is_excluded_file("test.py", exclude_patterns=["*.log"]) is False

    def test_case_insensitive_matching(self) -> None:
        """Test that matching is case-insensitive."""
        from fix_die_repeat.utils import is_excluded_file

        assert is_excluded_file("PACKAGE.LOCK") is True
        assert is_excluded_file("Package-Lock.Json") is True
        assert is_excluded_file("SCRIPT.MIN.JS") is True
