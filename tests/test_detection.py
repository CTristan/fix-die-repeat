"""Tests for detection module."""

from pathlib import Path
from unittest.mock import patch

import pytest

from fix_die_repeat.detection import (
    auto_detect_check_cmd,
    get_system_config_path,
    is_interactive,
    prompt_check_command,
    prompt_confirm_command,
    read_config_file,
    resolve_check_cmd,
    validate_check_cmd_or_exit,
    validate_command_exists,
    write_config_file,
)

# Constants for detection tests
TEST_COMMAND = "pytest"
TEST_REASON = "from test file"
MAX_RETRIES = 3


class TestReadConfigFile:
    """Tests for read_config_file function."""

    def test_reads_check_cmd_from_file(self, tmp_path: Path) -> None:
        """Test basic key-value parsing."""
        config_file = tmp_path / "config"
        config_file.write_text("check_cmd = pytest\n")
        result = read_config_file(config_file)
        assert result == "pytest"

    def test_reads_quoted_value(self, tmp_path: Path) -> None:
        r"""Test handles check_cmd = \"value\"."""
        config_file = tmp_path / "config"
        config_file.write_text('check_cmd = "uv run pytest"\n')
        result = read_config_file(config_file)
        assert result == "uv run pytest"

    def test_reads_single_quoted_value(self, tmp_path: Path) -> None:
        """Test handles check_cmd = 'value'."""
        config_file = tmp_path / "config"
        config_file.write_text("check_cmd = 'npm test'\n")
        result = read_config_file(config_file)
        assert result == "npm test"

    def test_ignores_comments(self, tmp_path: Path) -> None:
        """Test lines starting with # are ignored."""
        config_file = tmp_path / "config"
        config_file.write_text("# This is a comment\ncheck_cmd = pytest\n")
        result = read_config_file(config_file)
        assert result == "pytest"

    def test_ignores_empty_lines(self, tmp_path: Path) -> None:
        """Test empty lines are ignored."""
        config_file = tmp_path / "config"
        config_file.write_text("\ncheck_cmd = pytest\n\n")
        result = read_config_file(config_file)
        assert result == "pytest"

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        """Test returns None when file doesn't exist."""
        config_file = tmp_path / "nonexistent"
        result = read_config_file(config_file)
        assert result is None

    def test_returns_none_for_file_without_check_cmd(self, tmp_path: Path) -> None:
        """Test returns None when file exists but no check_cmd."""
        config_file = tmp_path / "config"
        config_file.write_text("other_key = value\n")
        result = read_config_file(config_file)
        assert result is None

    def test_returns_none_for_empty_file(self, tmp_path: Path) -> None:
        """Test returns None for empty file."""
        config_file = tmp_path / "config"
        config_file.write_text("")
        result = read_config_file(config_file)
        assert result is None

    def test_returns_none_for_invalid_path(self) -> None:
        """Test returns None for invalid path type."""
        result = read_config_file(12345)
        assert result is None


class TestWriteConfigFile:
    """Tests for write_config_file function."""

    def test_creates_file_with_check_cmd(self, tmp_path: Path) -> None:
        """Test creates new file with check_cmd."""
        config_file = tmp_path / "config"
        write_config_file(config_file, TEST_COMMAND)
        content = config_file.read_text()
        assert 'check_cmd = "pytest"' in content

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        """Test creates parent directories if needed."""
        config_file = tmp_path / "subdir" / "config"
        write_config_file(config_file, TEST_COMMAND)
        assert config_file.exists()
        content = config_file.read_text()
        assert 'check_cmd = "pytest"' in content

    def test_overwrites_existing_check_cmd(self, tmp_path: Path) -> None:
        """Test updates existing check_cmd."""
        config_file = tmp_path / "config"
        config_file.write_text('check_cmd = "npm test"\n')
        write_config_file(config_file, TEST_COMMAND)
        content = config_file.read_text()
        assert 'check_cmd = "pytest"' in content
        assert "npm test" not in content

    def test_preserves_comments_and_other_keys(self, tmp_path: Path) -> None:
        """Test preserves existing content when updating check_cmd."""
        config_file = tmp_path / "config"
        config_file.write_text("# Comment\nother_key = value\ncheck_cmd = npm test\n")
        write_config_file(config_file, TEST_COMMAND)
        content = config_file.read_text()
        assert "# Comment" in content
        assert "other_key = value" in content
        assert 'check_cmd = "pytest"' in content

    def test_raises_type_error_for_invalid_path(self) -> None:
        """Test raises TypeError for invalid path type."""
        with pytest.raises(TypeError, match="path must be a string"):
            write_config_file(12345, TEST_COMMAND)

    def test_appends_check_cmd_to_existing_file(self, tmp_path: Path) -> None:
        """Test appends check_cmd to file without it."""
        config_file = tmp_path / "config"
        config_file.write_text("# Config file\n")
        write_config_file(config_file, TEST_COMMAND)
        content = config_file.read_text()
        assert "# Config file" in content
        assert 'check_cmd = "pytest"' in content


