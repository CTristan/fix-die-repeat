"""Tests for sequencer workflow loading and validation."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fix_die_repeat.sequencer_workflow import (
    MAX_WORKFLOW_BYTES,
    PathSpec,
    WorkflowValidationError,
    load_workflow,
)

SHA256_HEX_LENGTH = 64


VALID_WORKFLOW = """\
schema_version: 1
id: check-fix
start: check
flags:
  review:
    type: boolean
    default: true
steps:
  check:
    instruction: Run checks.
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
    instruction: Fix the failures.
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


def _write_workflow(tmp_path: Path, content: str = VALID_WORKFLOW) -> Path:
    path = tmp_path / "workflow.yaml"
    path.write_text(content)
    return path


def test_load_workflow_resolves_flags_and_active_steps(tmp_path: Path) -> None:
    """The loader resolves immutable flags and removes inactive steps."""
    loaded = load_workflow(_write_workflow(tmp_path), {"review": "false"})

    assert loaded.workflow.id == "check-fix"
    assert loaded.flags == {"review": False}
    assert set(loaded.active_steps) == {"check", "fix"}
    assert len(loaded.fingerprint) == SHA256_HEX_LENGTH


def test_load_workflow_fingerprint_ignores_formatting(tmp_path: Path) -> None:
    """Comments and mapping presentation do not change semantic identity."""
    first = load_workflow(_write_workflow(tmp_path), {})
    reformatted = VALID_WORKFLOW.replace(
        "schema_version: 1",
        "# harmless comment\nschema_version: 1",
    )
    second_path = tmp_path / "reformatted.yaml"
    second_path.write_text(reformatted)

    second = load_workflow(second_path, {})

    assert second.fingerprint == first.fingerprint


def test_load_workflow_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    """Duplicate YAML keys fail instead of silently taking the last value."""
    content = VALID_WORKFLOW.replace("id: check-fix", "id: check-fix\nid: duplicate")

    with pytest.raises(WorkflowValidationError, match="duplicate_key"):
        load_workflow(_write_workflow(tmp_path, content), {})


def test_load_workflow_rejects_unhashable_yaml_keys(tmp_path: Path) -> None:
    """Unhashable YAML keys fail through the workflow validation contract."""
    content = "? [one, two]\n: value\n"

    with pytest.raises(WorkflowValidationError, match="invalid_yaml"):
        load_workflow(_write_workflow(tmp_path, content), {})


def test_load_workflow_rejects_unknown_fields(tmp_path: Path) -> None:
    """Unknown schema fields fail closed."""
    content = VALID_WORKFLOW.replace("start: check", "start: check\nsurprise: true")

    with pytest.raises(WorkflowValidationError, match="extra_forbidden"):
        load_workflow(_write_workflow(tmp_path, content), {})


def test_load_workflow_rejects_unknown_operations(tmp_path: Path) -> None:
    """Configuration cannot name arbitrary commands or operations."""
    content = VALID_WORKFLOW.replace(
        "op: git.working_tree_changed",
        "op: shell.command",
    )

    with pytest.raises(WorkflowValidationError, match="unsupported_operation"):
        load_workflow(_write_workflow(tmp_path, content), {})


def test_load_workflow_reports_multiple_semantic_gaps(tmp_path: Path) -> None:
    """Independent graph errors are reported together."""
    content = VALID_WORKFLOW.replace("start: check", "start: missing").replace(
        "to: fix",
        "to: nowhere",
    )

    with pytest.raises(WorkflowValidationError) as error:
        load_workflow(_write_workflow(tmp_path, content), {})

    codes = {gap.code for gap in error.value.gaps}
    assert {"missing_start", "missing_route_target"} <= codes


def test_load_workflow_rejects_undeclared_cycle(tmp_path: Path) -> None:
    """A cycle must disappear when declared repeat edges are removed."""
    content = VALID_WORKFLOW.replace("        repeat: true\n", "")

    with pytest.raises(WorkflowValidationError, match="undeclared_cycle"):
        load_workflow(_write_workflow(tmp_path, content), {})


def test_load_workflow_rejects_inactive_route_target(tmp_path: Path) -> None:
    """Resolved flags cannot leave a selectable route aimed at an inactive step."""
    content = (
        VALID_WORKFLOW.replace(
            "  fix:\n    instruction: Fix the failures.\n    mutates_repository: true",
            "  fix:\n    instruction: Fix the failures.\n    mutates_repository: true\n"
            "    applies_when:\n      flag:\n        name: review\n        equals: true",
        )
        .replace(
            "      - id: fix-failure\n        when:\n          op: json.pointer_equals\n"
            "          path:\n            scope: artifacts\n            value: result.json\n"
            "          pointer: /passed\n          expected: false\n        to: fix",
            "      - id: fix-failure\n        when: always\n        to: fix",
        )
        .replace(
            "      - id: finish\n        when: always\n        terminal:",
            "      - id: finish\n        when:\n          flag:\n            name: review\n"
            "            equals: true\n        terminal:",
        )
    )

    with pytest.raises(WorkflowValidationError, match="inactive_route_target"):
        load_workflow(_write_workflow(tmp_path, content), {"review": "false"})


