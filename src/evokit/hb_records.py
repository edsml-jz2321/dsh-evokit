"""Optional authoritative HeavenBase records, with physically separate holdout data."""

from __future__ import annotations

import json
import math
import os
import re
import time
import weakref
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .privacy import private_event
from .records import RecordStore
from .storage import canonical, digest, file_lock


def _entities(hb: Any) -> dict[str, Any]:
    class Version(hb.Entity):
        identifier = "evokit-version"
        kind = hb.field(hb.ShortText)
        key = hb.field(hb.ShortText)
        payload = hb.field(hb.Json)

    class Run(hb.Entity):
        identifier = "evokit-run"
        kind = hb.field(hb.ShortText)
        key = hb.field(hb.ShortText)
        payload = hb.field(hb.Json)
        version = hb.field(hb.ShortText).default("")
        split = hb.field(hb.ShortText).default("")

    class TaskResult(hb.Entity):
        identifier = "evokit-task-result"
        kind = hb.field(hb.ShortText)
        key = hb.field(hb.ShortText)
        payload = hb.field(hb.Json)
        run_id = hb.field(hb.ShortText)
        task_id = hb.field(hb.LongText(max_chars=2**63 - 1))
        group = hb.field(hb.LongText(max_chars=2**63 - 1))
        version = hb.field(hb.ShortText)
        epoch = hb.field(hb.ShortText)
        outcome = hb.field(hb.Float)

    class FailureCard(hb.Entity):
        identifier = "evokit-failure-card"
        kind = hb.field(hb.ShortText)
        key = hb.field(hb.ShortText)
        payload = hb.field(hb.Json)
        text = hb.field(hb.LongText(max_chars=2**63 - 1))
        embedding = hb.field(hb.Vector).optional()

    class Proposal(hb.Entity):
        identifier = "evokit-proposal"
        kind = hb.field(hb.ShortText)
        key = hb.field(hb.ShortText)
        payload = hb.field(hb.Json)
        version = hb.field(hb.ShortText)

    class LedgerEvent(hb.Entity):
        identifier = "evokit-ledger-event"
        kind = hb.field(hb.ShortText)
        key = hb.field(hb.ShortText)
        payload = hb.field(hb.Json)

    return {
        "epoch": Version,
        "current": Version,
        "version": Version,
        "run": Run,
        "status": Run,
        "result": TaskResult,
        "failure": FailureCard,
        "proposal": Proposal,
        "event": LedgerEvent,
    }


