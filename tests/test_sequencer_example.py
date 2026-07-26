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


@pytest.fixture(autouse=True)
def _hermetic_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the example repository independent of ambient Git configuration."""
    empty_config = tmp_path / "empty-gitconfig"
    empty_config.touch()
    empty_template = tmp_path / "empty-template"
    empty_template.mkdir()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_config))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(empty_template))
    for variable in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(variable, raising=False)


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
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.fail(
            f"invalid JSON response (rc={result.returncode}): "
            f"stdout={result.stdout!r}; stderr={result.stderr!r}",
            pytrace=False,
        )
    return result, payload


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


def _complete_check(
    repo: Path,
    target: Path,
    environment: dict[str, str],
    payload: dict[str, object],
) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
    _run_agent("check.py", target, environment, _artifact_root(payload))
    return _response(repo, environment, "done", "check")


def _complete_review(
    repo: Path,
    target: Path,
    environment: dict[str, str],
    payload: dict[str, object],
) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
    _run_agent("review.py", target, environment, _artifact_root(payload))
    return _response(repo, environment, "done", "review")


def _example_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(EXAMPLE_ROOT / "target", repo)
    _git(repo, "init")
    _git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "Tests")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "add", "app.txt")
    _git(repo, "commit", "-m", "Initial fixture")
    return repo


def test_check_fix_review_example_completes(tmp_path: Path) -> None:
    """The public protocol survives recovery, force, repetition, and completion."""
    repo = _example_repo(tmp_path)

    environment = os.environ.copy()
    environment["FDR_HOME"] = str(tmp_path / "fdr-home")
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        f"{PROJECT_ROOT}{os.pathsep}{existing_pythonpath}"
        if existing_pythonpath
        else str(PROJECT_ROOT)
    )

    initialized, init_payload = _response(
        repo,
        environment,
        "init",
        "--workflow",
        str(EXAMPLE_ROOT / "workflow.yaml"),
    )
    assert initialized.returncode == EXIT_CODES["proceed"]
    target = repo / "app.txt"

    issued, issued_payload = _complete_check(
        repo,
        target,
        environment,
        init_payload,
    )
    assert issued.returncode == EXIT_CODES["proceed"]
    _assert_root_fix_instruction(issued_payload)

    recovery, _ = _response(repo, environment, "next")
    assert recovery.returncode == EXIT_CODES["recovery"]
    recovered, _ = _response(repo, environment, "done", "fix", "--recover")
    assert recovered.returncode == EXIT_CODES["proceed"]

    forced, forced_payload = _response(repo, environment, "done", "fix", "--force")
    assert forced.returncode == EXIT_CODES["proceed"]
    assert forced_payload["forced"] is True
    repeated, _ = _complete_check(
        repo,
        target,
        environment,
        forced_payload,
    )
    assert repeated.returncode == EXIT_CODES["proceed"]
    recovery, _ = _response(repo, environment, "next")
    assert recovery.returncode == EXIT_CODES["recovery"]
    recovered, _ = _response(repo, environment, "done", "fix", "--recover")
    assert recovered.returncode == EXIT_CODES["proceed"]

    _run_agent("fix.py", target, environment)
    recheck, recheck_payload = _response(repo, environment, "done", "fix")
    assert recheck.returncode == EXIT_CODES["proceed"]
    review, review_payload = _complete_check(
        repo,
        target,
        environment,
        recheck_payload,
    )
    assert review.returncode == EXIT_CODES["proceed"]

    target.write_text("regressed\n")
    finding, finding_payload = _complete_review(
        repo,
        target,
        environment,
        review_payload,
    )
    assert finding.returncode == EXIT_CODES["proceed"]
    _assert_root_fix_instruction(finding_payload)

    _run_agent("fix.py", target, environment)
    fixed, fixed_payload = _response(repo, environment, "done", "fix")
    assert fixed.returncode == EXIT_CODES["proceed"]
    reviewed, reviewed_payload = _complete_check(
        repo,
        target,
        environment,
        fixed_payload,
    )
    assert reviewed.returncode == EXIT_CODES["proceed"]
    terminal, terminal_payload = _complete_review(
        repo,
        target,
        environment,
        reviewed_payload,
    )
    assert terminal.returncode == EXIT_CODES["terminal"]
    terminal_state = terminal_payload["terminal"]
    assert isinstance(terminal_state, dict)
    assert terminal_state["code"] == "checks-and-review-passed"
    assert terminal_state["status"] == "success"
