"""End-to-end subprocess test for the shipped sequencer example."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from fix_die_repeat.sequencer_engine import EXIT_CODES

GIT_PATH = shutil.which("git")
PROJECT_ROOT = Path(__file__).parents[1]
EXAMPLE_ROOT = PROJECT_ROOT / "examples" / "sequencer" / "check-fix-review"
PROCESS_TIMEOUT_SECONDS = 60


def _run(arguments: list[str], *, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=PROCESS_TIMEOUT_SECONDS,
    )


def _git(repo: Path, *arguments: str) -> None:
    if GIT_PATH is None:
        pytest.skip("Git is required for the sequencer example")
    subprocess.run(
        [GIT_PATH, "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        timeout=PROCESS_TIMEOUT_SECONDS,
    )


def _response(
    repo: Path,
    environment: dict[str, str],
    *arguments: str,
) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
    result = _run(
        [
            sys.executable,
            "-m",
            "fix_die_repeat.cli",
            "sequencer",
            "--run-id",
            "example",
            "--repo",
            str(repo),
            *arguments,
        ],
        environment=environment,
    )
    return result, json.loads(result.stdout)


def _run_agent(
    script: str,
    target: Path,
    environment: dict[str, str],
    artifact_root: Path | None = None,
) -> None:
    arguments = [sys.executable, str(EXAMPLE_ROOT / "agent" / script), str(target)]
    if artifact_root is not None:
        arguments.append(str(artifact_root))
    result = _run(arguments, environment=environment)
    assert result.returncode == 0, result.stderr


def _assert_root_fix_instruction(payload: dict[str, object]) -> None:
    step = payload["step"]
    assert isinstance(step, dict)
    assert "app.txt" in step["instruction"]
    assert "target/app.txt" not in step["instruction"]


def _artifact_root(payload: dict[str, object]) -> Path:
    step = payload["step"]
    assert isinstance(step, dict)
    return Path(step["artifact_root"])


def test_check_fix_review_example_completes(tmp_path: Path) -> None:
    """The public protocol survives recovery, force, repetition, and completion."""
    repo = tmp_path / "repo"
    shutil.copytree(EXAMPLE_ROOT / "target", repo)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "Tests")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "add", "app.txt")
    _git(repo, "commit", "-m", "Initial fixture")

    environment = os.environ.copy()
    environment["FDR_HOME"] = str(tmp_path / "fdr-home")
    environment["PYTHONPATH"] = str(PROJECT_ROOT)

    initialized, init_payload = _response(
        repo,
        environment,
        "init",
        "--workflow",
        str(EXAMPLE_ROOT / "workflow.yaml"),
    )
    assert initialized.returncode == EXIT_CODES["proceed"]
    artifact_root = _artifact_root(init_payload)
    target = repo / "app.txt"

    _run_agent("check.py", target, environment, artifact_root)
    issued, issued_payload = _response(repo, environment, "done", "check")
    assert issued.returncode == EXIT_CODES["proceed"]
    _assert_root_fix_instruction(issued_payload)

    recovery, _ = _response(repo, environment, "next")
    assert recovery.returncode == EXIT_CODES["recovery"]
    recovered, _ = _response(repo, environment, "done", "fix", "--recover")
    assert recovered.returncode == EXIT_CODES["proceed"]

    forced, forced_payload = _response(repo, environment, "done", "fix", "--force")
    assert forced.returncode == EXIT_CODES["proceed"]
    assert forced_payload["forced"] is True
    repeated_check_artifact_root = _artifact_root(forced_payload)

    _run_agent("check.py", target, environment, repeated_check_artifact_root)
    repeated, _ = _response(repo, environment, "done", "check")
    assert repeated.returncode == EXIT_CODES["proceed"]
    recovery, _ = _response(repo, environment, "next")
    assert recovery.returncode == EXIT_CODES["recovery"]
    recovered, _ = _response(repo, environment, "done", "fix", "--recover")
    assert recovered.returncode == EXIT_CODES["proceed"]

    _run_agent("fix.py", target, environment)
    recheck, recheck_payload = _response(repo, environment, "done", "fix")
    assert recheck.returncode == EXIT_CODES["proceed"]
    recheck_artifact_root = _artifact_root(recheck_payload)
    _run_agent("check.py", target, environment, recheck_artifact_root)
    review, review_payload = _response(repo, environment, "done", "check")
    assert review.returncode == EXIT_CODES["proceed"]

    review_artifact_root = _artifact_root(review_payload)
    _run_agent("review.py", target, environment, review_artifact_root)
    terminal, terminal_payload = _response(repo, environment, "done", "review")
    assert terminal.returncode == EXIT_CODES["terminal"]
    terminal_state = terminal_payload["terminal"]
    assert isinstance(terminal_state, dict)
    assert terminal_state["code"] == "checks-and-review-passed"
    assert terminal_state["status"] == "success"
