"""The three-piece scenario contract and optional replay and command capabilities."""

from __future__ import annotations

import copy
import importlib.util
import inspect
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .config import validate_config
from .storage import atomic_json, digest, file_lock

_ADAPTER_LOCK = threading.RLock()


@dataclass(frozen=True)
class Task:
    """One stable task; payload is scenario-owned JSON, group is optional."""

    id: str
    payload: Any
    group: str = "default"


@dataclass
class Result:
    """A score in [0, 1], explanation, numeric cost units, and optional trace/output.

    `cost` keys are scenario-declared units (for example tokens or USD). `seconds`
    defaults to measured wall time when omitted. `roles` can break costs down.
    """

    outcome: float | bool
    reason: str
    cost: dict[str, float] = field(default_factory=dict)
    trace_path: str | None = None
    output: Any = None
    seconds: float | None = None
    roles: dict[str, dict[str, float]] = field(default_factory=dict)

    def validate(self) -> None:
        """Reject invalid or non-finite scorer data before persistence.

        Args:
            None.

        Returns:
            None: Validation succeeds in place.

        Raises:
            ValueError: For malformed score, costs, latency or reason.
        """
        if not isinstance(self.outcome, (int, float)) or not math.isfinite(self.outcome):
            raise ValueError("Outcome must be finite")
        if not 0 <= self.outcome <= 1 or not isinstance(self.reason, str):
            raise ValueError("Outcome must be in [0, 1] and reason must be a string")
        for costs in [self.cost, *self.roles.values()]:
            for value in costs.values():
                if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise ValueError("Costs must be finite and nonnegative")
        if self.seconds is not None and (not math.isfinite(self.seconds) or self.seconds < 0):
            raise ValueError("Seconds must be finite and nonnegative")
        if self.trace_path is not None and not isinstance(self.trace_path, str):
            raise ValueError("Trace path must be a string or null")
        digest(asdict(self))


