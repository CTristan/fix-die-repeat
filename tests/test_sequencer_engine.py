"""Tests for sequencer state, routing, recovery, and concurrency."""

import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from fix_die_repeat import sequencer_engine
from fix_die_repeat.sequencer_engine import (
    DoneOptions,
    SequencerResult,
    SequencerService,
)
from fix_die_repeat.sequencer_state import StateError
from fix_die_repeat.sequencer_workflow import (
    LoadedWorkflow,
    WorkflowValidationError,
    load_workflow,
)

GIT_PATH = shutil.which("git")
RECOVERED_ATTEMPT_NUMBER = 2
HISTORY_OVERFLOW_EVENTS = 2


@pytest.fixture(autouse=True)
def _hermetic_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep engine repositories independent of ambient Git configuration."""
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


WORKFLOW = """\
schema_version: 1
id: check-fix
start: check
steps:
  check:
    instruction: Run checks and write result.json.
    mutates_repository: false
    postconditions:
      - id: result
        validator:
          op: json.pointer_type
          path:
            scope: artifacts
            value: result.json
          pointer: /passed
          expected: boolean
    routes:
      - id: fix-failure
        when:
          op: json.pointer_equals
          path:
            scope: artifacts
            value: result.json
          pointer: /passed
          expected: false
        to: fix
      - id: finish
        when: always
        terminal:
          code: passed
          status: success
          message: Checks passed.
  fix:
    instruction: Fix the failed checks.
    mutates_repository: true
    postconditions:
      - id: changed
        validator:
          op: git.working_tree_changed
    routes:
      - id: recheck
        when: always
        to: check
        repeat: true