class TestAutoDetect:
    """Tests for auto_detect_check_cmd function."""

    def test_detects_scripts_ci_sh(self, tmp_path: Path) -> None:
        """Test existing convention honored."""
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        ci_sh = scripts_dir / "ci.sh"
        ci_sh.write_text("#!/bin/bash\necho test\n")
        result = auto_detect_check_cmd(tmp_path)
        assert result is not None
        assert result[0] == "./scripts/ci.sh"

    def test_detects_makefile_test_target(self, tmp_path: Path) -> None:
        """Test detects Makefile with test target."""
        makefile = tmp_path / "Makefile"
        makefile.write_text("test:\n\techo running tests\n")
        result = auto_detect_check_cmd(tmp_path)
        assert result is not None
        assert result[0] == "make test"

    def test_detects_makefile_check_target(self, tmp_path: Path) -> None:
        """Test detects Makefile with check target."""
        makefile = tmp_path / "Makefile"
        makefile.write_text("check:\n\techo checking\n")
        result = auto_detect_check_cmd(tmp_path)
        assert result is not None
        assert result[0] == "make check"

    def test_detects_package_json_with_test_script(self, tmp_path: Path) -> None:
        """Test detects package.json with test script."""
        package_json = tmp_path / "package.json"
        package_json.write_text('{"scripts": {"test": "jest"}}')
        result = auto_detect_check_cmd(tmp_path)
        assert result is not None
        assert result[0] == "npm test"

    def test_ignores_package_json_default_test_script(self, tmp_path: Path) -> None:
        """Test ignores npm's placeholder test script."""
        package_json = tmp_path / "package.json"
        # npm's default placeholder
        package_json.write_text(
            '{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1"}}'
        )
        result = auto_detect_check_cmd(tmp_path)
        assert result is None

    def test_detects_cargo_toml(self, tmp_path: Path) -> None:
        """Test detects Cargo.toml."""
        cargo_toml = tmp_path / "Cargo.toml"
        cargo_toml.write_text('[package]\nname = "test"\n')
        result = auto_detect_check_cmd(tmp_path)
        assert result is not None
        assert result[0] == "cargo test"

    def test_detects_pyproject_with_pytest(self, tmp_path: Path) -> None:
        """Test detects pyproject.toml with pytest config."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[tool.pytest.ini_options]\n")
        result = auto_detect_check_cmd(tmp_path)
        assert result is not None
        assert result[0] == "uv run pytest"

    def test_detects_pyproject_without_pytest(self, tmp_path: Path) -> None:
        """Test detects pyproject.toml without pytest config."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[project]\nname = 'test'\n")
        result = auto_detect_check_cmd(tmp_path)
        assert result is not None
        assert result[0] == "uv run python -m pytest"

    def test_detects_go_mod(self, tmp_path: Path) -> None:
        """Test detects go.mod."""
        go_mod = tmp_path / "go.mod"
        go_mod.write_text("module test\n")
        result = auto_detect_check_cmd(tmp_path)
        assert result is not None
        assert result[0] == "go test ./..."

    def test_detects_gradle(self, tmp_path: Path) -> None:
        """Test detects build.gradle."""
        build_gradle = tmp_path / "build.gradle"
        build_gradle.write_text("plugins { id 'java' }\n")
        result = auto_detect_check_cmd(tmp_path)
        assert result is not None
        assert result[0] == "./gradlew test"

    def test_detects_gradle_kts(self, tmp_path: Path) -> None:
        """Test detects build.gradle.kts."""
        build_gradle_kts = tmp_path / "build.gradle.kts"
        build_gradle_kts.write_text("plugins { java }\n")
        result = auto_detect_check_cmd(tmp_path)
        assert result is not None
        assert result[0] == "./gradlew test"

    def test_detects_pom_xml(self, tmp_path: Path) -> None:
        """Test detects pom.xml."""
        pom_xml = tmp_path / "pom.xml"
        pom_xml.write_text("<project></project>\n")
        result = auto_detect_check_cmd(tmp_path)
        assert result is not None
        assert result[0] == "mvn test"

    def test_detects_mix_exs(self, tmp_path: Path) -> None:
        """Test detects mix.exs."""
        mix_exs = tmp_path / "mix.exs"
        mix_exs.write_text("defmodule Test.Mix\n")
        result = auto_detect_check_cmd(tmp_path)
        assert result is not None
        assert result[0] == "mix test"

    def test_detects_gemfile(self, tmp_path: Path) -> None:
        """Test detects Gemfile."""
        gemfile = tmp_path / "Gemfile"
        gemfile.write_text("source 'https://rubygems.org'\n")
        result = auto_detect_check_cmd(tmp_path)
        assert result is not None
        assert result[0] == "bundle exec rake test"

    def test_returns_none_for_empty_directory(self, tmp_path: Path) -> None:
        """Test returns None for empty directory."""
        result = auto_detect_check_cmd(tmp_path)
        assert result is None

    def test_priority_order(self, tmp_path: Path) -> None:
        """Test scripts/ci.sh takes priority over Makefile."""
        # Create scripts/ci.sh
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        ci_sh = scripts_dir / "ci.sh"
        ci_sh.write_text("#!/bin/bash\n")

        # Also create Makefile with test target
        makefile = tmp_path / "Makefile"
        makefile.write_text("test:\n\techo test\n")

        result = auto_detect_check_cmd(tmp_path)
        assert result is not None
        assert result[0] == "./scripts/ci.sh"

    def test_returns_none_for_invalid_path(self) -> None:
        """Test returns None for invalid path type."""
        result = auto_detect_check_cmd(12345)
        assert result is None


