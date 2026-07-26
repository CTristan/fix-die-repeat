"""Sequencer workflow loading and validation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast, override

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from yaml.constructor import ConstructorError
from yaml.events import AliasEvent

MAX_WORKFLOW_BYTES = 1024 * 1024
SHA256_HEX_LENGTH = 64
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

ARTIFACT_OPERATIONS = frozenset(
    {
        "path.exists",
        "file.non_empty",
        "json.valid",
        "json.pointer_equals",
        "json.pointer_type",
    },
)
GIT_OPERATIONS = frozenset(
    {
        "git.is_clean",
        "git.has_staged_changes",
        "git.has_unstaged_changes",
        "git.has_untracked_changes",
        "git.head_changed",
        "git.has_unpushed_commits",
        "git.working_tree_changed",
    },
)
SUPPORTED_OPERATIONS = ARTIFACT_OPERATIONS | GIT_OPERATIONS
JSON_TYPES = frozenset({"null", "boolean", "number", "string", "array", "object"})


@dataclass(frozen=True)
class ValidationGap:
    """One stable workflow validation failure."""

    code: str
    subject: str
    message: str


class WorkflowValidationError(ValueError):
    """A workflow failed structural or semantic validation."""

    def __init__(self, gaps: list[ValidationGap]) -> None:
        """Build one exception from all safely collected gaps."""
        self.gaps = gaps
        summary = "; ".join(f"{gap.code}: {gap.message}" for gap in gaps)
        super().__init__(summary)


class StrictModel(BaseModel):
    """Base model that rejects undeclared configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class PathSpec(StrictModel):
    """A path constrained to a declared sequencer scope."""

    scope: Literal["repo", "artifacts"]
    value: str

    @model_validator(mode="after")
    def validate_value(self) -> PathSpec:
        """Reject absolute and escaping paths before runtime resolution."""
        path = PurePosixPath(self.value)
        components = self.value.split("/")
        if (
            not self.value
            or "\x00" in self.value
            or "\\" in self.value
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in components)
        ):
            msg = "path must be a non-empty relative path without dot components"
            raise ValueError(msg)
        return self


class OperationSpec(StrictModel):
    """One closed-registry validator or predicate operation."""

    op: str
    path: PathSpec | None = None
    pointer: str | None = None
    expected: Any = None


class FlagDeclaration(StrictModel):
    """One immutable run-flag declaration."""

    type: Literal["boolean", "enum"]
    values: list[str] | None = None
    default: bool | str | None = None

    @model_validator(mode="after")
    def validate_declaration(self) -> FlagDeclaration:
        """Keep boolean and enum declarations internally consistent."""
        if self.type == "boolean":
            if self.values is not None:
                msg = "boolean flags cannot declare values"
                raise ValueError(msg)
            if self.default is not None and not isinstance(self.default, bool):
                msg = "boolean flag default must be true or false"
                raise ValueError(msg)
            return self

        if not self.values or len(self.values) != len(set(self.values)):
            msg = "enum flags require unique values"
            raise ValueError(msg)
        if any(not isinstance(value, str) or not value for value in self.values):
            msg = "enum values must be non-empty strings"
            raise ValueError(msg)
        if self.default is not None and self.default not in self.values:
            msg = "enum default must be one of its declared values"
            raise ValueError(msg)
        return self


class Postcondition(StrictModel):
    """A named validator applied before a step advances."""

    id: str
    validator: OperationSpec
    when: object | None = None


class TerminalSpec(StrictModel):
    """A declared terminal workflow result."""

    code: str
    status: Literal["success", "failure", "stopped"]
    message: str


class Route(StrictModel):
    """One ordered route from a completed step."""

    id: str
    when: object
    to: str | None = None
    terminal: TerminalSpec | None = None
    repeat: bool = False

    @model_validator(mode="after")
    def validate_destination(self) -> Route:
        """Require exactly one route destination."""
        if (self.to is None) == (self.terminal is None):
            msg = "route must declare exactly one of to or terminal"
            raise ValueError(msg)
        return self


class Step(StrictModel):
    """One external-agent instruction and its transition contract."""

    instruction: str = Field(min_length=1)
    mutates_repository: bool
    applies_when: object | None = None
    postconditions: list[Postcondition] = Field(default_factory=list)
    routes: list[Route] = Field(min_length=1)


class Workflow(StrictModel):
    """Version 1 sequencer workflow."""

    schema_version: Literal[1]
    id: str
    start: str
    flags: dict[str, FlagDeclaration] = Field(default_factory=dict)
    steps: dict[str, Step]


