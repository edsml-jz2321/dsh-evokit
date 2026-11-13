import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import evidence, mcp_environment, tool_error

from evokit import Workspace
from evokit.portability import export_records, import_records
from evokit.records import FilesRecordStore, open_store
from evokit.storage import canonical, digest
from evokit.tools import HB_TOOLS, TOOLS, available_tools, call_tool


def test_record_roundtrip_and_backend_migration(workspace, tmp_path):
    item, before, after, noise = evidence(workspace)
    verdict = workspace.gate(item["id"], before, after, noise, "mean", generic=True)
    adopted = workspace.adopt(verdict["id"], "measured improvement")
    workspace.measure_holdout(adopted["version"])
    workspace.cards(before)
    backup = tmp_path / "backup.jsonl"
    export_records(workspace.store, backup)
    other = "hb" if workspace.store.backend == "files" else "files"
    if other == "hb":
        pytest.importorskip("heavenbase")
    state = tmp_path / "restored"
    # Snapshots are independently copied, not mirrored into the record backend.
    for name in ("blobs", "versions", "playbooks"):
        shutil.copytree(workspace.state / name, state / name)
    restored = Workspace(workspace.root, workspace.scenario, state, storage={"backend": other})
    assert import_records(restored.store, backup) > 0
    restored.initialize()
    assert restored.current() == workspace.current()
    assert restored.history() == workspace.history()
    assert restored.metrics() == workspace.metrics()
    assert sorted(restored.results(before), key=lambda r: (r["task_id"], r["repeat"])) == sorted(
        workspace.results(before), key=lambda r: (r["task_id"], r["repeat"])
    )
    assert sorted(restored.cards(before), key=lambda c: json.dumps(c, sort_keys=True)) == sorted(
        workspace.cards(before), key=lambda c: json.dumps(c, sort_keys=True)
    )
    assert restored._epoch() == workspace._epoch()
    assert restored.store.items("run", private=True) == workspace.store.items("run", private=True)
    second = tmp_path / "second.jsonl"
    export_records(restored.store, second)
    final_store = FilesRecordStore(tmp_path / "final")
    import_records(final_store, second)
    assert final_store.history() == workspace.store.history()
    with pytest.raises(ValueError, match="empty"):
        import_records(restored.store, backup)
    # A rehashed envelope still cannot move holdout slots into public storage.
    header, *entries = [json.loads(line) for line in backup.read_bytes().splitlines()]
    next(e for e in entries if e["kind"] == "result" and e["private"])["private"] = False
    header["digest"] = digest(entries)
    invalid = tmp_path / "wrong-domain.jsonl"
    invalid.write_bytes(b"\n".join(canonical(e) for e in [header, *entries]) + b"\n")
    empty = FilesRecordStore(tmp_path / "rejected")
    with pytest.raises(ValueError, match="scoped"):
        import_records(empty, invalid)
    assert empty.history() == [] and empty.get("epoch", "singleton") is None
    header, *entries = [json.loads(line) for line in backup.read_bytes().splitlines()]
    hidden = next(e for e in entries if e["kind"] == "run" and e["private"])
    hidden_id = hidden["key"]
    hidden["value"]["split"] = "selection"
    for entry in entries:
        if (entry["kind"] in {"run", "status"} and entry["key"] == hidden_id) or (
            entry["kind"] == "result" and entry["key"].startswith(hidden_id + "/")
        ):
            entry["private"] = False
    header["digest"] = digest(entries)
    invalid.write_bytes(b"\n".join(canonical(e) for e in [header, *entries]) + b"\n")
    with pytest.raises(ValueError, match="split"):
        import_records(empty, invalid)
    assert empty.history() == [] and empty.get("epoch", "singleton") is None
    restored.close()


