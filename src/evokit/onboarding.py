"""Scenario scaffolding and operator readiness checks behind the CLI."""

from __future__ import annotations

import argparse
import json
import math
import sys
import uuid
from collections import defaultdict
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import yaml

from .guards import introduced_literal
from .scenario import Task
from .storage import atomic_json, digest, file_lock
from .tools import available_tools
from .workspace import Workspace

_RUNNER = '''"""Replace this scaffold with your system's run-and-score command."""
import json
import os
from pathlib import Path

# Read task JSON from EVOKIT_TASK_FILE. Execute against EVOKIT_VERSION_DIR.
# Write one Result JSON to EVOKIT_RESULT_FILE, then exit zero.
# Keep final scoring outside editable globs. Report real cost units.
task = json.loads(Path(os.environ["EVOKIT_TASK_FILE"]).read_text(encoding="utf-8"))
raise NotImplementedError("Implement the runner and scorer before doctor --run")
'''

_ADAPTER = '''"""Python scenario skeleton; replace run_and_score with a direct SDK call if useful."""
from pathlib import Path

from evokit import CommandScenario, Result, Scenario, Task


class Adapter(Scenario):
    """Delegate the three-piece contract to the configured command."""

    def __init__(self, editable: list[str], tasks_file: str, command: list[str] | str):
        self.editable = tuple(editable)
        self._command = CommandScenario(
            Path(__file__).parent,
            {"editable": editable, "tasks": tasks_file, "command": command},
        )

    def tasks(self) -> list[Task]:
        return list(self._command.tasks())

    def run_and_score(self, version_dir: Path, task: Task) -> Result:
        return self._command.run_and_score(version_dir, task)
'''


