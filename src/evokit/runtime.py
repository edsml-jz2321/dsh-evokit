"""Host-neutral publication, recovery accounting and operator reset primitives."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from evokit.storage import atomic_json, file_lock


def publish_with_retry(
    publish: Callable[[], Any], *, timeout: float = 180, interval: float = 0.25, on_error=None
) -> Any:
    """Retry publication without re-executing its producer or evaluation.

    Args:
        publish: Idempotent publication callback; it must not run an evaluation.
        timeout: Maximum retry duration in seconds.
        interval: Delay between transient publication errors.
        on_error: Optional diagnostic callback receiving the exception.

    Returns:
        Any: The successful callback result.

    Raises:
        OSError: Publication still fails at the deadline. The old file remains
            authoritative when the callback uses atomic_json.
    """
    if timeout < 0 or interval <= 0:
        raise ValueError("Invalid publication retry timing")
    deadline = time.monotonic() + timeout
    while True:
        try:
            return publish()
        except OSError as error:
            if on_error is not None:
                on_error(error)
            if time.monotonic() >= deadline:
                raise
            time.sleep(min(interval, max(0, deadline - time.monotonic())))


def watch_updates(publish, stopped, *, on_error=None, interval=2):
    """Publish periodic snapshots until an Event stops the monitor.

    Publication errors are reported and retried next interval; this function
    never restarts the evaluation. Caller owns final publish_with_retry.
    """
    if interval <= 0:
        raise ValueError("Invalid publication interval")
    while not stopped.wait(interval):
        try:
            publish()
        except OSError as error:
            if on_error is not None:
                on_error(error)


def recovery_preconditions(
    *,
    active_requests: int,
    pending_requests: int,
    accounting_reconciled: bool,
    probe_successes: int,
    probe_time: float,
    now: float | None = None,
) -> None:
    """Require idle, reconciled accounting and fresh direct-connectivity evidence.

    This is an operator primitive, never an improver tool. Callers authenticate
    their accounting/probe evidence and preserve an append-only reset record.
    No reservation is expired or erased by a circuit reset.
    """
    timestamp = time.time() if now is None else now
    if (
        type(active_requests) is not int
        or active_requests != 0
        or type(pending_requests) is not int
        or pending_requests != 0
        or accounting_reconciled is not True
    ):
        raise ValueError("Recovery requires idle and reconciled accounting")
    if (
        type(probe_successes) is not int
        or probe_successes < 1
        or not math.isfinite(probe_time)
        or not 0 <= timestamp - probe_time <= 900
    ):
        raise ValueError("Recovery requires fresh successful direct probes")


class RecoveryAccounting:
    """Restore earlier attempt usage once, separately from new lease charges.

    Args:
        usage: Prior accounted request rows with unique reservation identities.
        wall_seconds: Full prior attempt wall time, when recorded. Otherwise
            restored latency is explicitly a model-time lower bound.
        source: Opaque prior attempt identity for operator provenance.
    """

    def __init__(
        self,
        usage: list[dict],
        *,
        wall_seconds: float | None = None,
        source: str = "",
        cost_key: str = "cny",
    ):
        if not isinstance(cost_key, str) or not cost_key:
            raise ValueError("Recovery needs a declared monetary unit")
        self.cost_key = cost_key
        self.usage = copy.deepcopy(usage)
        identities = [u.get("reservation") for u in usage]
        if any(not isinstance(i, str) or not i for i in identities) or len(set(identities)) != len(
            identities
        ):
            raise ValueError("Prior usage needs distinct reservation identities")
        for row in usage:
            if any(
                type(row.get(k)) not in (int, float) or not math.isfinite(row[k]) or row[k] < 0
                for k in (cost_key, "seconds")
            ):
                raise ValueError("Prior usage must have accounted finite cost and latency")
        if wall_seconds is not None and (not math.isfinite(wall_seconds) or wall_seconds < 0):
            raise ValueError("Invalid prior wall time")
        self.wall_seconds, self.source = wall_seconds, source
        self._seeded = None
        self._applied = False

    def seed(self, usage: list[dict]) -> None:
        """Add history once to a client's usage list before the pipeline starts."""
        if self._seeded is usage:
            return
        if self._seeded is not None or {u.get("reservation") for u in usage} & {
            u["reservation"] for u in self.usage
        }:
            raise ValueError("Recovery usage was already installed elsewhere")
        usage.extend(copy.deepcopy(self.usage))
        self._seeded = usage

    def apply(self, result, *, usage_already_included: bool = False) -> None:
        """Restore prior cost/time once, or only wall time after seeded scoring.

        No budget settlement occurs here. Existing signed prior charges and new
        incremental leases remain separate. Trace persistence belongs to caller.
        """
        if self._applied:
            raise ValueError("Recovery accounting already applied")
        model_seconds = sum(u["seconds"] for u in self.usage)
        prior = self.wall_seconds if self.wall_seconds is not None else model_seconds
        if not usage_already_included:
            prior_cost = sum(u[self.cost_key] for u in self.usage)
            old_result_cost = result.cost.get(self.cost_key, 0)
            old_output_cost = result.output["cost"].get(self.cost_key, 0)
            result.cost[self.cost_key] = old_result_cost + prior_cost
            result.output["cost"][self.cost_key] = old_output_cost + prior_cost
            for name in ("input_tokens", "output_tokens"):
                if name in result.output["cost"]:
                    result.output["cost"][name] += sum(u.get(name, 0) for u in self.usage)
            if "calls" in result.output["cost"]:
                result.output["cost"]["calls"] += len(self.usage)
            result.output["latency"]["model_seconds"] = (
                result.output["latency"].get("model_seconds", 0) + model_seconds
            )
            for usage in self.usage:
                role = usage.get("role")
                if role:
                    role_cost = result.roles.setdefault(role, {self.cost_key: 0})
                    role_cost[self.cost_key] = (
                        role_cost.get(self.cost_key, 0) + usage[self.cost_key]
                    )
        elif self._seeded is None:
            raise ValueError("Included usage must have been seeded explicitly")
        result.seconds += prior
        result.output["latency"]["seconds"] += prior
        result.output["execution_recovery"] = {
            "original_run": self.source,
            "prior_paid_cost_included": sum(u[self.cost_key] for u in self.usage),
            "cost_unit": self.cost_key,
            "prior_sequential_model_seconds_included": model_seconds,
            "prior_wall_seconds": self.wall_seconds,
            "restored_latency_seconds": prior,
            "latency_is_lower_bound": self.wall_seconds is None,
            "new_lease_charges_only_incremental_requests": True,
        }
        self._applied = True