@dataclass(frozen=True)
class LoadedWorkflow:
    """A validated workflow resolved against immutable run flags."""

    source: Path
    workflow: Workflow
    flags: dict[str, bool | str]
    active_steps: dict[str, Step]
    fingerprint: str


class _StrictSafeLoader(yaml.SafeLoader):
    """Safe YAML loader that also rejects aliases and duplicate keys."""

    @override
    def compose_node(self, parent: yaml.Node | None, index: int) -> yaml.Node:
        if self.check_event(AliasEvent):
            event = self.get_event()
            msg = f"YAML aliases are not supported: {event.anchor}"
            raise ConstructorError(None, None, msg, event.start_mark)
        return cast("yaml.Node", super().compose_node(parent, index))

    @override
    def construct_mapping(
        self,
        node: yaml.MappingNode,
        deep: bool = False,
    ) -> dict[object, object]:
        """Construct a mapping while rejecting duplicate keys."""
        mapping: dict[object, object] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                msg = f"found unacceptable key: {exc}"
                context = "while constructing a mapping"
                raise ConstructorError(
                    context,
                    node.start_mark,
                    msg,
                    key_node.start_mark,
                ) from exc
            if duplicate:
                msg = f"duplicate key: {key}"
                context = "while constructing a mapping"
                raise ConstructorError(
                    context,
                    node.start_mark,
                    msg,
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _gap(code: str, subject: str, message: str) -> ValidationGap:
    return ValidationGap(code=code, subject=subject, message=message)


def _load_yaml(path: Path) -> object:
    try:
        with path.open("rb") as handle:
            content = handle.read(MAX_WORKFLOW_BYTES + 1)
    except OSError as exc:
        raise WorkflowValidationError(
            [_gap("workflow_unreadable", str(path), f"cannot read workflow: {exc}")],
        ) from exc
    if len(content) > MAX_WORKFLOW_BYTES:
        raise WorkflowValidationError(
            [_gap("workflow_too_large", str(path), "workflow exceeds 1 MiB")],
        )
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkflowValidationError(
            [_gap("workflow_encoding", str(path), "workflow must be UTF-8")],
        ) from exc
    try:
        return yaml.load(text, Loader=_StrictSafeLoader)  # noqa: S506
    except ConstructorError as exc:
        code = "duplicate_key" if "duplicate key" in str(exc) else "invalid_yaml"
        raise WorkflowValidationError([_gap(code, str(path), str(exc))]) from exc
    except yaml.YAMLError as exc:
        raise WorkflowValidationError([_gap("invalid_yaml", str(path), str(exc))]) from exc


def _pydantic_gaps(exc: ValidationError) -> list[ValidationGap]:
    gaps: list[ValidationGap] = []
    for error in exc.errors(include_url=False):
        subject = ".".join(str(part) for part in error["loc"]) or "workflow"
        gaps.append(_gap(str(error["type"]), subject, str(error["msg"])))
    return gaps


def _pairs(
    supplied: dict[str, str] | list[tuple[str, str]],
) -> tuple[dict[str, str], list[ValidationGap]]:
    items = supplied.items() if isinstance(supplied, dict) else supplied
    result: dict[str, str] = {}
    gaps: list[ValidationGap] = []
    for name, value in items:
        if name in result:
            gaps.append(_gap("duplicate_flag", name, f"flag {name!r} was supplied more than once"))
        else:
            result[name] = value
    return result, gaps


def _resolve_flags(
    workflow: Workflow,
    supplied: dict[str, str] | list[tuple[str, str]],
) -> tuple[dict[str, bool | str], list[ValidationGap]]:
    raw, gaps = _pairs(supplied)
    for name in sorted(raw.keys() - workflow.flags.keys()):
        gaps.append(_gap("unknown_flag", name, f"flag {name!r} is not declared"))

    resolved: dict[str, bool | str] = {}
    for name, declaration in workflow.flags.items():
        if name not in raw:
            if declaration.default is None:
                gaps.append(_gap("missing_flag", name, f"flag {name!r} is required"))
            else:
                resolved[name] = declaration.default
            continue

        value = raw[name]
        if declaration.type == "boolean":
            lowered = value.lower()
            if lowered not in {"true", "false"}:
                gaps.append(_gap("invalid_flag", name, f"flag {name!r} must be true or false"))
            else:
                resolved[name] = lowered == "true"
        elif declaration.values is not None and value in declaration.values:
            resolved[name] = value
        else:
            gaps.append(
                _gap(
                    "invalid_flag",
                    name,
                    f"flag {name!r} must be one of {declaration.values!r}",
                ),
            )
    return resolved, gaps


def _validate_id(value: str, subject: str, gaps: list[ValidationGap]) -> None:
    if not ID_PATTERN.fullmatch(value):
        gaps.append(_gap("invalid_id", subject, f"{value!r} is not a valid identifier"))


def _validate_operation_fields(
    operation: OperationSpec,
    subject: str,
    gaps: list[ValidationGap],
) -> None:
    if operation.op not in SUPPORTED_OPERATIONS:
        gaps.append(
            _gap(
                "unsupported_operation",
                subject,
                f"operation {operation.op!r} is not supported",
            ),
        )
        return

    fields = operation.model_fields_set
    if operation.op in ARTIFACT_OPERATIONS and operation.path is None:
        gaps.append(_gap("missing_operation_field", subject, f"{operation.op} requires path"))
    if operation.op in GIT_OPERATIONS and fields & {"path", "pointer", "expected"}:
        gaps.append(
            _gap("unexpected_operation_field", subject, f"{operation.op} accepts no fields"),
        )
    if operation.op in {"json.pointer_equals", "json.pointer_type"}:
        if operation.pointer is None or not (
            operation.pointer == "" or operation.pointer.startswith("/")
        ):
            gaps.append(_gap("invalid_pointer", subject, "JSON pointer must start with /"))
        if "expected" not in fields:
            gaps.append(
                _gap("missing_operation_field", subject, f"{operation.op} requires expected"),
            )
    elif fields & {"pointer", "expected"}:
        gaps.append(
            _gap("unexpected_operation_field", subject, f"{operation.op} does not accept pointer"),
        )
    if operation.op == "json.pointer_type" and operation.expected not in JSON_TYPES:
        gaps.append(
            _gap("invalid_json_type", subject, f"unknown JSON type {operation.expected!r}"),
        )


def _parse_operation(
    value: object,
    subject: str,
    gaps: list[ValidationGap],
) -> OperationSpec | None:
    if isinstance(value, OperationSpec):
        operation = value
    else:
        try:
            operation = OperationSpec.model_validate(value)
        except ValidationError as exc:
            gaps.extend(_pydantic_gaps(exc))
            return None
    _validate_operation_fields(operation, subject, gaps)
    return operation


def parse_condition(
    value: object,
    subject: str,
    gaps: list[ValidationGap],
) -> object:
    """Validate a condition and return its normalized representation."""
    if value == "always":
        return "always"
    if not isinstance(value, dict):
        gaps.append(_gap("invalid_condition", subject, "condition must be a mapping or always"))
        return value

    keys = set(value)
    if keys == {"flag"}:
        flag = value["flag"]
        if not isinstance(flag, dict) or set(flag) != {"name", "equals"}:
            gaps.append(_gap("invalid_flag_condition", subject, "flag requires name and equals"))
        return value
    if keys in ({"all"}, {"any"}):
        key = next(iter(keys))
        children = value[key]
        if not isinstance(children, list) or not children:
            gaps.append(_gap("invalid_condition", subject, f"{key} requires a non-empty list"))
        else:
            for index, child in enumerate(children):
                parse_condition(child, f"{subject}.{key}.{index}", gaps)
        return value
    if keys == {"not"}:
        parse_condition(value["not"], f"{subject}.not", gaps)
        return value

    _parse_operation(value, subject, gaps)
    return value


def _flag_condition_value(
    value: object,
    flags: dict[str, bool | str],
    subject: str,
    gaps: list[ValidationGap],
) -> bool | None:
    result: bool | None
    if value == "always":
        result = True
    elif not isinstance(value, dict):
        result = None
    elif set(value) == {"flag"} and isinstance(value["flag"], dict):
        flag = value["flag"]
        name = flag.get("name")
        if not isinstance(name, str) or name not in flags:
            gaps.append(_gap("unknown_flag", subject, f"flag {name!r} is not declared"))
            result = False
        else:
            result = flags[name] == flag.get("equals")
    elif set(value) in ({"all"}, {"any"}):
        key = next(iter(value))
        raw_children = value[key]
        if not isinstance(raw_children, list):
            return None
        children = [_flag_condition_value(child, flags, subject, gaps) for child in raw_children]
        if any(child is None for child in children):
            result = None
        else:
            values = [bool(child) for child in children]
            result = all(values) if key == "all" else any(values)
    elif set(value) == {"not"}:
        child = _flag_condition_value(value["not"], flags, subject, gaps)
        result = None if child is None else not child
    else:
        result = None
    return result


def _condition_key(value: object) -> str:
    def encode_non_json(item: object) -> dict[str, str]:
        return {
            "__invalid_type__": f"{type(item).__module__}.{type(item).__qualname__}",
            "representation": repr(item),
        }

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=encode_non_json,
    )


