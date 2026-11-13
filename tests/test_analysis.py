import pytest

from evokit import Task
from evokit.analysis import compare, estimate_noise, split_tasks


def row(task, score, repeat=0, group="g", cost=1, seconds=1):
    return {
        "task_id": task,
        "outcome": score,
        "repeat": repeat,
        "group": group,
        "cost": {"units": cost},
        "seconds": seconds,
    }


def test_seeded_group_stratified_splits():
    tasks = [Task(str(i), {}, "a" if i < 30 else "b") for i in range(60)]
    splits = split_tasks(tasks, 9)
    assert splits == split_tasks(list(reversed(tasks)), 9)
    assert splits != split_tasks(tasks, 10)
    assert [len(splits[name]) for name in splits] == [24, 24, 12, 0]
    assert len({value for ids in splits.values() for value in ids}) == 60
    for ids in splits.values():
        assert sum(int(value) < 30 for value in ids) == len(ids) // 2
    with pytest.raises(ValueError):
        split_tasks(tasks + [tasks[0]])


def test_paired_fix_break_groups_and_cost_latency():
    before = [row("a", 0), row("b", 1, group="other")]
    after = [row("a", 1, cost=2), row("b", 0, group="other", seconds=2)]
    result = compare(before, after, "majority")
    assert result["fixes"] == ["a"] and result["breaks"] == ["b"]
    assert result["net"] == 0 and result["groups"]["g"]["net"] == 1
    assert result["cost"]["units"]["ratio"] == 1.5
    assert result["latency"]["ratio"] == 1.5
    with pytest.raises(ValueError):
        compare(before, after[:1], "mean")


def test_repeat_aggregation_is_explicit_and_ties_fail():
    before = [row("a", 0, 0), row("a", 0, 1)]
    after = [row("a", 1, 0), row("a", 0, 1)]
    assert compare(before, after, "mean")["net"] == 0.5
    assert compare(before, after, "majority")["net"] == 0
    with pytest.raises(ValueError):
        compare(before, after, "implicit")
    with pytest.raises(ValueError):
        compare([row("a", 0.4)], [row("a", 0.8)], "majority")


def test_noise_flip_rates_spread_and_zero_noise():
    rows = [row("a", 0, 0), row("a", 1, 1), row("b", 1, 0), row("b", 1, 1)]
    noise = estimate_noise(rows)
    assert noise["flip_rate_per_task"] == {"a": 1, "b": 0}
    assert noise["net_change_spread"] > 0 and noise["threshold"] >= 1
    assert "variance" in noise["method"]
    assert estimate_noise([row("a", 1, 0), row("a", 1, 1)])["threshold"] == 0
    with pytest.raises(ValueError):
        estimate_noise([row("a", 1)])
