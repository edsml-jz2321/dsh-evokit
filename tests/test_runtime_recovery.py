"""Zero-provider regressions for generic G4 execution fixes."""

import ctypes
import hashlib
import json
import os
import threading
import time
from pathlib import Path

import pytest

from evokit import Result
from evokit.dsh import client_limits
from evokit.responses import IncompleteResponse, ResponseCache, ResponseValidationError
from evokit.runtime import (
    RecoveryAccounting,
    TransportCircuit,
    publish_with_retry,
    watch_updates,
)
from evokit.storage import atomic_json


def test_native_client_has_verified_long_context_and_bounded_output():
    import yaml

    patch = Path(__file__).resolve().parents[1] / "integrations/dsh/improver.patch.yml"
    model = yaml.safe_load(patch.read_text().replace("!!js ", ""))[0]["config"]["providers"][
        "evokit-improver"
    ]["models"][0]
    assert client_limits(context_tokens=524288, output_tokens=32768) == {
        k: model[k] for k in ("contextWindow", "maxTokens")
    }
    with pytest.raises(ValueError):
        client_limits(context_tokens=128000, output_tokens=128000)


@pytest.mark.parametrize(
    "finish,content,validate,error",
    [
        ("length", "{}", False, IncompleteResponse),
        ("stop", "{", False, ValueError),
        ("stop", "{}", True, ResponseValidationError),
    ],
)
def test_only_completed_domain_valid_responses_replay(tmp_path, finish, content, validate, error):
    cache = ResponseCache(tmp_path)
    calls = []

    def dispatch():
        calls.append(1)
        return {"content": content, "finish_reason": finish, "usage": [{"cny": 1}]}

    def validator(value):
        if validate:
            raise ValueError("assignment missing")

    key = "a" * 64
    for _ in range(2):
        with pytest.raises(error):
            cache.complete(key, dispatch, json.loads, validator)
    assert len(calls) == 2 and not (tmp_path / (key + ".json")).exists()
    assert len(list((tmp_path / "rejected").glob("*.json"))) == 2


@pytest.mark.parametrize("finish", [None, "length", "stop"])
def test_cache_revalidates_legacy_metadata_and_current_contract(tmp_path, finish):
    cache = ResponseCache(tmp_path)
    key = "b" * 64
    atomic_json(tmp_path / (key + ".json"), {"content": '{"value":1}', "finish_reason": finish})
    calls = []

    def dispatch():
        calls.append(1)
        return {"content": '{"value":2}', "finish_reason": "stop"}

    value = cache.complete(key, dispatch, json.loads, lambda value: value["value"])
    assert value["value"] == (1 if finish == "stop" else 2)
    assert len(calls) == (0 if finish == "stop" else 1)
    with pytest.raises(ResponseValidationError):
        cache.complete(key, dispatch, json.loads, lambda value: value["missing"])


def test_raw_structured_reply_is_diagnostic_until_domain_accepts(tmp_path):
    cache = ResponseCache(tmp_path)
    key = "c" * 64

    def reply():
        return {"finish_reason": "stop", "content": "{}"}

    cache.complete(key, reply, json.loads)
    assert not (tmp_path / (key + ".json")).exists()
    assert list((tmp_path / "unvalidated").glob("*.json"))


def test_recovery_includes_prior_cost_roles_and_latency_once():
    history = [{"reservation": "one", "cny": 1, "seconds": 5, "role": "generation"}]
    accounting = RecoveryAccounting(history, wall_seconds=7, source="failed")
    result = Result(
        0.5,
        "synthetic",
        {"cny": 2},
        output={"cost": {"cny": 2}, "latency": {"seconds": 10, "model_seconds": 3}},
        seconds=10,
        roles={"generation": {"cny": 2}},
    )
    accounting.apply(result)
    assert result.cost["cny"] == result.output["cost"]["cny"] == 3
    assert result.seconds == result.output["latency"]["seconds"] == 17
    assert result.output["latency"]["model_seconds"] == 8
    assert result.roles["generation"]["cny"] == 3
    assert result.output["execution_recovery"]["latency_is_lower_bound"] is False
    with pytest.raises(ValueError):
        accounting.apply(result)
    with pytest.raises(ValueError):
        RecoveryAccounting(history * 2)


