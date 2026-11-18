"""Optional loopback Chat Completions relay. No provider calls occur on import."""

from __future__ import annotations

import argparse
import copy
import hashlib
import http.client
import json
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from evokit.runtime import recovery_preconditions
from evokit.storage import atomic_json, canonical, digest, file_lock


def clear_cache_markers(value: Any) -> None:
    """Remove explicit cache controls recursively from a detached wire request."""
    if isinstance(value, dict):
        value.pop("cache_control", None)
        for item in value.values():
            clear_cache_markers(item)
    elif isinstance(value, list):
        for item in value:
            clear_cache_markers(item)


def cache_hints(body: dict[str, Any]) -> dict[str, Any]:
    """Apply Qwen ephemeral hints on a detached wire request.

    Args:
        body (dict[str, Any]): OpenAI Chat Completions request.

    Returns:
        dict[str, Any]: Deep copy. Qwen gets at most four text markers, including
            the last system text and newest message containing text. Other models
            are copied. DeepSeek explicit cache controls are removed.
    """
    wire = copy.deepcopy(body)
    if str(wire.get("model", "")).casefold().startswith("deepseek"):
        clear_cache_markers(wire)
        return wire
    if not str(wire.get("model", "")).casefold().startswith("qwen"):
        return wire
    texts = []
    systems = []
    newest = []
    for message in wire.get("messages", []):
        content = message.get("content")
        if isinstance(content, str):
            content = message["content"] = [{"type": "text", "text": content}]
        current = []
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    hinted = part.pop("cache_control", None) is not None
                    if (
                        part.get("type") == "text"
                        and isinstance(part.get("text"), str)
                        and part["text"]
                    ):
                        current.append(part)
                        texts.append((part, hinted))
        if current:
            newest = current[-1:]
            if message.get("role") == "system" and not systems:
                systems = current[-1:]
        message.pop("cache_control", None)
    selected = []
    for part in systems + newest + [p for p, hinted in texts if hinted]:
        if not any(part is existing for existing in selected) and len(selected) < 4:
            selected.append(part)

    clear_cache_markers(wire)
    for part in selected:
        part["cache_control"] = {"type": "ephemeral"}
    return wire


