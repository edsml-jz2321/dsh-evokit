"""Coordinator decisions, private surfaces, anchored contracts and legacy migration."""

import json
import shutil
from pathlib import Path

import pytest
import yaml
from conftest import Minimal, evidence

from evokit import Workspace
from evokit.analysis import split_tasks
from evokit.portability import export_records, import_records
from evokit.records import FilesRecordStore
from evokit.tools import OPERATOR_TOOLS, TOOLS, available_tools, call_tool


def contract(**overrides):
    return {
        "objective": "Maximize per-task success",
        "guards": {"latency_ratio": 1.2, "cost_ratio": {"calls": 1.2}},
        "confirmation_rule": {"min_net": 0},
        "noise_plan": {"repeats": 2, "method": "measured paired flip rate and net spread"},
        "budget": {"unit": "money", "total": 200, "per_generation": 40},
        "stagnation_window": 2,
        **overrides,
    }


def adopt(workspace, text="1\n"):
    item, before, after, noise = evidence(workspace, text)
    gate = workspace.gate(
        item["id"], before, after, noise, "mean", generic=True, rationale="generic simplification"
    )
    return workspace.adopt(gate["id"], "paired improvement")


def test_contract_anchoring_and_work_boundary(workspace):
    assert workspace.contract_read() is None
    recorded = workspace.contract_record(contract())
    assert recorded["confirmation_wear_budget"] == 4
    assert recorded["split_hash"] == workspace._epoch()["split_hash"]
    assert recorded["splits"] == workspace.describe()["counts"]
    assert workspace.contract_read() == recorded
    workspace.launch(workspace.current(), smoke=True, wait=True)
    with pytest.raises(ValueError, match="frozen"):
        workspace.contract_record(contract(confirmation_wear_budget=20))
    assert workspace.contract_read() == recorded


def test_confirmation_reuses_results_and_revert_is_append_only(workspace, monkeypatch):
    workspace.contract_record(contract())
    first = adopt(workspace)
    result = workspace.confirm_adoption(first["version"])
    assert result["verdict"] == "confirmed"
    assert result["fixes"] == workspace.describe()["counts"]["confirmation"]
    assert result["breaks"] == 0
    second = adopt(workspace, "1\nextra generic explanation\n")
    # Explicitly choose the contracted rule's negative evidence without model work.
    run_id = workspace._measurement_run(second["version"], "confirmation", 1, False)
    rows = workspace._rows(run_id)
    # Use a fresh valid run of the child with scripted negative observations.
    retained = "scripted-negative"
    workspace._prepare_run(second["version"], "confirmation", 1, 1, False, retained)
    for row in rows:
        workspace.store.add_result(retained, {**row, "outcome": 0}, private=True)
    # Select only this retained child measurement, keeping the measured parent.
    original = workspace._measurement_run
    monkeypatch.setattr(
        workspace,
        "_measurement_run",
        lambda version, split, workers, reuse_only: (
            retained if version == second["version"] else original(version, split, workers, True)
        ),
    )
    monkeypatch.setattr(
        workspace, "execute", lambda *_: pytest.fail("Retained measurements must not execute")
    )
    failed = workspace.confirm_adoption(second["version"], reuse_only=True)
    assert failed["verdict"] == "not_confirmed"
    assert failed["net"] < 0
    assert workspace.confirm_adoption(second["version"], reuse_only=True) == failed
    assert workspace.metrics()["confirmation"] == {"decisions": 2, "rate": 0.5}
    before = workspace.ledger.history()
    reverted = workspace.ledger_revert("Paired confirmation regressed under min_net=0")
    assert reverted["version"] == first["version"]
    assert reverted["parent"] == second["version"]
    assert workspace.current() == first["version"]
    if workspace.store.backend == "hb":
        assert all(e["kind"] != "revert" for _, e in workspace.store.items("event"))
        assert any(e["kind"] == "revert" for _, e in workspace.store.items("event", private=True))
    assert workspace.ledger.history()[: len(before)] == before
    assert workspace.history()["base_chain"][-3:] == [
        first["version"],
        second["version"],
        first["version"],
    ]
    assert next(p for p in workspace.proposals() if p["id"] == second["proposal_id"])[
        "not_confirmed"
    ]
    public = json.dumps([workspace.describe(), workspace.history(), workspace.proposals()])
    for task in workspace._split_ids("confirmation"):
        assert task not in public
    assert "Paired confirmation regressed" not in public
    backup = workspace.state.parent / "confirmation-backup.jsonl"
    export_records(workspace.store, backup)
    restored = FilesRecordStore(workspace.state.parent / "restored")
    import_records(restored, backup)
    assert restored.history() == workspace.ledger.history()


