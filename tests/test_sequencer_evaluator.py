"""Tests for sequencer artifact evaluation."""

from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

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


def test_persisted_flag_condition_uses_type_strict_equality(
    tmp_path: Path,
    context_factory: Callable[[Path, dict[str, bool | str]], EvaluationContext],
) -> None:
    """Corrupt boolean-number comparisons cannot pass through Python coercion."""
    context = context_factory(tmp_path / "artifacts", {"review": True})

    assert not evaluate_condition(
        {"flag": {"name": "review", "equals": 1}},
        context,
    )


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
    artifact.write_text("{}")
    operation = OperationSpec.model_validate(
        {
            "op": "json.valid",
            "path": {"scope": "artifacts", "value": "result.json"},
        },
    )
    handle = MagicMock()
    handle.__enter__.return_value = handle
    handle.read.return_value = b"x" * (MAX_JSON_ARTIFACT_BYTES + 1)

    with patch.object(Path, "open", return_value=handle):
        result = evaluate_operation(operation, context_factory(artifact_root, {}))

    assert not result.passed
    assert "exceeds 1 MiB" in result.message
    handle.read.assert_called_once_with(MAX_JSON_ARTIFACT_BYTES + 1)


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


def test_json_valid_reports_excessive_nesting(
    tmp_path: Path,
    context_factory: Callable[[Path, dict[str, bool | str]], EvaluationContext],
) -> None:
    """Deeply nested JSON fails through the ordinary operation-result path."""
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    (artifact_root / "result.json").write_text("[0]")
    operation = OperationSpec.model_validate(
        {
            "op": "json.valid",
            "path": {"scope": "artifacts", "value": "result.json"},
        },
    )

    class RecursiveContent(bytes):
        def decode(self, *args: object, **kwargs: object) -> str:
            del args, kwargs
            raise RecursionError

    handle = MagicMock()
    handle.__enter__.return_value = handle
    handle.read.return_value = RecursiveContent(b"[0]")
    with patch.object(Path, "open", return_value=handle):
        result = evaluate_operation(operation, context_factory(artifact_root, {}))

    assert not result.passed
    assert "nested too deeply" in result.message


@pytest.mark.parametrize(
    ("content", "expected"),
    [(None, False), (b"", False), (b"value", True)],
)
def test_file_non_empty_reports_file_state(
    tmp_path: Path,
    content: bytes | None,
    context_factory: Callable[[Path, dict[str, bool | str]], EvaluationContext],
    *,
    expected: bool,
) -> None:
    """The non-empty predicate distinguishes missing, empty, and populated files."""
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    if content is not None:
        (artifact_root / "result.txt").write_bytes(content)
    operation = OperationSpec.model_validate(
        {
            "op": "file.non_empty",
            "path": {"scope": "artifacts", "value": "result.txt"},
        },
    )

    result = evaluate_operation(operation, context_factory(artifact_root, {}))

    assert result.passed is expected


def test_path_exists_reports_existing_artifact(
    tmp_path: Path,
    context_factory: Callable[[Path, dict[str, bool | str]], EvaluationContext],
) -> None:
    """The existence predicate accepts an existing scoped artifact."""
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    (artifact_root / "result.txt").touch()
    operation = OperationSpec.model_validate(
        {
            "op": "path.exists",
            "path": {"scope": "artifacts", "value": "result.txt"},
        },
    )

    assert evaluate_operation(operation, context_factory(artifact_root, {})).passed


def test_json_valid_rejects_directory(
    tmp_path: Path,
    context_factory: Callable[[Path, dict[str, bool | str]], EvaluationContext],
) -> None:
    """JSON parsing rejects non-regular artifact paths."""
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    (artifact_root / "result.json").mkdir()
    operation = OperationSpec.model_validate(
        {
            "op": "json.valid",
            "path": {"scope": "artifacts", "value": "result.json"},
        },
    )

    result = evaluate_operation(operation, context_factory(artifact_root, {}))

    assert not result.passed
    assert "not a regular file" in result.message


def test_unsupported_operation_raises_evaluation_error(
    tmp_path: Path,
    context_factory: Callable[[Path, dict[str, bool | str]], EvaluationContext],
) -> None:
    """A corrupt persisted operation cannot leave the closed registry."""
    operation = OperationSpec.model_validate(
        {
            "op": "unsupported",
            "path": {"scope": "artifacts", "value": "result.txt"},
        },
    )

    with pytest.raises(EvaluationError, match="Unsupported operation: unsupported"):
        evaluate_operation(operation, context_factory(tmp_path / "artifacts", {}))


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


def test_json_equality_handles_deep_containers_without_recursion() -> None:
    """Structural equality does not recurse on nested untrusted containers."""
    left: object = 0
    right: object = 0
    for _ in range(1100):
        left = [left]
        right = [right]

    assert sequencer_evaluator._json_equal(left, right)


def test_json_pointer_type_success_message_reports_match(
    tmp_path: Path,
    context_factory: Callable[[Path, dict[str, bool | str]], EvaluationContext],
) -> None:
    """A successful type predicate describes the type as matching."""
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    (artifact_root / "result.json").write_text('{"passed": true}')
    operation = OperationSpec.model_validate(
        {
            "op": "json.pointer_type",
            "path": {"scope": "artifacts", "value": "result.json"},
            "pointer": "/passed",
            "expected": "boolean",
        },
    )

    result = evaluate_operation(operation, context_factory(artifact_root, {}))

    assert result.passed
    assert "matches expected type boolean" in result.message


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


def test_persisted_condition_rejects_excessive_nesting(
    tmp_path: Path,
    context_factory: Callable[[Path, dict[str, bool | str]], EvaluationContext],
) -> None:
    """Persisted condition evaluation stops at the shared depth limit."""
    condition: object = "always"
    for _ in range(sequencer_evaluator.MAX_CONDITION_DEPTH + 1):
        condition = {"not": condition}

    with pytest.raises(EvaluationError, match="Condition nesting exceeds"):
        evaluate_condition(condition, context_factory(tmp_path / "artifacts", {}))


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
