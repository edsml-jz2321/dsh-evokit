"""All relay transport tests use task-owned fake keys and a loopback upstream."""

import copy
import hashlib
import importlib.util
import json
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "generic_relay", Path(__file__).resolve().parents[1] / "integrations/relay/relay.py"
)
relay_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(relay_module)


def response(usage=True, model="qwen-test"):
    value = {"model": model, "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
    if usage:
        value["usage"] = {
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "total_tokens": 110,
            "prompt_tokens_details": {"cached_tokens": 20},
            "cache_creation_input_tokens": 40,
        }
    return value


@pytest.fixture
def upstream():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.server.calls.append((body, self.headers["Authorization"]))
            if hasattr(self.server, "barrier"):
                self.server.barrier.wait(timeout=10)
                self.server.release.wait(timeout=10)
            status, headers, raw = self.server.responses.pop(0)
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.calls = []
    server.responses = []
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield server
    server.shutdown()
    server.server_close()
    worker.join()


@pytest.fixture
def setup(tmp_path, upstream):
    paths = []
    hashes = []
    for i in range(3):
        key = f"fake-key-only-{i}"
        path = tmp_path / f"{i}.key"
        path.write_text(key)
        paths.append(str(path))
        hashes.append(hashlib.sha256(key.encode()).hexdigest())
    allow = tmp_path / "allowlist.sha256"
    allow.write_text("\n".join(hashes))
    token = tmp_path / "client.token"
    token.write_text("fake-local-token")
    config = {
        "endpoint": f"http://127.0.0.1:{upstream.server_port}/v1/chat/completions",
        "models": {
            "qwen-test": {
                "served": "qwen-test",
                "input_per_million": 10,
                "output_per_million": 20,
                "input_tokens": 20000,
                "output_tokens": 100,
            }
        },
        "key_files": paths,
        "allowlist_file": str(allow),
        "client_token_file": str(token),
        "budget": 1,
    }
    return config, tmp_path / "state"


def body(stream=False):
    return {
        "model": "qwen-test",
        "messages": [
            {"role": "system", "content": "policy"},
            {"role": "user", "content": "question"},
        ],
        "stream": stream,
        "max_completion_tokens": 100,
    }


def test_json_byte_admission_is_separate_from_client_token_context(setup, upstream):
    config, root = setup
    policy = config["models"]["qwen-test"]
    policy.update(
        input_tokens=524288, request_bytes=1048576, input_per_million=1, output_per_million=1
    )
    config["budget"] = 10
    relay = relay_module.Relay(config, root)
    server = relay.server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    request = body()
    request["messages"][-1]["content"] = "a" * 575802
    try:
        ok(upstream)
        data = json.dumps(request).encode()
        call = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
            data=data,
            headers={
                "Authorization": "Bearer fake-local-token",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(call) as response:
            assert response.status == 200
        start = next(r for r in relay.rows if r["kind"] == "start")
        assert Decimal(start["reserve"]) > Decimal(".575802")
        before = len(upstream.calls)
        request["messages"][-1]["content"] = "x" * 1048576
        with pytest.raises(ValueError, match="JSON byte"):
            relay.transact(request)
        assert len(upstream.calls) == before
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        relay.close()


def test_operator_circuit_reset_keeps_costs_and_refuses_unknown_usage(setup, monkeypatch):
    config, root = setup
    relay = relay_module.Relay(config, root)

    def disconnected(*args, **kwargs):
        raise ConnectionResetError("fake transport")

    monkeypatch.setattr(relay.opener, "open", disconnected)
    try:
        for _ in range(3):
            with pytest.raises(ValueError, match="Transport failure"):
                relay.transact(body())
        before = relay._committed()
        assert (root / "STOP.json").exists()
        with pytest.raises(ValueError):
            relay.reset_circuit(reason="verified", probe_successes=1, probe_time=0)
        record = relay.reset_circuit(
            reason="accounted and direct probes passed",
            probe_successes=1,
            probe_time=__import__("time").time(),
        )
        assert relay._committed() == before and record["thresholds_unchanged"]
        with pytest.raises(ValueError, match="Transport failure"):
            relay.transact(body())
        assert not (root / "STOP.json").exists()
        relay._stop("unknown_usage")
        with pytest.raises(ValueError):
            relay.reset_circuit(
                reason="verified", probe_successes=1, probe_time=__import__("time").time()
            )
    finally:
        relay.close()


def ok(server, value=None):
    server.responses.append((200, {}, json.dumps(response() if value is None else value).encode()))


def test_cache_wire_deep_copy_and_non_qwen():
    request = body()
    request["messages"] += [
        {
            "role": "assistant",
            "content": [{"type": "text", "text": str(i), "cache_control": {"type": "ephemeral"}}],
        }
        for i in range(8)
    ]
    request["messages"].append({"role": "tool", "content": None})
    request["tools"] = [{"type": "function", "cache_control": {"type": "ephemeral"}}]
    original = copy.deepcopy(request)
    wire = relay_module.cache_hints(request)
    assert request == original
    parts = [p for m in wire["messages"] for p in m.get("content") or []]
    assert sum("cache_control" in p for p in parts) == 4
    assert "cache_control" not in wire["tools"][0]
    assert wire["messages"][0]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert wire["messages"][-2]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    request["model"] = "deepseek-test"
    assert "cache_control" not in json.dumps(relay_module.cache_hints(request))
    assert "cache_control" in json.dumps(request)
    assert relay_module.cache_hints(request) is not request


def test_cache_cost_exact_and_invalid_counts():
    cost, counts = relay_module.usage_cost(
        response()["usage"], {"input_per_million": 10, "output_per_million": 20}
    )
    assert cost == Decimal("0.00112")  # 40 ordinary + 40*1.25 + 20*.1, plus output
    assert counts["cached_tokens"] == 20
    for edit in (
        {"prompt_tokens": True},
        {"total_tokens": 109},
        {"cache_creation_input_tokens": 90},
    ):
        with pytest.raises(ValueError):
            relay_module.usage_cost(
                {**response()["usage"], **edit}, {"input_per_million": 10, "output_per_million": 20}
            )


def test_role_routes_isolate_thinking_and_price_reasoning(setup, upstream):
    config, root = setup
    target = {
        "served": "deepseek-v4-1-flash-261017",
        "input_per_million": 2,
        "output_per_million": 8,
        "input_tokens": 128000,
        "output_tokens": 2048,
    }
    config["models"] = {
        "deepseek-v4.1-flash": target,
        "deepseek-improver": {
            **target,
            "request_model": "deepseek-v4.1-flash",
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
            "output_tokens": 32768,
        },
    }
    relay = relay_module.Relay(config, root)
    try:
        for model in config["models"]:
            request = {
                **body(),
                "model": model,
                "thinking": {"type": "disabled"},
                "temperature": 0,
                "messages": [
                    {"role": "user", "content": "question", "cache_control": {"type": "ephemeral"}}
                ],
            }
            value = response(model=target["served"])
            value["usage"] = {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "total_tokens": 110,
                "completion_tokens_details": {"reasoning_tokens": 8},
            }
            ok(upstream, value)
            before = copy.deepcopy(request)
            relay.transact(request)
            assert request == before
        target_wire, improver_wire = [call[0] for call in upstream.calls]
        assert target_wire["thinking"] == {"type": "disabled"}
        assert target_wire["temperature"] == 0
        assert "reasoning_effort" not in target_wire
        assert improver_wire["thinking"] == {"type": "enabled"}
        assert improver_wire["reasoning_effort"] == "high"
        assert improver_wire["model"] == target_wire["model"] == "deepseek-v4.1-flash"
        assert "cache_control" not in json.dumps(upstream.calls)
        starts = [r for r in relay.rows if r["kind"] == "start"]
        assert Decimal(starts[1]["reserve"]) == Decimal(".518144")
        assert relay.rows[-1]["reasoning_tokens"] == 8
        assert relay.rows[-1]["cost"] == "0.00028"
        assert relay._committed() == Decimal(".00056")
    finally:
        relay.close()


def test_reasoning_counters_are_completion_subsets():
    usage = response()["usage"]
    for invalid in (-1, True, 11):
        usage["completion_tokens_details"] = {"reasoning_tokens": invalid}
        with pytest.raises(ValueError):
            relay_module.usage_cost(usage, {"input_per_million": 2, "output_per_million": 8})


def test_pass_through_accounting_and_safe_diagnostics(setup, upstream):
    config, root = setup
    relay = relay_module.Relay(config, root)
    try:
        original = body()
        ok(upstream)
        raw, mime = relay.transact(original)
        assert json.loads(raw) == response() and mime == "application/json"
        assert original["messages"][0]["content"] == "policy"
        assert upstream.calls[0][0]["messages"][0]["content"][0]["cache_control"] == {
            "type": "ephemeral"
        }
        assert relay._committed() == Decimal("0.00112")
        start = next(r for r in relay.rows if r["kind"] == "start")
        assert Decimal(start["reserve"]) == Decimal("0.202")
        assert not (root / "STOP.json").exists()
        saved = "".join(p.read_text() for p in root.glob("*.json*"))
        assert all(
            secret not in saved
            for secret in ("fake-key-only", "policy", "question", "fake-local-token")
        )
    finally:
        relay.close()


@pytest.mark.parametrize("status", [401, 403, 429])
def test_key_rotation_state_and_retry_after(setup, upstream, status):
    config, root = setup
    relay = relay_module.Relay(config, root)
    try:
        upstream.responses.append(
            (status, {"Retry-After": "120"}, b"fake-key-only echoed credential")
        )
        ok(upstream)
        relay.transact(body())
        assert len(upstream.calls) == 2
        assert upstream.calls[0][1] != upstream.calls[1][1]
        if status == 401:
            assert relay.key_state["retired"] == [0]
        else:
            import time

            assert relay.key_state["off"]["0"] > time.time() + (100 if status == 429 else 0)
        assert "echoed" not in (root / "usage.jsonl").read_text()
    finally:
        relay.close()
    reopened = relay_module.Relay(config, root)
    assert reopened.key_state["retired"] == ([0] if status == 401 else [])
    reopened.close()


@pytest.mark.parametrize("failure", ["missing", "500", "redirect", "malformed"])
def test_unknown_usage_stops_and_never_retries(setup, upstream, failure):
    config, root = setup
    relay = relay_module.Relay(config, root)
    try:
        if failure == "missing":
            ok(upstream, response(usage=False))
        elif failure == "malformed":
            upstream.responses.append((200, {}, b'{"secret":"fake-key-only-0"'))
        else:
            upstream.responses.append(
                (
                    500 if failure == "500" else 307,
                    {"Location": "http://127.0.0.1:1/stolen"},
                    b"secret",
                )
            )
        with pytest.raises(ValueError, match="unknown usage"):
            relay.transact(body())
        assert relay._committed() == Decimal("0.202")
        with pytest.raises(ValueError, match="stopped"):
            relay.transact(body())
        assert len(upstream.calls) == 1
    finally:
        relay.close()
    reopened = relay_module.Relay(config, root)
    assert reopened._committed() == Decimal("0.202")
    reopened.close()


def test_restart_pending_and_budget_admission(setup, upstream):
    config, root = setup
    relay = relay_module.Relay(config, root)
    relay._append({"kind": "start", "id": "interrupted", "reserve": "0.202"})
    relay.close()
    reopened = relay_module.Relay(config, root)
    try:
        assert reopened._committed() == Decimal("0.202")
        assert reopened.rows[-1]["error_class"] == "InterruptedProcess"
        ok(upstream)
        reopened.transact(body())
    finally:
        reopened.close()
    config["budget"] = 0.1
    small = relay_module.Relay(config, root.parent / "small")
    try:
        with pytest.raises(ValueError, match="budget"):
            small.transact(body())
        assert len(upstream.calls) == 1
    finally:
        small.close()


def test_known_identity_failure_settles_before_stop(setup, upstream):
    config, root = setup
    relay = relay_module.Relay(config, root)
    try:
        ok(upstream, response(model="wrong-secret-echo"))
        with pytest.raises(ValueError, match="identity"):
            relay.transact(body())
        assert relay._committed() == Decimal("0.00112")
        assert "wrong-secret-echo" not in (root / "usage.jsonl").read_text()
    finally:
        relay.close()


def test_stream_forwarding_with_usage(setup, upstream):
    config, root = setup
    chunks = [
        response(usage=False),
        {"model": "qwen-test", "choices": [], "usage": response()["usage"]},
    ]
    raw = (
        b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks) + b"data: [DONE]\n\n"
    )
    upstream.responses.append((200, {"Content-Type": "text/event-stream"}, raw))
    relay = relay_module.Relay(config, root)
    emitted = []
    try:
        result, mime = relay.transact(body(True), emit=emitted.append)
        assert result == b"" and b"".join(emitted) == raw and mime == "text/event-stream"
        assert upstream.calls[0][0]["stream_options"]["include_usage"]
        assert relay._committed() == Decimal("0.00112")
    finally:
        relay.close()


def test_stream_identity_mismatch_withholds_completion_marker(setup, upstream):
    config, root = setup
    raw = b"data: " + json.dumps(response(model="wrong-served-build")).encode()
    raw += b"\n\ndata: [DONE]\n\n"
    upstream.responses.append((200, {"Content-Type": "text/event-stream"}, raw))
    relay = relay_module.Relay(config, root)
    emitted = []
    try:
        with pytest.raises(ValueError, match="identity"):
            relay.transact(body(True), emit=emitted.append)
        assert b"[DONE]" not in b"".join(emitted)
        assert relay._committed() == Decimal(".00112")
        assert (root / "STOP.json").exists()
    finally:
        relay.close()


@pytest.mark.parametrize("failure", ["identity", "usage", "overrun"])
def test_http_stream_refusal_is_an_explicit_client_error(setup, upstream, failure):
    config, root = setup
    value = response(model="wrong-served-build" if failure == "identity" else "qwen-test")
    expected = Decimal(".00112")
    if failure == "usage":
        value.pop("usage")
        expected = Decimal(".202")
    elif failure == "overrun":
        value["usage"].update(completion_tokens=20000, total_tokens=20100)
        expected = Decimal(".40092")
    raw = b"data: " + json.dumps(value).encode()
    raw += b"\n\ndata: [DONE]\n\n"
    upstream.responses.append((200, {"Content-Type": "text/event-stream"}, raw))
    relay = relay_module.Relay(config, root)
    server = relay.server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
            data=json.dumps(body(True)).encode(),
            headers={"Authorization": "Bearer fake-local-token"},
        )
        with opener.open(request) as response_stream:
            chunks = response_stream.read()
        assert b"[DONE]" not in chunks
        final = json.loads(chunks.split(b"data: ")[-1])
        assert final["error"]["type"] == "relay_refusal"
        assert relay._committed() == expected
        assert (root / "STOP.json").exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        relay.close()


