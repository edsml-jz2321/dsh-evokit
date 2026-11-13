import json
from concurrent.futures import ThreadPoolExecutor

import jsonschema
import pytest
from conftest import candidate, evidence, proposal

from evokit.storage import Ledger


def gate(workspace, data, **options):
    item, before, after, noise = data
    return workspace.gate(item["id"], before, after, noise, "majority", generic=True, **options)


def test_resume_never_reexecutes_completed_results(workspace):
    run = workspace.launch(workspace.current(), repeats=2, workers=3, wait=True)
    result_file = workspace.state / "runs" / run["run_id"] / "results.jsonl"
    original_rows = workspace.results(run["run_id"])
    original = result_file.read_bytes() if workspace.store.backend == "files" else None
    workspace.launch(workspace.current(), repeats=2, workers=3, wait=True, run_id=run["run_id"])
    assert workspace.results(run["run_id"]) == original_rows
    if workspace.store.backend == "files":
        assert result_file.read_bytes() == original
        with result_file.open("ab") as stream:
            stream.write(b'{"partial":')
        workspace.execute(run["run_id"])
        assert result_file.read_bytes() == original
    else:
        assert not result_file.exists()
    with pytest.raises(ValueError, match="Resume"):
        workspace.launch(workspace.current(), repeats=3, workers=3, wait=True, run_id=run["run_id"])


def test_resume_fills_missing_slots_and_preserves_committed_prefix(workspace):
    run = workspace.launch(workspace.current(), repeats=2, workers=3, wait=True)
    result_file = workspace.state / "runs" / run["run_id"] / "results.jsonl"
    if workspace.store.backend == "files":
        prefix = b"".join(result_file.read_bytes().splitlines(keepends=True)[:5])
        result_file.write_bytes(prefix)
    else:
        entries = [
            (k, v) for k, v in workspace.store.items("result") if k.startswith(run["run_id"] + "/")
        ]
        for key, _ in entries[5:]:
            workspace.store.workspaces[False].delete(
                (workspace.store.entities["result"], workspace.store._id("result", key))
            )
    original = workspace._execute_task
    calls = []

    def count(path, meta, task, repeat):
        calls.append(task.id)
        return original(path, meta, task, repeat)

    workspace._execute_task = count
    workspace.execute(run["run_id"])
    assert len(calls) == run["expected"] - 5
    if workspace.store.backend == "files":
        assert result_file.read_bytes().startswith(prefix)


def test_execution_errors_are_recorded_and_block_comparison(workspace):
    from evokit import Result, Workspace

    workspace.scenario.run_and_score = lambda directory, task: Result(float("nan"), "invalid")
    fresh = Workspace(
        workspace.root,
        workspace.scenario,
        workspace.root.parent / "invalid",
        storage={"backend": workspace.store.backend},
    )
    fresh.initialize()
    workspace = fresh
    run = workspace.launch(workspace.current(), wait=True)
    assert run["errors"] == run["expected"]
    assert all(row["error"] and row["outcome"] == 0 for row in workspace.results(run["run_id"]))
    with pytest.raises(ValueError, match="execution errors"):
        workspace.paired(run["run_id"], run["run_id"], "mean")


def test_smoke_and_failure_card_filters(workspace):
    before = workspace.launch(workspace.current(), wait=True)["run_id"]
    child = candidate(workspace)
    after = workspace.launch(child, wait=True)["run_id"]
    assert workspace.launch(child, smoke=True, wait=True)["expected"] == 2
    cards = workspace.cards(before, group="g0")
    assert cards and all(card["group"] == "g0" for card in cards)
    assert all(card["trace_excerpt_pointer"] for card in cards)
    assert workspace.cards(after) == []
    assert workspace.cards(after, parent_run=before, change="fix")
    assert workspace.cards(after, parent_run=before, change="break") == []


def test_proposal_schema_prior_and_playbook_tracking(workspace):
    child = candidate(workspace)
    item = proposal(workspace, child)
    assert (workspace.state / "playbooks" / f"{item['playbook_version']}.md").is_file()
    invalid = {
        key: value
        for key, value in item.items()
        if key in {"targets", "mechanism", "predicted_effect", "falsification", "prior"}
    }
    invalid.pop("falsification")
    with pytest.raises(jsonschema.ValidationError):
        workspace.propose(child, invalid)
    invalid["falsification"] = "loss"
    invalid["prior"] = {"attempt": "missing", "difference": "different"}
    with pytest.raises(ValueError, match="Prior"):
        workspace.propose(child, invalid)


