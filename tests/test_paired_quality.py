"""Prospective guard semantics, using synthetic evidence only."""

import math

import pytest

from evokit import paired_defined_guard, paired_quality_noise
from evokit.analysis import estimate_noise


def rows(values, repeat=0):
    return [
        {"task_id": str(i), "repeat": repeat, "group": "g", "error": False, "value": v}
        for i, v in enumerate(values)
    ]


def value(row):
    return row["value"]


def test_both_undefined_is_explicitly_inapplicable_without_an_invented_mean():
    result = paired_defined_guard(rows([None, None]), rows([None, None]), value)
    assert result["passed"] and result["not_applicable_both_sides"]
    assert not result["applicable"] and result["paired_defined"] == 0
    assert "parent" not in result and "child" not in result


def test_pass_delta_uses_the_same_defined_pairs():
    result = paired_defined_guard(
        rows([0.8, None, 0.2]), rows([0.7, 0.0, None]), value, margin=0.11
    )
    assert result["paired_defined"] == 1 and result["passed"]
    assert result["delta"] == pytest.approx(-0.1)
    assert result["parent_only_defined"] == result["child_only_defined"] == 1


def test_empty_count_rejects_new_empty_suites_and_allows_fewer():
    before = rows([0, 1, 0, 1])
    assert not paired_defined_guard(
        before, rows([1, 1, 0, 1]), value, aggregation="sum", higher_is_better=False
    )["passed"]
    assert paired_defined_guard(
        before, rows([0, 0, 0, 1]), value, aggregation="sum", higher_is_better=False
    )["passed"]


def test_density_excludes_empty_pairs_and_floors_margin_at_one_module():
    before, after = rows([1, 1, None, None]), rows([1, 0, 1, None])
    result = paired_defined_guard(before, after, value, one_observation_margin=True)
    assert result["paired_defined"] == 2 and result["noise_margin"] == 0.5 and result["passed"]
    assert not paired_defined_guard(
        before, rows([0, 0, 1, None]), value, one_observation_margin=True
    )["passed"]


def test_quality_sigma_uses_only_complete_defined_tasks():
    noise = paired_quality_noise(rows([1, 0, None]) + rows([0, 0, 1], 1), value)
    assert noise["defined_tasks"] == 2 and noise["excluded_tasks"] == 1
    assert noise["sigma"] == 0.5 and noise["margin"] == 0.98


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "text", True])
def test_quality_values_fail_closed(bad):
    with pytest.raises(ValueError):
        paired_defined_guard(rows([1]), rows([bad]), value)


def test_pairing_is_exact_and_never_discloses_private_ids():
    with pytest.raises(ValueError, match="identical task/repeat/group") as exc:
        paired_defined_guard(rows([1]), [{**rows([1])[0], "task_id": "PRIVATE"}], value)
    assert "PRIVATE" not in str(exc.value)
    with pytest.raises(ValueError, match="unique"):
        paired_defined_guard(rows([1]) * 2, rows([1]) * 2, value)


def test_primary_multiplier_is_configurable_and_legacy_default_stays_conservative():
    data = [{**r, "outcome": r["value"]} for r in rows([0.1, 0.1]) + rows([0.2, 0.2], 1)]
    default = estimate_noise(data)
    exact = estimate_noise(data, multiplier=1, observed_floor=False)
    assert default["threshold"] == pytest.approx(max(0.2, 1.96 * math.sqrt(0.02)))
    assert exact["threshold"] == pytest.approx(math.sqrt(0.02))
    assert exact["multiplier"] == 1 and not exact["observed_floor"]


@pytest.mark.parametrize("aggregation,expected", [("mean", math.sqrt(2)), ("majority", 2.0)])
def test_workspace_anchors_noise_policy_and_scales_pooled_threshold(
    tmp_path, monkeypatch, aggregation, expected
):
    from conftest import Minimal, evidence

    import evokit.workspace as module
    from evokit import Workspace

    class Sensitive(Minimal):
        noise_policy = {"multiplier": 1, "observed_floor": False}

    root = tmp_path / "scenario"
    root.mkdir()
    (root / "rule.txt").write_text("0")
    ws = Workspace(root, Sensitive())
    ws.initialize()
    original = module.estimate_noise

    def fixed(rows, **policy):
        assert policy == Sensitive.noise_policy
        return {**original(rows, **policy), "threshold": 2.0}

    monkeypatch.setattr(module, "estimate_noise", fixed)
    proposal, before, after, noise = evidence(ws, "1\ngeneric\n")
    verdict = ws.gate(proposal["id"], before, after, noise, aggregation, generic=True)
    assert verdict["threshold"] == pytest.approx(expected)
    assert ws._epoch()["policy"]["noise_policy"] == Sensitive.noise_policy
    ws.scenario.noise_policy = {"multiplier": 1.96, "observed_floor": True}
    with pytest.raises(ValueError, match="policy"):
        ws.launch(ws.current(), wait=True)
    ws.close()


def test_repeat_means_keep_count_and_density_margins_in_module_units():
    before = rows([0, 0]) + rows([0, 0], 1)
    after = rows([1, 0]) + rows([1, 0], 1)
    result = paired_defined_guard(
        before, after, value, aggregation="sum", higher_is_better=False, margin=1
    )
    assert result["passed"] and result["delta"] == 1
    assert result["paired_defined"] == 4 and result["paired_defined_tasks"] == 2
    density = paired_defined_guard(after, before, value, one_observation_margin=True)
    assert density["passed"] and density["noise_margin"] == 0.5
