"""Operator JSONL backup and backend migration, independent of artifact snapshots."""

from __future__ import annotations

import json
from pathlib import Path

from .privacy import PRIVATE_SPLITS, private_event
from .records import KINDS, RecordStore
from .storage import canonical, digest, file_lock, safe_name


def export_records(store: RecordStore, destination: Path) -> int:
    """Export every record, including operator secrets, to a new JSONL file.

    Args:
        store: Source record authority. Stop writers before exporting.
        destination: New private backup path; existing files are never replaced.

    Returns:
        int: Number of records, excluding the format header.

    Snapshots, playbook bytes, execution directories and trace files must be copied
    separately. This operator operation is deliberately absent from MCP.
    """
    entries = []
    for kind in KINDS:
        domains = (
            [True]
            if kind == "epoch"
            else [False, True]
            if kind in {"run", "status", "result"}
            else [False]
        )
        for private in domains:
            for key, value in store.items(kind, private=private):
                entries.append({"kind": kind, "key": key, "private": private, "value": value})
    for index, event in enumerate(store.history()):
        private = private_event(event["kind"], event["data"])
        entries.append(
            {"kind": "event", "key": f"{index:020d}", "private": private, "value": event}
        )
    with destination.open("xb") as stream:
        stream.write(
            canonical(
                {"format": "evokit-records/1", "count": len(entries), "digest": digest(entries)}
            )
            + b"\n"
        )
        for entry in entries:
            stream.write(canonical(entry) + b"\n")
    return len(entries)


def import_records(store: RecordStore, source: Path) -> int:
    """Validate and restore an operator backup into an empty record store.

    Args:
        store: Empty target backend, selected before initialization.
        source: Private evokit-records/1 JSONL file.

    Returns:
        int: Number of restored records.

    Raises:
        ValueError: Invalid format, truncated backup, visibility violation, or
            nonempty target. Validation completes before the first record write.
    """
    raw = source.read_bytes()
    if not raw.endswith(b"\n"):
        raise ValueError("Truncated records backup")
    header, *entries = [json.loads(line) for line in raw.splitlines()]
    if (
        header.get("format") != "evokit-records/1"
        or header.get("count") != len(entries)
        or header.get("digest") != digest(entries)
    ):
        raise ValueError("Invalid records backup")
    seen = set()
    events = []
    epoch = None
    previous = ""
    for entry in entries:
        if set(entry) != {"kind", "key", "private", "value"}:
            raise ValueError("Invalid record envelope")
        kind, key, private, value = (entry[k] for k in ("kind", "key", "private", "value"))
        if (
            kind not in (*KINDS, "event")
            or not isinstance(key, str)
            or type(private) is not bool
            or not isinstance(value, dict)
        ):
            raise ValueError("Invalid record types")
        identity = (kind, key, private)
        if identity in seen:
            raise ValueError("Duplicate record identity")
        seen.add(identity)
        if kind == "epoch":
            if not private or key != "singleton":
                raise ValueError("Epoch payloads are operator-only")
            epoch = value
        elif kind in {"run", "event"}:
            hidden = (
                (value.get("split") in PRIVATE_SPLITS)
                if kind == "run"
                else private_event(value["kind"], value["data"])
            )
            if hidden != private:
                raise ValueError("Holdout visibility violation")
        if kind == "event":
            if (
                key != f"{len(events):020d}"
                or value.get("previous") != previous
                or digest({k: v for k, v in value.items() if k != "hash"}) != value.get("hash")
            ):
                raise ValueError("Ledger integrity failure")
            previous = value["hash"]
            events.append(value)
    if (
        epoch is None
        or not events
        or events[0]["kind"] != "epoch"
        or events[0]["data"]["anchor_hash"] != digest(epoch)
    ):
        raise ValueError("Missing or invalid epoch anchor")
    runs = {(e["key"], e["private"]): e["value"] for e in entries if e["kind"] == "run"}
    tasks = {task["id"]: task for task in epoch["tasks"]}
    for (run_id, private), meta in runs.items():
        safe_name(run_id)
        split = meta.get("split")
        if split not in epoch["splits"] or meta.get("epoch") != epoch["id"]:
            raise ValueError("Run differs from epoch anchors")
        expected_ids = epoch["splits"][split][:2] if meta.get("smoke") else epoch["splits"][split]
        if (
            meta.get("task_ids") != expected_ids
            or private != (split in PRIVATE_SPLITS)
            or type(meta.get("repeats")) is not int
            or meta["repeats"] < 1
            or meta.get("scorer_version") != epoch["policy"]["scorer_version"]
        ):
            raise ValueError("Run split or scorer anchors are invalid")
    for entry in entries:
        kind, key, private, value = (entry[k] for k in ("kind", "key", "private", "value"))
        if kind in {"result", "status"}:
            run_id = key.split("/")[0] if kind == "result" else key
            meta = runs.get((run_id, private))
            if meta is None:
                raise ValueError("Orphan or incorrectly scoped result/status")
            if kind == "result" and (
                value.get("task_id") not in meta["task_ids"]
                or value.get("version") != meta["version"]
                or value.get("epoch") != meta["epoch"]
                or value.get("scorer_version") != meta["scorer_version"]
                or type(value.get("repeat")) is not int
                or not 0 <= value["repeat"] < meta["repeats"]
                or value.get("group") != tasks[value["task_id"]]["group"]
                or key != run_id + "/" + digest([value["task_id"], value["repeat"]])
            ):
                raise ValueError("Result identity differs from its run")
    # The import is an offline operator action. The epoch publishes last, so a
    # backend failure cannot make a partial restore appear initialized.
    with file_lock(store.state / "workspace.lock"):
        if store.history() or any(
            store.items(kind, private=p) for kind in KINDS for p in (False, True)
        ):
            raise ValueError("Import requires an empty target record store")
        for entry in entries:
            if entry["kind"] in {"epoch", "event"}:
                continue
            store.put(entry["kind"], entry["key"], entry["value"], private=entry["private"])
        store.restore_events(events)
        store.put("epoch", "singleton", epoch, private=True)
    return len(entries)
