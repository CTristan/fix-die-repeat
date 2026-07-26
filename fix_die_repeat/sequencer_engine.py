"""Sequencer routing, recovery, and persisted state engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fix_die_repeat.config import get_fdr_home
from fix_die_repeat.sequencer_evaluator import (
    EvaluationContext,
    OperationResult,
    evaluate_condition,
    evaluate_operation,
)
from fix_die_repeat.sequencer_git import (
    GitSnapshot,
    RepositoryInfo,
    capture_snapshot,
    resolve_repository,
)
from fix_die_repeat.sequencer_state import (
    PROTOCOL_VERSION,
    STATE_SCHEMA_VERSION,
    RunPaths,
    SequencerLock,
    StateError,
    read_state,
    run_paths,
    write_state,
)
from fix_die_repeat.sequencer_workflow import (
    LoadedWorkflow,
    Route,
    Step,
    WorkflowValidationError,
    load_workflow,
)

EXIT_CODES = {
    "proceed": 0,
    "environment_error": 2,
    "terminal": 3,
    "blocked": 4,
    "recovery": 5,
    "usage_error": 64,
    "internal_error": 70,
    "configuration_error": 78,
    "interrupted": 130,
}


@dataclass
class SequencerResult:
    """Stable command result before JSON serialization."""

    command: str
    outcome: str
    message: str
    repository: str | None = None
    workflow_id: str | None = None
    run_id: str | None = None
    state_revision: int | None = None
    configuration: dict[str, Any] = field(default_factory=dict)
    step: dict[str, Any] = field(default_factory=dict)
    forced: bool = False
    created: bool = False
    repeated: bool = False
    gaps: list[dict[str, str]] = field(default_factory=list)
    route: dict[str, Any] = field(default_factory=dict)
    terminal: dict[str, Any] = field(default_factory=dict)
    recovery: dict[str, Any] = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        """Return the documented process exit code."""
        return EXIT_CODES[self.outcome]

    def to_dict(self) -> dict[str, Any]:
        """Return the versioned machine-readable response."""
        return {
            "protocol_version": PROTOCOL_VERSION,
            "command": self.command,
            "outcome": self.outcome,
            "repository": self.repository,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "state_revision": self.state_revision,
            "configuration": self.configuration or None,
            "step": self.step or None,
            "forced": self.forced,
            "created": self.created,
            "repeated": self.repeated,
            "gaps": self.gaps,
            "route": self.route or None,
            "terminal": self.terminal or None,
            "recovery": self.recovery or None,
            "message": self.message,
        }


@dataclass(frozen=True)
class DoneOptions:
    """Optional configuration, force, and recovery controls for `done`."""

    workflow_path: Path | None = None
    force: bool = False
    recover: bool = False


@dataclass(frozen=True)
class _LocatedRun:
    """Resolved repository identity and its run paths."""

    repository: RepositoryInfo
    paths: RunPaths
    run_id: str


@dataclass(frozen=True)
class _ConfigurationCheck:
    """Live configuration relationship to persisted state."""

    status: Literal["matching", "missing", "invalid", "drifted"]
    loaded: LoadedWorkflow | None
    gaps: list[dict[str, str]]
    explicit_unreadable: bool = False


def _gap(code: str, subject: str, message: str) -> dict[str, str]:
    return {"code": code, "subject": subject, "message": message}


def _state_flags(state: dict[str, Any]) -> list[tuple[str, str]]:
    supplied: list[tuple[str, str]] = []
    for name, value in state["flags"].items():
        if isinstance(value, bool):
            supplied.append((name, str(value).lower()))
        else:
            supplied.append((name, str(value)))
    return supplied


def _step_payload(state: dict[str, Any], paths: RunPaths) -> dict[str, Any]:
    if state["status"] == "terminal":
        return {}
    step = state["workflow"]["steps"][state["cursor"]]
    return {
        "id": state["cursor"],
        "instruction": step["instruction"],
        "mutates_repository": step["mutates_repository"],
        "artifact_root": str(paths.artifacts),
    }


def _configuration_payload(
    state: dict[str, Any],
    paths: RunPaths,
    status: str,
) -> dict[str, Any]:
    return {
        "source": state["workflow_source"],
        "fingerprint": state["workflow_fingerprint"],
        "status": status,
        "state_path": str(paths.state),
    }


def _base_result(
    command: str,
    state: dict[str, Any],
    located: _LocatedRun,
    *,
    configuration_status: str = "matching",
) -> SequencerResult:
    return SequencerResult(
        command=command,
        outcome="proceed",
        message="Workflow can continue",
        repository=str(located.repository.root),
        workflow_id=state["workflow_id"],
        run_id=located.run_id,
        state_revision=state["revision"],
        configuration=_configuration_payload(state, located.paths, configuration_status),
        step=_step_payload(state, located.paths),
    )


def _blocked_result(
    command: str,
    located: _LocatedRun,
    gap: dict[str, str],
    *,
    state: dict[str, Any] | None = None,
    configuration_status: str = "matching",
) -> SequencerResult:
    if state is None:
        return SequencerResult(
            command=command,
            outcome="blocked",
            message=gap["message"],
            repository=str(located.repository.root),
            run_id=located.run_id,
            configuration={"state_path": str(located.paths.state), "status": "missing"},
            gaps=[gap],
        )
    result = _base_result(
        command,
        state,
        located,
        configuration_status=configuration_status,
    )
    result.outcome = "blocked"
    result.message = gap["message"]
    result.gaps = [gap]
    return result


def _state_outcome(
    command: str,
    state: dict[str, Any],
    located: _LocatedRun,
    *,
    newly_issued: bool = False,
) -> SequencerResult:
    result = _base_result(command, state, located)
    if state["status"] == "terminal":
        result.outcome = "terminal"
        result.message = state["terminal"]["message"]
        result.terminal = state["terminal"]
    elif state["attempt"]["status"] == "issued" and not newly_issued:
        result.outcome = "recovery"
        result.message = "Mutating step was already issued"
        result.recovery = {
            "reason": "mutating_step_already_issued",
            "acknowledgement": f"done {state['cursor']} --recover",
        }
    return result


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _require_loaded(check: _ConfigurationCheck) -> LoadedWorkflow:
    if check.loaded is None:
        msg = "Matching configuration has no loaded workflow"
        raise StateError(msg)
    return check.loaded


class SequencerService:
    """Public state-machine service used by the CLI."""

    def __init__(self, home: Path | None = None) -> None:
        """Use the configured FDR home unless a test or caller overrides it."""
        self.home = (home or get_fdr_home()).expanduser().resolve(strict=False)

    def _locate(self, repository: Path, run_id: str) -> _LocatedRun:
        info = resolve_repository(repository)
        return _LocatedRun(
            repository=info,
            paths=run_paths(self.home, info, run_id),
            run_id=run_id,
        )

    def _missing_result(self, command: str, located: _LocatedRun) -> SequencerResult:
        return _blocked_result(
            command,
            located,
            _gap("run_not_initialized", located.run_id, "Run has not been initialized"),
        )

    def _load_existing(self, located: _LocatedRun) -> dict[str, Any]:
        state = read_state(located.paths.state)
        repository_state = state.get("repository", {})
        if repository_state.get("root") != str(located.repository.root) or repository_state.get(
            "common_dir"
        ) != str(located.repository.common_dir):
            msg = "Persisted repository identity does not match this worktree"
            raise StateError(msg)
        return state

    def _configuration_check(
        self,
        state: dict[str, Any],
        workflow_path: Path | None,
    ) -> _ConfigurationCheck:
        explicit = workflow_path is not None
        source = workflow_path or Path(state["workflow_source"])
        if not source.exists():
            return _ConfigurationCheck(
                status="missing",
                loaded=None,
                gaps=[
                    _gap(
                        "configuration_missing",
                        str(source),
                        "Workflow source is missing",
                    ),
                ],
                explicit_unreadable=explicit,
            )
        try:
            loaded = load_workflow(source, _state_flags(state))
        except WorkflowValidationError as exc:
            gaps = [_gap(gap.code, gap.subject, gap.message) for gap in exc.gaps]
            unreadable = any(gap["code"] == "workflow_unreadable" for gap in gaps)
            return _ConfigurationCheck(
                status="invalid",
                loaded=None,
                gaps=gaps,
                explicit_unreadable=explicit and unreadable,
            )
        if (
            loaded.workflow.id != state["workflow_id"]
            or loaded.fingerprint != state["workflow_fingerprint"]
        ):
            return _ConfigurationCheck(
                status="drifted",
                loaded=loaded,
                gaps=[
                    _gap(
                        "configuration_drift",
                        loaded.workflow.id,
                        "Workflow semantics differ from persisted state",
                    ),
                ],
            )
        return _ConfigurationCheck(status="matching", loaded=loaded, gaps=[])

    def _apply_relocation(
        self,
        state: dict[str, Any],
        check: _ConfigurationCheck,
    ) -> bool:
        if check.loaded is None:
            return False
        source = str(check.loaded.source)
        if source == state["workflow_source"]:
            return False
        state["workflow_source"] = source
        state["revision"] += 1
        return True

    def _configuration_block(
        self,
        command: str,
        state: dict[str, Any],
        located: _LocatedRun,
        check: _ConfigurationCheck,
    ) -> SequencerResult:
        result = _base_result(
            command,
            state,
            located,
            configuration_status=check.status,
        )
        result.outcome = "environment_error" if check.explicit_unreadable else "blocked"
        result.message = check.gaps[0]["message"]
        result.gaps = check.gaps
        return result

    def init(
        self,
        repository: Path,
        run_id: str,
        workflow_path: Path,
        flags: list[tuple[str, str]],
    ) -> SequencerResult:
        """Create a run or report compatible persisted state."""
        load_workflow(workflow_path, flags)
        located = self._locate(repository, run_id)
        with SequencerLock(located.paths.lock):
            loaded = load_workflow(workflow_path, flags)
            if located.paths.state.exists():
                return self._repeat_init(loaded, located)
            located.paths.artifacts.mkdir(parents=True, exist_ok=True)
            initial = capture_snapshot(located.repository)
            start = loaded.active_steps[loaded.workflow.start]
            attempt, newly_issued = self._new_attempt(start, located.repository)
            state = {
                "state_schema_version": STATE_SCHEMA_VERSION,
                "protocol_version": PROTOCOL_VERSION,
                "revision": 1,
                "repository": {
                    "root": str(located.repository.root),
                    "common_dir": str(located.repository.common_dir),
                    "key": located.repository.key,
                },
                "run_id": run_id,
                "workflow_id": loaded.workflow.id,
                "workflow_source": str(loaded.source),
                "workflow_fingerprint": loaded.fingerprint,
                "workflow": loaded.workflow.model_dump(mode="json"),
                "flags": loaded.flags,
                "cursor": loaded.workflow.start,
                "status": "incomplete",
                "terminal": None,
                "attempt": attempt,
                "initial_snapshot": initial.to_dict(),
                "history": [],
            }
            write_state(located.paths.state, state)
            result = _state_outcome(
                "init",
                state,
                located,
                newly_issued=newly_issued,
            )
            result.created = True
            result.message = "Run initialized"
            return result

    def _repeat_init(
        self,
        loaded: LoadedWorkflow,
        located: _LocatedRun,
    ) -> SequencerResult:
        state = self._load_existing(located)
        if (
            state["workflow_id"] != loaded.workflow.id
            or state["workflow_fingerprint"] != loaded.fingerprint
            or state["flags"] != loaded.flags
        ):
            return _blocked_result(
                "init",
                located,
                _gap(
                    "incompatible_init",
                    located.run_id,
                    "Run already exists with different workflow semantics or flags",
                ),
                state=state,
                configuration_status="drifted",
            )
        relocated = str(loaded.source) != state["workflow_source"]
        if relocated:
            state["workflow_source"] = str(loaded.source)
            state["revision"] += 1
            write_state(located.paths.state, state)
        result = _state_outcome("init", state, located)
        result.repeated = not relocated
        result.message = "Existing run returned"
        return result

    @staticmethod
    def _new_attempt(
        step: Step,
        repository: RepositoryInfo,
    ) -> tuple[dict[str, Any], bool]:
        if not step.mutates_repository:
            return {"status": "pending", "snapshot": None, "number": 0}, False
        snapshot = capture_snapshot(repository)
        return {"status": "issued", "snapshot": snapshot.to_dict(), "number": 1}, True

    def status(
        self,
        repository: Path,
        run_id: str,
        workflow_path: Path | None = None,
    ) -> SequencerResult:
        """Report persisted cursor and configuration health without writing."""
        located = self._locate(repository, run_id)
        if not located.paths.state.exists():
            return self._missing_result("status", located)
        with SequencerLock(located.paths.lock):
            state = self._load_existing(located)
            check = self._configuration_check(state, workflow_path)
            result = _state_outcome("status", state, located)
            result.configuration = _configuration_payload(state, located.paths, check.status)
            if result.outcome in {"terminal", "recovery"}:
                result.gaps = check.gaps
                return result
            if check.status != "matching":
                result.outcome = "environment_error" if check.explicit_unreadable else "blocked"
                result.message = check.gaps[0]["message"]
                result.gaps = check.gaps
            return result

    def next(
        self,
        repository: Path,
        run_id: str,
        workflow_path: Path | None = None,
    ) -> SequencerResult:
        """Return the current instruction without advancing the cursor."""
        located = self._locate(repository, run_id)
        if not located.paths.state.exists():
            return self._missing_result("next", located)
        with SequencerLock(located.paths.lock):
            state = self._load_existing(located)
            if state["status"] == "terminal":
                return _state_outcome("next", state, located)
            check = self._configuration_check(state, workflow_path)
            if check.status != "matching":
                return self._configuration_block("next", state, located, check)
            relocated = self._apply_relocation(state, check)
            loaded = _require_loaded(check)
            step = loaded.active_steps[state["cursor"]]
            newly_issued = False
            if step.mutates_repository and state["attempt"]["status"] != "issued":
                state["attempt"], newly_issued = self._new_attempt(step, located.repository)
                state["revision"] += 1
            if relocated or newly_issued:
                write_state(located.paths.state, state)
            result = _state_outcome(
                "next",
                state,
                located,
                newly_issued=newly_issued,
            )
            result.repeated = not newly_issued
            return result

    def done(
        self,
        repository: Path,
        run_id: str,
        step_id: str,
        options: DoneOptions | None = None,
    ) -> SequencerResult:
        """Validate and advance the current step once."""
        resolved_options = options or DoneOptions()
        located = self._locate(repository, run_id)
        if not located.paths.state.exists():
            return self._missing_result("done", located)
        with SequencerLock(located.paths.lock):
            state = self._load_existing(located)
            if state["status"] == "terminal":
                return _state_outcome("done", state, located)
            check = self._configuration_check(state, resolved_options.workflow_path)
            if check.status != "matching":
                return self._configuration_block("done", state, located, check)
            if step_id != state["cursor"]:
                return self._step_order_block(state, located, step_id)
            loaded = _require_loaded(check)
            step = loaded.active_steps[state["cursor"]]
            if resolved_options.recover:
                return self._recover_step(state, located, step)
            return self._complete_step(
                state,
                located,
                check,
                step,
                force=resolved_options.force,
            )

    @staticmethod
    def _step_order_block(
        state: dict[str, Any],
        located: _LocatedRun,
        step_id: str,
    ) -> SequencerResult:
        completed = {
            event["from_step"] for event in state["history"] if event.get("type") == "transition"
        }
        code = "stale_step" if step_id in completed else "out_of_order_step"
        return _blocked_result(
            "done",
            located,
            _gap(code, step_id, f"Current step is {state['cursor']!r}"),
            state=state,
        )

    def _recover_step(
        self,
        state: dict[str, Any],
        located: _LocatedRun,
        step: Step,
    ) -> SequencerResult:
        if not step.mutates_repository or state["attempt"]["status"] != "issued":
            return _blocked_result(
                "done",
                located,
                _gap(
                    "recovery_not_required",
                    state["cursor"],
                    "Current step does not require recovery",
                ),
                state=state,
            )
        state["history"].append(
            {
                "type": "recovery",
                "step": state["cursor"],
                "revision": state["revision"],
                "timestamp": _timestamp(),
            },
        )
        state["attempt"] = {
            "status": "issued",
            "snapshot": capture_snapshot(located.repository).to_dict(),
            "number": state["attempt"]["number"] + 1,
        }
        state["revision"] += 1
        write_state(located.paths.state, state)
        result = _state_outcome("done", state, located, newly_issued=True)
        result.message = "Recovery acknowledged and step reissued"
        return result

    def _evaluation_context(
        self,
        state: dict[str, Any],
        located: _LocatedRun,
    ) -> EvaluationContext:
        issued_raw = state["attempt"].get("snapshot")
        issued = GitSnapshot.from_dict(issued_raw) if issued_raw is not None else None
        return EvaluationContext(
            repository=located.repository,
            artifacts=located.paths.artifacts,
            flags=state["flags"],
            initial=GitSnapshot.from_dict(state["initial_snapshot"]),
            issued=issued,
        )

    @staticmethod
    def _postcondition_gaps(
        step: Step,
        context: EvaluationContext,
    ) -> list[dict[str, str]]:
        gaps: list[dict[str, str]] = []
        for postcondition in step.postconditions:
            if postcondition.when is not None and not evaluate_condition(
                postcondition.when,
                context,
            ):
                continue
            evaluation: OperationResult = evaluate_operation(postcondition.validator, context)
            if not evaluation.passed:
                gaps.append(
                    _gap(
                        "postcondition_failed",
                        postcondition.id,
                        evaluation.message,
                    ),
                )
        return gaps

    @staticmethod
    def _select_route(
        step: Step,
        context: EvaluationContext,
    ) -> tuple[Route, list[dict[str, Any]]]:
        evaluations: list[dict[str, Any]] = []
        for route in step.routes:
            matched = evaluate_condition(route.when, context)
            evaluations.append({"id": route.id, "matched": matched})
            if matched:
                return route, evaluations
        msg = "Validated workflow produced no route"
        raise StateError(msg)

    def _complete_step(
        self,
        state: dict[str, Any],
        located: _LocatedRun,
        check: _ConfigurationCheck,
        step: Step,
        *,
        force: bool,
    ) -> SequencerResult:
        context = self._evaluation_context(state, located)
        gaps = self._postcondition_gaps(step, context)
        if gaps and not force:
            result = _base_result("done", state, located)
            result.outcome = "blocked"
            result.message = "Postconditions did not pass"
            result.gaps = gaps
            return result

        route, route_evaluations = self._select_route(step, context)
        from_step = state["cursor"]
        event = {
            "type": "transition",
            "from_step": from_step,
            "route": route.id,
            "route_evaluations": route_evaluations,
            "forced": bool(gaps),
            "gaps": gaps,
            "revision": state["revision"],
            "timestamp": _timestamp(),
        }
        self._apply_relocation(state, check)
        state["history"].append(event)
        state["revision"] += 1
        newly_issued = False
        if route.terminal is not None:
            state["status"] = "terminal"
            state["terminal"] = route.terminal.model_dump(mode="json")
            state["attempt"] = {"status": "complete", "snapshot": None, "number": 0}
        else:
            if route.to is None:
                msg = "Non-terminal route has no target"
                raise StateError(msg)
            state["cursor"] = route.to
            loaded = _require_loaded(check)
            next_step = loaded.active_steps[route.to]
            state["attempt"], newly_issued = self._new_attempt(next_step, located.repository)
        write_state(located.paths.state, state)
        result = _state_outcome(
            "done",
            state,
            located,
            newly_issued=newly_issued,
        )
        result.forced = bool(gaps)
        result.gaps = gaps
        result.route = {
            "id": route.id,
            "to": route.to,
            "terminal": route.terminal.model_dump(mode="json") if route.terminal else None,
            "evaluations": route_evaluations,
        }
        result.message = "Step advanced" if route.terminal is None else state["terminal"]["message"]
        return result
