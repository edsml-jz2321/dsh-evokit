"""File integrity, interprocess serialization and append-only experiment records."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import time
import uuid
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def file_lock(path: Path, *, timeout: float = 15) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        deadline = time.monotonic() + timeout
        while True:
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    raise ValueError(f"Workspace operation already active: {path.name}") from None
                time.sleep(0.05)
        try:
            # Windows permits locking beyond EOF. Initialize the sentinel only
            # after acquiring the byte lock so another opener cannot flush into
            # a range that the first writer has already locked.
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def safe_name(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", value):
        raise ValueError("Invalid identifier")
    return value


def safe_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or "\\" in value or path.is_absolute() or str(path) != value:
        raise ValueError(f"Invalid artifact path: {value!r}")
    for part in path.parts:
        if part in {".", ".."} or ":" in part or part.endswith((".", " ")):
            raise ValueError(f"Invalid artifact path: {value!r}")
        if part.split(".")[0].upper() in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }:
            raise ValueError(f"Windows reserved artifact path: {value!r}")
    return value


class Ledger:
    """Own append-only JSONL history with a hash chain and cross-process locking.

    Args:
        path (Path): JSONL destination. Its sibling lock serializes writers.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def history(self) -> list[dict[str, Any]]:
        """Read and verify every committed record.

        Args:
            None.

        Returns:
            list[dict[str, Any]]: Ordered events, including failed attempts.

        Raises:
            ValueError: If a record or hash chain is damaged.
        """
        with file_lock(self.path.with_suffix(".lock")):
            return self._history()

    def _history(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events = []
        previous = ""
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                checksum = event.pop("hash")
                if event["previous"] != previous or digest(event) != checksum:
                    raise ValueError("Ledger integrity failure")
                event["hash"] = checksum
                events.append(event)
                previous = checksum
        except (KeyError, json.JSONDecodeError) as exc:
            raise ValueError("Damaged ledger; preserve history and recover explicitly") from exc
        return events

    def append(self, kind: str, data: dict[str, Any]) -> dict[str, Any]:
        """Append one durable event without rewriting prior bytes.

        Args:
            kind (str): Event kind.
            data (dict[str, Any]): JSON event payload.

        Returns:
            dict[str, Any]: Committed event with hash and predecessor.
        """
        with file_lock(self.path.with_suffix(".lock")):
            history = self._history()
            event = {
                "kind": kind,
                "data": data,
                "time": time.time(),
                "previous": history[-1]["hash"] if history else "",
            }
            event["hash"] = digest(event)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("ab") as stream:
                stream.write(canonical(event) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            return event