@pytest.mark.parametrize("with_usage", [True, False])
def test_incomplete_stream_is_upper_settled_then_new_work_continues(setup, upstream, with_usage):
    config, root = setup
    raw = b"data: " + json.dumps(response(usage=with_usage)).encode() + b"\n\n"
    upstream.responses.append((200, {"Content-Type": "text/event-stream"}, raw))
    relay = relay_module.Relay(config, root)
    try:
        with pytest.raises(ValueError, match="settled at upper"):
            relay.transact(body(True))
        assert relay.rows[-1]["error_class"] == "IncompleteStream"
        assert relay._committed() == Decimal(".202")
        assert not (root / "STOP.json").exists()
        ok(upstream)
        relay.transact(body())
        assert len(upstream.calls) == 2
    finally:
        relay.close()


def test_client_disconnect_drains_known_usage_without_stopping_campaign(setup, upstream):
    config, root = setup
    raw = b"data: " + json.dumps(response()).encode() + b"\n\ndata: [DONE]\n\n"
    upstream.responses.append((200, {"Content-Type": "text/event-stream"}, raw))
    relay = relay_module.Relay(config, root)

    def disconnected(line):
        raise BrokenPipeError

    try:
        relay.transact(body(True), emit=disconnected)
        assert relay._committed() == Decimal(".00112")
        assert not (root / "STOP.json").exists()
    finally:
        relay.close()