def _active_steps(
    workflow: Workflow,
    flags: dict[str, bool | str],
    gaps: list[ValidationGap],
) -> dict[str, Step]:
    active: dict[str, Step] = {}
    for step_id, step in workflow.steps.items():
        if step.applies_when is None:
            active[step_id] = step
            continue
        parse_condition(step.applies_when, f"steps.{step_id}.applies_when", gaps)
        applies = _flag_condition_value(
            step.applies_when,
            flags,
            f"steps.{step_id}.applies_when",
            gaps,
        )
        if applies is None:
            gaps.append(
                _gap(
                    "invalid_applicability",
                    step_id,
                    "applies_when may use only immutable flag conditions",
                ),
            )
        elif applies:
            active[step_id] = step
    return active


@dataclass
class _Graph:
    """Active graph edges with declared repeat edges separated."""

    edges: dict[str, set[str]]
    non_repeat_edges: dict[str, set[str]]


@dataclass
class _RouteContext:
    """Shared state for validating one step's routes."""

    workflow: Workflow
    active: dict[str, Step]
    flags: dict[str, bool | str]
    graph: _Graph
    gaps: list[ValidationGap]
    step_id: str


@dataclass
class _RouteSeen:
    """Route identities and predicates already seen in one step."""

    ids: set[str]
    predicates: set[str]
    count: int