class TransportCircuit:
    """Persist request outcomes and explicitly reset only an explained idle stop."""

    def __init__(self, path: Path, *, consecutive: int = 3, window: int = 50, ratio: float = 0.1):
        if (
            type(consecutive) is not int
            or consecutive < 1
            or type(window) is not int
            or window < 1
            or not 0 <= ratio <= 1
        ):
            raise ValueError("Invalid transport circuit policy")
        self.path, self.consecutive, self.window, self.ratio = path, consecutive, window, ratio

    def record(self, success: bool, request_id: str, error_class: str = "") -> bool:
        """Record one accounted dispatch, preserving the configured thresholds."""
        if type(success) is not bool or not request_id:
            raise ValueError("A circuit outcome needs a dispatch identity")
        with file_lock(self.path.with_suffix(".lock")):
            state = (
                json.loads(self.path.read_text())
                if self.path.exists()
                else {"consecutive": 0, "events": []}
            )
            state["consecutive"] = 0 if success else state["consecutive"] + 1
            state["events"] = (
                state["events"]
                + [{"success": success, "request_id": request_id, "error_class": error_class}]
            )[-self.window :]
            atomic_json(self.path, state)
            return state["consecutive"] >= self.consecutive or (
                len(state["events"]) == self.window
                and sum(not e["success"] for e in state["events"]) / self.window > self.ratio
            )

    def reset(self, stop: Path, *, expected_stop: str, reason: str, **evidence) -> dict:
        """Archive circuit history and clear its stop after operator verification.

        Caller supplies authenticated budget/broker and direct probe evidence.
        The circuit never cancels, settles or rewrites any monetary reservation.
        Unknown-usage or mismatched stop markers remain closed.
        """
        recovery_preconditions(**evidence)
        with file_lock(self.path.with_suffix(".lock")):
            if (
                not reason.strip()
                or not self.path.is_file()
                or not stop.is_file()
                or stop.read_text().strip() != expected_stop
            ):
                raise ValueError("Only the expected explained transport stop may be reset")
            previous = self.path.read_bytes()
            record = {
                "reason": reason,
                "time": time.time(),
                "prior_state_sha256": hashlib.sha256(previous).hexdigest(),
                "prior_state": json.loads(previous),
                "evidence": evidence,
                "thresholds": {
                    "consecutive": self.consecutive,
                    "window": self.window,
                    "ratio": self.ratio,
                },
            }
            atomic_json(
                self.path.with_name(self.path.stem + "-reset-" + uuid.uuid4().hex + ".json"), record
            )
            atomic_json(self.path, {"consecutive": 0, "events": []})
            stop.unlink()
            return record