"""


def _git(repo: Path, *args: str) -> str:
    if GIT_PATH is None:
        pytest.skip("Git is required for sequencer tests")
    # The executable comes from shutil.which, and subprocess runs fixed argv without a shell.
    result = subprocess.run(
        [GIT_PATH, "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "Tests")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "tracked.txt").write_text("initial\n")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "Initial")
    return repo


def _workflow(tmp_path: Path) -> Path:
    path = tmp_path / "workflow.yaml"
    path.write_text(WORKFLOW)
    return path


def _service(tmp_path: Path) -> SequencerService:
    return SequencerService(home=tmp_path / "fdr-home")


def _write_result(result: SequencerResult, *, passed: bool) -> None:
    artifact_root = Path(result.step["artifact_root"])
    (artifact_root / "result.json").write_text(json.dumps({"passed": passed}))


def test_init_persists_state_outside_clean_repository(tmp_path: Path) -> None:
    """Initialization writes only under FDR_HOME and returns the first instruction."""
    repo = _repo(tmp_path)
    result = _service(tmp_path).init(repo, "run-1", _workflow(tmp_path), [])

    assert result.outcome == "proceed"
    assert result.created
    assert result.step["id"] == "check"
    assert _git(repo, "status", "--porcelain") == ""
    state_path = Path(result.configuration["state_path"])
    assert state_path.is_file()
    assert state_path.is_relative_to(tmp_path / "fdr-home")


def test_repeated_init_is_idempotent(tmp_path: Path) -> None:
    """Compatible init returns persisted state without another transition."""
    repo = _repo(tmp_path)
    workflow = _workflow(tmp_path)
    service = _service(tmp_path)
    first = service.init(repo, "run-1", workflow, [])
    second = service.init(repo, "run-1", workflow, [])

    assert second.outcome == "proceed"
    assert second.repeated
    assert second.state_revision == first.state_revision


def test_relocated_init_remains_repeated(tmp_path: Path) -> None:
    """Relocating a compatible source still returns an existing run."""
    repo = _repo(tmp_path)
    workflow = _workflow(tmp_path)
    service = _service(tmp_path)
    first = service.init(repo, "run-1", workflow, [])
    relocated = tmp_path / "relocated.yaml"
    workflow.rename(relocated)

    second = service.init(repo, "run-1", relocated, [])

    assert second.repeated
    assert second.configuration["relocated"] is True
    assert isinstance(first.state_revision, int)
    assert second.state_revision == first.state_revision + 1


def test_init_revalidates_workflow_under_transition_lock(tmp_path: Path) -> None:
    """A workflow change before lock acquisition cannot enter persisted state."""
    repo = _repo(tmp_path)
    workflow = _workflow(tmp_path)
    calls = 0

    def load_then_change(
        path: Path,
        flags: list[tuple[str, str]],
    ) -> LoadedWorkflow:
        nonlocal calls
        loaded = load_workflow(path, flags)
        calls += 1
        if calls == 1:
            path.write_text("schema_version: 999\n")
        return loaded

    with (
        patch(
            "fix_die_repeat.sequencer_engine.load_workflow",
            side_effect=load_then_change,
        ),
        pytest.raises(WorkflowValidationError),
    ):
        _service(tmp_path).init(repo, "run-1", workflow, [])

    assert not list((tmp_path / "fdr-home").rglob("state.json"))


def test_next_and_status_do_not_advance_read_only_step(tmp_path: Path) -> None:
    """Read-only retrieval keeps one cursor revision."""
    repo = _repo(tmp_path)
    service = _service(tmp_path)
    initialized = service.init(repo, "run-1", _workflow(tmp_path), [])

    next_result = service.next(repo, "run-1")
    status = service.status(repo, "run-1")

    assert next_result.step["id"] == "check"
    assert status.step["id"] == "check"
    assert status.state_revision == initialized.state_revision


def test_done_blocks_failed_postcondition_and_force_records_it(tmp_path: Path) -> None:
    """Force advances only past named postcondition gaps and audits them."""
    repo = _repo(tmp_path)
    service = _service(tmp_path)
    initialized = service.init(repo, "run-1", _workflow(tmp_path), [])

    blocked = service.done(repo, "run-1", "check")
    forced = service.done(repo, "run-1", "check", DoneOptions(force=True))

    assert blocked.outcome == "blocked"
    assert blocked.gaps[0]["subject"] == "result"
    assert forced.outcome == "terminal"
    assert forced.forced
    state = json.loads(Path(initialized.configuration["state_path"]).read_text())
    assert state["history"][-1]["forced"] is True
    assert state["history"][-1]["gaps"][0]["subject"] == "result"


def test_mutating_step_returns_recovery_until_reconciled(tmp_path: Path) -> None:
    """Issued mutating work cannot replay without acknowledgement."""
    repo = _repo(tmp_path)
    service = _service(tmp_path)
    initialized = service.init(repo, "run-1", _workflow(tmp_path), [])
    _write_result(initialized, passed=False)

    fix = service.done(repo, "run-1", "check")
    recovery = service.next(repo, "run-1")
    reissued = service.done(repo, "run-1", "fix", DoneOptions(recover=True))

    assert fix.step["id"] == "fix"
    assert recovery.outcome == "recovery"
    assert reissued.outcome == "proceed"
    assert reissued.step["id"] == "fix"
    state = json.loads(Path(initialized.configuration["state_path"]).read_text())
    assert state["attempt"]["number"] == RECOVERED_ATTEMPT_NUMBER
    assert state["history"][-1]["type"] == "recovery"
    assert state["history"][-1]["step"] == "fix"

    (repo / "tracked.txt").write_text("fixed\n")
    check = service.done(repo, "run-1", "fix")
    assert check.outcome == "proceed"
    assert check.step["id"] == "check"


def test_recovery_is_blocked_for_non_mutating_step(tmp_path: Path) -> None:
    """Recovery acknowledgement applies only to issued mutating work."""
    repo = _repo(tmp_path)
    service = _service(tmp_path)
    service.init(repo, "run-1", _workflow(tmp_path), [])

    result = service.done(repo, "run-1", "check", DoneOptions(recover=True))

    assert result.outcome == "blocked"
    assert result.gaps[0]["code"] == "recovery_not_required"


def test_done_completes_issued_mutating_step_without_recovery(tmp_path: Path) -> None:
    """A completed mutating step advances without a recovery acknowledgement."""
    repo = _repo(tmp_path)
    service = _service(tmp_path)
    initialized = service.init(repo, "run-1", _workflow(tmp_path), [])
    _write_result(initialized, passed=False)
    service.done(repo, "run-1", "check")
    (repo / "tracked.txt").write_text("fixed\n")

    result = service.done(repo, "run-1", "fix")

    assert result.outcome == "proceed"
    assert result.step["id"] == "check"


def test_mutating_step_blocks_when_working_tree_is_unchanged(tmp_path: Path) -> None:
    """An unchanged working tree keeps the mutating step blocked."""
    repo = _repo(tmp_path)
    service = _service(tmp_path)
    initialized = service.init(repo, "run-1", _workflow(tmp_path), [])
    _write_result(initialized, passed=False)
    service.done(repo, "run-1", "check")

    result = service.done(repo, "run-1", "fix")

    assert result.outcome == "blocked"
    assert result.gaps[0]["subject"] == "changed"
    assert result.step["id"] == "fix"


def test_done_rejects_stale_and_out_of_order_steps(tmp_path: Path) -> None:
    """Step names produce deterministic ordering gaps."""
    repo = _repo(tmp_path)
    service = _service(tmp_path)
    initialized = service.init(repo, "run-1", _workflow(tmp_path), [])
    _write_result(initialized, passed=False)
    service.done(repo, "run-1", "check")

    stale = service.done(repo, "run-1", "check")
    out_of_order = service.done(repo, "run-1", "future")

    assert stale.gaps[0]["code"] == "stale_step"
    assert out_of_order.gaps[0]["code"] == "out_of_order_step"


def test_status_survives_missing_and_drifted_workflow(tmp_path: Path) -> None:
    """Persisted state remains visible when configuration cannot continue."""
    repo = _repo(tmp_path)
    workflow = _workflow(tmp_path)
    service = _service(tmp_path)
    service.init(repo, "run-1", workflow, [])
    workflow.write_text(WORKFLOW.replace("Run checks", "Run every check"))

    drifted = service.status(repo, "run-1")
    workflow.unlink()
    missing = service.status(repo, "run-1")

    assert drifted.outcome == "blocked"
    assert drifted.configuration["status"] == "drifted"
    assert drifted.step["id"] == "check"
    assert missing.outcome == "blocked"
    assert missing.configuration["status"] == "missing"
    assert missing.step["id"] == "check"


def test_status_reports_explicitly_missing_workflow(tmp_path: Path) -> None:
    """An explicitly selected missing workflow remains an environment error."""
    repo = _repo(tmp_path)
    workflow = _workflow(tmp_path)
    service = _service(tmp_path)
    service.init(repo, "run-1", workflow, [])

    result = service.status(repo, "run-1", workflow_path=tmp_path / "missing.yaml")

    assert result.outcome == "environment_error"
    assert result.gaps[0]["code"] == "configuration_missing"


def test_configuration_drift_blocks_transition(tmp_path: Path) -> None:
    """A semantic workflow edit cannot continue old state."""
    repo = _repo(tmp_path)
    workflow = _workflow(tmp_path)
    service = _service(tmp_path)
    service.init(repo, "run-1", workflow, [])
    workflow.write_text(WORKFLOW.replace("Run checks", "Run every check"))

    result = service.next(repo, "run-1")

    assert result.outcome == "blocked"
    assert result.gaps[0]["code"] == "configuration_drift"


def test_configuration_check_reports_implicit_invalid_workflow(tmp_path: Path) -> None:
    """A malformed stored workflow blocks without hiding the run."""
    repo = _repo(tmp_path)
    workflow = _workflow(tmp_path)
    service = _service(tmp_path)
    service.init(repo, "run-1", workflow, [])
    workflow.write_text("[")

    result = service.status(repo, "run-1")

    assert result.outcome == "blocked"
    assert result.gaps[0]["code"] == "invalid_yaml"


def test_configuration_check_reports_explicit_unreadable_workflow(tmp_path: Path) -> None:
    """An explicitly selected unreadable workflow is an environment error."""
    repo = _repo(tmp_path)
    service = _service(tmp_path)
    service.init(repo, "run-1", _workflow(tmp_path), [])
    unreadable = tmp_path / "workflow-directory"
    unreadable.mkdir()

    result = service.status(repo, "run-1", workflow_path=unreadable)

    assert result.outcome == "environment_error"
    assert result.gaps[0]["code"] == "workflow_unreadable"


def test_relocation_and_transition_increment_revision_once(tmp_path: Path) -> None:
    """One persisted transition produces one revision even when its source moved."""
    repo = _repo(tmp_path)
    workflow = _workflow(tmp_path)
    service = _service(tmp_path)
    initialized = service.init(repo, "run-1", workflow, [])
    _write_result(initialized, passed=False)
    relocated = tmp_path / "relocated.yaml"
    workflow.rename(relocated)

    result = service.done(
        repo,
        "run-1",
        "check",
        DoneOptions(workflow_path=relocated),
    )

    assert isinstance(initialized.state_revision, int)
    assert result.state_revision == initialized.state_revision + 1
    assert result.configuration["source"] == str(relocated.resolve())


def test_matching_relocation_persists_when_done_is_blocked(tmp_path: Path) -> None:
    """A matching explicit source repairs relocation before postcondition blocking."""
    repo = _repo(tmp_path)
    workflow = _workflow(tmp_path)
    service = _service(tmp_path)
    initialized = service.init(repo, "run-1", workflow, [])
    relocated = tmp_path / "relocated.yaml"
    workflow.rename(relocated)

    result = service.done(
        repo,
        "run-1",
        "check",
        DoneOptions(workflow_path=relocated),
    )

    state = json.loads(Path(initialized.configuration["state_path"]).read_text())
    assert result.outcome == "blocked"
    assert state["workflow_source"] == str(relocated.resolve())
    assert result.state_revision == state["revision"]


def test_history_retains_only_recent_events() -> None:
    """The state history keeps its bounded, ordered tail."""
    state: dict[str, Any] = {"history": []}
    total = sequencer_engine.MAX_HISTORY_EVENTS + HISTORY_OVERFLOW_EVENTS

    for index in range(total):
        sequencer_engine._append_history(state, {"index": index})

    history = state["history"]
    assert isinstance(history, list)
    assert len(history) == sequencer_engine.MAX_HISTORY_EVENTS
    assert history[0]["index"] == HISTORY_OVERFLOW_EVENTS
    assert history[-1]["index"] == total - 1


@pytest.mark.parametrize(
    ("method", "arguments"),
    [
        ("status", ()),
        ("next", ()),
        ("done", ("check",)),
    ],
)
def test_state_disappearance_after_lock_returns_missing(
    tmp_path: Path,
    method: str,
    arguments: tuple[str, ...],
) -> None:
    """Commands recheck state after acquiring the transition lock."""
    repo = _repo(tmp_path)
    service = _service(tmp_path)

    with patch("fix_die_repeat.sequencer_engine._state_exists", side_effect=[True, False]):
        result = getattr(service, method)(repo, "run-1", *arguments)

    assert result.gaps[0]["code"] == "run_not_initialized"


def test_concurrent_done_calls_produce_one_cursor_transition(tmp_path: Path) -> None:
    """The lock permits one transition from a shared revision."""
    repo = _repo(tmp_path)
    service = _service(tmp_path)
    initialized = service.init(repo, "run-1", _workflow(tmp_path), [])
    _write_result(initialized, passed=False)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: service.done(repo, "run-1", "check"),
                range(2),
            ),
        )

    assert {result.outcome for result in results} == {"proceed", "blocked"}
    state = json.loads(Path(initialized.configuration["state_path"]).read_text())
    assert state["cursor"] == "fix"
    assert len(state["history"]) == 1


def test_missing_run_is_blocked(tmp_path: Path) -> None:
    """Commands cannot invent state without init."""
    result = _service(tmp_path).status(_repo(tmp_path), "missing")

    assert result.outcome == "blocked"
    assert result.gaps[0]["code"] == "run_not_initialized"


@pytest.mark.parametrize("key", ["root", "common_dir"])
def test_persisted_repository_identity_mismatch_fails_closed(
    tmp_path: Path,
    key: str,
) -> None:
    """Copied state cannot attach to a different repository identity."""
    repo = _repo(tmp_path)
    service = _service(tmp_path)
    initialized = service.init(repo, "run-1", _workflow(tmp_path), [])
    state_path = Path(initialized.configuration["state_path"])
    state = json.loads(state_path.read_text())
    state["repository"][key] = str(tmp_path / "different-repo")
    state_path.write_text(json.dumps(state))

    with pytest.raises(StateError, match="repository identity"):
        service.status(repo, "run-1")
