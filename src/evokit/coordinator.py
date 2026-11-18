"""META decisions over anchored epochs; Workspace supplies storage and execution."""

from __future__ import annotations

from typing import Any

import jsonschema
import yaml

from .analysis import compare
from .guards import introduced_literal
from .privacy import PRIVATE_SPLITS
from .storage import digest, file_lock

NONEMPTY = {"type": "string", "minLength": 1, "pattern": r"\S"}


def _text(description: str) -> dict[str, Any]:
    return {**NONEMPTY, "description": description}


CONTRACT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "objective",
        "guards",
        "confirmation_rule",
        "noise_plan",
        "budget",
        "stagnation_window",
    ],
    "properties": {
        "objective": _text("What the epoch optimizes, in plain words."),
        "guards": {
            "type": "object",
            "additionalProperties": False,
            "required": ["latency_ratio", "cost_ratio"],
            "description": "Child/parent ratio limits that confirmation must stay within.",
            "properties": {
                "latency_ratio": {
                    "type": "number",
                    "minimum": 0,
                    "description": "Maximum latency ratio, for example 1.2.",
                },
                "cost_ratio": {
                    "type": "object",
                    "additionalProperties": {"type": "number", "minimum": 0},
                    "description": "Maximum cost ratio per cost unit, for example "
                    '{"money": 1.2}.',
                },
            },
        },
        "confirmation_rule": {
            "type": "object",
            "additionalProperties": False,
            "required": ["min_net"],
            "description": "Keep rule for confirm_adoption.",
            "properties": {
                "min_net": {
                    "type": "number",
                    "maximum": 0,
                    "description": "Minimum paired net on the confirmation split, at most 0; "
                    "a negative value is a pre-chosen noise margin.",
                }
            },
        },
        "noise_plan": {
            "type": "object",
            "additionalProperties": False,
            "required": ["repeats", "method"],
            "description": "How incumbent noise will be measured.",
            "properties": {
                "repeats": {
                    "type": "integer",
                    "minimum": 2,
                    "description": "Repeats of the incumbent arm, at least 2.",
                },
                "method": _text("Method name, for example paired flip rate and net spread."),
            },
        },
        "budget": {
            "type": "object",
            "additionalProperties": False,
            "required": ["unit", "total", "per_generation"],
            "description": "Spending plan; recorded for META, not enforced by a scheduler.",
            "properties": {
                "unit": _text("Cost unit name reported by the scenario, for example money."),
                "total": {
                    "type": "number",
                    "minimum": 0,
                    "description": "Total budget for the epoch.",
                },
                "per_generation": {
                    "type": "number",
                    "minimum": 0,
                    "description": "Budget per generation.",
                },
            },
        },
        "stagnation_window": {
            "type": "integer",
            "minimum": 1,
            "description": "Generations without adoption after which META stops.",
        },
        "confirmation_wear_budget": {
            "type": "integer",
            "minimum": 1,
            "default": 4,
            "description": "Maximum confirmation openings in this epoch. Default 4.",
        },
    },
}


def confirmation_passes(aggregate: dict[str, Any], contract: dict[str, Any]) -> bool:
    """Evaluate the anchored net and ratio guards without touching storage."""
    guards = contract["guards"]
    if aggregate["net"] < contract["confirmation_rule"]["min_net"]:
        return False
    latency = aggregate["latency_ratio"]
    if latency is None or latency > guards["latency_ratio"]:
        return False
    return all(
        aggregate["cost_ratios"].get(unit, 1.0) is not None
        and aggregate["cost_ratios"].get(unit, 1.0) <= ceiling
        for unit, ceiling in guards["cost_ratio"].items()
    )