def test_incomplete_stream_preserves_known_cost_above_reservation(setup, upstream):
    config, root = setup
    value = response()
    value["usage"] = {
        "prompt_tokens": 20000,
        "completion_tokens": 100,
        "total_tokens": 20100,
        "cache_creation_input_tokens": 20000,
    }
    upstream.responses.append((200, {}, b"data: " + json.dumps(value).encode() + b"\n\n"))
    relay = relay_module.Relay(config, root)
    try:
        with pytest.raises(ValueError, match="settled at upper"):
            relay.transact(body(True))
        assert relay._committed() == Decimal(".252")
        assert (root / "STOP.json").exists()
    finally:
        relay.close()


def test_http_authentication_and_sanitized_errors(setup, upstream):
    config, root = setup
    relay = relay_module.Relay(config, root)
    server = relay.server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        for token, expected in (("wrong", 401), ("fake-local-token", 503)):
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
                data=b'{"model":"fake-key-only-0"}',
                headers={"Authorization": "Bearer " + token},
            )
            with pytest.raises(urllib.error.HTTPError) as error:
                opener.open(request)
            assert error.value.code == expected
            assert b"fake-key-only" not in error.value.read()
        assert not upstream.calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        relay.close()


def test_allowlist_rejects_before_network(setup, upstream):
    config, root = setup
    Path(config["key_files"][0]).write_text("not-allowed-fake-key")
    with pytest.raises(ValueError, match="allowlisted"):
        relay_module.Relay(config, root)
    assert not upstream.calls


