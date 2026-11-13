"""Backend contract and the original file layout for evolution records."""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .privacy import PRIVATE_SPLITS
from .storage import Ledger, atomic_json, canonical, digest, file_lock, safe_name

KINDS = ("epoch", "current", "version", "run", "status", "result", "failure", "proposal")


class RecordStore(ABC):
    """Persist JSON records; snapshots and execution files remain caller-owned.

    Keys are opaque stable strings. A put replaces a complete mutable record;
    result slots are create-only (identical retries succeed, conflicts fail).
    Ledger events use append/history/restore_events, not keyed put. Event order
    and committed result identity must survive reopen. Callers serialize lifecycle
    operations with the shared workspace locks; implementations serialize events.
    """

    backend: str
    state: Path

    def close(self) -> None:
        """Release backend resources; file stores have no persistent connections."""

    @abstractmethod
    def get(self, kind: str, key: str, *, private: bool = False) -> dict[str, Any] | None:
        """Return a detached record, or None when absent."""

    @abstractmethod
    def put(self, kind: str, key: str, value: dict[str, Any], *, private: bool = False) -> None:
        """Commit a complete record; result keys must match their immutable slot."""

    @abstractmethod
    def items(self, kind: str, *, private: bool = False) -> list[tuple[str, dict[str, Any]]]:
        """List detached key/payload pairs in stable key order."""

    @abstractmethod
    def history(self) -> list[dict[str, Any]]:
        """Read ordered operator history, including private measurement events."""

    @abstractmethod
    def append(self, kind: str, data: dict[str, Any]) -> dict[str, Any]:
        """Append one immutable event; return its committed envelope."""

    @abstractmethod
    def restore_events(self, events: list[dict[str, Any]]) -> None:
        """Restore validated envelopes into empty history, preserving hashes/order.

        The operator importer validates envelopes and holds the workspace lock.
        Implementations must preserve private event placement and refuse an
        existing history. This method never regenerates timestamps or hashes.
        """

    def results(self, run_id: str, *, private: bool = False) -> list[dict[str, Any]]:
        """Read committed result slots for one run."""
        return [v for k, v in self.items("result", private=private) if k.startswith(run_id + "/")]

    def add_result(self, run_id: str, row: dict[str, Any], *, private: bool = False) -> None:
        """Create an immutable completed slot, refusing conflicting repeats."""
        key = run_id + "/" + digest([row["task_id"], row["repeat"]])
        previous = self.get("result", key, private=private)
        if previous is not None:
            if canonical(previous) != canonical(row):
                raise ValueError("Completed result slot is immutable")
            return
        self.put("result", key, row, private=private)

    def recover_results(self, run_id: str, *, private: bool = False) -> None:
        """Prepare a run for appends after an interrupted write."""


