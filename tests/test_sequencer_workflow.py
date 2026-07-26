"""Tests for sequencer workflow loading and validation."""

from pathlib import Path
from types import MappingProxyType
from unittest.mock import MagicMock, patch

import pytest
import yaml

from fix_die_repeat.sequencer_workflow import (
    MAX_CONDITION_DEPTH,
    MAX_WORKFLOW_BYTES,
    SHA256_HEX_LENGTH,
    FlagDeclaration,
    PathSpec,
    WorkflowValidationError,
    load_workflow,
)

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


@pytest.mark.parametrize(
    "declaration",
    [
        {"type": "boolean", "values": ["true"]},
        {"type": "boolean", "default": "true"},
        {"type": "enum", "values": ["fast", "fast"]},
        {"type": "enum", "values": ["fast", ""]},
        {"type": "enum", "values": ["fast"], "default": "safe"},
    ],
)
def test_flag_declaration_rejects_inconsistent_values(declaration: dict[str, object]) -> None:
    """Flag declarations reject values outside their declared type contract."""
    with pytest.raises(ValueError, match=r"boolean|enum"):
        FlagDeclaration.model_validate(declaration)


def test_load_workflow_reports_missing_required_flag(tmp_path: Path) -> None:
    """A declared flag without a default must be supplied."""
    content = VALID_WORKFLOW.replace("    default: true\n", "")

    with pytest.raises(WorkflowValidationError) as raised:
        load_workflow(_write_workflow(tmp_path, content), {})

    assert [gap.code for gap in raised.value.gaps].count("missing_flag") == 1
    assert "unknown_flag" not in {gap.code for gap in raised.value.gaps}


def test_load_workflow_requires_final_fallback(tmp_path: Path) -> None:
    """Every route list ends with an unconditional fallback."""
    content = VALID_WORKFLOW.replace(
        "      - id: finish\n        when: always",
        """      - id: finish
        when:
          flag:
            name: review
            equals: true""",
    )

    with pytest.raises(WorkflowValidationError) as raised:
        load_workflow(_write_workflow(tmp_path, content), {})

    assert "missing_fallback" in {gap.code for gap in raised.value.gaps}


def test_load_workflow_rejects_early_fallback(tmp_path: Path) -> None:
    """An unconditional route cannot shadow later routes."""
    content = VALID_WORKFLOW.replace(
        """        when:
          op: json.pointer_equals
          path:
            scope: artifacts
            value: result.json
          pointer: /passed
          expected: false""",
        "        when: always",
        1,
    )

    with pytest.raises(WorkflowValidationError) as raised:
        load_workflow(_write_workflow(tmp_path, content), {})

    assert "early_fallback" in {gap.code for gap in raised.value.gaps}


def test_enum_flag_resolves_before_ordered_route_validation(tmp_path: Path) -> None:
    """Declared enum values remain available to ordered route predicates."""
    content = """\
schema_version: 1
id: enum-route
start: check
flags:
  mode:
    type: enum
    values: [fix, skip]
    default: skip
steps:
  check:
    instruction: Check.
    mutates_repository: false
    routes:
      - id: fix
        when:
          flag:
            name: mode
            equals: fix
        to: repair
      - id: finish
        when: always
        terminal:
          code: passed
          status: success
          message: Done.
  repair:
    instruction: Repair.
    mutates_repository: true
    routes:
      - id: recheck
        when: always
        to: check
        repeat: true
"""

    loaded = load_workflow(_write_workflow(tmp_path, content), {"mode": "fix"})

    assert loaded.flags == {"mode": "fix"}
    assert [route.id for route in loaded.workflow.steps["check"].routes] == ["fix", "finish"]


@pytest.mark.parametrize(
    ("condition", "code", "subject_suffix"),
    [
        (
            {"all": [{"flag": {"name": "missing", "equals": True}}]},
            "unknown_flag",
            ".all.0",
        ),
        (
            {"flag": {"name": "mode", "equals": "other"}},
            "invalid_flag_condition",
            "applies_when",
        ),
    ],
)
def test_flag_conditions_match_declarations(
    tmp_path: Path,
    condition: dict[str, object],
    code: str,
    subject_suffix: str,
) -> None:
    """Flag conditions reject unknown names and undeclared enum values."""
    serialized_condition = yaml.safe_dump(
        condition,
        default_flow_style=True,
        sort_keys=True,
    ).strip()
    content = f"""\
schema_version: 1
id: flag-condition
start: conditional
flags:
  mode:
    type: enum
    values: [fix, skip]
    default: skip
steps:
  conditional:
    instruction: Check.
    mutates_repository: false
    applies_when: {serialized_condition}
    routes:
      - id: finish
        when: always
        terminal:
          code: passed
          status: success
          message: Done.
"""

    with pytest.raises(WorkflowValidationError) as raised:
        load_workflow(_write_workflow(tmp_path, content), {})

    matching = [gap for gap in raised.value.gaps if gap.code == code]
    assert matching
    assert matching[0].subject.endswith(subject_suffix)


