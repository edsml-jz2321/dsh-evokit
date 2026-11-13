import asyncio
import importlib.util
import json
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from conftest import mcp_environment, tool_error
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from evokit import StageCache, Workspace
from evokit.tools import TOOLS, call_tool

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("example", ["toy-rules", "command-echo"])
def test_scripted_end_to_end_examples(example, tmp_path, backend_config, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "scripted", ROOT / "examples/scripted_improver.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original_open = module.Workspace.open

    def selected_open(config):
        backend_config(config)
        return original_open(config)

    monkeypatch.setattr(module.Workspace, "open", staticmethod(selected_open))
    result = module.evolve(example, tmp_path / "s")
    assert result["verdict"] == "promote" and result["comparison"]["net"] > 0
    assert result["metrics"]["holdout"]["measured_versions"] == 1
    if example == "toy-rules":
        workspace = original_open(tmp_path / "s" / "scenario.yaml")
        if workspace.store.backend == "hb":
            assert (workspace.state / "operator/cache/confirmation").is_dir()
            assert not (workspace.state / "cache/confirmation").exists()
        workspace.close()


def test_stage_cache_single_producer_and_invalidation(tmp_path):
    cache = StageCache(tmp_path / "cache")
    calls = []

    def compute():
        calls.append(1)
        return {"answer": 42}

    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(lambda _: cache.replay("first", {"code": 1}, compute), range(8)))
    assert len(calls) == 1 and sum(not replayed for _, replayed in values) == 1
    assert cache.replay("first", {"code": 2}, compute)[1] is False


def test_toy_replays_unchanged_stage_one_at_zero_cost(tmp_path, backend_config):
    root = tmp_path / "s"
    shutil.copytree(ROOT / "examples/toy-rules", root)
    backend_config(root / "scenario.yaml")
    workspace = Workspace.open(root / "scenario.yaml")
    before = workspace.launch(workspace.current(), wait=True)["run_id"]
    (root / "rules.json").write_text('{"threshold":0}')
    after = workspace.create_version()["version"]
    arm = workspace.launch(after, wait=True)["run_id"]
    assert sum(row["cost"]["stage1"] for row in workspace.results(before)) > 0
    assert all(row["cost"]["stage1"] == 0 for row in workspace.results(arm))
    assert all(row["output"]["stage1_replayed"] for row in workspace.results(arm))


def test_seeded_toy_noise_is_measured(tmp_path, backend_config):
    root = tmp_path / "s"
    shutil.copytree(ROOT / "examples/toy-rules", root)
    config = root / "scenario.yaml"
    config.write_text(config.read_text().replace("noise_rate: 0.0", "noise_rate: 0.5"))
    backend_config(config)
    workspace = Workspace.open(config)
    run = workspace.launch(workspace.current(), repeats=4, workers=3, wait=True)
    noise = workspace.noise(run["run_id"])
    assert 0 < noise["mean_flip_rate"] <= 1 and noise["threshold"] > 0


def test_cli_parity_and_detached_run(tmp_path, backend_config, record_backend):
    root = tmp_path / "s"
    shutil.copytree(ROOT / "examples/command-echo", root)
    config = str(root / "scenario.yaml")
    backend_config(config)
    expected = call_tool("version_current", {"config": config})
    completed = subprocess.run(
        [sys.executable, "-m", "evokit", "version_current", "--config", config],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == expected
    arm = call_tool("run_launch", {"config": config, "version": expected["version"], "smoke": True})
    deadline = time.monotonic() + (120 if record_backend["backend"] == "hb" else 40)
    while time.monotonic() < deadline:
        status = call_tool("run_status", {"config": config, "run_id": arm["run_id"]})
        if status["state"] == "completed":
            break
        time.sleep(0.1)
    assert status["state"] == "completed" and status["completed"] == 2
    arguments = tmp_path / "args.json"
    arguments.write_text(json.dumps({"run_id": arm["run_id"]}), encoding="utf-8-sig")
    file_call = subprocess.run(
        [
            sys.executable,
            "-m",
            "evokit",
            "run_status",
            "--config",
            config,
            "--args-file",
            str(arguments),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(file_call.stdout) == status


def test_mcp_stdio_lists_and_calls_tools(tmp_path, backend_config):
    root = tmp_path / "s"
    shutil.copytree(ROOT / "examples/command-echo", root)
    config = str(root / "scenario.yaml")

    backend_config(config)

    async def exercise():
        parameters = StdioServerParameters(
            command=sys.executable, args=["-m", "evokit", "mcp"], env=mcp_environment()
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                assert {tool.name for tool in listed.tools} == {tool.name for tool in TOOLS}
                description = await session.call_tool("scenario_describe", {"config": config})
                assert not tool_error(description)
                assert "counts" in json.loads(description.content[0].text)
                current = await session.call_tool("version_current", {"config": config})
                version = json.loads(current.content[0].text)["version"]
                arm = await session.call_tool(
                    "run_launch",
                    {"config": config, "version": version, "smoke": True, "wait": True},
                )
                assert not tool_error(arm) and json.loads(arm.content[0].text)["completed"] == 2
                invalid = await session.call_tool(
                    "run_launch", {"config": config, "version": version, "split": "holdout"}
                )
                assert tool_error(invalid)

    asyncio.run(exercise())
