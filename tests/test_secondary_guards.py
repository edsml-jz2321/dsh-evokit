from conftest import Minimal, evidence

from evokit import Workspace


def contract():
    return {
        "objective": "Improve primary subject to independent secondary quality",
        "guards": {"latency_ratio": 1.2, "cost_ratio": {"calls": 1.2}},
        "confirmation_rule": {"min_net": 0},
        "noise_plan": {"repeats": 2, "method": "core repeated incumbent estimate"},
        "budget": {"unit": "CNY", "total": 0, "per_generation": 0},
        "stagnation_window": 1,
    }


class Guarded(Minimal):
    def secondary_guards(self, parent, child, noise):
        # The toy secondary quality is deliberately independent of its primary.
        return {
            "passed": all("guard-fail" not in str(r["output"]) for r in child),
            "metrics": {"observations": len(child)},
        }


def test_secondary_selection_cannot_be_bypassed_by_primary_gain(tmp_path):
    root = tmp_path / "scenario"
    root.mkdir()
    (root / "rule.txt").write_text("0")
    ws = Workspace(root, Guarded())
    ws.initialize()
    ws.contract_record(contract())
    proposal, before, after, noise = evidence(ws, "1\nguard-fail\n")
    verdict = ws.gate(proposal["id"], before, after, noise, "mean", generic=True)
    assert verdict["comparison"]["net"] > 0
    assert verdict["verdict"] == "reject"
    assert not verdict["secondary_guards"]["passed"]


def test_secondary_confirmation_fails_aggregate_and_revert(tmp_path):
    root = tmp_path / "scenario"
    root.mkdir()
    (root / "rule.txt").write_text("0")
    ws = Workspace(root, Guarded())
    ws.initialize()
    ws.contract_record(contract())
    proposal, before, after, noise = evidence(ws, "1\ngeneric\n")
    verdict = ws.gate(proposal["id"], before, after, noise, "mean", generic=True)
    adoption = ws.adopt(verdict["id"], "primary improvement and passing secondary")
    run_id = ws._measurement_run(adoption["version"], "confirmation", 1, False)
    rows = ws._private_complete(run_id)
    # Build a retained child arm with a secondary regression and unchanged primary.
    retained = "private-quality-regression"
    ws._prepare_run(adoption["version"], "confirmation", 1, 1, False, retained)
    for row in rows:
        ws.store.add_result(retained, {**row, "output": "guard-fail"}, private=True)
    original = ws._measurement_run
    ws._measurement_run = lambda version, split, workers, reuse: (
        retained if version == adoption["version"] else original(version, split, workers, reuse)
    )
    aggregate = ws.confirm_adoption(adoption["version"])
    assert aggregate["net"] > 0
    assert aggregate["verdict"] == "not_confirmed"
    assert not aggregate["secondary_guards"]["passed"]
    assert not any(i in str(aggregate) for i in ws._split_ids("confirmation"))
    assert ws.ledger_revert("secondary quality regression")["not_confirmed"]
    assert ws.current() == adoption["parent"]