def test_load_workflow_resolves_flags_and_active_steps(tmp_path: Path) -> None:
    """The loader resolves immutable flags and removes inactive steps."""
    loaded = load_workflow(_write_workflow(tmp_path), {"review": "false"})

    assert loaded.workflow.id == "check-fix"
    assert loaded.flags == {"review": False}
    assert set(loaded.active_steps) == {"check", "fix"}
    assert len(loaded.fingerprint) == SHA256_HEX_LENGTH
    assert isinstance(loaded.flags, MappingProxyType)
    assert isinstance(loaded.active_steps, MappingProxyType)


def test_load_workflow_fingerprint_ignores_formatting(tmp_path: Path) -> None:
    """Comments and mapping presentation do not change semantic identity."""
    first = load_workflow(_write_workflow(tmp_path), {})
    reformatted = "# harmless comment\n" + yaml.safe_dump(
        yaml.safe_load(VALID_WORKFLOW),
        sort_keys=True,
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


def test_load_workflow_rejects_excessive_condition_nesting(tmp_path: Path) -> None:
    """Workflow validation stops at the shared condition-depth limit."""
    condition = "always"
    for _ in range(MAX_CONDITION_DEPTH + 1):
        condition = f"{{not: {condition}}}"
    content = VALID_WORKFLOW.replace(
        "    mutates_repository: false",
        f"    mutates_repository: false\n    applies_when: {condition}",
        1,
    )

    with pytest.raises(WorkflowValidationError, match="condition_too_deep"):
        load_workflow(_write_workflow(tmp_path, content), {})


def test_git_operation_fields_produce_one_gap(tmp_path: Path) -> None:
    """One invalid Git operation produces one field-contract diagnostic."""
    content = VALID_WORKFLOW.replace(
        """        when:
          op: json.pointer_equals
          path:
            scope: artifacts
            value: result.json
          pointer: /passed
          expected: false""",
        """        when:
          op: git.is_clean
          pointer: /passed
          expected: false""",
        1,
    )

    with pytest.raises(WorkflowValidationError) as raised:
        load_workflow(_write_workflow(tmp_path, content), {})

    codes = [gap.code for gap in raised.value.gaps]
    assert codes.count("unexpected_operation_field") == 1


@pytest.mark.parametrize("expected", ["2026-07-26", "!!binary aGVsbG8="])
def test_pointer_equals_rejects_yaml_only_expected_values(
    tmp_path: Path,
    expected: str,
) -> None:
    """Pointer equality accepts only values representable by JSON."""
    content = VALID_WORKFLOW.replace(
        "          expected: false",
        f"          expected: {expected}",
        1,
    )

    with pytest.raises(WorkflowValidationError, match="JSON-representable"):
        load_workflow(_write_workflow(tmp_path, content), {})


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
        "C:/foo/bar.json",
        "",
        "a/",
        "a//b",
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

    with pytest.raises(WorkflowValidationError) as raised:
        load_workflow(_write_workflow(tmp_path, content), {})

    expected_code = (
        "invalid_flag_condition" if condition.startswith("flag:") else "invalid_condition"
    )
    assert expected_code in {gap.code for gap in raised.value.gaps}


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


def test_load_workflow_reports_mixed_condition_keys(tmp_path: Path) -> None:
    """Mixed mapping keys cannot escape condition validation."""
    content = VALID_WORKFLOW.replace(
        "      - id: finish\n        when: always",
        "      - id: finish\n        when:\n          1: true\n          text: true",
    )

    with pytest.raises(WorkflowValidationError) as raised:
        load_workflow(_write_workflow(tmp_path, content), {})

    assert "invalid_condition" in {gap.code for gap in raised.value.gaps}


def test_load_workflow_wraps_excessive_yaml_nesting(tmp_path: Path) -> None:
    """Recursive YAML parser failures use the workflow validation contract."""
    path = _write_workflow(tmp_path)

    with (
        patch("fix_die_repeat.sequencer_workflow.yaml.load", side_effect=RecursionError),
        pytest.raises(WorkflowValidationError, match="invalid_yaml"),
    ):
        load_workflow(path, {})


def test_load_workflow_rejects_unhashable_json_type(tmp_path: Path) -> None:
    """A list cannot enter the JSON type-name registry lookup."""
    content = VALID_WORKFLOW.replace("          expected: boolean", "          expected: [boolean]")

    with pytest.raises(WorkflowValidationError, match="invalid_json_type"):
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


def test_unreachable_step_gaps_are_deterministic(tmp_path: Path) -> None:
    """Unreachable steps are reported in sorted order."""
    unreachable = """\
  zeta:
    instruction: Stop at zeta.
    mutates_repository: false
    routes:
      - id: stop-zeta
        when: always
        terminal:
          code: zeta
          status: stopped
          message: Zeta.
  alpha:
    instruction: Stop at alpha.
    mutates_repository: false
    routes:
      - id: stop-alpha
        when: always
        terminal:
          code: alpha
          status: stopped
          message: Alpha.
"""
    content = VALID_WORKFLOW + unreachable

    with pytest.raises(WorkflowValidationError) as error:
        load_workflow(_write_workflow(tmp_path, content), {})

    subjects = [gap.subject for gap in error.value.gaps if gap.code == "unreachable_step"]
    assert subjects == ["alpha", "zeta"]