class StageCache:
    """Persist JSON stage outputs, with one producer per key across worker processes.

    Args:
        root (Path): Epoch-local cache directory, separate for holdout.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def replay(self, stage: str, key: Any, compute: Callable[[], Any]) -> tuple[Any, bool]:
        """Replay a stage or compute it once.

        Args:
            stage (str): Scenario-defined stage name.
            key (Any): JSON dependencies, including code/config and task inputs.
            compute (Callable[[], Any]): Producer returning JSON data.

        Returns:
            tuple[Any, bool]: Stage output and whether it came from cache.
        """
        token = digest([stage, key])
        path = self.root / f"{token}.json"
        with file_lock(self.root / f"{token}.lock"):
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8")), True
            value = compute()
            atomic_json(path, value)
            return value, False


class Scenario(ABC):
    """Implement only editable, tasks and run_and_score.

    Scenarios are trusted executables and must be thread-safe, sandbox side effects,
    keep scoring independent of editable code, and declare all guard literals they
    can expose. The default scorer anchor is `1`; override when scoring changes.
    """

    editable: tuple[str, ...]
    scorer_version: str = "1"
    primer: str = ""
    literals: tuple[str, ...] = ()
    gold_strings: tuple[str, ...] = ()
    private_literals: tuple[str, ...] = ()
    calibration_labels: dict[str, float] = {}
    budgets: dict[str, Any] = {}
    execution_config: dict[str, Any] = {}
    noise_policy: dict[str, Any] = {"multiplier": 1.96, "observed_floor": True}

    def secondary_guards(self, parent: list[dict], child: list[dict], noise: list[dict]) -> dict:
        """Return aggregate-only scenario guards over paired results.

        Args:
            parent (list[dict]): Complete parent rows; may be operator-private.
            child (list[dict]): Complete child rows on the same tasks/repeats.
            noise (list[dict]): Repeated public incumbent selection rows.

        Returns:
            dict: Boolean `passed` and aggregate-only numeric `metrics`.
                Never include task identities, outputs, examples or traces.
        """
        return {"passed": True, "metrics": {}}

    @abstractmethod
    def tasks(self) -> Iterable[Task]:
        """Return stable JSON tasks.

        Args:
            None.

        Returns:
            Iterable[Task]: Tasks with unique nonempty ids and optional groups.
        """

    @abstractmethod
    def run_and_score(self, version_dir: Path, task: Task) -> Result:
        """Execute one task inside the supplied isolated checkout and score it.

        Args:
            version_dir (Path): Editable version plus frozen supporting files.
            task (Task): One task, with labels kept inside the trusted scorer.

        Returns:
            Result: Outcome, reason, costs and optional trace path.
        """

    def run_replay(self, version_dir: Path, task: Task, cache: StageCache, repeat: int) -> Result:
        """Optionally reuse stages; the default executes the three-piece contract.

        Args:
            version_dir (Path): Isolated checkout.
            task (Task): Task to execute.
            cache (StageCache): Epoch-local cache. Keys must include all stage inputs.
            repeat (int): Zero-based repeat for reproducible noise or cache keys.

        Returns:
            Result: Scored output with only newly incurred cost.
        """
        return self.run_and_score(version_dir, task)

    def render_failure(self, task: Task, row: dict[str, Any]) -> dict[str, Any]:
        """Render a generic, bounded failure card; scenarios may override it.

        Args:
            task (Task): Public diagnosis or selection task.
            row (dict[str, Any]): Result record with an archived trace pointer.

        Returns:
            dict[str, Any]: Payload summary, output, reason and excerpt pointer.
        """
        return {
            "task_id": task.id,
            "group": task.group,
            "payload_summary": json.dumps(task.payload, ensure_ascii=False)[:500],
            "output": row.get("output"),
            "outcome": row["outcome"],
            "reason": row["reason"],
            "trace_excerpt_pointer": row.get("trace_path"),
        }


class CommandScenario(Scenario):
    """Run a YAML-declared command using JSON files and EVOKIT_* environment paths.

    Args:
        root (Path): Config directory.
        config (dict[str, Any]): editable, tasks and command; other fields optional.
    """

    def __init__(self, root: Path, config: dict[str, Any]) -> None:
        validate_config(config)
        self.root = root
        self.config = copy.deepcopy(config)
        self.execution_config = self.config
        config = self.config
        self.editable = tuple(config["editable"])
        for name in (
            "scorer_version",
            "primer",
            "literals",
            "gold_strings",
            "private_literals",
            "budgets",
            "noise_policy",
        ):
            if name in config:
                setattr(self, name, config[name])

    def tasks(self) -> Iterable[Task]:
        """Read inline tasks or a config-relative JSON task array.

        Args:
            None.

        Returns:
            Iterable[Task]: Task records.
        """
        rows = self.config["tasks"]
        if isinstance(rows, str):
            rows = json.loads((self.root / rows).read_text(encoding="utf-8"))
        return [Task(**row) for row in rows]

    def run_and_score(self, version_dir: Path, task: Task) -> Result:
        """Run argv directly or an explicitly configured platform shell command.

        Args:
            version_dir (Path): Working directory of the command.
            task (Task): Written as JSON to EVOKIT_TASK_FILE.

        Returns:
            Result: Parsed EVOKIT_RESULT_FILE. Nonzero exits raise an error.
        """
        with tempfile.TemporaryDirectory(prefix="ek-") as temporary:
            directory = Path(temporary)
            task_file, result_file = directory / "task.json", directory / "result.json"
            atomic_json(task_file, asdict(task))
            environment = {
                **os.environ,
                "EVOKIT_TASK_FILE": str(task_file),
                "EVOKIT_RESULT_FILE": str(result_file),
                "EVOKIT_VERSION_DIR": str(version_dir),
            }
            command = self.config["command"]
            completed = subprocess.run(
                command,
                shell=isinstance(command, str),
                cwd=version_dir,
                env=environment,
                capture_output=True,
                text=True,
                timeout=self.config.get("timeout_seconds", 60),
                encoding="utf-8",
                errors="replace",
            )
            if completed.returncode:
                raise ValueError(
                    f"Command exited {completed.returncode}: {completed.stderr[-1000:]}"
                )
            result = Result(**json.loads(result_file.read_text(encoding="utf-8")))
            if result.trace_path:
                # A command may use its transient protocol directory for a trace.
                trace = Path(result.trace_path)
                trace = (trace if trace.is_absolute() else version_dir / trace).resolve()
                if not trace.is_file() or not (
                    trace.is_relative_to(version_dir.resolve())
                    or trace.is_relative_to(directory.resolve())
                ):
                    raise ValueError(
                        "Command trace must stay inside its checkout or protocol directory"
                    )
                target = version_dir / f"command-trace-{uuid.uuid4().hex}.txt"
                target.write_bytes(trace.read_bytes())
                result.trace_path = str(target)
            return result


def load_scenario(path: Path) -> Scenario:
    """Load a trusted Python adapter or YAML command adapter.

    Args:
        path (Path): YAML config; `adapter: ./adapter.py:Class` selects Python.

    Returns:
        Scenario: Adapter with optional configuration passed as `options` kwargs.

    Raises:
        ValueError: For invalid adapter paths or contract mismatches.
    """
    config = validate_config(yaml.safe_load(path.read_text(encoding="utf-8")))
    if "adapter" not in config:
        return CommandScenario(path.parent, config)
    module_path, class_name = config["adapter"].rsplit(":", 1)
    source = (path.parent / module_path).resolve()
    if not source.is_relative_to(path.parent.resolve()):
        raise ValueError("Adapter source must be inside the scenario root")
    module_name = "evokit_adapter_" + digest([str(source), source.read_text(encoding="utf-8")])[:24]
    spec = importlib.util.spec_from_file_location(module_name, source)
    assert spec is not None and spec.loader is not None
    with _ADAPTER_LOCK:
        module = sys.modules.get(spec.name)
        if module is None:
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            try:
                exec(compile(source.read_bytes(), str(source), "exec"), module.__dict__)
            except BaseException:
                if sys.modules.get(spec.name) is module:
                    sys.modules.pop(spec.name, None)
                raise
        adapter_class = getattr(module, class_name)
    try:
        inspect.signature(adapter_class).bind(**config.get("options", {}))
    except TypeError:
        raise ValueError("Unsupported Python adapter options; check its constructor") from None
    scenario = adapter_class(**config.get("options", {}))
    if not isinstance(scenario, Scenario):
        raise ValueError("Python adapter must inherit evokit.Scenario")
    return scenario
