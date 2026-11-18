"""Tool definitions: every tool and parameter is described; annotations match behavior."""

import asyncio
import hashlib
import shutil
import sys
from pathlib import Path

import pytest
from conftest import mcp_environment
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from evokit.mcp import INSTRUCTIONS
from evokit.tools import HB_TOOLS, OPERATOR_TOOLS, TOOLS, call_tool

ROOT = Path(__file__).resolve().parents[1]
ALL = {tool.name: tool for tool in TOOLS + OPERATOR_TOOLS + HB_TOOLS}
HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")
READ_ONLY = (True, False, True, False)
# (readOnly, destructive, idempotent, openWorld), checked against behavior below.
EXPECTED = {
    "scenario_describe": READ_ONLY,
    "version_current": READ_ONLY,
    "version_create": (False, False, False, False),
    "version_diff": READ_ONLY,
    "run_launch": (False, False, False, True),
    "run_interleaved": (False, False, True, True),
    "run_status": READ_ONLY,
    "run_results": READ_ONLY,
    "compare_paired": READ_ONLY,
    "noise_estimate": (False, False, False, False),
    "failure_cards": (False, False, True, False),
    "proposal_create": (False, False, False, False),
    "proposal_list": READ_ONLY,
    "gate_check": (False, False, False, False),
    "ledger_adopt": (False, False, False, False),
    "ledger_history": READ_ONLY,
    "meta_metrics": READ_ONLY,
    "contract_record": (False, False, False, False),
    "contract_read": READ_ONLY,
    "confirm_adoption": (False, False, True, True),
    "ledger_revert": (False, False, False, False),
    "final_measure": (False, True, False, True),
    "records_query": READ_ONLY,
    "failures_similar": (False, False, True, False),
}


def undescribed(schema, path):
    """Yield every schema property, at any depth, that lacks a description."""
    for name, value in schema.get("properties", {}).items():
        where = f"{path}.{name}"
        if not str(value.get("description", "")).strip():
            yield where
        yield from undescribed(value, where)


def test_every_tool_and_parameter_is_described():
    assert len(TOOLS) == 16 and set(ALL) == set(EXPECTED)
    for tool in ALL.values():
        assert len(tool.description) >= 120, tool.name
        assert not tool.description.startswith("["), tool.name
        assert "Returns" in tool.description and "use " in tool.description.lower(), tool.name
        assert tool.schema["properties"], tool.name
        assert list(undescribed(tool.schema, tool.name)) == []
        assert set(tool.schema["required"]) <= set(tool.schema["properties"])
        assert tuple(tool.annotations[hint] for hint in HINTS) == EXPECTED[tool.name]
        assert ("Read-only" in tool.description) == tool.annotations["readOnlyHint"]


def snapshot(root):
    """Content of every workspace file except lock sentinels and bytecode caches."""
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix != ".lock" and "__pycache__" not in path.parts
    }


def test_annotations_match_workspace_side_effects(tmp_path):
    root = tmp_path / "s"
    shutil.copytree(ROOT / "examples/toy-rules", root)
    config = root / "scenario.yaml"
    with config.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(
            "splits: {diagnosis: 0.3, selection: 0.3, confirmation: 0.2, final: 0.2}\n"
        )
    checked = set()

    def call(name, **arguments):
        tool = ALL[name]
        before = snapshot(root)
        result = call_tool(name, {"config": str(config), **arguments}, profile="operator")
        after = snapshot(root)
        if checked or name != "scenario_describe":
            assert (before == after) == tool.annotations["readOnlyHint"], name
        if tool.annotations["idempotentHint"] and not tool.annotations["readOnlyHint"]:
            assert call_tool(name, {"config": str(config), **arguments}, profile="operator")
            assert snapshot(root) == after, name
        checked.add(name)
        return result

    call("scenario_describe")  # The first call also creates the workspace.
    call("scenario_describe")
    base = call("version_current")["version"]
    call(
        "contract_record",
        contract={
            "objective": "Classify toy cases correctly",
            "guards": {"latency_ratio": 10, "cost_ratio": {"stage1": 10, "stage2": 10}},
            "confirmation_rule": {"min_net": 0},
            "noise_plan": {"repeats": 2, "method": "paired flip rate"},
            "budget": {"unit": "stage2", "total": 1000, "per_generation": 500},
            "stagnation_window": 2,
        },
    )
    call("contract_read")
    diagnosis = call("run_launch", version=base, split="diagnosis", workers=2, wait=True)
    call("run_status", run_id=diagnosis["run_id"])
    call("run_results", run_id=diagnosis["run_id"], limit=3)
    assert call("failure_cards", run_id=diagnosis["run_id"], details=False)
    (root / "rules.json").write_text('{"threshold": 0}\n', encoding="utf-8")
    child = call("version_create")["version"]
    call("version_diff", parent=base, child=child)
    proposal = call(
        "proposal_create",
        version=child,
        proposal={
            "targets": ["rules.json"],
            "mechanism": "Remove a systematic global decision offset",
            "predicted_effect": 4,
            "falsification": "Any paired net loss",
            "prior": {"attempt": None, "difference": "First attempt"},
        },
    )
    call("proposal_list")
    call("run_interleaved", arms={"parent-1": base, "child-1": child}, repeats=2, workers=2)
    noise = call("noise_estimate", run_id="parent-1")
    call("compare_paired", parent_run="parent-1", child_run="child-1", aggregation="majority")
    verdict = call(
        "gate_check",
        proposal_id=proposal["id"],
        parent_run="parent-1",
        child_run="child-1",
        noise_id=noise["noise_id"],
        aggregation="majority",
        generic=True,
    )
    assert verdict["verdict"] == "promote", verdict["reasons"]
    call("ledger_history")
    call("meta_metrics")
    call("ledger_adopt", verdict_id=verdict["id"], rationale="Generic offset correction")
    call("confirm_adoption", version=child, workers=2)
    call("ledger_revert", reason="Exercise the revert path")
    call("final_measure", workers=2)
    with pytest.raises(ValueError, match="Final measurement has opened"):
        call_tool("run_launch", {"config": str(config), "version": base, "wait": True})
    hb_only = {tool.name for tool in HB_TOOLS}
    assert checked == set(EXPECTED) - hb_only


def test_mcp_lists_descriptions_annotations_and_instructions():
    async def exercise():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "evokit", "mcp", "--profile", "operator"],
            env=mcp_environment(),
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                listed = await session.list_tools()
        return initialized, listed

    initialized, listed = asyncio.run(exercise())
    assert initialized.instructions == INSTRUCTIONS
    tools = {tool.name: tool for tool in listed.tools}
    assert set(tools) == {tool.name for tool in TOOLS + OPERATOR_TOOLS}
    for name, tool in tools.items():
        expected = ALL[name]
        dumped = tool.model_dump(by_alias=True, exclude_none=True)
        assert dumped["description"] == expected.description
        assert dumped["annotations"] == expected.annotations
        assert dumped["inputSchema"] == expected.schema
