"""Backend-neutral evolution lifecycle. CLI and MCP adapt this owning API."""

from __future__ import annotations

import copy
import difflib
import fnmatch
import hashlib
import importlib.resources
import inspect
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from .analysis import compare, estimate_noise, split_tasks
from .anchors import code_fingerprint, scorer_sources_match
from .config import validate_config
from .coordinator import Coordinator
from .guards import introduced_literal
from .privacy import PRIVATE_SPLITS, private_event
from .records import RecordStore, open_store
from .scenario import CommandScenario, Result, Scenario, StageCache, Task, load_scenario
from .storage import atomic_json, canonical, digest, file_lock, safe_name, safe_path

_IGNORED = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".evokit"}


class Workspace(Coordinator):
    """Own scenario snapshots, epoch anchors, runs and lineage under one directory.

    Args:
        root (Path): Scenario checkout, including immutable execution support.
        scenario (Scenario): Trusted three-piece adapter.
        state (Path | None): State root; defaults to `root/.evokit`.
        config (Path | None): YAML config needed only for detached CLI/MCP workers.
        playbook (Path | None): Optional level-1 skill source, snapshotted per proposal.
        storage (dict[str, Any] | None): Optional backend selection, fixed at initialization.
        store (RecordStore | None): Optional custom SDK store, mutually exclusive with storage.

    Use `initialize` once, then create versions and run arms. State is durable;
    reopening does not reset splits or lineage. Starting a new epoch requires a
    new state directory. Filesystem separation does not sandbox executable code.
    """

    def __init__(
        self,
        root: Path,
        scenario: Scenario,
        state: Path | None = None,
        config: Path | None = None,
        playbook: Path | None = None,
        storage: dict[str, Any] | None = None,
        store: RecordStore | None = None,
    ) -> None:
        self.root = root.resolve()
        self.scenario = scenario
        self.state = (state or self.root / ".evokit").resolve()
        if self.root.is_relative_to(self.state):
            raise ValueError("Workspace state must not contain the scenario checkout")
        self.config = config.resolve() if config else None
        self.playbook = playbook
        if store is not None and storage is not None:
            raise ValueError("Supply a RecordStore or storage settings, not both")
        self.store: RecordStore = store if store is not None else open_store(self.state, storage)
        self.ledger = self.store
        existing = self.store.get("epoch", "singleton", private=True)
        self._normalized_config = existing is None or existing.get("record_format") == 1

    def close(self) -> None:
        """Release record backend connections after this workspace is no longer used."""
        self.store.close()

    @classmethod
    def open(
        cls,
        config: str | Path,
        *,
        initialize: bool = True,
        state: Path | None = None,
        storage: dict[str, Any] | None = None,
    ) -> Workspace:
        """Load a YAML adapter and initialize its workspace if it is new.

        Args:
            config (str | Path): Scenario YAML path. Optional workspace is config-relative.
            initialize (bool): Freeze/check the epoch. False opens an empty target for import.
            state (Path | None): Operator state override; relative paths use the process cwd.
            storage (dict[str, Any] | None): Operator backend override; otherwise use YAML
                or the persisted selection. Existing authority cannot silently change.

        Returns:
            Workspace: Workspace preserving an existing epoch. Call close after use.
        """
        path = Path(config).resolve()
        data = validate_config(yaml.safe_load(path.read_text(encoding="utf-8")))
        state = state or path.parent / data.get("workspace", ".evokit")
        playbook = path.parent / data["playbook"] if "playbook" in data else None
        workspace = cls(
            path.parent,
            load_scenario(path),
            state,
            path,
            playbook,
            storage if storage is not None else data.get("storage"),
        )
        if initialize:
            try:
                workspace.initialize(
                    seed=data.get("seed", 0), stratified=data.get("stratified", True)
                )
            except BaseException:
                workspace.close()
                raise
        return workspace

    def _epoch(self) -> dict[str, Any]:
        epoch = self.store.get("epoch", "singleton", private=True)
        if epoch is None:
            raise ValueError("Workspace has no epoch")
        return epoch

    def _editable(self, name: str) -> bool:
        return any(fnmatch.fnmatchcase(name, pattern) for pattern in self.scenario.editable)

    def _scan(self, root: Path) -> dict[str, str]:
        files = {}
        names = set()
        epoch = self.store.get("epoch", "singleton", private=True)
        previous_state = epoch.get("state_path") if epoch else None
        excluded = root / safe_path(previous_state) if previous_state else None
        for directory, dirs, filenames in os.walk(root, followlinks=False):
            base = Path(directory)
            dirs[:] = sorted(
                name
                for name in dirs
                if name not in _IGNORED
                and (base / name).resolve() != self.state
                and (base / name) != excluded
            )
            for name in [*dirs, *filenames]:
                if (base / name).is_symlink() or getattr(
                    (base / name), "is_junction", lambda: False
                )():
                    raise ValueError(
                        "Symlinks and junctions are not accepted in scenario checkouts"
                    )
            for name in sorted(filenames):
                path = base / name
                relative = safe_path(path.relative_to(root).as_posix())
                if relative.casefold() in names:
                    raise ValueError("Case-colliding paths are not portable")
                names.add(relative.casefold())
                content = path.read_bytes()
                checksum = hashlib.sha256(content).hexdigest()
                blob = self.state / "blobs" / checksum
                if not blob.exists():
                    blob.parent.mkdir(parents=True, exist_ok=True)
                    temporary = blob.with_name(checksum + "." + uuid.uuid4().hex)
                    temporary.write_bytes(content)
                    os.replace(temporary, blob)
                files[relative] = checksum
        return files

    def _source_bytes(self, path: Path) -> bytes:
        if self._normalized_config and self.config and path.resolve() == self.config:
            return self._config_bytes(path.read_bytes())
        return path.read_bytes()

    @staticmethod
    def _config_bytes(content: bytes) -> bytes:
        config = yaml.safe_load(content)
        if not isinstance(config, dict):
            raise ValueError("Scenario config must be a mapping")
        return canonical({k: v for k, v in config.items() if k not in {"storage", "workspace"}})

    def _fixed_matches(self, files: dict[str, str]) -> bool:
        expected = self._epoch()["fixed"]
        if (
            not self._normalized_config
            or not self.config
            or not self.config.is_relative_to(self.root)
        ):
            return files == expected
        name = self.config.relative_to(self.root).as_posix()
        if name not in expected or name not in files:
            return files == expected
        if {k: v for k, v in files.items() if k != name} != {
            k: v for k, v in expected.items() if k != name
        }:
            return False
        return self._config_bytes(self._blob(files[name])) == self._config_bytes(
            self._blob(expected[name])
        )

    def _tasks_now(self) -> list[Task]:
        tasks = sorted(self.scenario.tasks(), key=lambda task: task.id)
        if not tasks or any(
            not isinstance(task.id, str) or not task.id or not isinstance(task.group, str)
            for task in tasks
        ):
            raise ValueError("Scenario requires nonempty tasks with string ids and groups")
        if len({task.id for task in tasks}) != len(tasks):
            raise ValueError("Task ids must be unique")
        canonical([asdict(task) for task in tasks])
        return tasks

    def _policy(self) -> dict[str, Any]:
        budget = self.scenario.budgets
        jsonschema.validate(
            self.scenario.noise_policy,
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "multiplier": {"type": "number", "minimum": 0},
                    "observed_floor": {"type": "boolean"},
                },
            },
        )
        jsonschema.validate(
            budget,
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "latency_ratio": {"type": "number", "minimum": 0},
                    "max_seconds": {"type": "number", "minimum": 0},
                    "cost_ratio": {
                        "type": "object",
                        "additionalProperties": {"type": "number", "minimum": 0},
                    },
                    "max_cost": {
                        "type": "object",
                        "additionalProperties": {"type": "number", "minimum": 0},
                    },
                },
            },
        )
        canonical(budget)
        execution = self.scenario.execution_config
        if self._normalized_config and isinstance(self.scenario, CommandScenario):
            execution = {k: v for k, v in execution.items() if k not in {"storage", "workspace"}}
        return {
            "editable": list(self.scenario.editable),
            "scorer_version": self.scenario.scorer_version,
            "literals": list(self.scenario.literals),
            "gold_strings": list(self.scenario.gold_strings),
            "private_literals": list(self.scenario.private_literals),
            "execution_config": execution,
            "budgets": self.scenario.budgets,
            "noise_policy": self.scenario.noise_policy,
        }

    def _scorer_sources(self) -> dict[str, str]:
        sources = set()
        epoch = self.store.get("epoch", "singleton", private=True)
        guarded = epoch is None or "callable:secondary_guards" in epoch["scorer_sources"]
        callback = self.scenario.secondary_guards
        if not guarded and getattr(callback, "__func__", callback) is not Scenario.secondary_guards:
            raise ValueError("Secondary guards require a new epoch with callback anchoring")
        for owner in (
            type(self.scenario),
            self.scenario.run_and_score,
            self.scenario.run_replay,
            *((self.scenario.secondary_guards,) if guarded else ()),
        ):
            try:
                source = inspect.getsourcefile(owner)
            except TypeError:
                source = None
            if source and Path(source).is_file():
                sources.add(Path(source).resolve())
        if self.config:
            sources.add(self.config)
        if not sources:
            raise ValueError(
                "Cannot locate scorer source; define the adapter in a Python source file"
            )
        result = {}
        for source in sorted(sources):
            if source.is_relative_to(self.root) and self._editable(
                source.relative_to(self.root).as_posix()
            ):
                raise ValueError(
                    "Scorer adapter and scenario config cannot be editable; separate system artifacts"
                )
            key = (
                "source:" + source.relative_to(self.root).as_posix()
                if self._normalized_config and source.is_relative_to(self.root)
                else str(source)
            )
            result[key] = hashlib.sha256(self._source_bytes(source)).hexdigest()
        methods = ("run_and_score", "run_replay") + (("secondary_guards",) if guarded else ())
        for name in methods:
            method = getattr(self.scenario, name)
            function = getattr(method, "__func__", method)
            code = getattr(function, "__code__", None)
            if code is None:
                raise ValueError("Scorer methods must expose Python code for epoch anchoring")
            result[f"callable:{name}"] = code_fingerprint(code)
        return result

    def _check_anchors(self) -> None:
        epoch = self._epoch()
        record = next(
            (event["data"] for event in self.ledger.history() if event["kind"] == "epoch"), None
        )
        if record is None or record.get("anchor_hash") != digest(epoch):
            raise ValueError("Frozen epoch integrity failure")
        from .anchors import compatible_v012_sources

        if not scorer_sources_match(
            compatible_v012_sources(epoch["scorer_sources"]),
            self._scorer_sources(),
            owned_source=str(Path(__file__).with_name("scenario.py").resolve()),
        ):
            raise ValueError("Frozen scorer implementation changed; start a new epoch")
        if digest([asdict(task) for task in self._tasks_now()]) != epoch["task_hash"]:
            raise ValueError("Frozen task source changed; start a new epoch")
        expected_policy = {
            "noise_policy": {"multiplier": 1.96, "observed_floor": True},
            **epoch["policy"],
        }
        if self._policy() != expected_policy:
            raise ValueError("Frozen scorer, edit scope or gate policy changed; start a new epoch")
        files = self._scan(self.root)
        fixed = {name: sha for name, sha in files.items() if not self._editable(name)}
        if not self._fixed_matches(fixed):
            raise ValueError("Frozen supporting files changed; start a new epoch")
        expected = split_tasks(
            [Task(**row) for row in epoch["tasks"]],
            epoch["seed"],
            stratified=epoch["stratified"],
            fractions=tuple(epoch.get("fractions", (0.4, 0.4, 0.2, 0))),
            legacy="holdout" in epoch["splits"],
            assignments=epoch.get("split_assignments"),
        )
        if epoch["splits"] != expected or digest(epoch["splits"]) != epoch["split_hash"]:
            raise ValueError("Frozen split integrity failure")

    def initialize(self, seed: int = 0, stratified: bool = True) -> dict[str, Any]:
        """Freeze task/scorer anchors and take the initial artifact snapshot.

        Args:
            seed (int): Split seed, used only for a new epoch.
            stratified (bool): Allocate each group across splits when true.

        Returns:
            dict[str, Any]: Public description containing holdout counts only.

        Raises:
            ValueError: If editable scope or tasks are invalid.
        """
        with file_lock(self.state / "workspace.lock"):
            if self.store.get("epoch", "singleton", private=True) is None:
                patterns = getattr(self.scenario, "editable", ())
                if not patterns or any(
                    not isinstance(p, str)
                    or p.startswith(("/", "\\"))
                    or ".." in p.split("/")
                    or ":" in p
                    or "\\" in p
                    for p in patterns
                ):
                    raise ValueError("editable must contain safe relative path globs")
                tasks = self._tasks_now()
                files = self._scan(self.root)
                editable = {name: sha for name, sha in files.items() if self._editable(name)}
                if not editable:
                    raise ValueError("No editable artifacts match; check editable globs")
                fractions = self._split_fractions()
                configuration = (
                    yaml.safe_load(self.config.read_text(encoding="utf-8")) if self.config else {}
                )
                assignments = configuration.get("split_assignments")
                splits = split_tasks(
                    tasks, seed, stratified=stratified, fractions=fractions, assignments=assignments
                )
                epoch = {
                    "state_path": self.state.relative_to(self.root).as_posix()
                    if self.state.is_relative_to(self.root)
                    else None,
                    "record_format": 1,
                    "fractions": fractions,
                    "id": uuid.uuid4().hex,
                    "seed": seed,
                    "stratified": stratified,
                    "tasks": [asdict(task) for task in tasks],
                    "task_hash": digest([asdict(task) for task in tasks]),
                    "splits": splits,
                    "split_assignments": assignments,
                    "split_hash": digest(splits),
                    "policy": self._policy(),
                    "scorer_sources": self._scorer_sources(),
                    "fixed": {name: sha for name, sha in files.items() if name not in editable},
                }
                version = self._save_version(editable)
                epoch["initial"] = version
                self.store.put("epoch", "singleton", epoch, private=True)
                self.ledger.append(
                    "epoch",
                    {
                        "epoch": epoch["id"],
                        "base": version,
                        "anchor_hash": digest(epoch),
                        "counts": {k: len(v) for k, v in splits.items()},
                    },
                )
                self.store.put("current", "singleton", {"version": version})
        self._check_anchors()
        return self.describe()

    def describe(self) -> dict[str, Any]:
        """Describe onboarding readiness and public capabilities.

        Args:
            None.

        Returns:
            dict[str, Any]: Editable globs, anchors, budgets and split counts; no holdout ids.
        """
        epoch = self._epoch()
        return {
            "backend": self.store.backend,
            "epoch": epoch["id"],
            "editable": list(self.scenario.editable),
            "scorer_version": epoch["policy"]["scorer_version"],
            "counts": self._split_counts(),
            "primer": self.scenario.primer,
            "budgets": self.scenario.budgets,
            "replay": type(self.scenario).run_replay is not Scenario.run_replay,
            "readiness": {
                "tasks": bool(epoch["tasks"]),
                "selection_nonempty": bool(epoch["splits"]["selection"]),
                "confirmation_nonempty": bool(self._split_ids("confirmation")),
                "final_nonempty": bool(self._split_ids("final")),
            },
        }

    def current(self) -> str:
        """Read the current pointer from authoritative append-only lineage.

        Args:
            None.

        Returns:
            str: Last adopted version, or the initial base.
        """
        events = self.ledger.history()
        for event in reversed(events):
            if event["kind"] in {"adoption", "revert"}:
                return event["data"]["version"]
        return self._epoch()["initial"]

    def _save_version(self, files: dict[str, str]) -> str:
        manifest = {"files": files}
        version = digest(manifest)
        path = self.state / "versions" / f"{version}.json"
        if not path.exists():
            atomic_json(path, manifest)
        self.store.put("version", version, {"version": version})
        return version

    def _manifest(self, version: str) -> dict[str, str]:
        if len(version) != 64 or any(char not in "0123456789abcdef" for char in version):
            raise ValueError("Version must be a SHA-256 identifier")
        manifest = json.loads((self.state / "versions" / f"{version}.json").read_text())
        if digest(manifest) != version:
            raise ValueError("Version manifest integrity failure")
        for name in manifest["files"]:
            safe_path(name)
            if not self._editable(name):
                raise ValueError("Version contains a noneditable artifact")
        return manifest["files"]

    def _blob(self, checksum: str) -> bytes:
        if len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
            raise ValueError("Invalid content digest")
        content = (self.state / "blobs" / checksum).read_bytes()
        if hashlib.sha256(content).hexdigest() != checksum:
            raise ValueError("Artifact content integrity failure")
        return content

    def materialize(self, version: str, destination: Path) -> Path:
        """Write one version and frozen support into a new isolated checkout.

        Args:
            version (str): Content address.
            destination (Path): Must not exist, preventing accidental overwrites.

        Returns:
            Path: Checkout directory.
        """
        files = {**self._epoch()["fixed"], **self._manifest(version)}
        destination.mkdir(parents=True, exist_ok=False)
        for name, checksum in files.items():
            path = destination / safe_path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self._blob(checksum))
        return destination

    def create_version(
        self,
        directory: str | Path | None = None,
        patch: str | None = None,
        parent: str | None = None,
    ) -> dict[str, Any]:
        """Snapshot a directory or apply a unified patch to an isolated parent.

        Args:
            directory (str | Path | None): Complete scenario checkout; defaults to root.
            patch (str | None): Git-compatible text patch, mutually exclusive with directory.
            parent (str | None): Parent for patching and lineage; defaults to current.

        Returns:
            dict[str, Any]: Version, parent and changed editable paths.

        Raises:
            ValueError: For an out-of-scope change, unsafe path or invalid patch.
        """
        self._check_anchors()
        self._ensure_evolving()
        parent = parent or self.current()
        self._manifest(parent)
        if patch is not None:
            if directory is not None:
                raise ValueError("Use directory or patch, not both")
            with tempfile.TemporaryDirectory(prefix="evokit-patch-") as temporary:
                candidate = self.materialize(parent, Path(temporary) / "v")
                patch_file = Path(temporary) / "change.patch"
                patch_file.write_text(patch, encoding="utf-8", newline="\n")
                completed = subprocess.run(
                    ["git", "apply", "--no-index", str(patch_file)],
                    cwd=candidate,
                    stdin=subprocess.DEVNULL,
                    env={**os.environ, "GIT_CEILING_DIRECTORIES": str(candidate.parent)},
                    capture_output=True,
                    text=True,
                )
                if completed.returncode:
                    raise ValueError(f"Patch rejected: {completed.stderr.strip()}")
                return self.create_version(candidate, parent=parent)
        files = self._scan(Path(directory or self.root).resolve())
        fixed = {name: sha for name, sha in files.items() if not self._editable(name)}
        if not self._fixed_matches(fixed):
            raise ValueError("Changes outside editable globs are forbidden")
        version = self._save_version(
            {name: sha for name, sha in files.items() if self._editable(name)}
        )
        changed = self._changed(parent, version)
        self.ledger.append("version", {"version": version, "parent": parent, "changed": changed})
        return {"version": version, "parent": parent, "changed": changed}

    def _changed(self, parent: str, child: str) -> list[str]:
        left, right = self._manifest(parent), self._manifest(child)
        return sorted(
            name for name in left.keys() | right.keys() if left.get(name) != right.get(name)
        )

    def diff(self, parent: str, child: str) -> dict[str, Any]:
        """Render an editable artifact diff after verifying snapshot content.

        Args:
            parent (str): Before version.
            child (str): After version.

        Returns:
            dict[str, Any]: Changed paths and unified text (binary files identified).
        """
        left, right = self._manifest(parent), self._manifest(child)
        chunks = []
        for name in self._changed(parent, child):
            before = self._blob(left[name]) if name in left else b""
            after = self._blob(right[name]) if name in right else b""
            try:
                chunks.extend(
                    difflib.unified_diff(
                        before.decode().splitlines(keepends=True),
                        after.decode().splitlines(keepends=True),
                        f"a/{name}",
                        f"b/{name}",
                    )
                )
            except UnicodeDecodeError:
                chunks.append(f"Binary artifact changed: {name}\n")
        return {
            "parent": parent,
            "child": child,
            "changed": self._changed(parent, child),
            "diff": "".join(chunks),
        }

    def _run_path(self, run_id: str) -> Path:
        private = self.store.get("run", safe_name(run_id), private=True) is not None
        root = self.state / "operator" if private and self.store.backend == "hb" else self.state
        return root / "runs" / safe_name(run_id)

    def _run_meta(self, run_id: str, *, holdout: bool = False) -> dict[str, Any]:
        safe_name(run_id)
        meta = self.store.get("run", run_id) or self.store.get("run", run_id, private=True)
        if meta is None:
            raise ValueError("Unknown run")
        if meta["split"] in PRIVATE_SPLITS and not holdout:
            raise ValueError("Holdout is measurement-only; details are not available to improvers")
        return meta

    def launch(
        self,
        version: str,
        split: str = "selection",
        repeats: int = 1,
        workers: int = 1,
        smoke: bool = False,
        run_id: str | None = None,
        wait: bool = False,
    ) -> dict[str, Any]:
        """Launch or resume a diagnosis/selection arm, pinning all execution inputs.

        Args:
            version (str): Artifact content address.
            split (str): `diagnosis` or `selection`; holdout is never allowed here.
            repeats (int): Positive repeat count.
            workers (int): Parallel task workers, between 1 and 256.
            smoke (bool): Restrict the split to its first two stable tasks.
            run_id (str | None): Existing matching arm to resume, or a new identifier.
            wait (bool): Complete in this process. False spawns a detached worker.

        Returns:
            dict[str, Any]: Public run status.

        Raises:
            ValueError: For mismatched resume parameters or holdout access.
        """
        if split not in {"diagnosis", "selection"}:
            raise ValueError("Only diagnosis and selection are improver-facing")
        if not wait and self.config is None:
            raise ValueError(
                "Detached runs require a YAML config; use wait=True for Python adapters"
            )
        run_id = self._prepare_run(version, split, repeats, workers, smoke, run_id)
        if wait:
            self.execute(run_id)
        else:
            path = self._run_path(run_id)
            with (path / "worker.log").open("ab") as stream:
                options: dict[str, Any] = (
                    {"start_new_session": True}
                    if os.name != "nt"
                    else {
                        "creationflags": subprocess.DETACHED_PROCESS
                        | subprocess.CREATE_NEW_PROCESS_GROUP
                    }
                )
                subprocess.Popen(
                    [sys.executable, "-m", "evokit", "_run", str(self.config), run_id],
                    stdin=subprocess.DEVNULL,
                    stdout=stream,
                    stderr=stream,
                    **options,
                )
        return self.status(run_id)

    def interleaved(
        self,
        arms: dict[str, str],
        split: str = "selection",
        repeats: int = 1,
        workers: int = 4,
        seed: int = 0,
    ) -> dict[str, Any]:
        """Complete matched public arms in rotating task/repeat blocks.

        All arms for one task/repeat share a worker pool and finish before the
        next block starts. Completed slots survive interruption; resume with the
        exact same arm names, versions and parameters. Holdout is unavailable.
        """
        if split not in {"diagnosis", "selection"} or len(arms) < 2:
            raise ValueError("Interleaving requires at least two public arms")
        if type(seed) is not int:
            raise ValueError("seed must be an integer")
        if (
            type(repeats) is not int
            or repeats < 1
            or type(workers) is not int
            or not 1 <= workers <= 256
        ):
            raise ValueError("repeats must be positive and workers must be in [1, 256]")
        names = sorted(safe_name(name) for name in arms)
        self._check_anchors()
        for version in arms.values():
            self._manifest(version)
        plan = {"arms": arms, "split": split, "repeats": repeats, "workers": workers, "seed": seed}
        with file_lock(self.state / "workspace.lock"):
            plans = [e["data"] for e in self.ledger.history() if e["kind"] == "interleaved"]
            overlapping = [p for p in plans if set(p["arms"]) & set(arms)]
            if overlapping and any(p != plan for p in overlapping):
                raise ValueError("Resume must preserve the original interleaving plan")
            if not overlapping:
                if any(
                    self.store.get("run", name) or self.store.get("run", name, private=True)
                    for name in names
                ):
                    raise ValueError("New interleaving plans require fresh run identifiers")
                self.ledger.append("interleaved", plan)
        for name in names:
            self._prepare_run(arms[name], split, repeats, workers, False, name)
        with ExitStack() as locks:
            for name in names:
                locks.enter_context(file_lock(self._run_path(name) / "execute.lock", timeout=0))
            self._check_anchors()
            metas = {name: self._run_meta(name) for name in names}
            done = {}
            for name in names:
                self.store.recover_results(name)
                done[name] = {(r["task_id"], r["repeat"]) for r in self._rows(name)}
                self.store.put("status", name, {"state": "running"})
            tasks = {task["id"]: Task(**task) for task in self._epoch()["tasks"]}
            try:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    block = seed
                    for task_id in metas[names[0]]["task_ids"]:
                        for repeat in range(repeats):
                            offset = block % len(names)
                            order = names[offset:] + names[:offset]
                            block += 1
                            futures = {
                                pool.submit(
                                    self._execute_task,
                                    self._run_path(name),
                                    metas[name],
                                    tasks[task_id],
                                    repeat,
                                ): name
                                for name in order
                                if (task_id, repeat) not in done[name]
                            }
                            for future in as_completed(futures):
                                self.store.add_result(futures[future], future.result())
                self._check_anchors()
                for name in names:
                    self.store.put("status", name, {"state": "completed"})
            except BaseException:
                for name in names:
                    self.store.put("status", name, {"state": "interrupted"})
                raise
        return {name: self.status(name) for name in names}

    def _prepare_run(
        self, version: str, split: str, repeats: int, workers: int, smoke: bool, run_id: str | None
    ) -> str:
        self._check_anchors()
        self._manifest(version)
        if (
            type(repeats) is not int
            or repeats < 1
            or type(workers) is not int
            or not 1 <= workers <= 256
        ):
            raise ValueError("repeats must be positive and workers must be in [1, 256]")
        epoch = self._epoch()
        ids = self._split_ids(split)[:2] if smoke else self._split_ids(split)
        if not ids:
            raise ValueError("The selected split is empty; supply more tasks")
        run_id = safe_name(run_id or uuid.uuid4().hex[:12])
        spec = {
            "version": version,
            "split": split,
            "repeats": repeats,
            "workers": workers,
            "smoke": smoke,
            "task_ids": ids,
            "epoch": epoch["id"],
            "scorer_version": epoch["policy"]["scorer_version"],
        }
        with file_lock(self.state / "workspace.lock"):
            if split not in PRIVATE_SPLITS:
                self._ensure_evolving()
            if self.store.get("run", run_id) or self.store.get("run", run_id, private=True):
                if self._run_meta(run_id, holdout=True) != spec:
                    raise ValueError("Resume must preserve the original arm parameters")
            else:
                self.store.put("run", run_id, spec, private=split in PRIVATE_SPLITS)
                self._run_path(run_id).mkdir(parents=True, exist_ok=True)
                self.ledger.append(
                    "run",
                    {
                        "run_id": run_id,
                        **{key: value for key, value in spec.items() if key != "task_ids"},
                    },
                )
        return run_id

    def _rows(self, run_id: str) -> list[dict[str, Any]]:
        meta = self._run_meta(run_id, holdout=True)
        return self.store.results(run_id, private=meta["split"] in PRIVATE_SPLITS)

    def execute(self, run_id: str) -> None:
        """Execute missing task/repeat slots only, under an exclusive run lock.

        Args:
            run_id (str): Prepared arm. Interrupted executions resume the same id.

        Returns:
            None: Results are committed per task to results.jsonl.
        """
        path = self._run_path(run_id)
        with file_lock(path / "execute.lock", timeout=0):
            self._check_anchors()
            meta = self._run_meta(run_id, holdout=True)
            if meta["split"] not in PRIVATE_SPLITS:
                self._ensure_evolving()
            rows = self._rows(run_id)
            private = meta["split"] in PRIVATE_SPLITS
            self.store.recover_results(run_id, private=private)
            done = {(row["task_id"], row["repeat"]) for row in rows}
            tasks = {task["id"]: Task(**task) for task in self._epoch()["tasks"]}
            slots = [
                (tasks[task_id], repeat)
                for task_id in meta["task_ids"]
                for repeat in range(meta["repeats"])
                if (task_id, repeat) not in done
            ]
            self.store.put("status", run_id, {"state": "running"}, private=private)
            try:
                with ThreadPoolExecutor(max_workers=meta["workers"]) as pool:
                    futures = [
                        pool.submit(self._execute_task, path, meta, task, repeat)
                        for task, repeat in slots
                    ]
                    for future in as_completed(futures):
                        self.store.add_result(run_id, future.result(), private=private)
                self._check_anchors()
                self.store.put("status", run_id, {"state": "completed"}, private=private)
            except BaseException:
                self.store.put("status", run_id, {"state": "interrupted"}, private=private)
                raise

    def _execute_task(
        self, path: Path, meta: dict[str, Any], task: Task, repeat: int
    ) -> dict[str, Any]:
        token = digest([task.id, repeat])[:16]
        directory = path / "arms" / token
        if directory.exists():
            shutil.rmtree(directory)
        self.materialize(meta["version"], directory)
        cache_root = self.state
        if meta["split"] in PRIVATE_SPLITS and self.store.backend == "hb":
            cache_root /= "operator"
        cache = StageCache(
            cache_root / "cache" / (meta["split"] if meta["split"] in PRIVATE_SPLITS else "public")
        )
        start = time.monotonic()
        error = False
        try:
            result = self.scenario.run_replay(directory, task, cache, repeat)
            result.validate()
        except Exception as exc:
            result = Result(False, f"Execution error: {type(exc).__name__}: {str(exc)[:1000]}")
            error = True
        row = asdict(result)
        row["outcome"] = float(result.outcome)
        row["seconds"] = result.seconds if result.seconds is not None else time.monotonic() - start
        if result.trace_path:
            trace = Path(result.trace_path)
            trace = trace if trace.is_absolute() else directory / trace
            if not trace.resolve().is_relative_to(directory.resolve()) or not trace.is_file():
                row["trace_path"] = None
                row["error"] = True
                row["reason"] = "Trace must be a file inside the isolated checkout"
                row["outcome"] = 0.0
            else:
                archived = path / "traces" / f"{token}.txt"
                archived.parent.mkdir(parents=True, exist_ok=True)
                archived.write_bytes(trace.read_bytes())
                row["trace_path"] = str(archived)
        row.update(
            {
                "task_id": task.id,
                "group": task.group,
                "repeat": repeat,
                "scorer_version": meta["scorer_version"],
                "version": meta["version"],
                "epoch": meta["epoch"],
                "error": error or row.get("error", False),
            }
        )
        return row

    def status(self, run_id: str) -> dict[str, Any]:
        """Get arm progress without exposing holdout details.

        Args:
            run_id (str): Public arm id.

        Returns:
            dict[str, Any]: State, expected and completed result counts.
        """
        meta = self._run_meta(run_id)
        state = (self.store.get("status", run_id) or {"state": "pending"})["state"]
        rows = self._rows(run_id)
        return {
            "run_id": run_id,
            "version": meta["version"],
            "split": meta["split"],
            "state": state,
            "completed": len(rows),
            "expected": len(meta["task_ids"]) * meta["repeats"],
            "errors": sum(row["error"] for row in rows),
        }

    def results(
        self, run_id: str, offset: int = 0, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Read committed public results, including outcome, cost and trace pointers.

        Args:
            run_id (str): Diagnosis/selection arm.
            offset (int): Zero-based first result, in stable storage order.
            limit (int | None): Maximum returned results; None returns the rest.

        Returns:
            list[dict[str, Any]]: Completed result rows; may be partial while running.
        """
        self._run_meta(run_id)
        if offset < 0 or (limit is not None and limit < 1):
            raise ValueError("Pagination requires offset >= 0 and limit >= 1")
        return self._rows(run_id)[offset : None if limit is None else offset + limit]

    def _complete(self, run_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        meta = self._run_meta(run_id)
        rows = self.results(run_id)
        expected = {
            (task_id, repeat) for task_id in meta["task_ids"] for repeat in range(meta["repeats"])
        }
        if {(row["task_id"], row["repeat"]) for row in rows} != expected:
            raise ValueError("Arm is incomplete")
        if any(row["error"] for row in rows):
            raise ValueError("Arm contains execution errors")
        if any(
            row["version"] != meta["version"]
            or row["epoch"] != meta["epoch"]
            or row["scorer_version"] != meta["scorer_version"]
            for row in rows
        ):
            raise ValueError("Run result anchors do not match arm")
        return meta, rows

    def paired(self, parent_run: str, child_run: str, aggregation: str) -> dict[str, Any]:
        """Compare complete arms with matching task/split/epoch anchors.

        Args:
            parent_run (str): Incumbent arm id.
            child_run (str): Candidate arm id.
            aggregation (str): `mean` or `majority`, explicitly chosen.

        Returns:
            dict[str, Any]: Paired score, group, cost and latency tables.
        """
        left, before = self._complete(parent_run)
        right, after = self._complete(child_run)
        if any(left[key] != right[key] for key in ("split", "epoch", "scorer_version", "smoke")):
            raise ValueError("Comparison requires matching split and anchors")
        return compare(before, after, aggregation)

    def noise(self, run_id: str) -> dict[str, Any]:
        """Record a reproducible threshold suggestion from repeated incumbent runs.

        Args:
            run_id (str): Complete public incumbent arm with at least two repeats.

        Returns:
            dict[str, Any]: Noise estimate and provenance id for gate_check.
        """
        meta, rows = self._complete(run_id)
        if meta["version"] != self.current() or meta["smoke"] or meta["split"] != "selection":
            raise ValueError("Noise must measure the current base on the full selection split")
        estimate = {
            **estimate_noise(rows, **self.scenario.noise_policy),
            "run_id": run_id,
            "version": meta["version"],
            "epoch": meta["epoch"],
        }
        noise_id = digest(estimate)[:16]
        self.ledger.append("noise", {"noise_id": noise_id, **estimate})
        return {"noise_id": noise_id, **estimate}

    def propose(self, version: str, proposal: dict[str, Any]) -> dict[str, Any]:
        """Validate and record a falsifiable proposal and its exact playbook version.

        Args:
            version (str): Candidate snapshot, created before proposing.
            proposal (dict[str, Any]): Five fields specified by proposal.schema.json.

        Returns:
            dict[str, Any]: Proposal with id, parent, version and playbook digest.
        """
        self._check_anchors()
        self._ensure_evolving()
        schema = json.loads(Path(__file__).with_name("proposal.schema.json").read_text())
        jsonschema.validate(proposal, schema)
        canonical(proposal)
        if not math.isfinite(proposal["predicted_effect"]):
            raise ValueError("Predicted effect must be finite net score-sum units")
        parent = self.current()
        changes = self._changed(parent, version)
        if not changes or not set(changes) <= set(proposal["targets"]):
            raise ValueError("Proposal targets must cover every changed artifact")
        if any(not self._editable(safe_path(target)) for target in proposal["targets"]):
            raise ValueError("Proposal targets must be editable artifact paths")
        prior = proposal["prior"]["attempt"]
        if prior is not None and prior not in {p["id"] for p in self.proposals()}:
            raise ValueError("Prior must name an earlier proposal, or null")
        if self.playbook:
            content = self.playbook.read_bytes()
        else:
            bundled = Path(__file__).resolve().parents[2] / "skills" / "evolve" / "SKILL.md"
            content = (
                bundled.read_bytes()
                if bundled.exists()
                else importlib.resources.files("evokit").joinpath("data/evolve.md").read_bytes()
            )
        playbook_version = hashlib.sha256(content).hexdigest()
        playbook_path = self.state / "playbooks" / f"{playbook_version}.md"
        playbook_path.parent.mkdir(parents=True, exist_ok=True)
        if not playbook_path.exists():
            playbook_path.write_bytes(content)
        item = {
            **proposal,
            "id": uuid.uuid4().hex[:16],
            "version": version,
            "parent": parent,
            "playbook_version": playbook_version,
        }
        self.ledger.append("proposal", item)
        return item

    def proposals(self) -> list[dict[str, Any]]:
        """Read all proposals, including unsuccessful ones.

        Args:
            None.

        Returns:
            list[dict[str, Any]]: Proposal event payloads in creation order.
        """
        failed = {
            e["data"]["proposal_id"]
            for e in self.ledger.history()
            if e["kind"] == "confirmation" and not e["data"]["confirmed"]
        }
        return [
            {**event["data"], "not_confirmed": event["data"]["id"] in failed}
            for event in self.ledger.history()
            if event["kind"] == "proposal"
        ]

    def cards(
        self,
        run_id: str,
        group: str | None = None,
        parent_run: str | None = None,
        change: str | None = None,
        aggregation: str = "mean",
        offset: int = 0,
        limit: int | None = None,
        details: bool = True,
    ) -> list[dict[str, Any]]:
        """Render the full failure index, or fixes/breaks against a parent.

        Args:
            run_id (str): Public arm id.
            group (str | None): Optional exact group filter.
            parent_run (str | None): Paired reference for change filtering.
            change (str | None): `fix` or `break`, requiring parent_run; defaults to failures.
            aggregation (str): `mean` or `majority` for parent comparison.
            offset (int): First failure card in stable result order.
            limit (int | None): Maximum cards to return; None returns the rest.
            details (bool): Include task outputs; false returns a compact index.

        Returns:
            list[dict[str, Any]]: Generic or scenario-rendered failure cards.
        """
        if change not in {None, "fix", "break"} or (change and not parent_run):
            raise ValueError("fix/break filters require parent_run")
        rows = self.results(run_id)
        ids = None
        if parent_run:
            comparison = self.paired(parent_run, run_id, aggregation)
            ids = set(comparison["fixes" if change == "fix" else "breaks"])
        tasks = {task["id"]: Task(**task) for task in self._epoch()["tasks"]}
        cards = [
            self.scenario.render_failure(tasks[row["task_id"]], row)
            for row in rows
            if (group is None or row["group"] == group)
            and (row["task_id"] in ids if ids is not None else row["outcome"] < 1)
        ]
        for row, card in zip(
            [
                row
                for row in rows
                if (group is None or row["group"] == group)
                and (row["task_id"] in ids if ids is not None else row["outcome"] < 1)
            ],
            cards,
        ):
            key = digest([run_id, row["task_id"], row["repeat"]])
            self.store.put(
                "failure", key, {"run_id": run_id, "version": row["version"], "card": card}
            )
        if offset < 0 or (limit is not None and limit < 1):
            raise ValueError("Pagination requires offset >= 0 and limit >= 1")
        page = cards[offset : None if limit is None else offset + limit]
        return (
            page
            if details
            else [
                {key: card[key] for key in ("task_id", "group", "outcome", "reason") if key in card}
                for card in page
            ]
        )

    def records_query(self, **filters: Any) -> list[dict[str, Any]]:
        """Query public HB records; see docs/tools.md for structured filters."""
        if self.store.backend != "hb":
            raise ValueError("records_query requires the hb backend")
        return self.store.query_records(**filters)

    def failures_similar(self, text: str, limit: int = 10) -> dict[str, Any]:
        """Search public HB failure cards using configured embeddings or token overlap."""
        if self.store.backend != "hb":
            raise ValueError("failures_similar requires the hb backend")
        for run_id, _ in self.store.items("run"):
            self.cards(run_id)
        return self.store.similar(text, limit)

    def gate(
        self,
        proposal_id: str,
        parent_run: str,
        child_run: str,
        noise_id: str,
        aggregation: str,
        rationale: str = "",
        generic: bool = False,
    ) -> dict[str, Any]:
        """Apply hard constraints and a measured noise threshold, recording the verdict.

        Args:
            proposal_id (str): Recorded proposal under evaluation.
            parent_run (str): Full selection arm of its parent.
            child_run (str): Full selection arm of its candidate.
            noise_id (str): Noise estimate from this current base and epoch.
            aggregation (str): Explicit `mean` or `majority` policy.
            rationale (str): Required explanation for adopt-no-harm.
            generic (bool): Human/improver attestation that the mechanism generalizes.

        Returns:
            dict[str, Any]: promote, adopt-no-harm or reject, with reasons and evidence.
        """
        proposal = next((item for item in self.proposals() if item["id"] == proposal_id), None)
        if proposal is None:
            raise ValueError("Unknown proposal")
        reasons = []
        comparison = None
        secondary = None
        threshold = None
        try:
            self._check_anchors()
            if proposal["parent"] != self.current():
                raise ValueError("Proposal parent is no longer the current base; rebase runner-up")
            delta = self.diff(proposal["parent"], proposal["version"])
            if not delta["changed"] or any(not self._editable(name) for name in delta["changed"]):
                raise ValueError("Gate requires a nonempty editable-only diff")
            # Public guard outcomes must never depend on secret holdout membership.
            epoch = self._epoch()
            literals = list(self.scenario.literals) + list(self.scenario.gold_strings)
            public_ids = epoch["splits"]["diagnosis"] + epoch["splits"]["selection"]
            left, right = self._manifest(proposal["parent"]), self._manifest(proposal["version"])
            if introduced_literal(
                {name: self._blob(sha) for name, sha in left.items()},
                {name: self._blob(sha) for name, sha in right.items()},
                public_ids,
                literals,
            ):
                raise ValueError("Instance-literal scan failed (matched values are withheld)")
            before, parent_rows = self._complete(parent_run)
            after, child_rows = self._complete(child_run)
            if before["version"] != proposal["parent"] or after["version"] != proposal["version"]:
                raise ValueError("Arms do not evaluate the proposal parent and candidate")
            if any(meta["split"] != "selection" or meta["smoke"] for meta in [before, after]):
                raise ValueError("Gate requires full selection arms; smoke/diagnosis cannot decide")
            comparison = self.paired(parent_run, child_run, aggregation)
            noises = [event["data"] for event in self.ledger.history() if event["kind"] == "noise"]
            noise = next((item for item in noises if item["noise_id"] == noise_id), None)
            if (
                noise is None
                or noise["version"] != proposal["parent"]
                or noise["epoch"] != before["epoch"]
            ):
                raise ValueError("Noise estimate must pin this parent and epoch")
            # Recompute provenance rather than trusting an arbitrary threshold supplied by a caller.
            _, noise_rows = self._complete(noise["run_id"])
            threshold = estimate_noise(noise_rows, **self.scenario.noise_policy)["threshold"]
            if aggregation == "mean":
                threshold /= math.sqrt(after["repeats"])
            secondary = self._secondary_guards(parent_rows, child_rows, noise_rows)
            if not secondary["passed"]:
                reasons.append("Scenario secondary quality guard failed")
            budget = self.scenario.budgets
            latency_limit = budget.get("latency_ratio", 1.2)
            latency = comparison["latency"]["ratio"]
            if latency is None or latency > latency_limit:
                reasons.append("Latency ratio exceeds budget")
            for unit, values in comparison["cost"].items():
                limit = budget.get("cost_ratio", {}).get(unit, 1.2)
                if values["ratio"] is None or values["ratio"] > limit:
                    reasons.append(f"Cost ratio exceeds budget for {unit}")
                total_cost = sum(row["cost"].get(unit, 0) for row in child_rows)
                if total_cost > budget.get("max_cost", {}).get(unit, math.inf):
                    reasons.append(f"Absolute cost budget exceeded for {unit}")
            if sum(row["seconds"] for row in child_rows) > budget.get("max_seconds", math.inf):
                reasons.append("Absolute task-seconds budget exceeded")
            if not generic:
                reasons.append("Generic mechanism attestation is required")
        except (ValueError, FileNotFoundError, jsonschema.ValidationError) as exc:
            reasons.append(str(exc))
        verdict = "reject"
        if not reasons and comparison is not None and threshold is not None:
            if comparison["net"] > threshold:
                verdict = "promote"
                reasons = ["Paired net gain exceeds measured noise threshold"]
            elif comparison["net"] >= 0 and rationale.strip():
                verdict = "adopt-no-harm"
                reasons = ["Nonnegative paired net with explicit generic-change rationale"]
            else:
                reasons = [
                    "Gain does not exceed noise; no-harm needs nonnegative net and a rationale"
                ]
        item = {
            "id": uuid.uuid4().hex[:16],
            "proposal_id": proposal_id,
            "parent_run": parent_run,
            "child_run": child_run,
            "noise_id": noise_id,
            "aggregation": aggregation,
            "rationale": rationale,
            "generic": generic,
            "verdict": verdict,
            "reasons": reasons,
            "threshold": threshold,
            "comparison": comparison,
            "secondary_guards": secondary,
        }
        self.ledger.append("verdict", item)
        return item

    def _secondary_guards(self, parent: list[dict], child: list[dict], noise: list[dict]) -> dict:
        # Detach evidence so an accidental callback mutation cannot alter core
        # comparison/settlement. Scenarios remain trusted executable code.
        try:
            result = self.scenario.secondary_guards(*copy.deepcopy((parent, child, noise)))
            if (
                not isinstance(result, dict)
                or set(result) != {"passed", "metrics"}
                or type(result["passed"]) is not bool
                or not isinstance(result["metrics"], dict)
            ):
                raise ValueError

            task_ids = {row["task_id"] for row in (*parent, *child, *noise)}

            def numeric_tree(value):
                if isinstance(value, dict):
                    return all(
                        isinstance(k, str) and k not in task_ids and numeric_tree(v)
                        for k, v in value.items()
                    )
                return type(value) in (int, float, bool) and math.isfinite(value)

            if not numeric_tree(result["metrics"]):
                raise ValueError
            canonical(result)
            return result
        except Exception:
            raise ValueError(
                "Secondary guards require boolean passed and aggregate numeric metrics"
            ) from None

    def adopt(self, verdict_id: str, rationale: str, queue: bool = False) -> dict[str, Any]:
        """Adopt a passing candidate or queue a runner-up, preserving base history.

        Args:
            verdict_id (str): Recorded verdict id; passing verdict required for adoption.
            rationale (str): Nonempty adoption or runner-up explanation.
            queue (bool): Append a runner-up event instead of changing current.

        Returns:
            dict[str, Any]: Adoption or runner-up record.
        """
        if not rationale.strip():
            raise ValueError("Adoption/runner-up rationale is required")
        with file_lock(self.state / "workspace.lock"):
            self._ensure_evolving()
            events = self.ledger.history()
            verdict = next(
                (
                    event["data"]
                    for event in events
                    if event["kind"] == "verdict" and event["data"]["id"] == verdict_id
                ),
                None,
            )
            if verdict is None:
                raise ValueError("Unknown verdict")
            proposal = next(
                item for item in self.proposals() if item["id"] == verdict["proposal_id"]
            )
            if queue:
                item = {
                    "proposal_id": proposal["id"],
                    "version": proposal["version"],
                    "base": self.current(),
                    "rationale": rationale,
                }
                self.ledger.append("runner_up", item)
                return item
            if verdict["verdict"] == "reject":
                raise ValueError("Cannot adopt a rejected verdict")
            # Recheck anchors, current base, costs and evidence immediately before adoption.
            checked = self.gate(
                proposal["id"],
                verdict["parent_run"],
                verdict["child_run"],
                verdict["noise_id"],
                verdict["aggregation"],
                verdict["rationale"],
                verdict["generic"],
            )
            if checked["verdict"] == "reject":
                raise ValueError("Adoption recheck rejected: " + "; ".join(checked["reasons"]))
            item = {
                "version": proposal["version"],
                "parent": self.current(),
                "proposal_id": proposal["id"],
                "verdict_id": checked["id"],
                "net": checked["comparison"]["net"],
                "rationale": rationale,
            }
            self.ledger.append("adoption", item)
            self.store.put("current", "singleton", {"version": proposal["version"]})
            return item

    def history(self) -> dict[str, Any]:
        """Read lineage and the pending runner-up queue without holdout details.

        Args:
            None.

        Returns:
            dict[str, Any]: Current base, base chain, queue and append-only events.
        """
        events = self.ledger.history()
        adopted = {event["data"]["proposal_id"] for event in events if event["kind"] == "adoption"}
        queue = {
            event["data"]["proposal_id"]: event["data"]
            for event in events
            if event["kind"] == "runner_up" and event["data"]["proposal_id"] not in adopted
        }
        public = [
            event
            for event in events
            if event["kind"] == "revert" or not private_event(event["kind"], event["data"])
        ]
        return {
            "current": self.current(),
            "base_chain": [self._epoch()["initial"]]
            + [
                event["data"]["version"]
                for event in events
                if event["kind"] in {"adoption", "revert"}
            ],
            "runner_ups": list(queue.values()),
            "events": [
                {
                    "kind": e["kind"],
                    "data": {k: e["data"][k] for k in ("version", "parent", "not_confirmed")},
                }
                if e["kind"] == "revert"
                else e
                for e in public
            ],
        }

    def measure_holdout(self, version: str, workers: int = 1) -> dict[str, int]:
        """Measure an already adopted version, outside improver CLI/MCP authority.

        Args:
            version (str): Adopted candidate. Holdout never participates in gate decisions.
            workers (int): Parallel workers.

        Returns:
            dict[str, int]: Counts only; no scores, tasks, run ids, outputs or traces.
        """
        if self.contract_read() is not None:
            self.confirm_adoption(version, workers=workers)
            return {"measured_versions": 1, "tasks": len(self._split_ids("confirmation"))}
        return self._legacy_measure_holdout(version, workers)

    def metrics(self) -> dict[str, Any]:
        """Report level-1 proposal quality and holdout measurement counts.

        Args:
            None.

        Returns:
            dict[str, Any]: Acceptance rate, prediction error, gain/cost and counts only
                for holdout confirmation (confirmation values are private to avoid feedback).
        """
        events = self.ledger.history()
        proposals = self.proposals()
        adopted = {
            event["data"]["proposal_id"]: event["data"]
            for event in events
            if event["kind"] == "adoption"
        }
        latest = {
            event["data"]["proposal_id"]: event["data"]
            for event in events
            if event["kind"] == "verdict" and event["data"]["comparison"] is not None
        }
        errors = [
            abs(item["predicted_effect"] - latest[item["id"]]["comparison"]["net"])
            for item in proposals
            if item["id"] in latest
        ]
        gains: dict[str, float | None] = {}
        totals: dict[str, float] = {}
        net = sum(item["net"] for item in adopted.values())
        public_runs = {
            event["data"]["run_id"]
            for event in events
            if event["kind"] == "run" and event["data"]["split"] not in PRIVATE_SPLITS
        }
        for run_id in public_runs:
            for row in self.results(run_id):
                for unit, value in row["cost"].items():
                    totals[unit] = totals.get(unit, 0) + value
        for unit, value in totals.items():
            gains[unit] = net / value if value else None
        confirmations = {
            event["data"]["version"]
            for event in events
            if event["kind"] in {"holdout_measurement", "confirmation"}
        }
        return {
            "proposals": len(proposals),
            "adoptions": len(adopted),
            "acceptance_rate": len(adopted) / len(proposals) if proposals else 0,
            "mean_absolute_prediction_error": sum(errors) / len(errors) if errors else None,
            "gain_per_cost": gains,
            "experiment_cost": totals,
            "holdout": {
                "measured_versions": len(confirmations),
                "pending_versions": len(
                    {item["version"] for item in adopted.values()} - confirmations
                ),
            },
            "confirmation": self._confirmation_metrics(),
            "playbook_versions": sorted({item["playbook_version"] for item in proposals}),
        }

    def _legacy_measure_holdout(self, version: str, workers: int = 1) -> dict[str, int]:
        """Measure an already adopted version, outside improver CLI/MCP authority.

        Args:
            version (str): Adopted candidate. Holdout never participates in gate decisions.
            workers (int): Parallel workers.

        Returns:
            dict[str, int]: Counts only; no scores, tasks, run ids, outputs or traces.
        """
        adoption = next(
            (
                event["data"]
                for event in self.ledger.history()
                if event["kind"] == "adoption" and event["data"]["version"] == version
            ),
            None,
        )
        if adoption is None:
            raise ValueError("Holdout measurement requires an already adopted version")
        run_ids = []
        for value in [adoption["parent"], version]:
            run_id = "h_" + digest([self._epoch()["id"], value])[:12]
            self._prepare_run(value, self._confirmation_split(), 1, workers, False, run_id)
            self.execute(run_id)
            if any(row["error"] for row in self._rows(run_id)):
                raise ValueError("Holdout execution failed; inspect private measurement records")
            run_ids.append(run_id)
        measured = compare(self._rows(run_ids[0]), self._rows(run_ids[1]), "mean")
        left, right = self._manifest(adoption["parent"]), self._manifest(version)
        private_guard = introduced_literal(
            {name: self._blob(sha) for name, sha in left.items()},
            {name: self._blob(sha) for name, sha in right.items()},
            self._split_ids("confirmation"),
            list(self.scenario.private_literals),
        )
        self.ledger.append(
            "holdout_measurement",
            {
                "version": version,
                "net": measured["net"],
                "confirmed": measured["net"] >= 0,
                "tasks": measured["tasks"],
                "private_literal_match": private_guard,
            },
        )
        return {"measured_versions": 1, "tasks": measured["tasks"]}