class Coordinator:
    """Operator lifecycle mixed into Workspace; transports enforce tool profiles.

    Contracts and decision receipts are hash-chained events. No mutable copy is
    authoritative. A private decision lock serializes openings without blocking
    the workspace lock needed by run preparation and result execution.
    """

    def _split_fractions(self) -> tuple[float, ...]:
        config = yaml.safe_load(self.config.read_text(encoding="utf-8")) if self.config else {}
        values = config.get("splits")
        if values is None:
            return (0.4, 0.4, 0.2, 0.0)
        if not isinstance(values, dict) or set(values) - {
            "diagnosis",
            "selection",
            "confirmation",
            "holdout",
            "final",
        }:
            raise ValueError(
                "splits must map diagnosis, selection, confirmation and optional final fractions"
            )
        if "holdout" in values and "confirmation" in values:
            raise ValueError("Use confirmation or its holdout alias, not both")
        if not {"diagnosis", "selection"} <= values.keys() or not (
            {"confirmation", "holdout"} & values.keys()
        ):
            raise ValueError("splits requires diagnosis, selection and confirmation")
        return tuple(
            values.get(k, values.get("holdout") if k == "confirmation" else 0)
            for k in ("diagnosis", "selection", "confirmation", "final")
        )

    def _confirmation_split(self) -> str:
        return "holdout" if "holdout" in self._epoch()["splits"] else "confirmation"

    def _split_ids(self, split: str) -> list[str]:
        if split in {"confirmation", "holdout"}:
            split = self._confirmation_split()
        return self._epoch()["splits"].get(split, [])

    def _split_counts(self) -> dict[str, int]:
        return {
            name: len(self._split_ids(name))
            for name in ("diagnosis", "selection", "confirmation", "final")
        }

    def _ensure_evolving(self) -> None:
        if any(e["kind"] == "final_open" for e in self.ledger.history()):
            raise ValueError(
                "Final measurement has opened; start a new epoch before further evolution"
            )

    def contract_record(self, contract: dict[str, Any]) -> dict[str, Any]:
        """Anchor META's contract before the first arm is prepared.

        Args:
            contract: Objective, ratio guards, min_net rule, noise plan, money
                budget, stagnation window and optional wear budget (default 4).

        Returns:
            dict: Contract with split counts and immutable scorer/split anchors.

        Raises:
            ValueError: Once any arm has been prepared, including a keyless smoke.
            jsonschema.ValidationError: For an invalid or incomplete contract.
        """
        with file_lock(self.state / "workspace.lock"):
            self._check_anchors()
            self._ensure_evolving()
            if any(e["kind"] in {"run", "work_started"} for e in self.ledger.history()):
                raise ValueError("Contract is frozen after work starts; start a new epoch")
            return self._record_contract(contract)

    def _record_contract(
        self, contract: dict[str, Any], retrospective_reason: str | None = None
    ) -> dict[str, Any]:
        jsonschema.validate(contract, CONTRACT_SCHEMA)
        epoch = self._epoch()
        item = {
            **contract,
            "confirmation_wear_budget": contract.get("confirmation_wear_budget", 4),
            "epoch": epoch["id"],
            "split_hash": epoch["split_hash"],
            "scorer_version": epoch["policy"]["scorer_version"],
            "splits": self._split_counts(),
            "retrospective_reason": retrospective_reason,
        }
        # Reject NaN/infinity and detach caller-owned objects before publishing.
        digest(item)
        self.ledger.append("epoch_contract", item)
        return item

    def contract_read(self) -> dict[str, Any] | None:
        """Read the latest anchored contract, or None for an uncontracted epoch."""
        for event in reversed(self.ledger.history()):
            if event["kind"] == "epoch_contract":
                item = event["data"]
                epoch = self._epoch()
                if (item["epoch"], item["split_hash"], item["scorer_version"], item["splits"]) != (
                    epoch["id"],
                    epoch["split_hash"],
                    epoch["policy"]["scorer_version"],
                    self._split_counts(),
                ):
                    raise ValueError("Contract anchors differ from epoch")
                return item
        return None

    def migrate_confirmation_contract(
        self, contract: dict[str, Any], reason: str
    ) -> dict[str, Any]:
        """Append a clearly retrospective contract for a legacy holdout epoch.

        Args:
            contract: Reviewed rule and campaign plan, explicitly retrospective.
            reason: Operator's explanation; never claims pre-registration.

        Returns:
            dict: Newly appended contract. Original epoch and ledger bytes remain.

        Raises:
            ValueError: For a modern/already migrated epoch or missing legacy runs.

        This SDK-only migration cannot execute tasks. Use confirm_adoption with
        reuse_only=True afterwards. It is absent from every tool profile.
        """
        if not reason.strip():
            raise ValueError("Migration requires a retrospective reason")
        with file_lock(self.state / "workspace.lock"):
            self._check_anchors()
            if self.contract_read() is not None or "holdout" not in self._epoch()["splits"]:
                raise ValueError("Only an uncontracted legacy epoch can be migrated")
            if not any(m["split"] == "holdout" for _, m in self.store.items("run", private=True)):
                raise ValueError("Migration requires retained legacy holdout measurements")
            return self._record_contract(contract, reason)

    def _current_adoption(self) -> dict[str, Any] | None:
        stack = []
        for event in self.ledger.history():
            if event["kind"] == "adoption":
                stack.append(event)
            elif event["kind"] == "revert":
                if not stack or stack[-1]["hash"] != event["data"]["adoption"]:
                    raise ValueError("Revert lineage does not match the active adoption")
                stack.pop()
        return stack[-1] if stack else None

    def _adoption(self, version: str) -> dict[str, Any]:
        for event in reversed(self.ledger.history()):
            if event["kind"] == "adoption" and event["data"]["version"] == version:
                return event
        raise ValueError("Confirmation requires an adopted version")

    def _private_complete(self, run_id: str) -> list[dict[str, Any]]:
        meta = self._run_meta(run_id, holdout=True)
        rows = self._rows(run_id)
        expected = {(t, i) for t in meta["task_ids"] for i in range(meta["repeats"])}
        if (
            meta["split"] not in PRIVATE_SPLITS
            or meta["smoke"]
            or meta["repeats"] != 1
            or {(r["task_id"], r["repeat"]) for r in rows} != expected
            or len(rows) != len(expected)
            or any(
                r["error"]
                or r["version"] != meta["version"]
                or r["epoch"] != meta["epoch"]
                or r["scorer_version"] != meta["scorer_version"]
                for r in rows
            )
        ):
            raise ValueError(
                "Private measurement is incomplete, erroneous or has mismatched anchors"
            )
        return rows

    def _measurement_run(self, version: str, split: str, workers: int, reuse_only: bool) -> str:
        epoch = self._epoch()
        for run_id, meta in self.store.items("run", private=True):
            if (
                meta["version"] == version
                and meta["split"] == split
                and not meta["smoke"]
                and meta["repeats"] == 1
                and meta["epoch"] == epoch["id"]
                and meta["scorer_version"] == epoch["policy"]["scorer_version"]
                and meta["task_ids"] == self._split_ids(split)
            ):
                try:
                    self._private_complete(run_id)
                except ValueError:
                    continue
                return run_id
        if reuse_only:
            raise ValueError("No complete retained measurement; reuse_only forbids execution")
        run_id = ("h_" if split in {"holdout", "confirmation"} else "f_") + digest(
            [epoch["id"], version]
        )[:12]
        self._prepare_run(version, split, 1, workers, False, run_id)
        self.execute(run_id)
        self._private_complete(run_id)
        return run_id

    def _private_audit(self, parent: str, child: str, split: str) -> bool:
        left, right = self._manifest(parent), self._manifest(child)
        return introduced_literal(
            {name: self._blob(sha) for name, sha in left.items()},
            {name: self._blob(sha) for name, sha in right.items()},
            self._split_ids(split),
            list(self.scenario.private_literals),
        )

    @staticmethod
    def _aggregate(comparison: dict[str, Any]) -> dict[str, Any]:
        return {
            "fixes": len(comparison["fixes"]),
            "breaks": len(comparison["breaks"]),
            "net": comparison["net"],
            "cost_ratios": {k: v["ratio"] for k, v in comparison["cost"].items()},
            "latency_ratio": comparison["latency"]["ratio"],
        }

    def confirm_adoption(
        self, version: str, workers: int = 1, reuse_only: bool = False
    ) -> dict[str, Any]:
        """Apply the anchored keep/revert rule to a paired adoption confirmation.

        Args:
            version: Adopted current child. A retrospective migration may confirm
                historical adoptions using retained results only.
            workers: Execution concurrency, 1 through 256.
            reuse_only: Refuse missing measurements instead of executing tasks.

        Returns:
            dict: Aggregate fixes, breaks, net, cost/latency ratios and verdict.
                No task identifiers, scores, outputs, traces or private run ids.

        Raises:
            ValueError: Missing contract, exhausted wear, invalid adoption or data.

        One opening consumes one wear slot even if execution fails. Resuming the
        same adoption reuses that slot; completed decisions are idempotent. A
        not-confirmed verdict marks the proposal but META calls ledger_revert.
        """
        if type(workers) is not int or not 1 <= workers <= 256:
            raise ValueError("workers must be in [1, 256]")
        with file_lock(self.state / "decision.lock"):
            self._check_anchors()
            contract = self.contract_read()
            if contract is None:
                raise ValueError("Record the epoch contract before confirmation")
            adoption = (
                self._current_adoption() if version == self.current() else self._adoption(version)
            )
            if adoption is None:
                raise ValueError("Confirmation requires an active adoption")
            identity = adoption["hash"]
            events = self.ledger.history()
            previous = next(
                (
                    e["data"]
                    for e in events
                    if e["kind"] == "confirmation" and e["data"]["adoption"] == identity
                ),
                None,
            )
            if previous:
                return previous["aggregate"]
            self._ensure_evolving()
            if version != self.current() and not (reuse_only and contract["retrospective_reason"]):
                raise ValueError("Confirm the current adoption only")
            openings = [e for e in events if e["kind"] == "confirmation_started"]
            if not any(e["data"]["adoption"] == identity for e in openings):
                if len(openings) >= contract["confirmation_wear_budget"]:
                    raise ValueError("Confirmation wear budget exhausted; start a fresh epoch")
                self.ledger.append(
                    "confirmation_started", {"adoption": identity, "version": version}
                )
            split = self._confirmation_split()
            parent = adoption["data"]["parent"]
            before = self._measurement_run(parent, split, workers, reuse_only)
            after = self._measurement_run(version, split, workers, reuse_only)
            aggregate = self._aggregate(
                compare(self._private_complete(before), self._private_complete(after), "mean")
            )
            passed = confirmation_passes(aggregate, contract)
            verdict = next(
                e["data"]
                for e in events
                if e["kind"] == "verdict" and e["data"]["id"] == adoption["data"]["verdict_id"]
            )
            noise = next(
                e["data"]
                for e in events
                if e["kind"] == "noise" and e["data"]["noise_id"] == verdict["noise_id"]
            )
            _, noise_rows = self._complete(noise["run_id"])
            secondary = self._secondary_guards(
                self._private_complete(before), self._private_complete(after), noise_rows
            )
            aggregate["secondary_guards"] = secondary
            passed = passed and secondary["passed"]
            aggregate["verdict"] = "confirmed" if passed else "not_confirmed"
            self.ledger.append(
                "confirmation",
                {
                    "adoption": identity,
                    "version": version,
                    "parent": parent,
                    "proposal_id": adoption["data"]["proposal_id"],
                    "confirmed": bool(passed),
                    "aggregate": aggregate,
                    "contract_hash": digest(contract),
                    "private_literal_match": self._private_audit(parent, version, split),
                },
            )
            return aggregate

    def ledger_revert(self, reason: str) -> dict[str, Any]:
        """Append a revert of current to its adoption parent; preserve all history.

        Args:
            reason: META's nonempty reason, stored privately in the operator ledger.

        Returns:
            dict: Child, new current parent, reason and not-confirmed bit.

        Raises:
            ValueError: Missing reason, initial current, or final already opened.
        """
        if not reason.strip():
            raise ValueError("Revert requires a reason")
        with file_lock(self.state / "workspace.lock"):
            self._check_anchors()
            self._ensure_evolving()
            child = self.current()
            adoption = self._current_adoption()
            if adoption is None:
                raise ValueError("Initial version has no adoption parent")
            data = adoption["data"]
            failed = any(
                e["kind"] == "confirmation"
                and e["data"]["adoption"] == adoption["hash"]
                and not e["data"]["confirmed"]
                for e in self.ledger.history()
            )
            item = {
                "parent": child,
                "version": data["parent"],
                "proposal_id": data["proposal_id"],
                "adoption": adoption["hash"],
                "reason": reason,
                "not_confirmed": failed,
            }
            self.ledger.append("revert", item)
            self.store.put("current", "singleton", {"version": item["version"]})
            return item

    def final_measure(self, workers: int = 1) -> dict[str, Any]:
        """Open final once and pair current with the epoch's starting version.

        Args:
            workers: Execution concurrency, 1 through 256.

        Returns:
            dict: Aggregate fixes, breaks, net and cost/latency ratios, no decisions.

        Raises:
            ValueError: No final tasks/contract, invalid concurrency, or a prior open.

        The open event is committed before execution. Failure still consumes the
        opening. No adoption, revert or further public arm is allowed afterwards.
        """
        if type(workers) is not int or not 1 <= workers <= 256:
            raise ValueError("workers must be in [1, 256]")
        with file_lock(self.state / "decision.lock"):
            with file_lock(self.state / "workspace.lock"):
                self._check_anchors()
                self._ensure_evolving()
                if self.contract_read() is None or not self._split_ids("final"):
                    raise ValueError(
                        "Final measurement requires a contract and a separate final split"
                    )
                if any(
                    (self.store.get("status", run_id, private=p) or {}).get("state") != "completed"
                    for p in (False, True)
                    for run_id, _ in self.store.items("run", private=p)
                ):
                    raise ValueError("Finish existing arms before opening final")
                current, initial = self.current(), self._epoch()["initial"]
                self.ledger.append("final_open", {"version": current, "initial": initial})
            before = self._measurement_run(initial, "final", workers, False)
            after = self._measurement_run(current, "final", workers, False)
            result = self._aggregate(
                compare(self._private_complete(before), self._private_complete(after), "mean")
            )
            self.ledger.append(
                "final_measurement",
                {
                    "version": current,
                    "aggregate": result,
                    "private_literal_match": self._private_audit(initial, current, "final"),
                },
            )
            return result

    def _confirmation_metrics(self) -> dict[str, Any]:
        measured = [e["data"] for e in self.ledger.history() if e["kind"] == "confirmation"]
        return {
            "decisions": len(measured),
            "rate": sum(e["confirmed"] for e in measured) / len(measured) if measured else None,
        }