class TestValidateCommandExists:
    """Tests for validate_command_exists function."""

    def test_valid_system_command(self) -> None:
        """Test e.g., ls."""
        assert validate_command_exists("ls") is True

    def test_valid_path_command(self, tmp_path: Path) -> None:
        """Test e.g., ./scripts/ci.sh (create temp script)."""
        script = tmp_path / "test.sh"
        script.write_text("#!/bin/bash\necho test\n")
        script.chmod(0o755)
        assert validate_command_exists(str(script)) is True

    def test_invalid_command(self) -> None:
        """Test nonexistent binary."""
        assert validate_command_exists("thiscommanddoesnotexist12345") is False

    def test_shell_wrapper_passes(self) -> None:
        """Test bash -lc '...' validates bash."""
        assert validate_command_exists("bash -lc 'echo test'") is True

    def test_path_command_not_executable(self, tmp_path: Path) -> None:
        """Test exists but not +x."""
        script = tmp_path / "test.sh"
        script.write_text("#!/bin/bash\necho test\n")
        # Make it not executable
        script.chmod(0o644)
        assert validate_command_exists(str(script)) is False

    def test_empty_command(self) -> None:
        """Test empty command returns False."""
        assert validate_command_exists("") is False

    def test_invalid_syntax(self) -> None:
        """Test invalid command syntax returns False."""
        assert validate_command_exists("cmd with 'unclosed quote") is False

    def test_sh_wrapper_passes(self) -> None:
        """Test sh wrapper validates sh."""
        assert validate_command_exists("sh -c 'echo test'") is True

    def test_zsh_wrapper_passes(self) -> None:
        """Test zsh wrapper validates zsh."""
        assert validate_command_exists("zsh -c 'echo test'") is True


class TestValidateCheckCmdOrExit:
    """Tests for validate_check_cmd_or_exit function."""

    def test_valid_command_does_not_exit(self) -> None:
        """Test valid command doesn't raise SystemExit."""
        # This should not raise
        validate_check_cmd_or_exit("ls")

    def test_invalid_command_exits(self, tmp_path: Path) -> None:
        """Test invalid command raises SystemExit."""
        # Create a mock script path that doesn't exist
        fake_cmd = tmp_path / "nonexistent.sh"
        with pytest.raises(SystemExit) as exc_info:
            validate_check_cmd_or_exit(str(fake_cmd))
        assert exc_info.value.code == 1


class TestPromptConfirmCommand:
    """Tests for prompt_confirm_command function."""

    def test_accepts_y(self) -> None:
        """Test accepts 'y' input."""
        # Mock click.confirm to return True
        with patch("click.confirm", return_value=True):
            result = prompt_confirm_command(TEST_COMMAND, TEST_REASON)
            assert result is True

    def test_accepts_empty_enter(self) -> None:
        """Test accepts Enter (default True)."""
        # Mock click.confirm with default=True
        with patch("click.confirm", return_value=True):
            result = prompt_confirm_command(TEST_COMMAND, TEST_REASON)
            assert result is True

    def test_declines_n(self) -> None:
        """Test declines 'n' input."""
        # Mock click.confirm to return False
        with patch("click.confirm", return_value=False):
            result = prompt_confirm_command(TEST_COMMAND, TEST_REASON)
            assert result is False