def test_wear_exhaustion_and_failures_consume_one_opening(workspace):
    workspace.contract_record(contract(confirmation_wear_budget=1))
    first = adopt(workspace)
    with pytest.raises(ValueError, match="reuse_only"):
        workspace.confirm_adoption(first["version"], reuse_only=True)
    assert len([e for e in workspace.ledger.history() if e["kind"] == "confirmation_started"]) == 1
    assert workspace.confirm_adoption(first["version"])["verdict"] == "confirmed"
    second = adopt(workspace, "1\na simpler generic explanation\n")
    with pytest.raises(ValueError, match="wear budget exhausted"):
        workspace.confirm_adoption(second["version"])
    with pytest.raises(ValueError, match="reason"):
        workspace.ledger_revert(" ")
    workspace.ledger_revert("operator rollback")
    workspace.ledger_revert("operator rollback of first adoption")
    with pytest.raises(ValueError, match="no adoption parent"):
        workspace.ledger_revert("already initial")


def test_missing_contract_unadopted_and_guards(workspace):
    with pytest.raises(ValueError, match="contract"):
        workspace.confirm_adoption(workspace.current())
    workspace.contract_record(contract(guards={"latency_ratio": 0, "cost_ratio": {}}))
    with pytest.raises(ValueError, match="adoption"):
        workspace.confirm_adoption(workspace.current())
    child = adopt(workspace)
    assert workspace.confirm_adoption(child["version"])["verdict"] == "not_confirmed"


def final_workspace(tmp_path, record_backend):
    root = tmp_path / "scenario"
    shutil.copytree(Path(__file__).resolve().parents[1] / "examples/command-echo", root)
    config = root / "scenario.yaml"
    data = yaml.safe_load(config.read_text())
    data["splits"] = {"diagnosis": 0.3, "selection": 0.3, "confirmation": 0.2, "final": 0.2}
    data["storage"] = record_backend
    config.write_text(yaml.safe_dump(data), encoding="utf-8")
    return Workspace.open(config), config


def test_final_once_and_improver_profile_isolation(tmp_path, record_backend):
    workspace, config = final_workspace(tmp_path, record_backend)
    try:
        args = {"config": str(config)}
        public_names = {t.name for t in available_tools(str(config))}
        assert not public_names & {t.name for t in OPERATOR_TOOLS}
        assert set(t.name for t in TOOLS) <= public_names
        for tool in OPERATOR_TOOLS:
            with pytest.raises(ValueError, match="Unknown tool"):
                call_tool(tool.name, args)
        recorded = call_tool(
            "contract_record", {**args, "contract": contract()}, profile="operator"
        )
        assert call_tool("contract_read", args, profile="operator") == recorded
        assert "confirmation" not in call_tool("meta_metrics", args)
        assert "confirmation" in call_tool("meta_metrics", args, profile="operator")
        result = call_tool("final_measure", args, profile="operator")
        assert set(result) == {"fixes", "breaks", "net", "cost_ratios", "latency_ratio"}
        assert result["net"] == 0
        with pytest.raises(ValueError, match="Final measurement has opened"):
            call_tool("final_measure", args, profile="operator")
        with pytest.raises(ValueError, match="Final measurement has opened"):
            workspace.launch(workspace.current(), wait=True)
        with pytest.raises(ValueError, match="Final measurement has opened"):
            workspace.create_version()
        for run_id, _ in workspace.store.items("run", private=True):
            for method in (workspace.status, workspace.results, workspace.cards):
                with pytest.raises(ValueError, match="not available"):
                    method(run_id)
        public = json.dumps([workspace.describe(), workspace.history(), workspace.metrics()])
        for task in workspace._split_ids("final"):
            assert task not in public
    finally:
        workspace.close()


def test_no_final_and_deterministic_four_splits(workspace):
    tasks = workspace.scenario.tasks()
    a = split_tasks(tasks, 193, fractions=(0.3, 0.3, 0.2, 0.2))
    assert a == split_tasks(list(reversed(tasks)), 193, fractions=(0.3, 0.3, 0.2, 0.2))
    assert len({t for ids in a.values() for t in ids}) == len(tasks)
    assert a["final"]
    with pytest.raises(ValueError, match="separate final"):
        workspace.final_measure()