def test_promotion_adoption_chain_runner_ups_and_metrics(workspace):
    data = evidence(workspace)
    verdict = gate(workspace, data)
    assert verdict["verdict"] == "promote"
    workspace.adopt(verdict["id"], "retest later", queue=True)
    assert len(workspace.history()["runner_ups"]) == 1
    adopted = workspace.adopt(verdict["id"], "paired evidence supports correction")
    assert workspace.current() == adopted["version"]
    assert len(workspace.history()["base_chain"]) == 2
    assert workspace.history()["runner_ups"] == []
    metrics = workspace.metrics()
    assert metrics["acceptance_rate"] == 1 and metrics["gain_per_cost"]["calls"] > 0
    assert metrics["mean_absolute_prediction_error"] is not None
    with pytest.raises(ValueError, match="recheck"):
        workspace.adopt(verdict["id"], "duplicate adoption")


@pytest.mark.parametrize(
    "content,reason",
    [("1 expensive\n", "Cost"), ("1 slow\n", "Latency"), ("1 public-marker\n", "Instance-literal")],
)
def test_gate_hard_constraints(workspace, content, reason):
    if "public-marker" in content:
        task_id = workspace._epoch()["splits"]["selection"][0]
        content = content.replace("public-marker", task_id)
    verdict = gate(workspace, evidence(workspace, content))
    assert verdict["verdict"] == "reject"
    assert any(reason in message for message in verdict["reasons"])
    with pytest.raises(ValueError):
        workspace.adopt(verdict["id"], "cannot adopt")


def test_gate_no_harm_needs_rationale_and_generic_attestation(workspace):
    data = evidence(workspace, "0 generic-change\n")
    assert gate(workspace, data)["verdict"] == "reject"
    assert (
        gate(workspace, data, rationale="simplifies the generic rule")["verdict"] == "adopt-no-harm"
    )
    item, before, after, noise = data
    verdict = workspace.gate(item["id"], before, after, noise, "mean", rationale="simpler")
    assert verdict["verdict"] == "reject"


def test_gate_anchor_mutation_and_holdout_isolation(workspace):
    data = evidence(workspace)
    workspace.scenario.scorer_version = "changed"
    assert gate(workspace, data)["verdict"] == "reject"
    with pytest.raises(ValueError, match="improver-facing"):
        workspace.launch(workspace.current(), split="holdout", wait=True)


def test_smoke_cannot_authorize_adoption(workspace):
    item, before, after, noise = evidence(workspace)
    smoke = workspace.launch(item["version"], repeats=2, workers=3, smoke=True, wait=True)
    verdict = workspace.gate(item["id"], before, smoke["run_id"], noise, "majority", generic=True)
    assert verdict["verdict"] == "reject"


def test_epoch_file_cannot_reassign_holdout(workspace):
    data = evidence(workspace)
    epoch = workspace._epoch()
    epoch["splits"]["confirmation"], epoch["splits"]["selection"] = (
        epoch["splits"]["selection"],
        epoch["splits"]["confirmation"],
    )
    workspace.store.put("epoch", "singleton", epoch, private=True)
    verdict = gate(workspace, data)
    assert verdict["verdict"] == "reject"
    assert "epoch integrity" in " ".join(verdict["reasons"])


def test_absolute_budget_counts_all_repeats(workspace):
    # Freeze the budget at initialization; changing it afterwards is also rejected.
    from evokit import Workspace

    workspace.scenario.budgets = {"max_cost": {"calls": 15}}
    fresh = Workspace(
        workspace.root,
        workspace.scenario,
        workspace.root.parent / "private",
        storage={"backend": workspace.store.backend},
    )
    fresh.initialize()
    verdict = gate(fresh, evidence(fresh))
    assert verdict["verdict"] == "reject"
    assert "Absolute cost" in " ".join(verdict["reasons"])


def test_holdout_measurement_hidden_from_every_public_output(workspace):
    verdict = gate(workspace, evidence(workspace))
    adopted = workspace.adopt(verdict["id"], "adopt before final measurement")
    assert workspace.measure_holdout(adopted["version"])["measured_versions"] == 1
    epoch = workspace._epoch()
    public = json.dumps([workspace.describe(), workspace.history(), workspace.metrics()])
    for task_id in epoch["splits"]["confirmation"]:
        assert task_id not in public
    assert workspace.metrics()["holdout"]["measured_versions"] == 1
    for run_id, _ in workspace.store.items("run", private=True):
        for read in [workspace.status, workspace.results, workspace.cards, workspace.noise]:
            with pytest.raises(ValueError, match="Holdout"):
                read(run_id)


def test_ledger_append_only_integrity_and_parallel_writers(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append("first", {"value": 1})
    prefix = ledger.path.read_bytes()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: ledger.append("event", {"value": i}), range(12)))
    assert ledger.path.read_bytes().startswith(prefix)
    assert len(ledger.history()) == 13
    ledger.path.write_bytes(ledger.path.read_bytes().replace(b'"value":1', b'"value":9', 1))
    with pytest.raises(ValueError, match="integrity"):
        ledger.history()