class HBRecordStore(RecordStore):
    """Own public and operator HB workspaces without a JSONL mirror.

    Args:
        state: Snapshot/execution directory and shared lifecycle lock location.
        settings: backend=hb, optional workspace id and public/operator backends.

    A process must set HOME and USERPROFILE to EVOKIT_HB_HOME before importing HB,
    and run one non-reset hb setup there. No default machine home is accessed.
    The operator workspace must never be served to an improver.
    """

    backend = "hb"

    def __init__(self, state: Path, settings: dict[str, Any]) -> None:
        allowed = {"backend", "workspace", "backends", "operator_backends"}
        if set(settings) - allowed:
            raise ValueError("Unknown HB storage settings")
        home = os.environ.get("EVOKIT_HB_HOME")
        if not home or any(
            Path(os.environ.get(k, "")).resolve() != Path(home).resolve()
            for k in ("HOME", "USERPROFILE")
        ):
            raise ValueError(
                "Set HOME and USERPROFILE to a private EVOKIT_HB_HOME; run hb setup once"
            )
        if not (Path(home) / ".heavenbase").is_dir():
            raise ValueError("Run hb setup once in EVOKIT_HB_HOME (never --reset)")
        try:
            import heavenbase as hb
        except ImportError as exc:
            raise ValueError("HB backend requires evokit[hb]") from exc
        self.state = state
        self.entities = _entities(hb)
        name = settings.get("workspace", "evokit-" + digest(str(state))[:16])
        self.context = hb.Context.load()
        self.workspaces = {}
        try:
            from heavenbase.workspace import WorkspaceSpec

            try:
                WorkspaceSpec.load(context=self.context)
            except KeyError as exc:
                raise ValueError("Run hb setup once in EVOKIT_HB_HOME (never --reset)") from exc
            for private, suffix, option in [
                (False, "", "backends"),
                (True, "-operator", "operator_backends"),
            ]:
                # SQLite is durable for all fields; vector near queries use HB's exact
                # scan route when no external vector index has been selected.
                backends = settings.get(option) or {
                    "main": {
                        "type": "sqlite",
                        "name": "main",
                        "database": (state / f"hb{suffix}.db").as_uri(),
                    }
                }
                ws = hb.HeavenBase(name + suffix, backends=backends, context=self.context)
                for entity in dict.fromkeys(self.entities.values()):
                    ws.register(entity)
                self.workspaces[private] = ws
        except BaseException:
            self.context.close()
            raise
        self._finalizer = weakref.finalize(self, self.context.close)
        self.embedding: Callable[[str], list[float]] | None = None

    def close(self) -> None:
        """Close this store's HB Context and its owned backend connections."""
        self._finalizer()

    def _id(self, kind: str, key: str) -> str:
        return "r" + digest([kind, key])[:60]

    def get(self, kind: str, key: str, *, private: bool = False) -> dict[str, Any] | None:
        row = self.workspaces[private].get(self._id(kind, key), entity=self.entities[kind])
        return json.loads(canonical(row["payload"])) if row else None

    def put(self, kind: str, key: str, value: dict[str, Any], *, private: bool = False) -> None:
        canonical(value)
        ws, entity = self.workspaces[private], self.entities[kind]
        object_id = self._id(kind, key)
        existing = ws.get(object_id, entity=entity)
        if kind == "result":
            run_id, token = key.split("/")
            if token != digest([value["task_id"], value["repeat"]]):
                raise ValueError("Result key must match its slot")
            if existing is not None:
                if canonical(existing["payload"]) != canonical(value):
                    raise ValueError("Completed result slot is immutable")
                return
        fields = {"object_id": object_id, "kind": kind, "key": key, "payload": value}
        if kind == "run":
            fields.update({k: value[k] for k in ("version", "split")})
        if kind == "result":
            fields.update(
                {k: value[k] for k in ("task_id", "group", "version", "epoch", "outcome")}
            )
            fields["run_id"] = key.split("/")[0]
        if kind == "proposal":
            fields["version"] = value["version"]
        if kind == "failure":
            fields["text"] = canonical(value).decode()
            if self.embedding:
                fields["embedding"] = self._embed(fields["text"])
            elif existing is None or existing.get("text") != fields["text"]:
                fields["embedding"] = None
        if existing is None:
            ws.upsert(entity, fields)
        else:
            # set is a patch. In particular status updates must retain row identity
            # and every previously stored field; upsert would replace the row.
            ws.set(
                fields["object_id"],
                {k: v for k, v in fields.items() if k != "object_id"},
                entity=entity,
            )

    def items(self, kind: str, *, private: bool = False) -> list[tuple[str, dict[str, Any]]]:
        entity = self.entities[kind]
        rows = self.workspaces[private].query(entity).where(entity.kind == kind).execute().rows()
        return sorted((r["key"], json.loads(canonical(r["payload"]))) for r in rows)

    def results(self, run_id: str, *, private: bool = False) -> list[dict[str, Any]]:
        """Read one arm through HB's typed run_id filter."""
        entity = self.entities["result"]
        rows = (
            self.workspaces[private].query(entity).where(entity.run_id == run_id).execute().rows()
        )
        return [json.loads(canonical(r["payload"])) for r in sorted(rows, key=lambda r: r["key"])]

    def history(self) -> list[dict[str, Any]]:
        with file_lock(self.state / "events.lock"):
            return self._history()

    def _history(self) -> list[dict[str, Any]]:
        entries = sorted(self.items("event") + self.items("event", private=True))
        previous = ""
        events = []
        for _, event in entries:
            data = {k: v for k, v in event.items() if k != "hash"}
            if data["previous"] != previous or digest(data) != event["hash"]:
                raise ValueError("Ledger integrity failure")
            previous = event["hash"]
            events.append(event)
        return events

    def append(self, kind: str, data: dict[str, Any]) -> dict[str, Any]:
        with file_lock(self.state / "events.lock"):
            history = self._history()
            event = {
                "kind": kind,
                "data": data,
                "time": time.time(),
                "previous": history[-1]["hash"] if history else "",
            }
            event["hash"] = digest(event)
            private = private_event(kind, data)
            if kind == "proposal":
                self.put("proposal", data["id"], data)
            self.put("event", f"{len(history):020d}", event, private=private)
            return event

    def restore_events(self, events: list[dict[str, Any]]) -> None:
        with file_lock(self.state / "events.lock"):
            if self._history():
                raise ValueError("Event restore requires empty history")
            for index, event in enumerate(events):
                kind, data = event["kind"], event["data"]
                private = private_event(kind, data)
                self.put("event", f"{index:020d}", event, private=private)
                if kind == "proposal":
                    self.put("proposal", data["id"], data)

    def query_records(self, **filters: Any) -> list[dict[str, Any]]:
        """Query public task results by exact dimensions and optional paired flips.

        Args:
            **filters: task, group, outcome, version, run_id, epoch, proposal,
                generation, flipped_between (two version ids), limit (1..1000).

        Returns:
            list[dict[str, Any]]: Public result payloads enriched with run_id.

        Raises:
            ValueError: Unknown filters or invalid filter values.
        """
        limit = filters.pop("limit", 100)
        flipped = filters.pop("flipped_between", None)
        proposal = filters.pop("proposal", None)
        generation = filters.pop("generation", None)
        aliases = {
            "task": "task_id",
            "group": "group",
            "outcome": "outcome",
            "version": "version",
            "run_id": "run_id",
            "epoch": "epoch",
        }
        if set(filters) - aliases.keys() or type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Invalid records query filters or limit")
        versions = None
        if proposal is not None:
            item = self.get("proposal", proposal)
            versions = {item["version"]} if item else set()
        if generation is not None:
            if type(generation) is not int or generation < 0:
                raise ValueError("generation is a nonnegative adoption index")
            chain = [e["data"]["base"] for e in self.history() if e["kind"] == "epoch"] + [
                e["data"]["version"] for e in self.history() if e["kind"] == "adoption"
            ]
            selected = {chain[generation]} if generation < len(chain) else set()
            versions = selected if versions is None else versions & selected
        entity = self.entities["result"]
        query = self.workspaces[False].query(entity)
        for key, value in filters.items():
            query = query.where(getattr(entity, aliases[key]) == value)
        rows = query.execute().rows()
        if flipped is not None:
            if (
                not isinstance(flipped, (list, tuple))
                or len(flipped) != 2
                or any(not isinstance(v, str) for v in flipped)
            ):
                raise ValueError("flipped_between requires two version ids")
            # Mean outcomes per task and split, across all repeats/runs. Avoid
            # mixing diagnosis with selection or different epochs.
            totals: dict[tuple[str, str, str], dict[str, list[float]]] = {}
            runs = dict(self.items("run"))
            for raw in self.workspaces[False].query(entity).execute().rows():
                if raw["version"] in flipped:
                    key = (raw["epoch"], runs[raw["run_id"]]["split"], raw["task_id"])
                    totals.setdefault(key, {}).setdefault(raw["version"], []).append(raw["outcome"])
            changed = {
                key
                for key, values in totals.items()
                if all(v in values for v in flipped)
                and sum(values[flipped[0]]) / len(values[flipped[0]])
                != sum(values[flipped[1]]) / len(values[flipped[1]])
            }
            rows = [
                r
                for r in rows
                if r["version"] in flipped
                and (r["epoch"], runs[r["run_id"]]["split"], r["task_id"]) in changed
            ]
        return [
            {**r["payload"], "run_id": r["run_id"]}
            for r in sorted(rows, key=lambda r: r["key"])
            if versions is None or r["version"] in versions
        ][:limit]

    def _embed(self, text: str) -> list[float]:
        values = self.embedding(text)
        if not values or any(not math.isfinite(v) for v in values) or not any(values):
            raise ValueError("Embedding must be a nonzero finite vector")
        return [float(v) for v in values]

    def similar(self, text: str, limit: int = 10) -> dict[str, Any]:
        """Rank only public failure cards using an injected embedder or token overlap.

        Args:
            text: Failure description to search.
            limit: Result cap in 1..100.

        Returns:
            dict[str, Any]: Method and ranked card payloads with scores. The local
                fallback uses Unicode casefolded word-set Jaccard similarity.
        """
        if not text.strip() or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Supply nonempty text and limit in 1..100")
        ws, entity = self.workspaces[False], self.entities["failure"]
        if self.embedding:
            # Reindex when the process explicitly configures an embedding provider.
            # No provider or key is auto-discovered, and HB never embeds implicitly.
            for key, value in self.items("failure"):
                self.put("failure", key, value)
            query_vector = self._embed(text)
            rows = (
                ws.query(entity)
                .near(entity.embedding, query_vector, top_k=limit, metric="cosine")
                .execute()
                .rows()
            )

            def score(row):
                vector = row["embedding"]
                if len(vector) != len(query_vector):
                    raise ValueError("Embedding dimensions changed")
                return sum(a * b for a, b in zip(vector, query_vector)) / math.sqrt(
                    sum(v * v for v in vector) * sum(v * v for v in query_vector)
                )

            method = "hb-vector-cosine"
        else:
            rows = ws.query(entity).execute().rows()
            words = set(re.findall(r"\w+", text.casefold()))

            def score(row):
                tokens = set(re.findall(r"\w+", row["text"].casefold()))
                return len(words & tokens) / len(words | tokens) if words | tokens else 0.0

            method = "local-token-jaccard"
        ranked = sorted(
            ((score(row), row) for row in rows), key=lambda item: (-item[0], item[1]["key"])
        )[:limit]
        return {
            "method": method,
            "matches": [{"score": value, **row["payload"]} for value, row in ranked],
        }
