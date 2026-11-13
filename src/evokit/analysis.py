"""Deterministic splitting and paired analysis, independent of transports and storage."""

from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from typing import Any

from .scenario import Task
from .storage import digest


def split_tasks(
    tasks: list[Task],
    seed: int = 0,
    *,
    stratified: bool = True,
    fractions: tuple[float, ...] = (0.4, 0.4, 0.2, 0.0),
    legacy: bool = False,
    assignments: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    """Assign stable ids to seeded or exact evolution partitions.

    Args:
        tasks (list[Task]): Unique tasks; input order does not affect assignment.
        seed (int): Epoch seed.
        stratified (bool): Preserve proportions within each optional group.
        fractions (tuple[float, ...]): Diagnosis, selection, confirmation and final
            shares. Three values retain the legacy zero-final interpretation.
        legacy (bool): Use the old holdout name when checking retained epochs.
        assignments (dict[str, list[str]] | None): Optional exact disjoint partition
            with diagnosis, selection, confirmation and final keys. Overrides
            seeded allocation; incompatible with legacy holdout naming.

    Returns:
        dict[str, list[str]]: Disjoint sorted ids. Tiny strata may yield empty splits.

    Raises:
        ValueError: For duplicate ids, invalid proportions or non-partition assignments.
    """
    if len({task.id for task in tasks}) != len(tasks):
        raise ValueError("Task ids must be unique")
    if assignments is not None:
        names = {"diagnosis", "selection", "confirmation", "final"}
        if not isinstance(assignments, dict) or set(assignments) != names or legacy:
            raise ValueError("Explicit splits require diagnosis, selection, confirmation and final")
        if any(
            not isinstance(ids, list) or any(not isinstance(i, str) for i in ids)
            for ids in assignments.values()
        ):
            raise ValueError("Explicit splits must contain lists of task ids")
        flattened = [i for ids in assignments.values() for i in ids]
        if (
            len(flattened) != len(set(flattened))
            or set(flattened) != {t.id for t in tasks}
            or any(not assignments[k] for k in names - {"final"})
        ):
            raise ValueError("Explicit splits must partition all tasks without overlap")
        return {
            k: sorted(assignments[k]) for k in ("diagnosis", "selection", "confirmation", "final")
        }
    if len(fractions) == 3:
        fractions = (*fractions, 0.0)
    if (
        len(fractions) != 4
        or any(not math.isfinite(n) or n <= 0 for n in fractions[:3])
        or not math.isfinite(fractions[3])
        or fractions[3] < 0
    ):
        raise ValueError("Three positive fractions and an optional nonnegative final are required")
    if not math.isclose(sum(fractions), 1):
        raise ValueError("Split fractions must sum to 1")
    groups: dict[str, list[str]] = defaultdict(list)
    for task in tasks:
        groups[task.group if stratified else "all"].append(task.id)
    names = ("diagnosis", "selection", "holdout" if legacy else "confirmation", "final")
    splits: dict[str, list[str]] = {name: [] for name in names}
    for group, ids in sorted(groups.items()):
        ids.sort()
        random.Random(digest([seed, group])).shuffle(ids)
        # Largest remainder rounding avoids systematically starving holdout in small strata.
        raw = [len(ids) * fraction for fraction in fractions]
        sizes = [int(value) for value in raw]
        for index in sorted(range(4), key=lambda i: (raw[i] - sizes[i], -i), reverse=True)[
            : len(ids) - sum(sizes)
        ]:
            sizes[index] += 1
        offset = 0
        for name, size in zip(splits, sizes, strict=True):
            splits[name].extend(ids[offset : offset + size])
            offset += size
    return {name: sorted(ids) for name, ids in splits.items() if not (legacy and name == "final")}


def aggregate(rows: list[dict[str, Any]], method: str) -> dict[str, dict[str, Any]]:
    if method not in {"mean", "majority"}:
        raise ValueError("aggregation must be explicit: mean or majority")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["task_id"]].append(row)
    result = {}
    for task_id, items in grouped.items():
        values = [float(item["outcome"]) for item in items]
        if method == "majority" and any(value not in (0, 1) for value in values):
            raise ValueError("Majority requires binary outcomes; use mean for scores")
        score = statistics.mean(values)
        if method == "majority":
            score = float(score > 0.5)  # Ties fail, deliberately conservative.
        costs = {
            key: statistics.mean(item.get("cost", {}).get(key, 0) for item in items)
            for key in {key for item in items for key in item.get("cost", {})}
        }
        result[task_id] = {
            "score": score,
            "group": items[0]["group"],
            "cost": costs,
            "seconds": statistics.mean(item["seconds"] for item in items),
            "repeats": len(items),
        }
    return result


def _ratio(parent: float, child: float) -> float | None:
    return child / parent if parent else (1.0 if not child else None)