def _validate_postconditions(
    step_id: str,
    step: Step,
    flags: dict[str, bool | str],
    gaps: list[ValidationGap],
) -> None:
    ids: set[str] = set()
    for postcondition in step.postconditions:
        _validate_id(postcondition.id, f"steps.{step_id}.postconditions", gaps)
        if postcondition.id in ids:
            gaps.append(
                _gap(
                    "duplicate_postcondition_id",
                    step_id,
                    f"duplicate postcondition {postcondition.id!r}",
                ),
            )
        ids.add(postcondition.id)
        subject = f"steps.{step_id}.postconditions.{postcondition.id}"
        _parse_operation(postcondition.validator, subject, gaps)
        if postcondition.when is None:
            continue
        parse_condition(postcondition.when, f"{subject}.when", gaps)
        applies = _flag_condition_value(postcondition.when, flags, f"{subject}.when", gaps)
        if applies is None:
            gaps.append(
                _gap(
                    "invalid_postcondition_applicability",
                    postcondition.id,
                    "postcondition when may use only immutable flags",
                ),
            )


def _validate_route_identity(
    route: Route,
    index: int,
    seen: _RouteSeen,
    context: _RouteContext,
) -> None:
    _validate_id(route.id, f"steps.{context.step_id}.routes", context.gaps)
    if route.id in seen.ids:
        context.gaps.append(
            _gap("duplicate_route_id", context.step_id, f"duplicate route {route.id!r}"),
        )
    seen.ids.add(route.id)
    parse_condition(
        route.when,
        f"steps.{context.step_id}.routes.{route.id}",
        context.gaps,
    )
    predicate = _condition_key(route.when)
    if predicate in seen.predicates:
        context.gaps.append(
            _gap(
                "duplicate_route_predicate",
                context.step_id,
                f"route {route.id!r} repeats an earlier predicate",
            ),
        )
    seen.predicates.add(predicate)
    if route.when == "always" and index != seen.count - 1:
        context.gaps.append(
            _gap("early_fallback", route.id, "always is allowed only on the last route"),
        )


def _record_route_target(
    route: Route,
    context: _RouteContext,
) -> None:
    if route.to is None:
        if route.terminal is not None:
            _validate_id(
                route.terminal.code,
                f"steps.{context.step_id}.routes.{route.id}",
                context.gaps,
            )
        return
    if route.to not in context.workflow.steps:
        context.gaps.append(
            _gap(
                "missing_route_target",
                route.id,
                f"target step {route.to!r} does not exist",
            ),
        )
        return
    if context.step_id not in context.active:
        return
    if route.to not in context.active:
        condition_value = _flag_condition_value(
            route.when,
            context.flags,
            f"steps.{context.step_id}.routes.{route.id}",
            context.gaps,
        )
        if condition_value is not False:
            context.gaps.append(
                _gap(
                    "inactive_route_target",
                    route.id,
                    f"target step {route.to!r} is inactive",
                ),
            )
        return
    context.graph.edges[context.step_id].add(route.to)
    if not route.repeat:
        context.graph.non_repeat_edges[context.step_id].add(route.to)


