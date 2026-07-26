"""Tests for sequencer artifact evaluation."""

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from fix_die_repeat import sequencer_evaluator
from fix_die_repeat.sequencer_evaluator import (
    MAX_JSON_ARTIFACT_BYTES,
    EvaluationContext,
    EvaluationError,
    evaluate_condition,
    evaluate_operation,
)
from fix_die_repeat.sequencer_git import DirtyState, GitSnapshot, RepositoryInfo
from fix_die_repeat.sequencer_workflow import OperationSpec


@pytest.fixture
def context_factory(
    tmp_path: Path,
) -> Callable[[Path, dict[str, bool | str]], EvaluationContext]:
    """Build evaluation contexts with one shared baseline snapshot."""
    snapshot = GitSnapshot(
        head=None,
        symbolic_ref=None,
        dirty=DirtyState(staged=False, unstaged=False, untracked=False),
        digest="",
    )

    def build(
        artifacts: Path,
        flags: dict[str, bool | str],
    ) -> EvaluationContext:
        return EvaluationContext(
            repository=RepositoryInfo(root=tmp_path, common_dir=tmp_path / ".git", key="repo"),
            artifacts=artifacts,
            flags=flags,
            initial=snapshot,
            issued=None,
        )

    return build


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_json_valid_rejects_non_standard_constants(
    tmp_path: Path,
    constant: str,
    context_factory: Callable[[Path, dict[str, bool | str]], EvaluationContext],
) -> None:
    """JSON validators reject constants outside the JSON standard."""
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    (artifact_root / "result.json").write_text(
        f'{{"result": {constant}}}',
        encoding="utf-8",
    )
    context = context_factory(artifact_root, {})
    operation = OperationSpec.model_validate(
        {
            "op": "json.valid",
            "path": {"scope": "artifacts", "value": "result.json"},
        },
    )

    result = evaluate_operation(operation, context)

    assert not result.passed
    assert "not valid JSON" in result.message


@pytest.mark.parametrize("flag", [{}, {"name": "review"}, {"equals": True}])
def test_persisted_flag_condition_requires_name_and_equals(
    tmp_path: Path,
    flag: dict[str, object],
    context_factory: Callable[[Path, dict[str, bool | str]], EvaluationContext],
) -> None:
    """Corrupt persisted flag conditions fail closed."""
    context = context_factory(tmp_path / "artifacts", {"review": True})

    with pytest.raises(EvaluationError, match="Invalid persisted flag condition"):
        evaluate_condition({"flag": flag}, context)


def test_json_valid_rejects_oversized_artifact(
    tmp_path: Path,
    context_factory: Callable[[Path, dict[str, bool | str]], EvaluationContext],
) -> None:
    """JSON artifact reads are bounded before parsing."""
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    (artifact_root / "result.json").write_bytes(b"x" * (MAX_JSON_ARTIFACT_BYTES + 1))
    operation = OperationSpec.model_validate(
        {
            "op": "json.valid",
            "path": {"scope": "artifacts", "value": "result.json"},
        },
    )

    result = evaluate_operation(operation, context_factory(artifact_root, {}))

    assert not result.passed
    assert "exceeds 1 MiB" in result.message


def test_json_valid_bounds_read_when_artifact_grows_after_stat(
    tmp_path: Path,
    context_factory: Callable[[Path, dict[str, bool | str]], EvaluationContext],
) -> None:
    """The read limit remains authoritative when a file grows after inspection."""
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    artifact = artifact_root / "result.json"
    artifact.write_bytes(b"x" * (MAX_JSON_ARTIFACT_BYTES + 1))
    operation = OperationSpec.model_validate(
        {
            "op": "json.valid",
            "path": {"scope": "artifacts", "value": "result.json"},
        },
    )
    real_stat = artifact.stat()

    with patch.object(
        Path,
        "stat",
        return_value=SimpleNamespace(st_mode=real_stat.st_mode, st_size=1),
    ):
        result = evaluate_operation(operation, context_factory(artifact_root, {}))

    assert not result.passed
    assert "exceeds 1 MiB" in result.message


def test_json_valid_reports_invalid_utf8(
    tmp_path: Path,
    context_factory: Callable[[Path, dict[str, bool | str]], EvaluationContext],
) -> None:
    """UTF-8 decoding failures retain their specific diagnostic."""
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    (artifact_root / "result.json").write_bytes(b'{"value":"\xff"}')
    operation = OperationSpec.model_validate(
        {
            "op": "json.valid",
            "path": {"scope": "artifacts", "value": "result.json"},
        },
    )

    result = evaluate_operation(operation, context_factory(artifact_root, {}))

    assert not result.passed
    assert "not valid UTF-8 JSON" in result.message