def initialize_scenario(
    directory: Path,
    editable: list[str],
    tasks: Path,
    command: list[str] | str | None = None,
    *,
    adapter: str = "command",
    seed: int = 17,
) -> dict[str, Any]:
    """Write a portable command or Python scenario without overwriting files.

    Args:
        directory (Path): New or existing scenario root.
        editable (list[str]): Safe root-relative artifact globs.
        tasks (Path): Existing JSON task array, copied as tasks.json when external.
        command (list[str] | str | None): Trusted run/score argv or shell command.
            None creates an intentionally unfinished standalone Python runner.
        adapter (str): `command` for YAML or `python` for a delegating SDK skeleton.
        seed (int): Deterministic split seed.

    Returns:
        dict[str, Any]: Config path and created filenames, with no task payloads.

    Raises:
        ValueError: For invalid inputs or a conflicting destination.
    """
    directory, tasks = directory.resolve(), tasks.resolve()
    if adapter not in {"command", "python"}:
        raise ValueError("adapter must be command or python")
    if not editable or any(
        not isinstance(p, str)
        or not p
        or p.startswith(("/", "\\"))
        or ".." in p.split("/")
        or ":" in p
        or "\\" in p
        for p in editable
    ):
        raise ValueError("editable must contain safe relative path globs")
    rows = json.loads(tasks.read_text(encoding="utf-8-sig"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("Task file must be a nonempty JSON array")
    parsed = [Task(**row) for row in rows]
    if any(not isinstance(t.id, str) or not t.id or not isinstance(t.group, str) for t in parsed):
        raise ValueError("Tasks require nonempty string ids and string groups")
    if len({t.id for t in parsed}) != len(parsed):
        raise ValueError("Task ids must be unique")
    digest(rows)
    if command is not None and not (
        isinstance(command, str)
        and command.strip()
        or isinstance(command, list)
        and command
        and all(isinstance(x, str) and x for x in command)
    ):
        raise ValueError("Command must be nonempty argv or a shell string")
    files = {}
    if command is None:
        command = ["python", "runner.py"]
        files["runner.py"] = _RUNNER
    task_name = (
        tasks.relative_to(directory).as_posix() if tasks.is_relative_to(directory) else "tasks.json"
    )
    if not tasks.is_relative_to(directory):
        files[task_name] = json.dumps(rows, indent=2, allow_nan=False) + "\n"
    config: dict[str, Any] = {"seed": seed}
    if adapter == "command":
        config.update(editable=editable, tasks=task_name, command=command)
    else:
        config.update(
            adapter="./adapter.py:Adapter",
            options={
                "editable": editable,
                "tasks_file": task_name,
                "command": command,
            },
        )
        files["adapter.py"] = _ADAPTER
    files["scenario.yaml"] = yaml.safe_dump(config, sort_keys=False)
    conflicts = [name for name in files if (directory / name).exists()]
    if conflicts:
        raise ValueError("Refusing to overwrite: " + ", ".join(conflicts))
    directory.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        for name, content in files.items():
            path = directory / name
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                created.append(path)
                stream.write(content)
    except BaseException:
        for path in created:
            path.unlink()
        raise
    return {
        "config": str(directory / "scenario.yaml"),
        "created": sorted(files),
        "adapter": adapter,
    }


def doctor(
    config: Path, *, run: bool = False, labels: dict[str, float] | None = None
) -> dict[str, Any]:
    """Check a trusted scenario without invoking its runner unless explicitly enabled.

    Args:
        config (Path): Scenario YAML. Normal workspace initialization freezes anchors.
        run (bool): Run two public diagnosis tasks twice, plus labeled public tasks
            not in that smoke once. No failed slot is retried and holdout never runs.
        labels (dict[str, float] | None): Expected public task scores in [0, 1].
            None uses the adapter's public calibration_labels entries.

    Returns:
        dict[str, Any]: Public checks, stable config identity, measured cost per
        invocation, and a selection-noise suggestion. Skipped checks are incomplete.

    Raises:
        ValueError: For invalid configuration or nonpublic/invalid explicit labels.
    """
    config = config.resolve()
    workspace = Workspace.open(config)
    try:
        with file_lock(workspace.state / "decision.lock") if run else nullcontext():
            # The operator checker reads frozen splits internally; output contains counts only.
            epoch = workspace._epoch()
            splits = epoch["splits"]
            public = set(splits["diagnosis"]) | set(splits["selection"])
            supplied = labels is not None
            labels = (
                labels
                if supplied
                else {k: v for k, v in workspace.scenario.calibration_labels.items() if k in public}
            )
            if not isinstance(labels, dict) or any(
                k not in public
                or not isinstance(v, (int, float))
                or not math.isfinite(v)
                or not 0 <= v <= 1
                for k, v in labels.items()
            ):
                raise ValueError("Labels must map public task ids to finite scores in [0, 1]")
            identities = []
            for _ in range(2):
                version = workspace.create_version()["version"]
                data = yaml.safe_load(config.read_text(encoding="utf-8"))
                identities.append(
                    digest(
                        {
                            "version": version,
                            "config": data,
                            "scorer_version": workspace.scenario.scorer_version,
                        }
                    )
                )
            files = workspace._manifest(version)
            leaked = introduced_literal(
                {},
                {name: workspace._blob(sha) for name, sha in files.items()},
                sorted(public),
                list(workspace.scenario.literals) + list(workspace.scenario.gold_strings),
            )
            groups = [set(ids) for ids in splits.values()]
            schema = next(t.schema for t in available_tools() if t.name == "run_launch")
            isolated = all(not a & b for i, a in enumerate(groups) for b in groups[i + 1 :])
            isolated = isolated and "holdout" not in schema["properties"]["split"]["enum"]
            checks = {
                "reproducibility": {"status": "pass" if len(set(identities)) == 1 else "fail"},
                "holdout_isolation": {
                    "status": "pass"
                    if isolated
                    and all(
                        workspace._split_ids(n) for n in ("diagnosis", "selection", "confirmation")
                    )
                    else "fail",
                    "scope": "split and tool boundary; trusted code is not sandboxed",
                },
                "leak_scan": {
                    "status": "fail" if leaked else "pass",
                    "scope": "public ids and declared public literals only",
                },
                "dry_run": {"status": "skipped"},
                "scorer_sanity": {"status": "skipped", "labels": len(labels)},
            }
            report: dict[str, Any] = {
                "config_identity": identities[0],
                "version": version,
                "checks": checks,
                "counts": workspace._split_counts(),
                "noise_suggestion": {
                    "split": "selection",
                    "repeats": 2,
                    "instruction": "Repeat the incumbent, then call noise_estimate; a two-task check is not a promotion threshold.",
                },
                "cost_per_task": None,
            }
            if run and not any(c["status"] == "fail" for c in checks.values()):
                with file_lock(workspace.state / "workspace.lock"):
                    workspace._ensure_evolving()
                    workspace.ledger.append("work_started", {"source": "doctor"})
                smoke = sorted(splits["diagnosis"])[:2]
                if not smoke:
                    checks["dry_run"] = {"status": "fail", "reason": "Diagnosis split is empty"}
                else:
                    tasks = {
                        row["id"]: Task(**row) for row in epoch["tasks"] if row["id"] in public
                    }
                    slots = [(task, repeat) for repeat in range(2) for task in smoke]
                    slots += [(task, 0) for task in sorted(labels) if task not in smoke]
                    root = workspace.state / "doctor" / uuid.uuid4().hex
                    rows = []
                    for task_id, repeat in slots:
                        meta = {
                            "version": version,
                            "split": "diagnosis" if task_id in groups[0] else "selection",
                            "scorer_version": workspace.scenario.scorer_version,
                            "epoch": epoch["id"],
                        }
                        row = workspace._execute_task(root, meta, tasks[task_id], repeat)
                        rows.append(row)
                        atomic_json(root / "results.json", rows)
                        if row["error"]:
                            break
                    failed = any(row["error"] for row in rows)
                    checks["dry_run"] = {
                        "status": "fail" if failed else "pass",
                        "invocations": len(rows),
                        "planned": len(slots),
                    }
                    first = {row["task_id"]: row for row in rows if row["repeat"] == 0}
                    if labels and not failed:
                        agreement = sum(
                            math.isclose(first[k]["outcome"], value, abs_tol=1e-9)
                            for k, value in labels.items()
                        )
                        checks["scorer_sanity"] = {
                            "status": "pass" if agreement == len(labels) else "fail",
                            "labels": len(labels),
                            "agreement": agreement / len(labels),
                        }
                    totals: dict[str, float] = defaultdict(float)
                    for row in rows:
                        for unit, value in row["cost"].items():
                            totals[unit] += value
                    report["cost_per_task"] = (
                        None
                        if failed
                        else {unit: value / len(rows) for unit, value in totals.items()}
                    )
                    report["seconds_per_task"] = sum(row["seconds"] for row in rows) / len(rows)
                    report["outcome_agreement"] = (
                        None
                        if failed
                        else sum(
                            first[k]["outcome"]
                            == next(
                                row["outcome"]
                                for row in rows
                                if row["task_id"] == k and row["repeat"] == 1
                            )
                            for k in smoke
                        )
                        / len(smoke)
                    )
            report["ready"] = all(c["status"] == "pass" for c in checks.values())
            return report
    finally:
        workspace.close()


def run_cli(arguments: list[str]) -> dict[str, Any]:
    """Parse onboarding CLI arguments and return one JSON report.

    Args:
        arguments (list[str]): Arguments starting with `init` or `doctor`.

    Returns:
        dict[str, Any]: Created files or readiness checks.
    """
    parser = argparse.ArgumentParser(prog="evokit " + arguments[0])
    if arguments[0] == "init":
        parser.add_argument("directory", type=Path)
        parser.add_argument("--editable", action="append")
        parser.add_argument("--tasks", type=Path)
        parser.add_argument("--adapter", choices=("command", "python"), default="command")
        parser.add_argument("--seed", type=int, default=17)
        commands = parser.add_mutually_exclusive_group()
        commands.add_argument("--command")
        commands.add_argument("--command-argv", nargs=argparse.REMAINDER)
        args = parser.parse_args(arguments[1:])
        if sys.stdin.isatty() and (args.editable is None or args.tasks is None):
            args.editable = args.editable or [
                p.strip() for p in input("Editable globs (comma separated): ").split(",")
            ]
            args.tasks = args.tasks or Path(input("Task JSON file: ").strip())
            if args.command is None and args.command_argv is None:
                args.command = (
                    input("Run/score command (blank creates unfinished runner.py): ").strip()
                    or None
                )
        if not args.editable or args.tasks is None:
            raise ValueError("Supply --editable and --tasks (or use an interactive terminal)")
        return initialize_scenario(
            args.directory,
            args.editable,
            args.tasks,
            args.command_argv if args.command_argv is not None else args.command,
            adapter=args.adapter,
            seed=args.seed,
        )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--run", action="store_true", help="Explicitly execute the trusted public scorer"
    )
    parser.add_argument(
        "--labels", type=Path, help="JSON map of public task ids to expected scores"
    )
    args = parser.parse_args(arguments[1:])
    return doctor(
        args.config,
        run=args.run,
        labels=json.loads(args.labels.read_text(encoding="utf-8-sig")) if args.labels else None,
    )
