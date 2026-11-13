import pytest
import yaml

from evokit import Task, Workspace
from evokit.analysis import split_tasks


def test_explicit_splits_preserve_repo_holdout():
    tasks = [Task(i, {}, group=i.split("-")[0]) for i in ("a-1", "a-2", "b-1")]
    assignments = {"diagnosis": ["a-1"], "selection": ["a-2"], "confirmation": ["b-1"], "final": []}
    assert split_tasks(tasks, 999, assignments=assignments) == assignments
    assert split_tasks(tasks[::-1], 0, assignments=assignments) == assignments


@pytest.mark.parametrize("change", ["overlap", "missing", "unknown", "empty", "extra", "type"])
def test_invalid_explicit_partitions_fail(change):
    splits = {"diagnosis": ["a"], "selection": ["b"], "confirmation": ["c"], "final": []}
    if change == "overlap":
        splits["final"] = ["a"]
    elif change == "missing":
        splits["confirmation"] = ["a"]
    elif change == "unknown":
        splits["final"] = ["x"]
    elif change == "empty":
        splits["selection"] = []
        splits["final"] = ["b"]
    elif change == "extra":
        splits["holdout"] = []
    else:
        splits["selection"] = "b"
    with pytest.raises(ValueError):
        split_tasks([Task(i, {}) for i in "abc"], assignments=splits)


def test_workspace_reopen_and_tamper(tmp_path):
    (tmp_path / "setting.txt").write_text("base")
    splits = {"diagnosis": ["a"], "selection": ["b"], "confirmation": ["c"], "final": []}
    config = {
        "editable": ["setting.txt"],
        "tasks": [{"id": i, "payload": {}} for i in "abc"],
        "command": ["unused"],
        "split_assignments": splits,
    }
    path = tmp_path / "scenario.yaml"
    path.write_text(yaml.safe_dump(config))
    workspace = Workspace.open(path)
    assert workspace.describe()["counts"] == {k: len(v) for k, v in splits.items()}
    workspace.close()
    Workspace.open(path).close()
    config["split_assignments"]["diagnosis"], config["split_assignments"]["selection"] = (
        ["b"],
        ["a"],
    )
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="Frozen"):
        Workspace.open(path)
