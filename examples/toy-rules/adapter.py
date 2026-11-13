"""Two-stage scripted classifier with optional seeded outcome noise and replay."""

import hashlib
import json
import random
from pathlib import Path

from evokit import Result, Scenario, StageCache, Task


class ToyRules(Scenario):
    """Classify numbers by a generic positive threshold, with group-stratified tasks."""

    editable = ("rules.json", "stage1.json")
    scorer_version = "toy-positive-v1"
    # Synthetic compute latency keeps this deterministic demo independent of disk jitter.
    budgets = {"latency_ratio": 1.2, "cost_ratio": {"stage1": 1.2, "stage2": 1.2}}
    primer = "Stage 1 scales an input; stage 2 compares it with a threshold. Improve generic rules."

    def __init__(self, noise_seed: int = 0, noise_rate: float = 0.0) -> None:
        self.noise_seed = noise_seed
        self.noise_rate = noise_rate

    def tasks(self) -> list[Task]:
        return [
            Task(f"toy-case-{index:03d}", {"value": index - 30}, "low" if index < 30 else "high")
            for index in range(60)
        ]

    def run_and_score(self, version_dir: Path, task: Task) -> Result:
        return self.run_replay(version_dir, task, StageCache(version_dir / ".cache"), 0)

    def run_replay(self, version_dir: Path, task: Task, cache: StageCache, repeat: int) -> Result:
        stage1 = json.loads((version_dir / "stage1.json").read_text())
        value, cached = cache.replay(
            "scale-v1",
            {"input": task.payload, "config": stage1},
            lambda: task.payload["value"] * stage1["scale"],
        )
        threshold = json.loads((version_dir / "rules.json").read_text())["threshold"]
        prediction = value > threshold
        passed = prediction == (task.payload["value"] > 0)
        seed = int(hashlib.sha256(f"{self.noise_seed}:{task.id}:{repeat}".encode()).hexdigest(), 16)
        flipped = random.Random(seed).random() < self.noise_rate
        if flipped:
            passed = not passed
        trace = version_dir / "trace.txt"
        trace.write_text(
            f"stage1={value}; replayed={cached}; stage2={prediction}; noise={flipped}\n",
            encoding="utf-8",
        )
        return Result(
            passed,
            "classification agrees" if passed else "classification disagrees",
            {"stage1": 0 if cached else 1, "stage2": 1},
            str(trace),
            {"prediction": prediction, "stage1_replayed": cached},
            seconds=0.01,
        )
