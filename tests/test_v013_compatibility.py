"""Keep released epoch anchors immutable while accepting reviewed core upgrades."""

import hashlib
from pathlib import Path

import pytest
from conftest import Minimal, evidence

from evokit import Scenario, Workspace
from evokit.anchors import V012_SCENARIO_SHA256, V013_SCENARIO_SHA256, V015_SCENARIO_SHA256
from evokit.storage import digest


@pytest.mark.parametrize("legacy", [V012_SCENARIO_SHA256, V013_SCENARIO_SHA256])
def test_legacy_default_guard_epoch_reopens_without_rewrite(tmp_path, legacy):
    root = tmp_path / "scenario"
    root.mkdir()
    (root / "rule.txt").write_text("0")
    ws = Workspace(root, Minimal())
    ws.initialize()
    epoch = ws._epoch()
    # Reproduce v0.1.2's anchor set. The released package module is the only
    # accepted old checksum; user sources/callables keep their exact anchors.
    if legacy == V012_SCENARIO_SHA256:
        epoch["scorer_sources"].pop("callable:secondary_guards")
    for key, value in list(epoch["scorer_sources"].items()):
        if value == V015_SCENARIO_SHA256:
            epoch["scorer_sources"][key] = legacy
    epoch.pop("split_assignments", None)
    epoch["policy"].pop("noise_policy")
    events = ws.store.history()
    events[0]["data"]["anchor_hash"] = digest(epoch)
    events[0]["hash"] = digest({k: v for k, v in events[0].items() if k != "hash"})
    ws.store.put("epoch", "singleton", epoch, private=True)
    ws.store.ledger.path.unlink()
    ws.store.restore_events(events)
    before = {p.relative_to(ws.state): p.read_bytes() for p in ws.state.rglob("*") if p.is_file()}
    ws.close()
    reopened = Workspace(root, Minimal())
    reopened.initialize()
    reopened._check_anchors()
    after = {
        p.relative_to(reopened.state): p.read_bytes()
        for p in reopened.state.rglob("*")
        if p.is_file()
    }
    assert before == after
    reopened.scenario.secondary_guards = lambda *args: {"passed": True, "metrics": {}}
    with pytest.raises(ValueError, match="new epoch"):
        reopened._check_anchors()
    reopened.close()


def test_compatibility_digest_matches_reviewed_installed_source():
    source = Path(__import__("evokit.scenario", fromlist=["Scenario"]).__file__)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == V015_SCENARIO_SHA256


def test_callback_code_tamper_and_adoption_recheck(tmp_path):
    root = tmp_path / "s"
    root.mkdir()
    (root / "rule.txt").write_text("0")
    ws = Workspace(root, Minimal())
    ws.initialize()
    proposal, before, after, noise = evidence(ws)
    verdict = ws.gate(proposal["id"], before, after, noise, "mean", generic=True)
    assert verdict["verdict"] == "promote"
    ws.scenario.secondary_guards = lambda parent, child, noise: {"passed": True, "metrics": {}}
    with pytest.raises(ValueError, match="recheck rejected"):
        ws.adopt(verdict["id"], "must recheck callback anchor")
    assert ws.current() == proposal["parent"]
    ws.close()


@pytest.mark.parametrize(
    "metrics", [{"reason": "private task text"}, {"values": [1, 2]}, {"nan": float("nan")}]
)
def test_secondary_result_is_numeric_only(tmp_path, metrics):
    ws = Workspace(tmp_path, Minimal())
    ws.scenario.secondary_guards = lambda *args: {"passed": True, "metrics": metrics}
    with pytest.raises(ValueError, match="aggregate numeric"):
        ws._secondary_guards([], [], [])
    ws.close()


def test_downstream_capability_refusal_accepts_v013():
    import inspect

    from evokit.analysis import split_tasks

    # This matches a downstream adapter's capability predicate.
    assert "assignments" in inspect.signature(split_tasks).parameters
    assert hasattr(Scenario, "secondary_guards")


def test_secondary_metric_names_cannot_be_task_ids(tmp_path):
    ws = Workspace(tmp_path, Minimal())
    ws.scenario.secondary_guards = lambda *args: {"passed": True, "metrics": {"private-task": 1}}
    with pytest.raises(ValueError, match="aggregate numeric"):
        ws._secondary_guards([{"task_id": "private-task"}], [], [])
    ws.close()