def test_legacy_migration_on_copy_preserves_history_and_alias(tmp_path, monkeypatch):
    root = tmp_path / "original"
    root.mkdir()
    (root / "rule.txt").write_text("0\n")
    legacy = Workspace(root, Minimal())
    # Construct the old format before its epoch event is anchored, as the old
    # release did. This is fixture creation, never a migration edit to history.
    import evokit.workspace as module

    current_split = module.split_tasks
    with monkeypatch.context() as context:
        context.setattr(
            module,
            "split_tasks",
            lambda *args, **kw: current_split(*args, **{**kw, "legacy": True}),
        )
        legacy.initialize(seed=193)
    child = adopt(legacy)
    legacy.measure_holdout(child["version"])
    frozen = legacy._epoch()
    events = legacy.ledger.history()
    saved = tmp_path / "copy"
    shutil.copytree(legacy.state, saved)
    copied = Workspace(root, Minimal(), state=saved)
    monkeypatch.setattr(
        copied, "execute", lambda *_: pytest.fail("Migration cannot execute model work")
    )
    copied.migrate_confirmation_contract(
        contract(), "Retrospective operator rule for retained legacy evidence"
    )
    assert copied.confirm_adoption(child["version"], reuse_only=True)["verdict"] == "confirmed"
    assert copied._epoch() == frozen
    assert copied.ledger.history()[: len(events)] == events
    assert legacy.ledger.history() == events
    assert copied._split_ids("confirmation") == copied._split_ids("holdout")
    assert "holdout" not in copied.describe()["counts"]
    with pytest.raises(ValueError, match="already|uncontracted"):
        copied.migrate_confirmation_contract(contract(), "duplicate")


def test_revert_tracks_occurrence_when_initial_content_is_adopted(workspace):
    initial = workspace.current()
    first = adopt(workspace, "0\ngeneric explanation\n")
    returned = adopt(workspace, "0\n")
    assert returned["version"] == initial
    assert (
        workspace.ledger_revert("Revert the adopted initial content")["version"] == first["version"]
    )
    assert workspace.ledger_revert("Return to epoch start")["version"] == initial
    with pytest.raises(ValueError, match="no adoption parent"):
        workspace.ledger_revert("No active adoption remains")


def test_final_refuses_prepared_arm_without_status(tmp_path, record_backend):
    workspace, _ = final_workspace(tmp_path, record_backend)
    try:
        workspace.contract_record(contract())
        run_id = workspace._prepare_run(workspace.current(), "selection", 1, 1, False, "prepared")
        with pytest.raises(ValueError, match="Finish existing arms"):
            workspace.final_measure()
        assert not any(e["kind"] == "final_open" for e in workspace.ledger.history())
        workspace.execute(run_id)
        assert workspace.final_measure()["net"] == 0
        with pytest.raises(ValueError, match="Final measurement has opened"):
            workspace.execute(run_id)
    finally:
        workspace.close()


def test_revert_unwinds_repeated_content_and_confirmation_occurrences(workspace):
    workspace.contract_record(contract())
    initial = workspace.current()
    first = adopt(workspace, "0\ngeneric B\n")
    receipt = workspace.confirm_adoption(first["version"])
    second = adopt(workspace, "0\ngeneric C\n")
    third = adopt(workspace, "0\ngeneric B\n")
    assert third["version"] == first["version"]
    assert workspace.ledger_revert("Undo repeated B")["version"] == second["version"]
    assert workspace.ledger_revert("Undo C")["version"] == first["version"]
    assert workspace.confirm_adoption(first["version"], reuse_only=True) == receipt
    assert workspace.metrics()["confirmation"]["decisions"] == 1
    assert workspace.ledger_revert("Undo original B")["version"] == initial


def test_failed_final_still_consumes_opening(tmp_path, record_backend, monkeypatch):
    workspace, _ = final_workspace(tmp_path, record_backend)
    try:
        workspace.contract_record(contract())

        def fail(_):
            raise RuntimeError("scripted executor failure")

        monkeypatch.setattr(workspace, "execute", fail)
        with pytest.raises(RuntimeError, match="executor failure"):
            workspace.final_measure()
        with pytest.raises(ValueError, match="Final measurement has opened"):
            workspace.final_measure()
    finally:
        workspace.close()