def usage_cost(usage: dict[str, Any], rates: dict[str, Any]) -> tuple[Decimal, dict[str, int]]:
    """Validate usage and price creation at 125%, hits at 10%, other input at 100%.

    Args:
        usage (dict[str, Any]): Provider usage, including prompt token details.
        rates (dict[str, Any]): Input/output prices per million, in one budget unit.

    Returns:
        tuple[Decimal, dict[str, int]]: Exact cost and sanitized token counters.

    Raises:
        ValueError: Missing, overlapping, negative or malformed usage counters.
    """
    inputs, outputs = usage["prompt_tokens"], usage["completion_tokens"]
    details = usage.get("prompt_tokens_details") or {}
    hit = details.get("cached_tokens", 0)
    created = usage.get(
        "cache_creation_input_tokens", details.get("cache_creation_input_tokens", 0)
    )
    if (
        "cache_creation_input_tokens" in usage
        and "cache_creation_input_tokens" in details
        and usage["cache_creation_input_tokens"] != details["cache_creation_input_tokens"]
    ):
        raise ValueError("Conflicting creation counters")
    counts = {
        "input_tokens": inputs,
        "output_tokens": outputs,
        "cached_tokens": hit,
        "cache_creation_input_tokens": created,
        "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get(
            "reasoning_tokens", 0
        ),
    }
    if any(type(n) is not int or n < 0 for n in counts.values()) or hit + created > inputs:
        raise ValueError("Invalid usage counters")
    if usage.get("total_tokens") != inputs + outputs:
        raise ValueError("Invalid usage total")
    if counts["reasoning_tokens"] > outputs:
        raise ValueError("Reasoning tokens must be included in completion tokens")
    cost = (
        (Decimal(inputs - hit - created) + Decimal(hit) / 10 + Decimal(created) * Decimal("1.25"))
        * Decimal(str(rates["input_per_million"]))
        + Decimal(outputs) * Decimal(str(rates["output_per_million"]))
    ) / 1_000_000
    return cost, counts


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Relay:
    """Own a bounded file-key pool and durable reservations for one host campaign.

    Args:
        config (dict[str, Any]): See README for the closed JSON configuration.
        root (Path): Private state directory; only one relay may own it.

    Call close after shutting down its HTTP server. Hosts receive only the local
    client token. Keys are read exclusively from explicit files, never environment.
    """

    def __init__(self, config: dict[str, Any], root: Path) -> None:
        self.config = copy.deepcopy(config)
        self.root = root.resolve()
        self._validate()
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.capacity = threading.BoundedSemaphore(config.get("max_concurrency", 100))
        self.in_flight = {}
        self._owner = file_lock(self.root / "relay.lock")
        self._owner.__enter__()
        try:
            self.rows = []
            path = self.root / "usage.jsonl"
            if path.exists():
                raw = path.read_bytes()
                if raw and not raw.endswith(b"\n"):
                    raise ValueError("Incomplete ledger; operator reconciliation required")
                self.rows = [json.loads(line) for line in raw.splitlines()]
            identity = digest(self.config)
            if self.rows and self.rows[0] != {"kind": "config", "digest": identity}:
                raise ValueError("Relay configuration changed; preserve and reconcile prior state")
            if not self.rows:
                self._append({"kind": "config", "digest": identity})
            pending = {r["id"] for r in self.rows if r["kind"] == "start"} - {
                r["id"] for r in self.rows if r["kind"] == "end"
            }
            for rid in sorted(pending):
                start = next(r for r in self.rows if r.get("id") == rid and r["kind"] == "start")
                self._transport_failure(rid, start["reserve"], "InterruptedProcess")
            self.client_token = Path(config["client_token_file"]).read_text().strip()
            if not self.client_token or "\n" in self.client_token or "\r" in self.client_token:
                raise ValueError("Invalid client token file")
            allowed = {
                line.split()[0]
                for line in Path(config["allowlist_file"]).read_text().splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            }
            if not allowed or any(
                len(s) != 64 or any(c not in "0123456789abcdef" for c in s) for s in allowed
            ):
                raise ValueError("Invalid SHA-256 allowlist")
            self.keys = [Path(path).read_text().strip() for path in config["key_files"]]
            hashes = [hashlib.sha256(key.encode()).hexdigest() for key in self.keys]
            if (
                not self.keys
                or len(set(hashes)) != len(hashes)
                or not set(hashes) <= allowed
                or any(not key or any(c.isspace() for c in key) for key in self.keys)
            ):
                raise ValueError("Key files must be distinct and allowlisted")
            state = self.root / "keys.json"
            self.key_state = (
                json.loads(state.read_text())
                if state.exists()
                else {"cursor": 0, "retired": [], "off": {}}
            )
            self.opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), _NoRedirect()
            )
        except BaseException:
            self._owner.__exit__(None, None, None)
            raise

    def _validate(self):
        required = {
            "endpoint",
            "models",
            "key_files",
            "allowlist_file",
            "client_token_file",
            "budget",
        }
        optional = {
            "timeout_seconds",
            "reset_hour",
            "reset_minute",
            "utc_offset_hours",
            "retry_after_seconds",
            "max_concurrency",
            "per_key_concurrency",
            "consecutive_failures",
            "failure_window",
            "failure_ratio",
        }
        if not required <= self.config.keys() or self.config.keys() - required - optional:
            raise ValueError("Unknown or missing relay configuration keys")
        endpoint = urllib.parse.urlsplit(self.config["endpoint"])
        local = endpoint.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            endpoint.scheme != "https"
            and not (endpoint.scheme == "http" and local)
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError("Endpoint requires HTTPS (HTTP only for loopback fake upstream)")

        def positive(value):
            return type(value) in (int, float) and math.isfinite(value) and value > 0

        if not positive(self.config["budget"]) or not self.config["models"]:
            raise ValueError("Positive budget and model policies required")
        for policy in self.config["models"].values():
            required_policy = {
                "served",
                "input_per_million",
                "output_per_million",
                "input_tokens",
                "output_tokens",
            }
            optional_policy = {"request_model", "thinking", "reasoning_effort", "request_bytes"}
            if (
                not required_policy <= policy.keys()
                or policy.keys() - required_policy - optional_policy
            ):
                raise ValueError("Unknown or missing model policy")
            if "request_bytes" in policy and (
                type(policy["request_bytes"]) is not int or policy["request_bytes"] < 1
            ):
                raise ValueError("request_bytes must be a positive JSON byte bound")
            if "request_model" in policy and (
                not isinstance(policy["request_model"], str) or not policy["request_model"]
            ):
                raise ValueError("Invalid request model")
            if "thinking" in policy and policy["thinking"] not in (
                {"type": "enabled"},
                {"type": "disabled"},
            ):
                raise ValueError("Invalid route thinking policy")
            if "reasoning_effort" in policy and policy["reasoning_effort"] not in (
                "none",
                "low",
                "high",
                "max",
            ):
                raise ValueError("Invalid route reasoning effort")
            if (
                not isinstance(policy["served"], str)
                or not policy["served"]
                or any(not positive(policy[k]) for k in ("input_per_million", "output_per_million"))
                or any(
                    type(policy[k]) is not int or policy[k] <= 0
                    for k in ("input_tokens", "output_tokens")
                )
            ):
                raise ValueError("Invalid model policy")
        if not positive(self.config.get("timeout_seconds", 1800)):
            raise ValueError("timeout_seconds must be positive")
        for key, low, high, default in (
            ("reset_hour", 0, 23, 0),
            ("reset_minute", 0, 59, 5),
            ("utc_offset_hours", -23, 23, 8),
        ):
            value = self.config.get(key, default)
            if type(value) is not int or not low <= value <= high:
                raise ValueError("Invalid reset clock")
        if not positive(self.config.get("retry_after_seconds", 20)):
            raise ValueError("retry_after_seconds must be positive")
        for key, default in (
            ("max_concurrency", 100),
            ("per_key_concurrency", 70),
            ("consecutive_failures", 3),
            ("failure_window", 50),
        ):
            if type(self.config.get(key, default)) is not int or self.config.get(key, default) <= 0:
                raise ValueError("Concurrency and circuit counts must be positive integers")
        if (
            not positive(self.config.get("failure_ratio", 0.1))
            or self.config.get("failure_ratio", 0.1) > 1
        ):
            raise ValueError("failure_ratio must be in (0, 1]")

    def close(self) -> None:
        """Release exclusive state ownership after all requests finish.

        Args:
            None.

        Returns:
            None: Releases the process lock.
        """
        self._owner.__exit__(None, None, None)

    def _append(self, row):
        with self.lock:
            with (self.root / "usage.jsonl").open("ab") as stream:
                stream.write(canonical(row) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            self.rows.append(row)
            if row["kind"] == "end":
                self._check_circuit()

    def _transport_failure(self, rid, reserve, error_class, counts=None, known_cost=None):
        with self.lock:
            charge = max(Decimal(str(reserve)), known_cost or Decimal(0))
            self._append(
                {
                    "kind": "end",
                    "id": rid,
                    "cost": str(charge),
                    "settlement": "upper_reservation",
                    "error_class": error_class,
                    "transport_failure": True,
                    **(counts or {}),
                }
            )
            if charge > Decimal(str(reserve)) or self._committed() > Decimal(
                str(self.config["budget"])
            ):
                self._stop("accounted_reservation_overrun")

    def _check_circuit(self):
        with self.lock:
            reset = max(
                (i for i, r in enumerate(self.rows) if r["kind"] == "circuit_reset"), default=-1
            )
            ends = [r for r in self.rows[reset + 1 :] if r["kind"] == "end"]
            consecutive = 0
            for row in reversed(ends):
                if not row.get("transport_failure"):
                    break
                consecutive += 1
            window = self.config.get("failure_window", 50)
            recent = ends[-window:]
            if (
                consecutive >= self.config.get("consecutive_failures", 3)
                or len(recent) == window
                and sum(bool(r.get("transport_failure")) for r in recent) / window
                > self.config.get("failure_ratio", 0.1)
            ):
                self._stop("transport_circuit_breaker")

    def reset_circuit(self, *, reason: str, probe_successes: int, probe_time: float) -> dict:
        """Operator-only resume after settled requests and fresh successful probes.

        Preserves all charges and thresholds. Unknown-usage, identity and budget
        stops cannot be reset through this path. No public MCP tool exposes it.
        """
        with self.condition:
            stop = self.root / "STOP.json"
            if (
                not reason.strip()
                or not stop.exists()
                or json.loads(stop.read_text()).get("reason") != "transport_circuit_breaker"
            ):
                raise ValueError("Only an explained transport circuit stop may be reset")
            starts = {r["id"] for r in self.rows if r["kind"] == "start"}
            ends = {r["id"]: r for r in self.rows if r["kind"] == "end"}
            recovery_preconditions(
                active_requests=sum(self.in_flight.values()),
                pending_requests=len(starts - ends.keys()),
                accounting_reconciled=all(r.get("cost") is not None for r in ends.values()),
                probe_successes=probe_successes,
                probe_time=probe_time,
            )
            row = {
                "kind": "circuit_reset",
                "reason": reason,
                "time": time.time(),
                "probe_successes": probe_successes,
                "probe_time": probe_time,
                "committed": str(self._committed()),
                "thresholds_unchanged": True,
            }
            self._append(row)
            stop.unlink()
            return row

    @contextmanager
    def _reservation(self, model, reserve):
        with self.condition:
            while True:
                if (self.root / "STOP.json").exists():
                    raise ValueError("Relay stopped; operator reconciliation required")
                if self._committed() + reserve > Decimal(str(self.config["budget"])):
                    raise ValueError("Insufficient budget for uncached upper bound")
                index = self._key()
                if self.in_flight.get(index, 0) < self.config.get("per_key_concurrency", 70):
                    break
                self.condition.wait(0.05)
            self.in_flight[index] = self.in_flight.get(index, 0) + 1
            rid = uuid.uuid4().hex
            self._append(
                {
                    "kind": "start",
                    "id": rid,
                    "model": model,
                    "reserve": str(reserve),
                    "time": time.time(),
                }
            )
        try:
            yield index, rid
        finally:
            with self.condition:
                self.in_flight[index] -= 1
                self.condition.notify_all()

    def _stop(self, reason):
        if not (self.root / "STOP.json").exists():
            atomic_json(self.root / "STOP.json", {"reason": reason, "time": time.time()})

    def _committed(self):
        ends = {r["id"]: r for r in self.rows if r["kind"] == "end"}
        return sum(
            (
                Decimal(ends[r["id"]]["cost"])
                if r["id"] in ends and ends[r["id"]].get("cost") is not None
                else Decimal(r["reserve"])
            )
            for r in self.rows
            if r["kind"] == "start"
        )

    def _save_keys(self):
        atomic_json(self.root / "keys.json", self.key_state)

    def _reset(self):
        zone = timezone(timedelta(hours=self.config.get("utc_offset_hours", 8)))
        now = datetime.now(zone)
        reset = now.replace(
            hour=self.config.get("reset_hour", 0),
            minute=self.config.get("reset_minute", 5),
            second=0,
            microsecond=0,
        )
        return (reset if reset > now else reset + timedelta(days=1)).timestamp()

    def _key(self):
        for _ in self.keys:
            index = self.key_state["cursor"] % len(self.keys)
            self.key_state["cursor"] += 1
            if (
                index not in self.key_state["retired"]
                and self.key_state["off"].get(str(index), 0) <= time.time()
            ):
                self._save_keys()
                return index
        self._save_keys()
        raise ValueError("No usable allowlisted key; wait for reset or Retry-After")

    def transact(self, body: dict[str, Any], *, emit: Any = None) -> tuple[bytes, str]:
        """Reserve, forward once, settle usage and optionally emit streaming bytes.

        Args:
            body (dict[str, Any]): Chat Completions request; model must be configured.
            emit (Any): Optional callback receiving bytes during SSE delivery.
                A disconnect does not stop draining upstream accounting.

        Returns:
            tuple[bytes, str]: Buffered JSON or SSE bytes (empty when emitted), MIME type.

        Raises:
            ValueError: Admission failure or sanitized failure. Transport failures
                settle at the upper bound; unknown response usage stops requests.
        """
        with self.capacity:
            if (self.root / "STOP.json").exists():
                raise ValueError("Relay stopped; operator reconciliation required")
            model = body.get("model")
            policy = self.config["models"].get(model)
            if policy is None or body.get("n", 1) != 1:
                raise ValueError("Unsupported model or completion count")
            wire = copy.deepcopy(body)
            wire["model"] = policy.get("request_model", model)
            for field in ("thinking", "reasoning_effort"):
                if field in policy:
                    wire[field] = copy.deepcopy(policy[field])
            if "thinking" in policy:
                wire.pop("enable_thinking", None)
            wire = cache_hints(wire)
            requested = wire.get(
                "max_completion_tokens", wire.get("max_tokens", policy["output_tokens"])
            )
            if type(requested) is not int or not 0 < requested <= policy["output_tokens"]:
                raise ValueError("Invalid output token bound")
            if "max_tokens" in wire and "max_completion_tokens" in wire:
                raise ValueError("Use one output limit field")
            if "max_tokens" not in wire and "max_completion_tokens" not in wire:
                wire["max_completion_tokens"] = requested
            streaming = wire.get("stream", False)
            if type(streaming) is not bool:
                raise ValueError("stream must be boolean")
            if streaming:
                wire["stream_options"] = {**wire.get("stream_options", {}), "include_usage": True}
            payload = canonical(wire)
            if len(payload) > policy.get("request_bytes", 4 * policy["input_tokens"] + 8192):
                raise ValueError("Request exceeds JSON byte admission bound")
            reserve = (
                Decimal(max(policy["input_tokens"], len(payload) + 8192))
                * Decimal(str(policy["input_per_million"]))
                + Decimal(policy["output_tokens"]) * Decimal(str(policy["output_per_million"]))
            ) / 1_000_000
            for _ in self.keys:
                with self._reservation(model, reserve) as (index, rid):
                    request = urllib.request.Request(
                        self.config["endpoint"],
                        data=payload,
                        headers={
                            "Authorization": "Bearer " + self.keys[index],
                            "Content-Type": "application/json",
                        },
                    )
                    try:
                        response = self.opener.open(
                            request, timeout=self.config.get("timeout_seconds", 1800)
                        )
                    except urllib.error.HTTPError as error:
                        with self.lock, error:
                            status = error.code
                            self._append(
                                {
                                    "kind": "end",
                                    "id": rid,
                                    "status": status,
                                    "cost": "0" if status in (401, 403, 429) else None,
                                }
                            )
                            if status == 401:
                                self.key_state["retired"].append(index)
                            elif status == 403:
                                self.key_state["off"][str(index)] = max(
                                    self.key_state["off"].get(str(index), 0), self._reset()
                                )
                            elif status == 429:
                                retry = error.headers.get("Retry-After")
                                try:
                                    delay = float(retry)
                                except (ValueError, TypeError):
                                    try:
                                        delay = (
                                            parsedate_to_datetime(retry).timestamp() - time.time()
                                        )
                                    except (ValueError, TypeError, AttributeError, OverflowError):
                                        delay = self.config.get("retry_after_seconds", 20)
                                if not math.isfinite(delay):
                                    delay = self.config.get("retry_after_seconds", 20)
                                self.key_state["off"][str(index)] = max(
                                    self.key_state["off"].get(str(index), 0),
                                    time.time() + max(1, delay),
                                )
                            else:
                                self._stop("unknown_usage")
                                raise ValueError("Upstream failure; unknown usage") from None
                            self._save_keys()
                            continue
                    except Exception as error:
                        self._transport_failure(rid, reserve, type(error).__name__)
                        raise ValueError(
                            "Transport failure; settled at upper reservation"
                        ) from None
                    usage = None
                    served = set()
                    raw = bytearray()
                    terminal = bytearray()
                    disconnected = False
                    done = not streaming
                    try:
                        with response:
                            if response.status != 200:
                                raise ValueError
                            if streaming:
                                for line in response:
                                    if done or (
                                        line.startswith(b"data:") and line[5:].strip() == b"[DONE]"
                                    ):
                                        terminal.extend(line)
                                    elif emit is not None and not disconnected:
                                        try:
                                            emit(line)
                                        except (ConnectionError, OSError):
                                            disconnected = True
                                    elif emit is None:
                                        raw.extend(line)
                                    if line.startswith(b"data:"):
                                        data = line[5:].strip()
                                        if data == b"[DONE]":
                                            done = True
                                        elif data:
                                            chunk = json.loads(data)
                                            if chunk.get("model"):
                                                served.add(chunk["model"])
                                            if chunk.get("usage") is not None:
                                                usage = chunk["usage"]
                            else:
                                raw.extend(response.read())
                                chunk = json.loads(raw)
                                served.add(chunk.get("model"))
                                usage = chunk.get("usage")
                        try:
                            cost, counts = usage_cost(usage, policy)
                        except (ValueError, KeyError, TypeError, AttributeError):
                            if done:
                                raise
                            # EOF before [DONE] is a transport failure even when
                            # the final usage chunk never arrived.
                            cost, counts = None, None
                    except (OSError, http.client.HTTPException) as error:
                        try:
                            known_cost, known_counts = usage_cost(usage, policy)
                        except (ValueError, KeyError, TypeError, AttributeError):
                            known_cost, known_counts = None, None
                        self._transport_failure(
                            rid, reserve, type(error).__name__, known_counts, known_cost
                        )
                        raise ValueError(
                            "Transport failure; settled at upper reservation"
                        ) from None
                    except Exception:
                        self._stop("unknown_usage")
                        raise ValueError("Invalid response; unknown usage") from None
                    if not done:
                        self._transport_failure(rid, reserve, "IncompleteStream", counts, cost)
                        raise ValueError("Transport failure; settled at upper reservation")
                    # Persist known usage before identity or envelope checks.
                    self._append(
                        {
                            "kind": "end",
                            "id": rid,
                            "status": 200,
                            "cost": str(cost),
                            "served": policy["served"] if served == {policy["served"]} else None,
                            **counts,
                        }
                    )
                    if cost > reserve or self._committed() > Decimal(str(self.config["budget"])):
                        self._stop("accounted_reservation_overrun")
                        raise ValueError("Accounted reservation overrun")
                    if served != {policy["served"]}:
                        self._stop("invalid_response")
                        raise ValueError("Response identity or stream completion failed")
                    # A host may treat [DONE] as success immediately. Release it
                    # only after identity, complete usage and money checks pass.
                    if emit is None:
                        raw.extend(terminal)
                    elif terminal and not disconnected:
                        try:
                            emit(bytes(terminal))
                        except (ConnectionError, OSError):
                            pass
                    return bytes(raw), "text/event-stream" if streaming else "application/json"
            raise ValueError("Key pool unavailable after bounded rotation")

    def server(self, port: int = 0) -> ThreadingHTTPServer:
        """Create a loopback-only authenticated HTTP server; caller owns its thread.

        Args:
            port (int): Local port, zero chooses an available port.

        Returns:
            ThreadingHTTPServer: Serve with serve_forever, then shutdown/server_close.
        """
        relay = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                import hmac

                if self.path != "/v1/chat/completions":
                    self.send_error(404)
                    return
                if not hmac.compare_digest(
                    self.headers.get("Authorization", ""), "Bearer " + relay.client_token
                ):
                    self.send_error(401)
                    return
                headers_sent = False

                def emit(line):
                    nonlocal headers_sent
                    if not headers_sent:
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Connection", "close")
                        self.end_headers()
                        headers_sent = True
                    self.wfile.write(line)
                    self.wfile.flush()

                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if (
                        not 0
                        < length
                        <= max(
                            p.get("request_bytes", 4 * p["input_tokens"] + 8192)
                            for p in relay.config["models"].values()
                        )
                    ):
                        raise ValueError
                    body = json.loads(self.rfile.read(length))
                    raw, content_type = relay.transact(
                        body, emit=emit if body.get("stream") else None
                    )
                    if headers_sent:
                        return
                    self.send_response(200)
                except Exception:
                    if headers_sent:
                        # OpenAI clients can accept normal EOF after a finish
                        # reason. Make relay rejection explicit on the SSE wire.
                        try:
                            self.wfile.write(
                                b'data: {"error":{"type":"relay_refusal","message":'
                                b'"Request refused; inspect sanitized relay state"}}\n\n'
                            )
                            self.wfile.flush()
                        except (ConnectionError, OSError):
                            pass
                        self.close_connection = True
                        return
                    raw = b'{"error":{"type":"relay_refusal","message":"Request refused; inspect sanitized relay state"}}'
                    content_type = "application/json"
                    self.send_response(503)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (ConnectionError, OSError):
                    pass

        return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main() -> None:
    """Run the optional relay using explicit config/state paths.

    Args:
        None: Reads CLI flags.

    Returns:
        None: Serves until interrupted; never prints key data or upstream errors.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--port", type=int, default=29872)
    args = parser.parse_args()
    relay = Relay(json.loads(args.config.read_text()), args.state)
    server = relay.server(args.port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        relay.close()


if __name__ == "__main__":
    main()
