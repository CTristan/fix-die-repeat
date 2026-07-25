"""Closed-registry sequencer artifact and predicate evaluation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from fix_die_repeat.sequencer_git import (
    GitProbeError,
    GitSnapshot,
    RepositoryInfo,
    evaluate_git_operation,
)
from fix_die_repeat.sequencer_workflow import GIT_OPERATIONS, OperationSpec

if TYPE_CHECKING:
    from pathlib import Path


class EvaluationError(RuntimeError):
    """An operation could not produce a reliable true or false result."""


@dataclass(frozen=True)
class EvaluationContext:
    """Runtime roots and immutable state available to predicates."""

    repository: RepositoryInfo
    artifacts: Path
    flags: dict[str, bool | str]
    initial: GitSnapshot
    issued: GitSnapshot | None


@dataclass(frozen=True)
class OperationResult:
    """One operation's boolean result and stable explanation."""

    passed: bool
    message: str


def _resolve_path(operation: OperationSpec, context: EvaluationContext) -> Path:
    if operation.path is None:
        msg = f"{operation.op} requires a path"
        raise EvaluationError(msg)
    base = context.repository.root if operation.path.scope == "repo" else context.artifacts
    resolved_base = base.resolve(strict=False)
    resolved = (resolved_base / operation.path.value).resolve(strict=False)
    if not resolved.is_relative_to(resolved_base):
        msg = f"Path {operation.path.value!r} escapes {operation.path.scope} scope"
        raise EvaluationError(msg)
    return resolved


def _read_json(path: Path) -> tuple[object | None, str | None]:
    if not path.is_file():
        return None, f"{path} is not a regular file"
    try:
        return json.loads(path.read_text()), None
    except json.JSONDecodeError as exc:
        return None, f"{path} is not valid JSON: {exc.msg}"
    except OSError as exc:
        msg = f"Cannot read {path}: {exc}"
        raise EvaluationError(msg) from exc


def _json_pointer(value: object, pointer: str) -> tuple[bool, object | None]:
    if pointer == "":
        return True, value
    current = value
    for encoded in pointer.removeprefix("/").split("/"):
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            return False, None
    return True, current


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _evaluate_json(operation: OperationSpec, path: Path) -> OperationResult:
    value, error = _read_json(path)
    if error is not None:
        return OperationResult(passed=False, message=error)
    if operation.op == "json.valid":
        return OperationResult(passed=True, message=f"{path} contains valid JSON")
    found, selected = _json_pointer(value, operation.pointer or "")
    if not found:
        return OperationResult(
            passed=False,
            message=f"{path} has no value at {operation.pointer}",
        )
    if operation.op == "json.pointer_equals":
        passed = selected == operation.expected
        comparison = "equals" if passed else "does not equal"
        return OperationResult(
            passed=passed,
            message=f"{operation.pointer} {comparison} {operation.expected!r}",
        )
    actual_type = _json_type(selected)
    passed = actual_type == operation.expected
    return OperationResult(
        passed=passed,
        message=f"{operation.pointer} has type {actual_type}, expected {operation.expected}",
    )


def evaluate_operation(
    operation: OperationSpec,
    context: EvaluationContext,
) -> OperationResult:
    """Evaluate one validated closed-registry operation."""
    if operation.op in GIT_OPERATIONS:
        try:
            passed = evaluate_git_operation(
                operation.op,
                context.repository,
                initial=context.initial,
                issued=context.issued,
            )
        except GitProbeError as exc:
            raise EvaluationError(str(exc)) from exc
        return OperationResult(
            passed=passed,
            message=f"{operation.op} returned {str(passed).lower()}",
        )

    path = _resolve_path(operation, context)
    if operation.op == "path.exists":
        passed = path.exists()
        return OperationResult(
            passed=passed,
            message=f"{path} {'exists' if passed else 'does not exist'}",
        )
    if operation.op == "file.non_empty":
        try:
            passed = path.is_file() and path.stat().st_size > 0
        except OSError as exc:
            msg = f"Cannot inspect {path}: {exc}"
            raise EvaluationError(msg) from exc
        return OperationResult(
            passed=passed,
            message=f"{path} is {'non-empty' if passed else 'empty or missing'}",
        )
    if operation.op.startswith("json."):
        return _evaluate_json(operation, path)
    msg = f"Unsupported operation: {operation.op}"
    raise EvaluationError(msg)


def _operation_from_condition(value: dict[str, Any]) -> OperationSpec:
    try:
        return OperationSpec.model_validate(value)
    except ValidationError as exc:
        msg = f"Invalid persisted operation: {exc}"
        raise EvaluationError(msg) from exc


def evaluate_condition(value: object, context: EvaluationContext) -> bool:
    """Evaluate one validated condition tree."""
    if value == "always":
        return True
    if not isinstance(value, dict):
        msg = "Invalid persisted condition"
        raise EvaluationError(msg)
    if set(value) == {"flag"}:
        flag = value["flag"]
        if not isinstance(flag, dict):
            msg = "Invalid persisted flag condition"
            raise EvaluationError(msg)
        return context.flags.get(str(flag.get("name"))) == flag.get("equals")
    if set(value) == {"all"}:
        return all(evaluate_condition(child, context) for child in value["all"])
    if set(value) == {"any"}:
        return any(evaluate_condition(child, context) for child in value["any"])
    if set(value) == {"not"}:
        return not evaluate_condition(value["not"], context)
    return evaluate_operation(_operation_from_condition(value), context).passed