def test_backup_validation_precedes_all_writes(workspace, tmp_path):
    path = tmp_path / "records.jsonl"
    export_records(workspace.store, path)
    path.write_bytes(path.read_bytes()[:-1])
    target = FilesRecordStore(tmp_path / "target")
    with pytest.raises(ValueError, match="Truncated"):
        import_records(target, path)
    assert target.history() == []
    assert target.get("epoch", "singleton") is None


def test_backend_choice_is_durable_and_cannot_silently_switch(workspace):
    reopened = open_store(workspace.state)
    assert reopened.backend == workspace.store.backend
    reopened.close()
    with pytest.raises(ValueError, match="fixed"):
        open_store(
            workspace.state, {"backend": "hb" if workspace.store.backend == "files" else "files"}
        )


def test_hb_query_similarity_and_private_workspace(workspace):
    if workspace.store.backend != "hb":
        pytest.skip("HB capability")
    item, before, after, noise = evidence(workspace)
    verdict = workspace.gate(item["id"], before, after, noise, "mean", generic=True)
    workspace.adopt(verdict["id"], "measure after adoption")
    workspace.measure_holdout(item["version"])
    results = workspace.records_query(task=workspace.results(before)[0]["task_id"], outcome=0)
    assert results and {r["run_id"] for r in results} == {before}
    assert all(
        r["version"] == item["version"] for r in workspace.records_query(proposal=item["id"])
    )
    assert all(r["version"] == item["version"] for r in workspace.records_query(generation=1))
    assert workspace.records_query(
        flipped_between=[workspace.results(before)[0]["version"], item["version"]]
    )
    assert workspace.records_query(group="absent") == []
    fallback = workspace.failures_similar("rule decision")
    assert fallback["method"] == "local-token-jaccard" and fallback["matches"]
    calls = []

    def fake_embedding(text):
        calls.append(text)
        return [1.0, 1.0 if "rule" in text else 0.0, 0.5]

    workspace.store.embedding = fake_embedding
    vector = workspace.failures_similar("rule decision", limit=3)
    assert calls and vector["method"] == "hb-vector-cosine" and len(vector["matches"]) == 3
    entity = workspace.store.entities["failure"]
    embedded = workspace.store.workspaces[False].query(entity).execute().rows()
    workspace.store.embedding = None
    workspace.failures_similar("rule decision")
    retained = workspace.store.workspaces[False].query(entity).execute().rows()
    assert {r["key"]: r["embedding"] for r in retained} == {
        r["key"]: r["embedding"] for r in embedded
    }
    # Inspect the actual public HB entities, beyond evokit's filtered facades.
    public = workspace.store.workspaces[False]
    all_public = json.dumps(
        [
            public.query(entity).execute().rows()
            for entity in dict.fromkeys(workspace.store.entities.values())
        ]
    )
    for task_id in workspace._epoch()["splits"]["confirmation"]:
        assert task_id not in all_public
    assert "private_literal_match" not in all_public
    assert workspace.store.get("epoch", "singleton") is None
    assert workspace.store.items("run", private=True)
    private_id, private_meta = workspace.store.items("run", private=True)[0]
    assert workspace.records_query(run_id=private_id) == []
    assert workspace.records_query(task=private_meta["task_ids"][0]) == []
    assert not (workspace.state / "epoch.json").exists()
    assert not (workspace.state / "ledger.jsonl").exists()
    assert not list(workspace.state.rglob("results.jsonl"))