class FilesRecordStore(RecordStore):
    """Keep the v0 JSON/JSONL layout and newline commit semantics.

    Args:
        state: Private evokit state directory. Visibility is enforced by Workspace;
            use an OS boundary when giving an improver direct filesystem access.
    """

    backend = "files"

    def __init__(self, state: Path) -> None:
        self.state = state
        self.ledger = Ledger(state / "ledger.jsonl")
        self._completed: dict[str, dict[tuple[str, int], str]] = {}
        self._result_stamps: dict[str, tuple[int, int] | None] = {}

    def _path(self, kind: str, key: str) -> Path:
        if kind in {"epoch", "current"}:
            if key != "singleton":
                raise ValueError("Invalid singleton key")
            return self.state / f"{kind}.json"
        if kind in {"run", "status"}:
            return self.state / "runs" / safe_name(key) / f"{kind}.json"
        if kind in {"version", "failure", "proposal"}:
            return self.state / "records" / kind / f"{safe_name(key)}.json"
        raise ValueError(f"Unknown record kind: {kind}")

    def get(self, kind: str, key: str, *, private: bool = False) -> dict[str, Any] | None:
        if kind == "result":
            run_id, token = key.split("/")
            return next(
                (r for r in self.results(run_id) if digest([r["task_id"], r["repeat"]]) == token),
                None,
            )
        path = self._path(kind, key)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def put(self, kind: str, key: str, value: dict[str, Any], *, private: bool = False) -> None:
        if kind == "result":
            run_id, token = key.split("/")
            if token != digest([value["task_id"], value["repeat"]]):
                raise ValueError("Result key must match its slot")
            self.add_result(safe_name(run_id), value, private=private)
        else:
            atomic_json(self._path(kind, key), value)

    def items(self, kind: str, *, private: bool = False) -> list[tuple[str, dict[str, Any]]]:
        if kind in {"epoch", "current"}:
            value = self.get(kind, "singleton")
            return [("singleton", value)] if value is not None else []
        if kind in {"run", "status", "result"}:
            entries = []
            for path in sorted((self.state / "runs").glob("*/run.json")):
                meta = json.loads(path.read_text())
                if (meta["split"] in PRIVATE_SPLITS) != private:
                    continue
                if kind == "result":
                    entries.extend(
                        (path.parent.name + "/" + digest([r["task_id"], r["repeat"]]), r)
                        for r in self.results(path.parent.name)
                    )
                else:
                    value = self.get(kind, path.parent.name)
                    if value is not None:
                        entries.append((path.parent.name, value))
            return entries
        if private:
            return []
        return [
            (p.stem, json.loads(p.read_text()))
            for p in sorted((self.state / "records" / kind).glob("*.json"))
        ]

    def results(self, run_id: str, *, private: bool = False) -> list[dict[str, Any]]:
        path = self.state / "runs" / safe_name(run_id) / "results.jsonl"
        rows = []
        if path.exists():
            for line in path.read_bytes().splitlines(keepends=True):
                if not line.endswith(b"\n"):
                    break
                rows.append(json.loads(line))
        keys = [(r["task_id"], r["repeat"]) for r in rows]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate completed results")
        return rows

    def add_result(self, run_id: str, row: dict[str, Any], *, private: bool = False) -> None:
        # execute owns the exclusive run lock and initializes this index once.
        # Preserve immutable slots without reparsing the JSONL on every append.
        if run_id not in self._completed or self._result_stamps.get(run_id) != self._result_stamp(
            run_id
        ):
            self.recover_results(run_id, private=private)
        slots = self._completed[run_id]
        slot = (row["task_id"], row["repeat"])
        checksum = digest(row)
        if slot in slots:
            if slots[slot] != checksum:
                raise ValueError("Completed result slot is immutable")
            return
        path = self.state / "runs" / safe_name(run_id) / "results.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as stream:
            stream.write(canonical(row) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        slots[slot] = checksum
        self._result_stamps[run_id] = self._result_stamp(run_id)

    def _result_stamp(self, run_id: str) -> tuple[int, int] | None:
        path = self.state / "runs" / safe_name(run_id) / "results.jsonl"
        try:
            info = path.stat()
        except FileNotFoundError:
            return None
        return info.st_size, info.st_mtime_ns

    def recover_results(self, run_id: str, *, private: bool = False) -> None:
        path = self.state / "runs" / safe_name(run_id) / "results.jsonl"
        if path.exists():
            raw = path.read_bytes()
            committed = raw.rfind(b"\n") + 1
            if committed != len(raw):
                with path.open("r+b") as stream:
                    stream.truncate(committed)
        self._completed[run_id] = {
            (r["task_id"], r["repeat"]): digest(r) for r in self.results(run_id)
        }
        self._result_stamps[run_id] = self._result_stamp(run_id)

    def history(self) -> list[dict[str, Any]]:
        return self.ledger.history()

    def append(self, kind: str, data: dict[str, Any]) -> dict[str, Any]:
        return self.ledger.append(kind, data)

    def restore_events(self, events: list[dict[str, Any]]) -> None:
        with file_lock(self.ledger.path.with_suffix(".lock")):
            if self.ledger._history():
                raise ValueError("Event restore requires empty history")
            self.ledger.path.parent.mkdir(parents=True, exist_ok=True)
            with self.ledger.path.open("ab") as stream:
                for event in events:
                    stream.write(canonical(event) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())


def open_store(state: Path, storage: dict[str, Any] | None = None) -> RecordStore:
    """Select a backend once; reopening cannot silently switch record authority.

    Args:
        state: Workspace state root.
        storage: Optional settings with backend=files or hb. HB settings are
            documented in docs/storage.md. Omission reuses the persisted choice.

    Returns:
        RecordStore: Selected store. HB imports occur only on the HB path.

    Raises:
        ValueError: Backend selection differs from an initialized workspace.
    """
    path = state / "storage.json"
    with file_lock(state / "storage.lock"):
        persisted = json.loads(path.read_text()) if path.exists() else None
        legacy_files = persisted is None and (state / "epoch.json").exists()
        if legacy_files:
            persisted = {"backend": "files"}
        requested = storage if storage is not None else persisted or {"backend": "files"}
        if not isinstance(requested, dict) or requested.get("backend") not in {"files", "hb"}:
            raise ValueError("storage.backend must be files or hb")
        if persisted is not None and requested != persisted:
            raise ValueError("Record backend is fixed; export/import into a new workspace")
        requested = json.loads(canonical(requested))
        if persisted is None or legacy_files:
            store = _construct_store(state, requested)
            atomic_json(path, requested)
            return store
    # Reopening a selected store must not hold its selection lock while HB
    # reconstructs schemas. Otherwise progress polling can starve a new worker.
    return _construct_store(state, requested)


def _construct_store(state: Path, settings: dict[str, Any]) -> RecordStore:
    if settings["backend"] == "hb":
        from .hb_records import HBRecordStore

        return HBRecordStore(state, settings)
    if set(settings) != {"backend"}:
        raise ValueError("files storage accepts only backend")
    return FilesRecordStore(state)
