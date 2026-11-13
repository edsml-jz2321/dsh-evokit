"""Run either example end to end, without model calls, in a task-owned temporary copy.

Usage: uv run python examples/scripted_improver.py toy-rules
       uv run python examples/scripted_improver.py command-echo
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from evokit import Workspace


def evolve(example: str, destination: Path) -> dict:
    """Exercise proposal, smoke, parallel arms, paired gate, adoption and measurement."""
    source = Path(__file__).parent / example
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".evokit", "__pycache__"))
    workspace = Workspace.open(destination / "scenario.yaml")
    parent = workspace.current()
    diagnosis = workspace.launch(parent, split="diagnosis", workers=2, wait=True)
    failures = workspace.cards(diagnosis["run_id"])
    workspace.launch(parent, smoke=True, workers=2, wait=True)
    control = workspace.launch(parent, repeats=3, workers=2, wait=True)
    noise = workspace.noise(control["run_id"])
    if example == "toy-rules":
        target, replacement = "rules.json", '{"threshold": 0}\n'
    elif example == "command-echo":
        target, replacement = "prefix.txt", "\n"
    else:
        raise ValueError("Unknown example")
    (destination / target).write_text(replacement, encoding="utf-8")
    child = workspace.create_version()["version"]
    proposal = workspace.propose(
        child,
        {
            "targets": [target],
            "mechanism": "Remove a systematic global decision offset",
            "predicted_effect": 4,
            "falsification": "Any paired net loss or budget violation",
            "prior": {"attempt": None, "difference": "First generic offset correction"},
        },
    )
    workspace.launch(child, smoke=True, workers=2, wait=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        incumbent_future = pool.submit(workspace.launch, parent, repeats=3, workers=2, wait=True)
        candidate_future = pool.submit(workspace.launch, child, repeats=3, workers=2, wait=True)
        control, candidate = incumbent_future.result(), candidate_future.result()
    paired = workspace.paired(control["run_id"], candidate["run_id"], "majority")
    verdict = workspace.gate(
        proposal["id"],
        control["run_id"],
        candidate["run_id"],
        noise["noise_id"],
        "majority",
        generic=True,
    )
    if verdict["verdict"] == "reject":
        raise ValueError(verdict["reasons"])
    workspace.adopt(verdict["id"], "Scripted experiment confirms the generic correction")
    workspace.measure_holdout(child, workers=2)
    return {
        "example": example,
        "failure_cards": len(failures),
        "comparison": paired,
        "verdict": verdict["verdict"],
        "current": workspace.current(),
        "metrics": workspace.metrics(),
    }


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="ek-demo-") as temporary:
        print(json.dumps(evolve(sys.argv[1], Path(temporary) / "s"), indent=2))