class TestPromptCheckCommand:
    """Tests for prompt_check_command function."""

    def test_returns_user_input(self) -> None:
        """Test returns user input."""
        with patch("click.prompt", return_value="pytest"):
            result = prompt_check_command()
            assert result == "pytest"

    def test_retries_on_empty_input(self) -> None:
        """Test retries on empty input."""
        # Mock click.prompt to return empty string first, then pytest
        call_count = [0]

        def mock_prompt(*_args: object, **_kwargs: object) -> str:
            call_count[0] += 1
            if call_count[0] == 1:
                return ""
            return "pytest"

        with patch("click.prompt", side_effect=mock_prompt):
            result = prompt_check_command()
            assert result == "pytest"

    def test_exits_after_max_retries(self) -> None:
        """Test exits after 3 empty inputs."""
        # Mock click.prompt to always return empty string
        with patch("click.prompt", return_value=""):
            with pytest.raises(SystemExit) as exc_info:
                prompt_check_command()
            assert exc_info.value.code == 1


class TestIsInteractive:
    """Tests for is_interactive function."""

    def test_returns_true_for_tty(self) -> None:
        """Test returns True when stdin is a TTY."""
        with patch("sys.stdin.isatty", return_value=True):
            assert is_interactive() is True

    def test_returns_false_for_pipe(self) -> None:
        """Test returns False when stdin is piped."""
        with patch("sys.stdin.isatty", return_value=False):
            assert is_interactive() is False


class TestGetSystemConfigPath:
    """Tests for get_system_config_path function."""

    def test_default_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test returns ~/.config/fix-die-repeat/config by default."""
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        path = get_system_config_path()
        expected = str(Path("~/.config/fix-die-repeat/config").expanduser())
        assert path == expected

    def test_xdg_config_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test respects XDG_CONFIG_HOME env var."""
        monkeypatch.setenv("XDG_CONFIG_HOME", "/custom/config")
        path = get_system_config_path()
        expected = "/custom/config/fix-die-repeat/config"
        assert path == expected


