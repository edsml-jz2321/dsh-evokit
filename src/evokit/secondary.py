"""Domain-neutral paired guards with explicit missing-value denominators."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Callable
from typing import Any, Literal

Extractor = Callable[[dict[str, Any]], float | None]


def _keyed(rows: list[dict], extract: Extractor) -> dict[tuple, float | None]:
    values = {}
    for row in rows:
        key = (row["task_id"], row["repeat"], row["group"])
        if row.get("error") or key in values:
            raise ValueError("Secondary guards require unique, non-errored observations")
        value = extract(row)
        if value is not None and (type(value) not in (float, int) or not math.isfinite(value)):
            raise ValueError("Secondary quality must be finite numeric or undefined")
        values[key] = value
    if not values:
        raise ValueError("Secondary guards require observations")
    return values


def paired_defined_guard(
    parent: list[dict],
    child: list[dict],
    extract: Extractor,
    *,
    margin: float = 0,
    higher_is_better: bool = True,
    aggregation: Literal["mean", "sum"] = "mean",
    one_observation_margin: bool = False,
) -> dict:
    """Compare a secondary quality only where both paired values are defined.

    Args:
        parent (list[dict]): Unique task/repeat/group observations.
        child (list[dict]): Observations with exactly the same pairing keys.
        extract (Extractor): Return a finite value or None when inapplicable.
        margin (float): Nonnegative tolerated drop, in aggregate units.
        higher_is_better (bool): False for quantities such as failure counts.
        aggregation (Literal["mean", "sum"]): Aggregate task/group means across repeats.
        one_observation_margin (bool): Floor a mean margin at 1 / defined tasks.

    Returns:
        dict: Aggregate-only verdict, applicability and missing-pair counts. An
            inapplicable comparison passes, with no invented means or zeros;
            a separate count guard can protect against newly missing values.

    Raises:
        ValueError: Invalid margins, values, aggregation or incomplete pairing.
    """
    if type(margin) not in (int, float) or not math.isfinite(margin) or margin < 0:
        raise ValueError("Secondary margin must be finite and nonnegative")
    if aggregation not in {"mean", "sum"} or (one_observation_margin and aggregation != "mean"):
        raise ValueError("Invalid secondary aggregation or observation margin")
    left, right = _keyed(parent, extract), _keyed(child, extract)
    if left.keys() != right.keys():
        raise ValueError("Secondary guards require identical task/repeat/group pairing")
    observations = defaultdict(list)
    for key in left:
        if left[key] is not None and right[key] is not None:
            observations[key[0], key[2]].append((left[key], right[key]))
    pairs = [
        tuple(statistics.mean(pair[i] for pair in values) for i in (0, 1))
        for values in observations.values()
    ]
    both_missing = sum(left[k] is None and right[k] is None for k in left)
    result = {
        "observations": len(left),
        "paired_defined": sum(map(len, observations.values())),
        "paired_defined_tasks": len(pairs),
        "both_undefined": both_missing,
        "parent_only_defined": sum(left[k] is not None and right[k] is None for k in left),
        "child_only_defined": sum(left[k] is None and right[k] is not None for k in left),
        "applicable": bool(pairs),
        "not_applicable_both_sides": both_missing == len(left),
        "noise_margin": float(margin),
        "passed": True,
    }
    if not pairs:
        return result
    aggregate = statistics.mean if aggregation == "mean" else sum
    before, after = (aggregate(pair[i] for pair in pairs) for i in (0, 1))
    effective = max(margin, 1 / len(pairs)) if one_observation_margin else margin
    delta = after - before
    result.update(
        parent=before,
        child=after,
        delta=delta,
        noise_margin=effective,
        passed=(delta if higher_is_better else -delta) >= -effective - 1e-12,
    )
    return result


def paired_quality_noise(rows: list[dict], extract: Extractor, *, multiplier: float = 1.96) -> dict:
    """Estimate macro paired-delta spread from complete defined incumbent repeats.

    Args:
        rows (list[dict]): At least two repeats, possibly with undefined qualities.
        extract (Extractor): Finite secondary value or None for each observation.
        multiplier (float): Nonnegative standard-deviation multiplier.

    Returns:
        dict: Aggregate-only spread, margin and defined/excluded task counts.
            Tasks missing any repeat are counted and excluded from this estimate.

    Raises:
        ValueError: Invalid multiplier, duplicate rows or incomplete repeats.
    """
    if type(multiplier) not in (int, float) or not math.isfinite(multiplier) or multiplier < 0:
        raise ValueError("Noise multiplier must be finite and nonnegative")
    values = _keyed(rows, extract)
    tasks = defaultdict(dict)
    for (task, repeat, group), value in values.items():
        tasks[task, group][repeat] = value
    repeats = set(next(iter(tasks.values())))
    if len(repeats) < 2 or any(set(v) != repeats for v in tasks.values()):
        raise ValueError("Quality noise requires complete incumbent repeats")
    defined = [list(v.values()) for v in tasks.values() if all(x is not None for x in v.values())]
    sigma = (
        math.sqrt(sum(2 * statistics.variance(v) for v in defined)) / len(defined) if defined else 0
    )
    return {
        "tasks": len(tasks),
        "defined_tasks": len(defined),
        "excluded_tasks": len(tasks) - len(defined),
        "repeats": len(repeats),
        "applicable": bool(defined),
        "sigma": sigma,
        "multiplier": multiplier,
        "margin": multiplier * sigma,
    }
