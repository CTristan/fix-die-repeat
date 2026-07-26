"""Tests for sequencer artifact evaluation."""

from pathlib import Path

import pytest

from fix_die_repeat.sequencer_evaluator import EvaluationContext, evaluate_operation
from fix_die_repeat.sequencer_git import DirtyState, GitSnapshot, RepositoryInfo
from fix_die_repeat.sequencer_workflow import OperationSpec


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_json_valid_rejects_non_standard_constants(tmp_path: Path, constant: str) -> None:
    """JSON validators reject constants outside the JSON standard."""
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    (artifact_root / "result.json").write_text(
        f'{{"result": {constant}}}',
        encoding="utf-8",
    )
    snapshot = GitSnapshot(
        head=None,
        symbolic_ref=None,
        dirty=DirtyState(staged=False, unstaged=False, untracked=False),
        digest="",
    )
    context = EvaluationContext(
        repository=RepositoryInfo(root=tmp_path, common_dir=tmp_path / ".git", key="repo"),
        artifacts=artifact_root,
        flags={},
        initial=snapshot,
        issued=None,
    )
    operation = OperationSpec.model_validate(
        {
            "op": "json.valid",
            "path": {"scope": "artifacts", "value": "result.json"},
        },
    )

    result = evaluate_operation(operation, context)

    assert not result.passed
    assert "not valid JSON" in result.message