def test_load_workflow_rejects_duplicate_route_predicates(tmp_path: Path) -> None:
    """Duplicate canonical predicates cannot hide behind different route IDs."""
    content = VALID_WORKFLOW.replace(
        "      - id: finish\n        when: always",
        "      - id: duplicate\n        when:\n          op: json.pointer_equals\n"
        "          path:\n            scope: artifacts\n            value: result.json\n"
        "          pointer: /passed\n          expected: false\n        terminal:\n"
        "          code: duplicate\n          status: failure\n          message: Duplicate.\n"
        "      - id: finish\n        when: always",
    )

    with pytest.raises(WorkflowValidationError, match="duplicate_route_predicate"):
        load_workflow(_write_workflow(tmp_path, content), {})


def test_load_workflow_rejects_unknown_and_duplicate_flags(tmp_path: Path) -> None:
    """Run flags must match declarations exactly."""
    path = _write_workflow(tmp_path)

    with pytest.raises(WorkflowValidationError, match="unknown_flag"):
        load_workflow(path, {"missing": "true"})

    with pytest.raises(WorkflowValidationError, match="duplicate_flag"):
        load_workflow(path, [("review", "true"), ("review", "false")])


def test_load_workflow_rejects_oversized_file(tmp_path: Path) -> None:
    """Workflow input is bounded before YAML parsing."""
    path = tmp_path / "large.yaml"
    handle = MagicMock()
    handle.__enter__.return_value = handle
    handle.read.return_value = b"x" * (MAX_WORKFLOW_BYTES + 1)

    with (
        patch.object(Path, "open", return_value=handle),
        pytest.raises(WorkflowValidationError, match="workflow_too_large"),
    ):
        load_workflow(path, {})

    handle.read.assert_called_once_with(MAX_WORKFLOW_BYTES + 1)


@pytest.mark.parametrize(
    "value",
    [
        "/absolute/result.json",
        ".",
        "../result.json",
        "artifacts/./result.json",
        "artifacts/../result.json",
        "result\x00.json",
        r"C:\absolute\result.json",
        r"..\result.json",
    ],
)
def test_path_spec_rejects_absolute_dot_and_nul_paths(value: str) -> None:
    """Workflow paths cannot escape through either path-separator convention."""
    with pytest.raises(ValueError, match="relative path"):
        PathSpec(scope="artifacts", value=value)


@pytest.mark.parametrize("value", ["result.json", "reports/check/result.json"])
def test_path_spec_accepts_valid_relative_paths(value: str) -> None:
    """Portable relative paths remain valid."""
    assert PathSpec(scope="artifacts", value=value).value == value


def test_load_workflow_handles_long_acyclic_graph(tmp_path: Path) -> None:
    """Graph validation does not depend on the Python recursion limit."""
    step_count = 1100
    steps: list[str] = []
    for index in range(step_count):
        step_id = f"step-{index}"
        steps.extend(
            [
                f"  {step_id}:",
                "    instruction: Continue.",
                "    mutates_repository: false",
                "    routes:",
                f"      - id: route-{index}",
                "        when: always",
            ],
        )
        if index + 1 < step_count:
            steps.append(f"        to: step-{index + 1}")
        else:
            steps.extend(
                [
                    "        terminal:",
                    "          code: complete",
                    "          status: success",
                    "          message: Complete.",
                ],
            )
    content = "\n".join(
        [
            "schema_version: 1",
            "id: long-chain",
            "start: step-0",
            "steps:",
            *steps,
            "",
        ],
    )

    loaded = load_workflow(_write_workflow(tmp_path, content), {})

    assert len(loaded.active_steps) == step_count


@pytest.mark.parametrize(
    "condition",
    [
        "flag:\n        name: [review]\n        equals: true",
        "all: 1",
    ],
)
def test_load_workflow_reports_malformed_applicability(
    tmp_path: Path,
    condition: str,
) -> None:
    """Malformed raw applicability conditions return validation gaps."""
    content = VALID_WORKFLOW.replace(
        "    instruction: Fix the failures.",
        f"    instruction: Fix the failures.\n    applies_when:\n      {condition}",
    )

    with pytest.raises(WorkflowValidationError):
        load_workflow(_write_workflow(tmp_path, content), {})


@pytest.mark.parametrize("condition", ["2026-07-25", "!!binary 'aGVsbG8='"])
def test_load_workflow_reports_non_json_route_scalars(
    tmp_path: Path,
    condition: str,
) -> None:
    """Loader-specific YAML scalars become validation gaps instead of exceptions."""
    content = VALID_WORKFLOW.replace(
        "      - id: finish\n        when: always",
        f"      - id: finish\n        when: {condition}",
    )

    with pytest.raises(WorkflowValidationError, match="invalid_condition"):
        load_workflow(_write_workflow(tmp_path, content), {})


def test_unknown_flag_gaps_are_deterministic(tmp_path: Path) -> None:
    """Unknown supplied flags are reported in sorted order."""
    with pytest.raises(WorkflowValidationError) as error:
        load_workflow(
            _write_workflow(tmp_path),
            {"zeta": "true", "alpha": "true"},
        )

    subjects = [gap.subject for gap in error.value.gaps if gap.code == "unknown_flag"]
    assert subjects == ["alpha", "zeta"]
