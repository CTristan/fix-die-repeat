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
    _collect_git_files,
    _should_exclude_file,
    configure_logger,
    detect_large_files,
    format_duration,
    get_changed_files,
    get_file_line_count,
    get_file_size,
    get_git_revision_hash,
    is_excluded_file,
    is_running_in_dev_mode,
    play_completion_sound,
    rotate_file,
    run_command,
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
    def test_editable_install_detection(self, mock_distribution: MagicMock) -> None:
        """Test detection of editable install via direct_url.json."""
        # Create a mock distribution with direct_url.json
        mock_dist = MagicMock()
        content = '{"dir_info": {"editable": true}, "url_info": {}}'
        mock_dist.read_text.return_value = content
        mock_distribution.return_value = mock_dist

        result = is_running_in_dev_mode()

        # Should detect editable install
        assert result is True
        # Verify that read_text was called with the correct filename
        mock_dist.read_text.assert_called_once_with("direct_url.json")

    @patch("fix_die_repeat.utils.importlib.metadata.distribution")
    def test_non_editable_install(self, mock_distribution: MagicMock) -> None:
        """Test detection of non-editable install."""
        # Create a mock distribution with editable: false
        mock_dist = MagicMock()
        content = '{"dir_info": {"editable": false}, "url_info": {}}'
        mock_dist.read_text.return_value = content
        mock_distribution.return_value = mock_dist

        result = is_running_in_dev_mode()

        # Should detect non-editable (or handle gracefully)
        assert isinstance(result, bool)
        # Verify that read_text was called with the correct filename
        mock_dist.read_text.assert_called_once_with("direct_url.json")

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


class TestRotateFile:
    """Tests for rotate_file function."""

    def test_rotate_file_no_rotation_under_threshold(self, tmp_path: Path) -> None:
        """Test that file is not rotated when under threshold."""
        test_file = tmp_path / "test.yaml"
        test_file.write_text("line1\nline2")

        result = rotate_file(test_file, max_lines=10)

        assert result is None
        assert test_file.exists()
        assert not any(tmp_path.glob("test-*.yaml"))

    def test_rotate_file_rename_on_rotation(self, tmp_path: Path) -> None:
        """Test that file is renamed when over threshold."""
        test_file = tmp_path / "test.yaml"
        test_file.write_text("line1\nline2\nline3")

        result = rotate_file(test_file, max_lines=2, date_suffix="2026-03")

        expected_rotated = tmp_path / "test-2026-03.yaml"
        assert result == expected_rotated
        assert not test_file.exists()
        assert expected_rotated.exists()
        assert expected_rotated.read_text() == "line1\nline2\nline3"

    def test_rotate_file_append_if_exists(self, tmp_path: Path) -> None:
        """Test that file content is appended if rotated file already exists."""
        test_file = tmp_path / "test.yaml"
        test_file.write_text("line1\nline2\nline3")

        rotated_file = tmp_path / "test-2026-03.yaml"
        rotated_file.write_text("old content\n")

        result = rotate_file(test_file, max_lines=2, date_suffix="2026-03")

        assert result == rotated_file
        assert not test_file.exists()
        # My new implementation uses YAML separator and ensures newlines
        content = rotated_file.read_text()
        assert "old content\n" in content
        assert "\n---\n" in content
        assert "line1" in content

    def test_rotate_file_no_separator_for_txt(self, tmp_path: Path) -> None:
        """Test that non-YAML files don't get a separator on append."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("line1\nline2\nline3")

        rotated_file = tmp_path / "test-2026-03.txt"
        rotated_file.write_text("old text")

        rotate_file(test_file, max_lines=2, date_suffix="2026-03")

        # My new implementation ensures newlines between appends
        assert rotated_file.read_text() == "old text\nline1\nline2\nline3\n"

    def test_rotate_file_is_directory(self, tmp_path: Path) -> None:
        """Test rotation fails if path is a directory."""
        test_dir = tmp_path / "test_dir"
        test_dir.mkdir()
        result = rotate_file(test_dir)
        assert result is None

    def test_rotate_file_not_existent(self, tmp_path: Path) -> None:
        """Test rotation of non-existent file."""
        result = rotate_file(tmp_path / "missing.yaml")
        assert result is None

    def test_rotate_file_malformed_yaml(self, tmp_path: Path) -> None:
        """Test rotation with malformed YAML file."""
        test_file = tmp_path / "test.yaml"
        # Invalid YAML (mapping key without value)
        test_file.write_text("invalid: yaml: content\n" * 10)

        rotated_file = tmp_path / "test-2026-03.yaml"
        rotated_file.write_text("old content\n")

        # rotate_file calls get_file_line_count internally to determine line count.
        # With 10 lines and max_lines=2, rotation should occur.
        rotate_file(test_file, max_lines=2, date_suffix="2026-03")

        # Should fallback to string concatenation
        content = rotated_file.read_text()
        assert "old content\n" in content
        assert "\n---\n" in content
        assert "invalid: yaml: content" in content

    def test_rotate_file_permission_error(self, tmp_path: Path) -> None:
        """Test rotation behavior under permission errors."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content\n" * 10)

        # Mock open to raise PermissionError
        with patch.object(Path, "open", side_effect=PermissionError("Permission denied")):
            with pytest.raises(PermissionError):
                rotate_file(test_file, max_lines=2)
