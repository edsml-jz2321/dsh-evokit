import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from evokit import Result, Scenario, Task, Workspace


class Minimal(Scenario):
    editable = ("rule.txt",)

    def tasks(self):
        return [Task(f"case-{i:03d}", {"value": i}, f"g{i % 2}") for i in range(30)]

    def run_and_score(self, version_dir, task):
        text = (version_dir / "rule.txt").read_text()
        trace = version_dir / "trace.txt"
        trace.write_text(f"value={task.payload['value']}; rule={text}")
        return Result(
            text.startswith("1"),
            "rule decision",
            {"calls": 2 if "expensive" in text else 1},
            str(trace),
            text[:30],
            seconds=2 if "slow" in text else 1,
        )


@pytest.fixture(scope="session", autouse=True)
def private_hb_home(tmp_path_factory):
    """One private process home; setup is never reset and never touches user HB."""
    if importlib.util.find_spec("heavenbase") is None:
        yield
        return
    home = tmp_path_factory.mktemp("hbhome")
    names = ("HOME", "USERPROFILE", "EVOKIT_HB_HOME")
    original = {name: os.environ.get(name) for name in names}
    for name in names:
        os.environ[name] = str(home)
    subprocess.run(
        [sys.executable, "-m", "heavenbase.cli.app", "setup"], check=True, capture_output=True
    )
    yield
    for name, value in original.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


@pytest.fixture(params=["files", "hb"])
def record_backend(request, private_hb_home):
    if request.param == "hb" and importlib.util.find_spec("heavenbase") is None:
        pytest.skip("Install evokit[hb] for backend parity")
    return {"backend": request.param}


@pytest.fixture
def workspace(tmp_path, record_backend):
    root = tmp_path / "s"
    root.mkdir()
    (root / "rule.txt").write_text("0\n", newline="\n")
    (root / "support.txt").write_text("frozen\n")
    workspace = Workspace(root, Minimal(), storage=record_backend)
    workspace.initialize(seed=17)
    yield workspace
    workspace.close()


def candidate(workspace, content="1\n"):
    (workspace.root / "rule.txt").write_text(content, newline="\n")
    return workspace.create_version()["version"]


def proposal(workspace, child):
    return workspace.propose(
        child,
        {
            "targets": ["rule.txt"],
            "mechanism": "generic improvement",
            "predicted_effect": 2,
            "falsification": "net is negative",
            "prior": {"attempt": None, "difference": "initial attempt"},
        },
    )


def evidence(workspace, content="1\n"):
    parent = workspace.current()
    child = candidate(workspace, content)
    item = proposal(workspace, child)
    before = workspace.launch(parent, repeats=2, workers=3, wait=True)["run_id"]
    after = workspace.launch(child, repeats=2, workers=3, wait=True)["run_id"]
    noise = workspace.noise(before)["noise_id"]
    return item, before, after, noise


def tool_error(result):
    """Read the protocol flag identically with official MCP SDK 1 and 2 models."""
    return result.model_dump(by_alias=True).get("isError", False)


def mcp_environment():
    """Forward the private HB home through the SDK's sanitized subprocess env."""
    return {
        name: os.environ[name]
        for name in ("HOME", "USERPROFILE", "EVOKIT_HB_HOME")
        if name in os.environ
    }


@pytest.fixture
def backend_config(record_backend):
    """Select the fixture backend through the real optional YAML contract."""
    configured = set()

    def configure(path):
        path = Path(path)
        if path not in configured:
            with path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write("\nstorage: " + json.dumps(record_backend) + "\n")
            configured.add(path)

    return configure