def test_hb_conditional_tools_and_cli(tmp_path, record_backend):
    root = tmp_path / "s"
    root.mkdir()
    (root / "rule.txt").write_text("0")
    # Keep the scorer outside the scenario; only three onboarding pieces remain.
    adapter = root / "adapter.py"
    adapter.write_text("from conftest import Minimal\n")
    # Use the bundled command adapter so a separate CLI process can load it.
    source = __import__("pathlib").Path(__file__).resolve().parents[1] / "examples/command-echo"
    shutil.rmtree(root)
    shutil.copytree(source, root)
    config = root / "scenario.yaml"
    with config.open("a") as stream:
        stream.write("\nstorage: " + json.dumps(record_backend) + "\n")
    tools = available_tools(str(config))
    assert {t.name for t in tools} == {
        t.name for t in TOOLS + (HB_TOOLS if record_backend["backend"] == "hb" else ())
    }
    if record_backend["backend"] == "files":
        with pytest.raises(ValueError, match="files backend"):
            call_tool("records_query", {"config": str(config)})
    else:
        assert call_tool("records_query", {"config": str(config)}) == []
    version = call_tool("version_current", {"config": str(config)})["version"]
    arm = call_tool("run_launch", {"config": str(config), "version": version, "smoke": True})
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        status = call_tool("run_status", {"config": str(config), "run_id": arm["run_id"]})
        if status["state"] == "completed":
            break
        time.sleep(0.2)
    assert status["state"] == "completed" and status["completed"] == 2
    backup = tmp_path / "operator.jsonl"
    exported = subprocess.run(
        [
            sys.executable,
            "-m",
            "evokit",
            "export",
            "--config",
            str(config),
            "--output",
            str(backup),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(exported.stdout)["records"] > 0
    target = tmp_path / "imported"
    imported = subprocess.run(
        [
            sys.executable,
            "-m",
            "evokit",
            "import",
            "--config",
            str(config),
            "--state",
            str(target),
            "--backend",
            record_backend["backend"],
            "--input",
            str(backup),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(imported.stdout)["records"] > 0


def test_core_import_and_execution_with_hb_blocked(tmp_path):
    script = tmp_path / "without_hb.py"
    script.write_text("""import sys
from pathlib import Path
class BlockHB:
    def find_spec(self, fullname, *args):
        if fullname == "heavenbase" or fullname.startswith("heavenbase."):
            raise ImportError("HB deliberately unavailable")
sys.meta_path.insert(0, BlockHB())
from evokit import Workspace, Scenario, Task, Result
class Minimal(Scenario):
    editable = ("rule.txt",)
    def tasks(self): return [Task(str(i), {}) for i in range(20)]
    def run_and_score(self, version_dir, task): return Result(True, "ok")
root = Path(sys.argv[1]); root.mkdir(); (root / "rule.txt").write_text("rule")
ws = Workspace(root, Minimal()); ws.initialize()
assert ws.launch(ws.current(), wait=True)["state"] == "completed"
assert not any(n.startswith("heavenbase") for n in sys.modules)
""")
    subprocess.run(
        [sys.executable, str(script), str(tmp_path / "nohb")], check=True, capture_output=True
    )


def test_command_yaml_migration_reopens_after_deployment_change(tmp_path, private_hb_home):
    pytest.importorskip("heavenbase")
    root = tmp_path / "command"
    shutil.copytree(Path(__file__).resolve().parents[1] / "examples/command-echo", root)
    config = root / "scenario.yaml"
    original = config.read_text() + "\nworkspace: records-files\n"
    config.write_text(original)
    source = Workspace.open(config)
    run = source.launch(source.current(), wait=True)
    backup = tmp_path / "records.jsonl"
    export_records(source.store, backup)
    # Keep the new state outside the scenario scan, like the original .evokit.
    target = tmp_path / "hb-state"
    restored = Workspace.open(config, initialize=False, state=target, storage={"backend": "hb"})
    import_records(restored.store, backup)
    for name in ("blobs", "versions"):
        shutil.copytree(source.state / name, target / name)
    expected = source.results(run["run_id"])
    source.close()
    restored.close()
    config.write_text(
        original.replace("workspace: records-files", "workspace: ../hb-state")
        + "\nstorage: {backend: hb}\n"
    )
    reopened = Workspace.open(config)
    assert sorted(reopened.results(run["run_id"]), key=lambda r: r["task_id"]) == sorted(
        expected, key=lambda r: r["task_id"]
    )
    assert reopened.launch(reopened.current(), smoke=True, wait=True)["state"] == "completed"
    reopened.close()


def test_python_execution_settings_remain_anchored(workspace):
    # These are scenario-owned execution settings, not YAML deployment options.
    from conftest import Minimal

    scenario = Minimal()
    scenario.execution_config = {"workspace": "runtime-a", "storage": "model-a"}
    fresh = Workspace(
        workspace.root,
        scenario,
        workspace.root.parent / "anchored",
        storage={"backend": workspace.store.backend},
    )
    fresh.initialize()
    scenario.execution_config["workspace"] = "runtime-b"
    with pytest.raises(ValueError, match="policy changed"):
        fresh.launch(fresh.current(), wait=True)
    fresh.close()


def test_long_identity_and_failure_payload_parity(tmp_path, record_backend):
    from evokit import Result, Scenario, Task

    class Large(Scenario):
        editable = ("rule.txt",)

        def tasks(self):
            return [Task(f"case-{i}-" + "a" * 300, {}, "g" * 300) for i in range(20)]

        def run_and_score(self, version_dir, task):
            return Result(False, "reason" * 15000, output="output" * 15000)

    root = tmp_path / "large"
    root.mkdir()
    (root / "rule.txt").write_text("0")
    ws = Workspace(root, Large(), storage=record_backend)
    ws.initialize()
    arm = ws.launch(ws.current(), smoke=True, wait=True)
    assert arm["errors"] == 0 and arm["completed"] == 2
    cards = ws.cards(arm["run_id"])
    assert cards and cards[0]["output"] == "output" * 15000
    if ws.store.backend == "hb":
        assert ws.records_query(task=cards[0]["task_id"], group="g" * 300)
        assert ws.failures_similar("reason")["matches"]
    ws.close()


def test_files_append_reads_existing_results_once(tmp_path, monkeypatch):
    store = FilesRecordStore(tmp_path / "linear")
    reads = []
    original = store.results

    def counted(run_id, **kwargs):
        reads.append(run_id)
        return original(run_id, **kwargs)

    monkeypatch.setattr(store, "results", counted)
    store.recover_results("arm")
    for index in range(100):
        row = {"task_id": str(index), "repeat": 0, "outcome": 0}
        store.add_result("arm", row)
        store.add_result("arm", row)
    assert reads == ["arm"]
    assert len(original("arm")) == 100
    with pytest.raises(ValueError, match="immutable"):
        store.add_result("arm", {"task_id": "0", "repeat": 0, "outcome": 1})


def test_bound_mcp_advertises_backend_capabilities(tmp_path, record_backend):
    import asyncio

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    root = tmp_path / "mcp"
    shutil.copytree(Path(__file__).resolve().parents[1] / "examples/command-echo", root)
    config = root / "scenario.yaml"
    with config.open("a") as stream:
        stream.write("\nstorage: " + json.dumps(record_backend) + "\n")

    async def exercise():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "evokit", "mcp", "--config", str(config)],
            env=mcp_environment(),
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                expected = TOOLS + (HB_TOOLS if record_backend["backend"] == "hb" else ())
                assert {t.name for t in listed.tools} == {t.name for t in expected}
                if record_backend["backend"] == "hb":
                    result = await session.call_tool("records_query", {"config": str(config)})
                    assert not tool_error(result) and json.loads(result.content[0].text) == []
                    result = await session.call_tool(
                        "records_query", {"config": str(config), "split": "holdout"}
                    )
                    assert tool_error(result)
                wrong = await session.call_tool(
                    "scenario_describe", {"config": str(tmp_path / "other.yaml")}
                )
                assert tool_error(wrong)

    asyncio.run(exercise())


def test_hb_uses_patch_semantics_for_existing_rows(workspace):
    if workspace.store.backend != "hb":
        pytest.skip("HB patch semantics")
    store = workspace.store
    store.put("status", "patch-probe", {"state": "running"})
    ws, entity = store.workspaces[False], store.entities["status"]
    object_id = store._id("status", "patch-probe")
    ws.set(object_id, {"version": "preserve-typed-field"}, entity=entity)
    store.put("status", "patch-probe", {"state": "completed"})
    row = ws.get(object_id, entity=entity)
    assert row["version"] == "preserve-typed-field"
    assert row["payload"] == {"state": "completed"}


def test_hb_refuses_unconfigured_process_home(tmp_path, monkeypatch):
    monkeypatch.delenv("EVOKIT_HB_HOME", raising=False)
    with pytest.raises(ValueError, match="private EVOKIT_HB_HOME"):
        open_store(tmp_path / "refused", {"backend": "hb"})


def test_original_files_workspace_cannot_silently_switch(tmp_path):
    legacy = FilesRecordStore(tmp_path / "legacy")
    legacy.put("epoch", "singleton", {"legacy": True})
    assert not (legacy.state / "storage.json").exists()
    with pytest.raises(ValueError, match="fixed"):
        open_store(legacy.state, {"backend": "hb"})
    reopened = open_store(legacy.state)
    assert reopened.backend == "files"
    assert reopened.get("epoch", "singleton") == {"legacy": True}


def test_record_store_immutable_slots_and_event_restore(workspace, tmp_path):
    arm = workspace.launch(workspace.current(), smoke=True, wait=True)
    row = workspace.results(arm["run_id"])[0]
    key = arm["run_id"] + "/" + digest([row["task_id"], row["repeat"]])
    store = open_store(tmp_path / "contract", {"backend": workspace.store.backend})
    store.put("result", key, row)
    store.put("result", key, row)
    assert store.results(arm["run_id"]) == [row]
    with pytest.raises(ValueError, match="immutable"):
        store.put("result", key, {**row, "reason": "different"})
    history = workspace.store.history()
    store.restore_events(history)
    assert store.history() == history
    with pytest.raises(ValueError, match="empty history"):
        store.restore_events(history)
    store.close()


def test_config_snapshot_bytes_and_copied_candidate(tmp_path, record_backend):
    root = tmp_path / "scenario"
    shutil.copytree(Path(__file__).resolve().parents[1] / "examples/command-echo", root)
    config = root / "scenario.yaml"
    with config.open("a") as stream:
        stream.write("\nstorage: " + json.dumps(record_backend) + "\n")
    original = config.read_bytes()
    workspace = Workspace.open(config)
    checkout = workspace.materialize(workspace.current(), tmp_path / "materialized")
    assert (checkout / "scenario.yaml").read_bytes() == original
    copied = tmp_path / "candidate"
    shutil.copytree(root, copied, ignore=shutil.ignore_patterns(".evokit"))
    name = next(iter(workspace._manifest(workspace.current())))
    artifact = copied / name
    artifact.write_bytes(artifact.read_bytes() + b"\n")
    assert workspace.create_version(copied)["changed"] == [name]
    workspace.close()


def test_serialized_store_handles_observe_completed_slots(tmp_path, record_backend):
    state = tmp_path / "handles"
    first = open_store(state, record_backend)
    second = open_store(state, record_backend)
    row = {
        "task_id": "task-a",
        "repeat": 0,
        "version": "v",
        "epoch": "e",
        "group": "g",
        "outcome": 0,
    }
    other = {**row, "task_id": "task-b"}
    key = "run/" + digest([row["task_id"], 0])
    other_key = "run/" + digest([other["task_id"], 0])
    first.put("result", key, row)
    second.put("result", other_key, other)
    first.put("result", other_key, other)
    assert len(first.results("run")) == 2
    with pytest.raises(ValueError, match="immutable"):
        first.put("result", other_key, {**other, "outcome": 1})
    first.close()
    second.close()