def test_transport_upper_settlement_and_circuit_breaker(setup, upstream):
    config, root = setup
    relay = relay_module.Relay(config, root)

    class Broken:
        def open(self, *args, **kwargs):
            raise TimeoutError("fake-key-only-0 must never enter diagnostics")

    relay.opener = Broken()
    try:
        for i in range(3):
            with pytest.raises(ValueError, match="settled at upper"):
                relay.transact(body())
            assert (root / "STOP.json").exists() == (i == 2)
        assert relay._committed() == Decimal("0.606")
        ends = [r for r in relay.rows if r["kind"] == "end"]
        assert all(r["error_class"] == "TimeoutError" for r in ends)
        assert len({r["id"] for r in ends}) == 3
        assert "fake-key-only" not in (root / "usage.jsonl").read_text()
        with pytest.raises(ValueError, match="stopped"):
            relay.transact(body())
    finally:
        relay.close()


def test_rolling_circuit_breaker(setup):
    config, root = setup
    relay = relay_module.Relay(config, root)
    try:
        for i in range(50):
            rid = str(i)
            relay._append({"kind": "start", "id": rid, "reserve": "0.001"})
            if i % 8 == 0:
                relay._transport_failure(rid, ".001", "TimeoutError")
            else:
                relay._append({"kind": "end", "id": rid, "cost": "0"})
        # The final window crosses the ratio threshold on a successful request,
        # too: the breaker is evaluated after every completed attempt.
        relay._check_circuit()
        assert (root / "STOP.json").exists()
    finally:
        relay.close()