def test_mcp_profiles_reject_guessed_operator_tools(tmp_path, record_backend):
    import asyncio
    import sys

    from conftest import mcp_environment, tool_error
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    workspace, config = final_workspace(tmp_path, record_backend)
    workspace.close()

    async def exercise(profile):
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "evokit", "mcp", "--profile", profile, "--config", str(config)],
            env=mcp_environment(),
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = {t.name for t in (await session.list_tools()).tools}
                assert ("confirm_adoption" in listed) == (profile == "operator")
                for tool in OPERATOR_TOOLS:
                    if profile == "improver":
                        result = await session.call_tool(tool.name, {"config": str(config)})
                        assert tool_error(result)
                if profile == "operator":
                    result = await session.call_tool(
                        "contract_record", {"config": str(config), "contract": contract()}
                    )
                    assert not tool_error(result)

    asyncio.run(exercise("improver"))
    asyncio.run(exercise("operator"))


def test_private_audit_is_retained_without_becoming_a_verdict_oracle(tmp_path):
    root = tmp_path / "audit"
    root.mkdir()
    (root / "rule.txt").write_text("0\n", newline="\n")
    scenario = Minimal()
    scenario.private_literals = ("secret-operator-literal",)
    workspace = Workspace(root, scenario)
    workspace.initialize()
    workspace.contract_record(contract())
    child = adopt(workspace, "1\nsecret-operator-literal\n")
    aggregate = workspace.confirm_adoption(child["version"])
    record = next(e["data"] for e in workspace.ledger.history() if e["kind"] == "confirmation")
    assert record["private_literal_match"]
    assert aggregate["verdict"] == "confirmed"
    assert "private_literal_match" not in aggregate
    assert "private_literal_match" not in json.dumps(workspace.history())
    assert not workspace.proposals()[0]["not_confirmed"]


def test_scorer_relocation_preserves_content_and_callable_anchors():
    from evokit.anchors import scorer_sources_match

    expected = {
        "C:\\old\\evokit\\scenario.py": "abc",
        "source:scenario.yaml": "config",
        "callable:run_and_score": "code",
    }
    relocated = {
        "C:\\new\\evokit\\scenario.py": "abc",
        "source:scenario.yaml": "config",
        "callable:run_and_score": "code",
    }
    assert not scorer_sources_match(expected, relocated)
    assert scorer_sources_match(expected, relocated, owned_source="C:\\new\\evokit\\scenario.py")
    for key in relocated:
        assert not scorer_sources_match(
            expected, {**relocated, key: "changed"}, owned_source="C:\\new\\evokit\\scenario.py"
        )
    assert not scorer_sources_match(
        expected,
        {**relocated, "C:\\extra\\scenario.py": "abc"},
        owned_source="C:\\new\\evokit\\scenario.py",
    )


def test_doctor_execution_also_freezes_contract(tmp_path, record_backend):
    from evokit.onboarding import doctor

    workspace, config = final_workspace(tmp_path, record_backend)
    try:
        doctor(config, run=True)
        with pytest.raises(ValueError, match="frozen"):
            workspace.contract_record(contract())
    finally:
        workspace.close()


def test_holdout_config_alias_preserves_four_way_assignments(tmp_path, record_backend):
    workspace, config = final_workspace(tmp_path, record_backend)
    try:
        expected = workspace._epoch()["splits"]
        alias_root = tmp_path / "alias"
        shutil.copytree(config.parent, alias_root, ignore=shutil.ignore_patterns(".evokit"))
        alias_config = alias_root / "scenario.yaml"
        data = yaml.safe_load(alias_config.read_text())
        data["splits"]["holdout"] = data["splits"].pop("confirmation")
        alias_config.write_text(yaml.safe_dump(data), encoding="utf-8")
        alias = Workspace.open(alias_config)
        try:
            assert alias._epoch()["splits"] == expected
            assert alias.describe()["counts"] == workspace.describe()["counts"]
        finally:
            alias.close()
    finally:
        workspace.close()


def test_doctor_and_final_are_serialized(tmp_path, record_backend, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from evokit.onboarding import doctor

    workspace, config = final_workspace(tmp_path, record_backend)
    workspace.contract_record(contract())
    executing, release, final_started = Event(), Event(), Event()
    original = Workspace._execute_task

    def controlled(self, *args, **kwargs):
        if not executing.is_set():
            executing.set()
            assert release.wait(20)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Workspace, "_execute_task", controlled)

    def open_final():
        final_started.set()
        return workspace.final_measure()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            checking = pool.submit(doctor, config, run=True)
            assert executing.wait(120 if record_backend["backend"] == "hb" else 20)
            measuring = pool.submit(open_final)
            assert final_started.wait(20)
            assert not release.wait(0.05)
            assert not measuring.done()
            assert not any(e["kind"] == "final_open" for e in workspace.ledger.history())
            release.set()
            checking.result(timeout=30)
            assert measuring.result(timeout=30)["net"] == 0
    finally:
        release.set()
        workspace.close()