@pytest.mark.parametrize(
    ("actual", "expected", "expected_outcome"),
    [(True, 1, "fail"), (1, True, "fail"), (1, 1.0, "pass")],
)
def test_json_pointer_equals_respects_json_types(
    tmp_path: Path,
    actual: object,
    expected: object,
    expected_outcome: str,
    context_factory: Callable[[Path, dict[str, bool | str]], EvaluationContext],
) -> None:
    """Booleans and numbers cannot match through Python coercion."""
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    (artifact_root / "result.json").write_text(
        f'{{"value": {str(actual).lower()}}}',
        encoding="utf-8",
    )
    operation = OperationSpec.model_validate(
        {
            "op": "json.pointer_equals",
            "path": {"scope": "artifacts", "value": "result.json"},
            "pointer": "/value",
            "expected": expected,
        },
    )

    result = evaluate_operation(operation, context_factory(artifact_root, {}))

    assert result.passed is (expected_outcome == "pass")


def test_json_pointer_equals_respects_nested_json_types(
    tmp_path: Path,
    context_factory: Callable[[Path, dict[str, bool | str]], EvaluationContext],
) -> None:
    """Nested booleans and numbers retain distinct JSON types."""
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    (artifact_root / "result.json").write_text('{"value": {"nested": [true]}}')
    operation = OperationSpec.model_validate(
        {
            "op": "json.pointer_equals",
            "path": {"scope": "artifacts", "value": "result.json"},
            "pointer": "/value",
            "expected": {"nested": [1]},
        },
    )

    result = evaluate_operation(operation, context_factory(artifact_root, {}))

    assert not result.passed


@pytest.mark.parametrize("pointer", ["/values/01", "/values/\N{ARABIC-INDIC DIGIT ONE}"])
def test_json_pointer_rejects_noncanonical_array_indices(
    tmp_path: Path,
    pointer: str,
    context_factory: Callable[[Path, dict[str, bool | str]], EvaluationContext],
) -> None:
    """Array indices use canonical ASCII decimal syntax."""
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    (artifact_root / "result.json").write_text('{"values": ["first", "second"]}')
    operation = OperationSpec.model_validate(
        {
            "op": "json.pointer_equals",
            "path": {"scope": "artifacts", "value": "result.json"},
            "pointer": pointer,
            "expected": "second",
        },
    )

    result = evaluate_operation(operation, context_factory(artifact_root, {}))

    assert not result.passed


@pytest.mark.parametrize(("key", "children"), [("all", 1), ("any", 1), ("all", []), ("any", [])])
def test_persisted_compound_condition_requires_non_empty_list(
    tmp_path: Path,
    key: str,
    children: object,
    context_factory: Callable[[Path, dict[str, bool | str]], EvaluationContext],
) -> None:
    """Corrupt persisted compound conditions fail closed."""
    context = context_factory(tmp_path / "artifacts", {})

    with pytest.raises(EvaluationError, match=f"Invalid persisted {key} condition"):
        evaluate_condition({key: children}, context)


def test_persisted_not_condition_inverts_child(
    tmp_path: Path,
    context_factory: Callable[[Path, dict[str, bool | str]], EvaluationContext],
) -> None:
    """Persisted negation evaluates its child before inversion."""
    context = context_factory(tmp_path / "artifacts", {"review": True})

    assert not evaluate_condition({"not": {"flag": {"name": "review", "equals": True}}}, context)


def test_artifact_path_rejects_symlink_escape(
    tmp_path: Path,
    context_factory: Callable[[Path, dict[str, bool | str]], EvaluationContext],
) -> None:
    """Resolved artifact paths cannot escape through a symlink."""
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (artifact_root / "link").symlink_to(outside, target_is_directory=True)
    operation = OperationSpec.model_validate(
        {
            "op": "path.exists",
            "path": {"scope": "artifacts", "value": "link/result.json"},
        },
    )

    with pytest.raises(EvaluationError, match="escapes artifacts scope"):
        evaluate_operation(operation, context_factory(artifact_root, {}))


def test_git_probe_error_uses_evaluation_error_contract(
    tmp_path: Path,
    context_factory: Callable[[Path, dict[str, bool | str]], EvaluationContext],
) -> None:
    """Git probe failures remain closed evaluator errors."""
    operation = OperationSpec.model_validate({"op": "git.is_clean"})
    context = context_factory(tmp_path / "artifacts", {})

    with (
        patch.object(
            sequencer_evaluator,
            "evaluate_git_operation",
            side_effect=sequencer_evaluator.GitProbeError("probe failed"),
        ),
        pytest.raises(EvaluationError, match="probe failed"),
    ):
        evaluate_operation(operation, context)