def test_concurrent_reservations_and_key_state_survive_restart(setup, upstream):
    config, root = setup
    config["per_key_concurrency"] = 1
    relay = relay_module.Relay(config, root)
    upstream.barrier = threading.Barrier(3)
    upstream.release = threading.Event()
    upstream.responses.extend([(401, {}, b"secret"), (403, {}, b"secret")])
    # Retire/off requests must not rotate into an extra unsynchronized request.
    # Disable remaining keys before releasing the two in-flight responses.
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(relay.transact, body()) for _ in range(2)]
        upstream.barrier.wait(timeout=10)
        assert relay._committed() == Decimal(".404")
        assert len({auth for _, auth in upstream.calls}) == 2
        with relay.lock:
            relay.key_state["retired"].append(2)
            relay._save_keys()
        upstream.release.set()
        for future in futures:
            with pytest.raises(ValueError, match="key|Key"):
                future.result(timeout=10)
    retired = set(relay.key_state["retired"])
    off = dict(relay.key_state["off"])
    assert 2 in retired and len(retired) == 2 and len(off) == 1
    relay.close()
    reopened = relay_module.Relay(config, root)
    try:
        assert set(reopened.key_state["retired"]) == retired
        assert reopened.key_state["off"] == off
        assert reopened._committed() == 0
    finally:
        reopened.close()
