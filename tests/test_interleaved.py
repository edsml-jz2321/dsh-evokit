"""Matched arms share each task window and resume only missing committed slots."""

import threading
import time

import pytest
from conftest import candidate


def test_matched_blocks_overlap_and_resume(workspace, monkeypatch):
    base = workspace.current()
    child = candidate(workspace)
    original = workspace._execute_task
    calls = []
    active = []
    overlaps = []
    lock = threading.Lock()

    def observed(path, meta, task, repeat):
        with lock:
            active.append((task.id, repeat, meta["version"]))
            calls.append((path.name, task.id, repeat))
            assert len({(t, r) for t, r, _ in active}) == 1
            if len(active) > 1:
                overlaps.append(tuple(active))
        time.sleep(0.01)
        result = original(path, meta, task, repeat)
        with lock:
            active.remove((task.id, repeat, meta["version"]))
        return result

    monkeypatch.setattr(workspace, "_execute_task", observed)
    arms = {"control": base, "candidate": child}
    statuses = workspace.interleaved(arms, repeats=2, workers=2, seed=7)
    assert all(s["state"] == "completed" for s in statuses.values())
    assert overlaps
    assert workspace.paired("control", "candidate", "majority")["net"] > 0
    count = len(calls)
    workspace.interleaved(arms, repeats=2, workers=2, seed=7)
    assert len(calls) == count
    with pytest.raises(ValueError, match="original interleaving"):
        workspace.interleaved(arms, repeats=2, workers=2, seed=8)
    with pytest.raises(ValueError, match="original interleaving"):
        workspace.interleaved({"control": base, "new": child}, repeats=2, workers=2, seed=7)


def test_public_only_and_fresh_identifiers(workspace):
    base = workspace.current()
    with pytest.raises(ValueError, match="public arms"):
        workspace.interleaved({"a": base, "b": base}, split="holdout")
    workspace.launch(base, wait=True, run_id="existing")
    with pytest.raises(ValueError, match="fresh run"):
        workspace.interleaved({"existing": base, "b": base})