def test_seeded_recovery_and_unrecorded_wall_are_explicit():
    history = [{"reservation": "one", "cny": 1, "seconds": 5}]
    accounting = RecoveryAccounting(history)
    usage = []
    accounting.seed(usage)
    accounting.seed(usage)
    assert len(usage) == 1
    result = Result(
        0.5,
        "synthetic",
        {"cny": 1},
        output={"cost": {"cny": 1}, "latency": {"seconds": 2, "model_seconds": 5}},
        seconds=2,
    )
    accounting.apply(result, usage_already_included=True)
    assert result.cost["cny"] == 1 and result.seconds == 7
    assert result.output["execution_recovery"]["latency_is_lower_bound"]


def test_recovery_supports_declared_cost_units_and_shared_cost_mapping():
    history = [
        {"reservation": "prior", "usd": 0.25, "seconds": 2, "input_tokens": 4, "output_tokens": 5}
    ]
    cost = {"usd": 1, "input_tokens": 10, "output_tokens": 20, "calls": 1}
    result = Result(
        0.5,
        "synthetic",
        cost,
        output={"cost": cost, "latency": {"seconds": 3, "model_seconds": 1}},
        seconds=3,
    )
    RecoveryAccounting(history, cost_key="usd").apply(result)
    assert result.cost["usd"] == result.output["cost"]["usd"] == 1.25
    assert result.output["cost"]["input_tokens"] == 14
    assert result.output["cost"]["output_tokens"] == 25
    assert result.output["cost"]["calls"] == 2


def test_reset_preserves_charges_requires_idle_and_fresh_evidence(tmp_path):
    path = tmp_path / "circuit.json"
    stop = tmp_path / "STOP"
    circuit = TransportCircuit(path)
    for i in range(3):
        assert circuit.record(False, str(i)) == (i == 2)
    before = path.read_bytes()
    stop.write_text("circuit")
    evidence = {
        "active_requests": 0,
        "pending_requests": 0,
        "accounting_reconciled": True,
        "probe_successes": 1,
        "probe_time": time.time(),
    }
    for change in (
        {"active_requests": 1},
        {"pending_requests": 1},
        {"accounting_reconciled": False},
        {"probe_time": 0},
        {"probe_successes": 0},
    ):
        with pytest.raises(ValueError):
            circuit.reset(
                stop, expected_stop="circuit", reason="operator", **{**evidence, **change}
            )
        assert path.read_bytes() == before and stop.exists()
    receipt = circuit.reset(stop, expected_stop="circuit", reason="operator", **evidence)
    assert receipt["prior_state_sha256"] == hashlib.sha256(before).hexdigest()
    assert not stop.exists() and json.loads(path.read_text()) == {"consecutive": 0, "events": []}
    assert list(tmp_path.glob("circuit-reset-*.json"))
    assert not circuit.record(False, "new")


@pytest.mark.skipif(os.name != "nt", reason="real Windows deny-delete handle")
def test_publication_retries_real_windows_deny_delete_without_reexecution(tmp_path):
    from ctypes import wintypes as w

    target = tmp_path / "status.json"
    atomic_json(target, {"old": True})
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [
        w.LPCWSTR,
        w.DWORD,
        w.DWORD,
        ctypes.c_void_p,
        w.DWORD,
        w.DWORD,
        w.HANDLE,
    ]
    api.CreateFileW.restype = w.HANDLE
    api.CloseHandle.argtypes = [w.HANDLE]
    handle = api.CreateFileW(str(target), 0x80000000, 3, None, 3, 0, None)
    assert handle not in (None, ctypes.c_void_p(-1).value)
    errors = []
    released = threading.Event()

    def on_error(error):
        errors.append(error)
        assert json.loads(target.read_text()) == {"old": True}
        if not released.is_set():
            api.CloseHandle(handle)
            released.set()

    try:
        publish_with_retry(
            lambda: atomic_json(target, {"new": True}), timeout=2, interval=0.01, on_error=on_error
        )
    finally:
        if not released.is_set():
            api.CloseHandle(handle)
    assert errors and json.loads(target.read_text()) == {"new": True}
    assert not list(tmp_path.glob("*.tmp"))


def test_monitor_survives_publication_error_and_final_retry_is_bounded():
    stopped = threading.Event()
    calls = []
    errors = []

    def publish():
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError("sharing")
        stopped.set()

    watch_updates(publish, stopped, interval=0.001, on_error=errors.append)
    assert len(calls) == 2 and len(errors) == 1
    with pytest.raises(PermissionError):
        publish_with_retry(lambda: (_ for _ in ()).throw(PermissionError()), timeout=0)