def _validate_routes(
    step_id: str,
    step: Step,
    context: _RouteContext,
) -> None:
    context.step_id = step_id
    seen = _RouteSeen(ids=set(), predicates=set(), count=len(step.routes))
    for index, route in enumerate(step.routes):
        _validate_route_identity(route, index, seen, context)
        _record_route_target(route, context)
    if step.routes[-1].when != "always":
        context.gaps.append(
            _gap("missing_fallback", step_id, "last route must use when: always"),
        )


def _find_unreachable(
    start: str,
    active: dict[str, Step],
    edges: dict[str, set[str]],
) -> set[str]:
    if start not in active:
        return set()
    reachable: set[str] = set()
    pending = [start]
    while pending:
        current = pending.pop()
        if current in reachable:
            continue
        reachable.add(current)
        pending.extend(edges.get(current, set()) - reachable)
    return active.keys() - reachable


def _contains_cycle(edges: dict[str, set[str]]) -> bool:
    visiting = 1
    visited = 2
    state: dict[str, int] = {}
    for start in edges:
        if state.get(start) == visited:
            continue
        state[start] = visiting
        stack = [(start, iter(edges.get(start, set())))]
        while stack:
            step_id, targets = stack[-1]
            try:
                target = next(targets)
            except StopIteration:
                state[step_id] = visited
                stack.pop()
                continue
            target_state = state.get(target, 0)
            if target_state == visiting:
                return True
            if target_state == 0:
                state[target] = visiting
                stack.append((target, iter(edges.get(target, set()))))
    return False


def _validate_graph(
    workflow: Workflow,
    active: dict[str, Step],
    flags: dict[str, bool | str],
    gaps: list[ValidationGap],
) -> None:
    if workflow.start not in workflow.steps:
        gaps.append(_gap("missing_start", workflow.start, "start step does not exist"))
    elif workflow.start not in active:
        gaps.append(_gap("inactive_start", workflow.start, "start step is inactive"))

    graph = _Graph(
        edges={step_id: set() for step_id in active},
        non_repeat_edges={step_id: set() for step_id in active},
    )
    route_context = _RouteContext(
        workflow=workflow,
        active=active,
        flags=flags,
        graph=graph,
        gaps=gaps,
        step_id="",
    )
    for step_id, step in workflow.steps.items():
        _validate_id(step_id, f"steps.{step_id}", gaps)
        _validate_postconditions(step_id, step, flags, gaps)
        _validate_routes(step_id, step, route_context)

    gaps.extend(
        _gap("unreachable_step", step_id, f"step {step_id!r} is unreachable")
        for step_id in _find_unreachable(workflow.start, active, graph.edges)
    )
    if _contains_cycle(graph.non_repeat_edges):
        gaps.append(
            _gap(
                "undeclared_cycle",
                "steps",
                "workflow contains a cycle without a declared repeat edge",
            ),
        )


def load_workflow(
    path: Path,
    supplied_flags: dict[str, str] | list[tuple[str, str]],
) -> LoadedWorkflow:
    """Load, resolve, and validate a sequencer workflow."""
    raw = _load_yaml(path)
    if not isinstance(raw, dict):
        raise WorkflowValidationError(
            [_gap("invalid_workflow_root", str(path), "workflow root must be a mapping")],
        )
    try:
        workflow = Workflow.model_validate(raw)
    except ValidationError as exc:
        raise WorkflowValidationError(_pydantic_gaps(exc)) from exc

    gaps: list[ValidationGap] = []
    _validate_id(workflow.id, "workflow.id", gaps)
    for flag_name in workflow.flags:
        _validate_id(flag_name, f"flags.{flag_name}", gaps)
    resolved_flags, flag_gaps = _resolve_flags(workflow, supplied_flags)
    gaps.extend(flag_gaps)
    active = _active_steps(workflow, resolved_flags, gaps)
    _validate_graph(workflow, active, resolved_flags, gaps)
    if gaps:
        raise WorkflowValidationError(gaps)

    canonical = json.dumps(
        {
            "workflow": workflow.model_dump(mode="json"),
            "flags": resolved_flags,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    fingerprint = hashlib.sha256(canonical).hexdigest()
    return LoadedWorkflow(
        source=path.expanduser().resolve(strict=False),
        workflow=workflow,
        flags=resolved_flags,
        active_steps=active,
        fingerprint=fingerprint,
    )