class TestResolveCheckCmd:
    """Integration tests for resolve_check_cmd function."""

    def test_cli_flag_takes_priority(self, tmp_path: Path) -> None:
        """Test CLI flag takes priority over all other sources."""
        result = resolve_check_cmd(
            cli_check_cmd="custom-command",
            project_config_path=tmp_path / "config",
            system_config_path=str(tmp_path / "system_config"),
            project_root=tmp_path,
        )
        assert result == "custom-command"

    def test_project_config_over_system_config(
        self,
        tmp_path: Path,
    ) -> None:
        """Test project config takes priority over system config."""
        project_config = tmp_path / ".fix-die-repeat" / "config"
        project_config.parent.mkdir(parents=True)
        project_config.write_text('check_cmd = "project-command"\n')

        system_config = tmp_path / "system_config"
        system_config.write_text('check_cmd = "system-command"\n')

        result = resolve_check_cmd(
            cli_check_cmd=None,
            project_config_path=project_config,
            system_config_path=str(system_config),
            project_root=tmp_path,
        )
        assert result == "project-command"

    def test_system_config_used_when_no_project_config(
        self,
        tmp_path: Path,
    ) -> None:
        """Test system config is used when no project config."""
        # Create a valid system config
        system_config = tmp_path / "system_config"
        system_config.write_text('check_cmd = "ls"\n')

        # No project config
        project_config = tmp_path / ".fix-die-repeat" / "config"

        result = resolve_check_cmd(
            cli_check_cmd=None,
            project_config_path=project_config,
            system_config_path=str(system_config),
            project_root=tmp_path,
        )
        assert result == "ls"

    def test_system_config_fallthrough_on_bad_command(
        self,
        tmp_path: Path,
    ) -> None:
        """Test system config with bad command falls through to auto-detect."""
        # Create system config with invalid command
        system_config = tmp_path / "system_config"
        system_config.write_text('check_cmd = "nonexistent-command-12345"\n')

        # Create a project file for auto-detection
        cargo_toml = tmp_path / "Cargo.toml"
        cargo_toml.write_text('[package]\nname = "test"\n')

        project_config = tmp_path / ".fix-die-repeat" / "config"

        with patch("fix_die_repeat.detection.is_interactive", return_value=False):
            result = resolve_check_cmd(
                cli_check_cmd=None,
                project_config_path=project_config,
                system_config_path=str(system_config),
                project_root=tmp_path,
            )
        assert result == "cargo test"

    def test_auto_detect_with_confirmation(self, tmp_path: Path) -> None:
        """Test auto-detect with user confirmation."""
        # Create pyproject.toml for detection
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[tool.pytest.ini_options]\n")

        project_config = tmp_path / ".fix-die-repeat" / "config"
        system_config = tmp_path / "system_config"

        with patch("fix_die_repeat.detection.is_interactive", return_value=True):
            with patch("click.confirm", return_value=True):
                result = resolve_check_cmd(
                    cli_check_cmd=None,
                    project_config_path=project_config,
                    system_config_path=str(system_config),
                    project_root=tmp_path,
                )
        assert result == "uv run pytest"

    def test_auto_detect_declined_falls_to_prompt(self, tmp_path: Path) -> None:
        """Test auto-detect declined falls to interactive prompt."""
        # Create Cargo.toml for detection
        cargo_toml = tmp_path / "Cargo.toml"
        cargo_toml.write_text('[package]\nname = "test"\n')

        project_config = tmp_path / ".fix-die-repeat" / "config"
        system_config = tmp_path / "system_config"

        with patch("fix_die_repeat.detection.is_interactive", return_value=True):
            with patch("click.confirm", return_value=False):
                with patch("click.prompt", return_value="custom-test"):
                    result = resolve_check_cmd(
                        cli_check_cmd=None,
                        project_config_path=project_config,
                        system_config_path=str(system_config),
                        project_root=tmp_path,
                    )
        assert result == "custom-test"

    def test_no_tty_exits_with_error(self, tmp_path: Path) -> None:
        """Test non-interactive mode without config exits."""
        project_config = tmp_path / ".fix-die-repeat" / "config"
        system_config = tmp_path / "system_config"

        with patch("fix_die_repeat.detection.is_interactive", return_value=False):
            with pytest.raises(SystemExit) as exc_info:
                resolve_check_cmd(
                    cli_check_cmd=None,
                    project_config_path=project_config,
                    system_config_path=str(system_config),
                    project_root=tmp_path,
                )
        assert exc_info.value.code == 1

    def test_persists_to_project_config_after_auto_detect(
        self,
        tmp_path: Path,
    ) -> None:
        """Test auto-detected command is persisted to project config."""
        # Create pyproject.toml for detection
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[tool.pytest.ini_options]\n")

        project_config = tmp_path / ".fix-die-repeat" / "config"
        system_config = tmp_path / "system_config"

        with patch("fix_die_repeat.detection.is_interactive", return_value=True):
            with patch("click.confirm", return_value=True):
                resolve_check_cmd(
                    cli_check_cmd=None,
                    project_config_path=project_config,
                    system_config_path=str(system_config),
                    project_root=tmp_path,
                )

        # Check that config was persisted
        assert project_config.exists()
        content = project_config.read_text()
        assert 'check_cmd = "uv run pytest"' in content

    def test_persists_to_project_config_after_prompt(self, tmp_path: Path) -> None:
        """Test prompted command is persisted to project config."""
        project_config = tmp_path / ".fix-die-repeat" / "config"
        system_config = tmp_path / "system_config"

        with patch("fix_die_repeat.detection.is_interactive", return_value=True):
            with patch("click.prompt", return_value="pytest"):
                resolve_check_cmd(
                    cli_check_cmd=None,
                    project_config_path=project_config,
                    system_config_path=str(system_config),
                    project_root=tmp_path,
                )

        # Check that config was persisted
        assert project_config.exists()
        content = project_config.read_text()
        assert 'check_cmd = "pytest"' in content

    def test_no_detection_creates_empty_fdr_dir(
        self,
        tmp_path: Path,
    ) -> None:
        """Test that .fix-die-repeat directory is created when needed."""
        project_config = tmp_path / ".fix-die-repeat" / "config"
        system_config = tmp_path / "system_config"

        # Ensure fdr_dir doesn't exist initially
        assert not project_config.parent.exists()

        with patch("fix_die_repeat.detection.is_interactive", return_value=True):
            with patch("click.prompt", return_value="pytest"):
                resolve_check_cmd(
                    cli_check_cmd=None,
                    project_config_path=project_config,
                    system_config_path=str(system_config),
                    project_root=tmp_path,
                )

        # Check that directory was created
        assert project_config.parent.exists()