def compare(
    parent: list[dict[str, Any]], child: list[dict[str, Any]], aggregation: str
) -> dict[str, Any]:
    """Compare arms on exactly the same tasks and repeats.

    Args:
        parent (list[dict[str, Any]]): Completed incumbent rows.
        child (list[dict[str, Any]]): Completed candidate rows.
        aggregation (str): `mean` for scores; `majority` for binary votes (ties fail).

    Returns:
        dict[str, Any]: Fixes, breaks, net score sum, groups, costs and latency ratios.

    Raises:
        ValueError: If pairing, groups or repeat counts differ.
    """
    left, right = aggregate(parent, aggregation), aggregate(child, aggregation)
    if not left or left.keys() != right.keys():
        raise ValueError("Paired comparison requires identical nonempty task sets")
    if any(
        left[key]["repeats"] != right[key]["repeats"] or left[key]["group"] != right[key]["group"]
        for key in left
    ):
        raise ValueError("Paired comparison requires identical groups and repeat counts")
    deltas = {key: right[key]["score"] - left[key]["score"] for key in sorted(left)}
    groups = {}
    for group in sorted({item["group"] for item in left.values()}):
        values = [delta for key, delta in deltas.items() if left[key]["group"] == group]
        groups[group] = {
            "tasks": len(values),
            "fixes": sum(n > 0 for n in values),
            "breaks": sum(n < 0 for n in values),
            "net": sum(values),
        }
    units = {key for item in [*left.values(), *right.values()] for key in item["cost"]}
    costs = {}
    for unit in sorted(units):
        before = sum(item["cost"].get(unit, 0) for item in left.values())
        after = sum(item["cost"].get(unit, 0) for item in right.values())
        costs[unit] = {"parent": before, "child": after, "ratio": _ratio(before, after)}
    before = sum(item["seconds"] for item in left.values())
    after = sum(item["seconds"] for item in right.values())
    return {
        "aggregation": aggregation,
        "tasks": len(left),
        "fixes": [key for key, value in deltas.items() if value > 0],
        "breaks": [key for key, value in deltas.items() if value < 0],
        "net": sum(deltas.values()),
        "per_task": deltas,
        "groups": groups,
        "cost": costs,
        "latency": {"parent": before, "child": after, "ratio": _ratio(before, after)},
    }


def estimate_noise(
    rows: list[dict[str, Any]], *, multiplier: float = 1.96, observed_floor: bool = True
) -> dict[str, Any]:
    """Estimate paired net spread from repeated incumbent task outcomes.

    Args:
        rows (list[dict[str, Any]]): At least two complete repeats per task.
        multiplier (float): Standard-deviation multiplier fixed by the epoch.
        observed_floor (bool): Retain the historical absolute-repeat-net floor.

    Returns:
        dict[str, Any]: Pairwise flip rates, spread, observed nets and threshold method.

    Raises:
        ValueError: If repeats are incomplete or fewer than two.
    """
    if (
        type(multiplier) not in (float, int)
        or not math.isfinite(multiplier)
        or multiplier < 0
        or type(observed_floor) is not bool
    ):
        raise ValueError("Invalid noise policy")
    tasks: dict[str, dict[int, float]] = defaultdict(dict)
    for row in rows:
        if row["repeat"] in tasks[row["task_id"]]:
            raise ValueError("Duplicate noise observation")
        tasks[row["task_id"]][row["repeat"]] = float(row["outcome"])
    if not tasks:
        raise ValueError("Noise requires observations")
    repeats = sorted(next(iter(tasks.values())))
    if len(repeats) < 2 or any(sorted(values) != repeats for values in tasks.values()):
        raise ValueError("Noise requires at least two complete repeats")
    pairs = [(a, b) for a in repeats for b in repeats if a < b]
    flips = {
        key: sum(values[a] != values[b] for a, b in pairs) / len(pairs)
        for key, values in tasks.items()
    }
    nets = [sum(values[b] - values[a] for values in tasks.values()) for a, b in pairs]
    spread = math.sqrt(sum(2 * statistics.variance(values.values()) for values in tasks.values()))
    threshold = max(max(abs(net) for net in nets) if observed_floor else 0, multiplier * spread)
    return {
        "repeats": len(repeats),
        "tasks": len(tasks),
        "flip_rate_per_task": flips,
        "mean_flip_rate": statistics.mean(flips.values()),
        "net_change_spread": spread,
        "observed_pair_nets": nets,
        "threshold": threshold,
        "multiplier": multiplier,
        "observed_floor": observed_floor,
        "method": "max(enabled observed absolute repeat net floor, multiplier * sqrt(sum(2 * sample variance))); "
        "net score-sum units; conservative single-repeat reference, not a confidence guarantee",
    }
