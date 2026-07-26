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
from fix_die_repeat.sequencer_workflow import (
    GIT_OPERATIONS,
    MAX_CONDITION_DEPTH,
    OperationSpec,
)

MAX_JSON_ARTIFACT_BYTES = 1024 * 1024

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


def _parse_json_content(path: Path, content: bytes) -> tuple[object | None, str | None]:
    def reject_constant(value: str) -> None:
        msg = f"non-standard constant {value}"
        raise ValueError(msg)

    try:
        raw = content.decode("utf-8")
        return json.loads(raw, parse_constant=reject_constant), None
    except json.JSONDecodeError as exc:
        return None, f"{path} is not valid JSON: {exc.msg}"
    except UnicodeDecodeError as exc:
        return None, f"{path} is not valid UTF-8 JSON: {exc}"
    except ValueError as exc:
        return None, f"{path} is not valid JSON: {exc}"
    except RecursionError:
        return None, f"{path} is nested too deeply to evaluate"


def _read_json(path: Path) -> tuple[object | None, str | None]:
    if not path.is_file():
        return None, f"{path} is not a regular file"
    try:
        size = path.stat().st_size
    except OSError as exc:
        msg = f"Cannot inspect {path}: {exc}"
        raise EvaluationError(msg) from exc
    if size > MAX_JSON_ARTIFACT_BYTES:
        return None, f"{path} exceeds 1 MiB"

    try:
        with path.open("rb") as handle:
            content = handle.read(MAX_JSON_ARTIFACT_BYTES + 1)
    except OSError as exc:
        msg = f"Cannot read {path}: {exc}"
        raise EvaluationError(msg) from exc
    if len(content) > MAX_JSON_ARTIFACT_BYTES:
        return None, f"{path} exceeds 1 MiB"
    return _parse_json_content(path, content)


def _json_pointer(value: object, pointer: str) -> tuple[bool, object | None]:
    if pointer == "":
        return True, value
    current = value
    for encoded in pointer.removeprefix("/").split("/"):
        index_text = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and index_text in current:
            current = current[index_text]
        elif (
            isinstance(current, list)
            and index_text.isascii()
            and index_text.isdecimal()
            and (index_text == "0" or not index_text.startswith("0"))
            and int(index_text) < len(current)
        ):
            current = current[int(index_text)]
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


def _json_equal(left: object, right: object) -> bool:
    pending = [(left, right)]
    while pending:
        left_item, right_item = pending.pop()
        if _json_type(left_item) != _json_type(right_item):
            return False
        if isinstance(left_item, list) and isinstance(right_item, list):
            if len(left_item) != len(right_item):
                return False
            pending.extend(zip(left_item, right_item, strict=True))
        elif isinstance(left_item, dict) and isinstance(right_item, dict):
            if left_item.keys() != right_item.keys():
                return False
            pending.extend((left_item[key], right_item[key]) for key in left_item)
        elif left_item != right_item:
            return False
    return True


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
        passed = _json_equal(selected, operation.expected)
        comparison = "equals" if passed else "does not equal"
        return OperationResult(
            passed=passed,
            message=f"{operation.pointer} {comparison} {operation.expected!r}",
        )
    actual_type = _json_type(selected)
    passed = actual_type == operation.expected
    if passed:
        message = (
            f"{operation.pointer} has type {actual_type}, "
            f"which matches expected type {operation.expected}"
        )
    else:
        message = f"{operation.pointer} has type {actual_type}, expected {operation.expected}"
    return OperationResult(
        passed=passed,
        message=message,
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


def evaluate_condition(
    value: object,
    context: EvaluationContext,
    *,
    depth: int = 0,
) -> bool:
    """Evaluate one validated condition tree."""
    if depth > MAX_CONDITION_DEPTH:
        msg = f"Condition nesting exceeds {MAX_CONDITION_DEPTH}"
        raise EvaluationError(msg)
    if value == "always":
        return True
    if not isinstance(value, dict):
        msg = "Invalid persisted condition"
        raise EvaluationError(msg)
    if set(value) == {"flag"}:
        flag = value["flag"]
        if not isinstance(flag, dict) or "name" not in flag or "equals" not in flag:
            msg = "Invalid persisted flag condition"
            raise EvaluationError(msg)
        return context.flags.get(str(flag["name"])) == flag["equals"]
    if set(value) in ({"all"}, {"any"}):
        key = next(iter(value))
        children = value[key]
        if not isinstance(children, list) or not children:
            msg = f"Invalid persisted {key} condition"
            raise EvaluationError(msg)
        combine = all if key == "all" else any
        return combine(evaluate_condition(child, context, depth=depth + 1) for child in children)
    if set(value) == {"not"}:
        return not evaluate_condition(value["not"], context, depth=depth + 1)
    return evaluate_operation(_operation_from_condition(value), context).passed
