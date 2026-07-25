"""Subprocess-style tests for the sequencer CLI protocol."""

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner, Result

from fix_die_repeat.cli import main
from fix_die_repeat.sequencer_engine import EXIT_CODES

GIT_PATH = shutil.which("git")

WORKFLOW = """\
schema_version: 1
id: one-step
start: check
steps:
  check:
    instruction: Write result.json.
    mutates_repository: false
    postconditions:
      - id: result
        validator:
          op: json.pointer_equals
          path:
            scope: artifacts
            value: result.json
          pointer: /passed
          expected: true
    routes:
      - id: finish
        when: always
        terminal:
          code: passed
          status: success
          message: Passed.
"""


def _git(repo: Path, *args: str) -> None:
    if GIT_PATH is None:
        pytest.skip("Git is required for sequencer CLI tests")
    subprocess.run(
        [GIT_PATH, "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    return repo


def _workflow(tmp_path: Path) -> Path:
    path = tmp_path / "workflow.yaml"
    path.write_text(WORKFLOW)
    return path


def _invoke(
    runner: CliRunner,
    repo: Path,
    command: list[str],
) -> Result:
    return runner.invoke(
        main,
        [
            "sequencer",
            "--run-id",
            "run-1",
            "--repo",
            str(repo),
            *command,
        ],
    )


def _payload(result: Result) -> dict[str, object]:
    return json.loads(result.stdout)


def test_root_help_lists_sequencer_and_preserves_loop_options() -> None:
    """The new namespace does not replace existing root options."""
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "--check-cmd" in result.output
    assert "sequencer" in result.output


def test_init_emits_one_json_response(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Successful commands write one machine response and no diagnostic."""
    monkeypatch.setenv("FDR_HOME", str(tmp_path / "home"))
    runner = CliRunner()
    result = _invoke(
        runner,
        _repo(tmp_path),
        ["init", "--workflow", str(_workflow(tmp_path))],
    )

    payload = _payload(result)
    assert result.exit_code == 0
    assert payload["outcome"] == "proceed"
    assert payload["protocol_version"] == 1
    assert result.stderr == ""


def test_blocked_and_terminal_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Domain outcomes preserve the documented prototype exit codes."""
    monkeypatch.setenv("FDR_HOME", str(tmp_path / "home"))
    runner = CliRunner()
    repo = _repo(tmp_path)
    workflow = _workflow(tmp_path)
    initialized = _invoke(runner, repo, ["init", "--workflow", str(workflow)])
    step_payload = _payload(initialized)["step"]
    assert isinstance(step_payload, dict)
    artifact_root = Path(step_payload["artifact_root"])

    blocked = _invoke(runner, repo, ["done", "check"])
    (artifact_root / "result.json").write_text('{"passed": true}')
    terminal = _invoke(runner, repo, ["done", "check"])

    assert blocked.exit_code == EXIT_CODES["blocked"]
    assert _payload(blocked)["outcome"] == "blocked"
    assert terminal.exit_code == EXIT_CODES["terminal"]
    terminal_payload = _payload(terminal)["terminal"]
    assert isinstance(terminal_payload, dict)
    assert terminal_payload["status"] == "success"


def test_usage_error_is_json_with_exit_64(tmp_path: Path) -> None:
    """Sequencer parsing errors do not fall back to Click's text protocol."""
    result = _invoke(CliRunner(), _repo(tmp_path), ["init"])

    assert result.exit_code == EXIT_CODES["usage_error"]
    payload = _payload(result)
    assert payload["command"] == "init"
    assert payload["outcome"] == "usage_error"
    assert "Error:" in result.stderr


def test_environment_error_is_json_with_exit_2(tmp_path: Path) -> None:
    """Repository probe failures stay distinct from usage failures."""
    workflow = _workflow(tmp_path)
    result = _invoke(
        CliRunner(),
        tmp_path,
        ["init", "--workflow", str(workflow)],
    )

    assert result.exit_code == EXIT_CODES["environment_error"]
    assert _payload(result)["outcome"] == "environment_error"


def test_internal_error_is_json_with_exit_70(tmp_path: Path) -> None:
    """Unexpected failures do not leak a traceback into stdout."""
    with patch(
        "fix_die_repeat.cli.SequencerService.init",
        side_effect=RuntimeError("unexpected"),
    ):
        result = _invoke(
            CliRunner(),
            _repo(tmp_path),
            ["init", "--workflow", str(_workflow(tmp_path))],
        )

    assert result.exit_code == EXIT_CODES["internal_error"]
    assert _payload(result)["outcome"] == "internal_error"
    assert "unexpected" in result.stderr


def test_interruption_is_json_with_exit_130(tmp_path: Path) -> None:
    """Catchable interruption returns the documented retry signal."""
    with patch(
        "fix_die_repeat.cli.SequencerService.init",
        side_effect=KeyboardInterrupt,
    ):
        result = _invoke(
            CliRunner(),
            _repo(tmp_path),
            ["init", "--workflow", str(_workflow(tmp_path))],
        )

    assert result.exit_code == EXIT_CODES["interrupted"]
    assert _payload(result)["outcome"] == "interrupted"


def test_force_and_recover_are_mutually_exclusive(tmp_path: Path) -> None:
    """Invalid command combinations are usage errors before state access."""
    result = _invoke(
        CliRunner(),
        _repo(tmp_path),
        ["done", "check", "--force", "--recover"],
    )

    assert result.exit_code == EXIT_CODES["usage_error"]
    payload = _payload(result)
    assert payload["command"] == "done"
    assert payload["outcome"] == "usage_error"


@pytest.mark.parametrize(
    "arguments",
    [
        ["sequencer", "--run-id", "../escape", "status"],
        [
            "sequencer",
            "--run-id",
            "run-1",
            "init",
            "--workflow",
            "workflow.yaml",
            "--flag",
            "missing=true",
        ],
        [
            "sequencer",
            "--run-id",
            "run-1",
            "init",
            "--workflow",
            "workflow.yaml",
            "--flag",
            "review=true",
            "--flag",
            "review=false",
        ],
    ],
)
def test_invalid_protocol_values_are_usage_errors(
    tmp_path: Path,
    arguments: list[str],
) -> None:
    """Invalid identifiers and supplied flags fail at the protocol boundary."""
    workflow = _workflow(tmp_path)
    normalized = [str(workflow) if value == "workflow.yaml" else value for value in arguments]
    result = CliRunner().invoke(main, normalized)

    assert result.exit_code == EXIT_CODES["usage_error"]
    assert _payload(result)["outcome"] == "usage_error"
