import importlib.util
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from conftest import Minimal, candidate, evidence, proposal

from evokit import CommandScenario, Result, Task, Workspace
from evokit.anchors import code_fingerprint
from evokit.guards import introduced_literal
from evokit.scenario import load_scenario

ADAPTER = """from __future__ import annotations
from dataclasses import dataclass
from evokit import Scenario, Task, Result

@dataclass
class Input:
    value: int = 1

class Adapter(Scenario):
    editable = ("rule.txt",)
    def tasks(self):
        return [Task(f"sample-{i:03d}", {"value": i}) for i in range(15)]
    def run_and_score(self, version_dir, task):
        return Result(bool(Input().value), "measured")
"""


def test_yaml_adapter_with_future_annotations_and_dataclass(tmp_path, backend_config):
    (tmp_path / "adapter.py").write_text(ADAPTER)
    (tmp_path / "rule.txt").write_text("0")
    config = tmp_path / "scenario.yaml"
    config.write_text("adapter: ./adapter.py:Adapter\n")
    backend_config(config)
    workspace = Workspace.open(config)
    assert workspace.launch(workspace.current(), smoke=True, wait=True)["errors"] == 0


def test_concurrent_adapter_loading_waits_for_complete_module(tmp_path):
    slow = ADAPTER.replace(
        "from dataclasses import dataclass",
        "from dataclasses import dataclass\nimport time\ntime.sleep(0.1)",
    )
    (tmp_path / "adapter.py").write_text(slow)
    config = tmp_path / "scenario.yaml"
    config.write_text("adapter: ./adapter.py:Adapter\n")
    with ThreadPoolExecutor(max_workers=4) as pool:
        adapters = list(pool.map(lambda _: load_scenario(config), range(4)))
    assert all(len(adapter.tasks()) == 15 for adapter in adapters)


def test_known_scorer_source_cannot_overlap_editable_scope(tmp_path, backend_config):
    (tmp_path / "adapter.py").write_text(ADAPTER.replace('("rule.txt",)', '("*.py", "rule.txt")'))
    (tmp_path / "rule.txt").write_text("0")
    config = tmp_path / "scenario.yaml"
    config.write_text("adapter: ./adapter.py:Adapter\n")
    backend_config(config)
    with pytest.raises(ValueError, match="cannot be editable"):
        Workspace.open(config)


def test_direct_adapter_source_outside_root_is_anchored(tmp_path, record_backend):
    source = tmp_path / "external_adapter.py"
    source.write_text(ADAPTER)
    spec = importlib.util.spec_from_file_location("external_test_adapter", source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    root = tmp_path / "system"
    root.mkdir()
    (root / "rule.txt").write_text("0")
    workspace = Workspace(root, module.Adapter(), storage=record_backend)
    workspace.initialize()
    source.write_text(ADAPTER + "\n# changed scorer source\n")
    with pytest.raises(ValueError, match="scorer implementation"):
        Workspace(root, module.Adapter(), storage=record_backend).initialize()
    with pytest.raises(ValueError, match="scorer implementation"):
        workspace.launch(workspace.current(), wait=True)


def test_runtime_scorer_replacement_is_anchored(workspace):
    workspace.scenario.run_and_score = lambda directory, task: None
    with pytest.raises(ValueError, match="scorer implementation"):
        workspace.launch(workspace.current(), wait=True)


def test_code_fingerprint_survives_interpreter_warmup(workspace):
    before = workspace._scorer_sources()
    for _ in range(30):
        workspace.launch(workspace.current(), smoke=True, wait=True)
        assert workspace._scorer_sources() == before


@pytest.mark.parametrize("path_kind", ["absolute", "traversal", "protocol"])
def test_command_trace_containment(tmp_path, path_kind):
    directory = tmp_path / "checkout"
    directory.mkdir()
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("private sentinel")
    trace = str(sentinel) if path_kind == "absolute" else "../sentinel.txt"
    script = directory / "run.py"
    script.write_text(
        "import json, os\nfrom pathlib import Path\n"
        + (
            "trace = Path(os.environ['EVOKIT_TASK_FILE']).with_name('trace.txt')\n"
            "trace.write_text('protocol trace')\n"
            if path_kind == "protocol"
            else f"trace = {trace!r}\n"
        )
        + "Path(os.environ['EVOKIT_RESULT_FILE']).write_text(json.dumps({"
        "'outcome': True, 'reason': 'ok', 'trace_path': str(trace)}))\n"
    )
    scenario = CommandScenario(
        directory, {"editable": ["rule.txt"], "tasks": [], "command": [sys.executable, "run.py"]}
    )
    if path_kind == "protocol":
        result = scenario.run_and_score(directory, Task("public-case", {}))
        assert Path(result.trace_path).read_text() == "protocol trace"
    else:
        with pytest.raises(ValueError, match="trace must stay"):
            scenario.run_and_score(directory, Task("public-case", {}))
        assert not list(directory.glob("command-trace-*"))


def test_guard_checks_introductions_not_removals_or_substrings():
    ids = ["case-001", "1"]
    assert not introduced_literal({"x": b"case-001\n0\n"}, {"x": b"1\n"}, ids, [])
    assert not introduced_literal({}, {"x": b"case-001extra\n"}, ids, [])
    assert introduced_literal({}, {"x": b"'case-001'\n"}, ids, [])
    assert introduced_literal({}, {"x": b"'1'\n"}, ids, [])
    assert introduced_literal({}, {"x": b"the gold phrase\n"}, [], ["gold phrase"])


def test_numeric_ids_do_not_block_generic_scalar_change(workspace):
    class NumericIds(Minimal):
        def tasks(self):
            return [Task(str(i), {"value": i}) for i in range(30)]

    fresh = Workspace(
        workspace.root,
        NumericIds(),
        workspace.root.parent / "numeric",
        storage={"backend": workspace.store.backend},
    )
    fresh.initialize()
    item, before, after, noise = evidence(fresh)
    assert (
        fresh.gate(item["id"], before, after, noise, "mean", generic=True)["verdict"] == "promote"
    )


def test_public_guard_is_independent_of_private_membership(workspace):
    hidden = workspace._epoch()["splits"]["confirmation"][0]
    child = candidate(workspace, f"1 {hidden}\n")
    item = proposal(workspace, child)
    matched = workspace.gate(item["id"], "unknown", "unknown", "unknown", "mean", generic=True)
    other = candidate(workspace, "1 nonexistent-holdout-reference\n")
    other_proposal = proposal(workspace, other)
    unmatched = workspace.gate(
        other_proposal["id"], "unknown", "unknown", "unknown", "mean", generic=True
    )
    # Both reach the same public evidence-validation failure, with no private match status.
    assert matched["reasons"] == unmatched["reasons"]
    assert "Instance-literal" not in json.dumps(matched)


def test_private_literal_audit_is_recorded_without_improver_feedback(workspace):
    workspace.scenario.private_literals = ("private-reference-marker",)
    fresh = Workspace(
        workspace.root,
        workspace.scenario,
        workspace.root.parent / "audit",
        storage={"backend": workspace.store.backend},
    )
    fresh.initialize()
    item, before, after, noise = evidence(fresh, "1 private-reference-marker\n")
    verdict = fresh.gate(item["id"], before, after, noise, "mean", generic=True)
    assert verdict["verdict"] == "promote"
    adopted = fresh.adopt(verdict["id"], "public paired evidence")
    fresh.measure_holdout(adopted["version"])
    measurement = next(
        event["data"] for event in fresh.ledger.history() if event["kind"] == "holdout_measurement"
    )
    assert measurement["private_literal_match"] is True
    assert "private_literal_match" not in json.dumps([fresh.history(), fresh.metrics()])


def test_public_declared_gold_is_guarded(workspace):
    workspace.scenario.gold_strings = ("public-reference-marker",)
    fresh = Workspace(
        workspace.root,
        workspace.scenario,
        workspace.root.parent / "gold",
        storage={"backend": workspace.store.backend},
    )
    fresh.initialize()
    item, before, after, noise = evidence(fresh, "1 public-reference-marker\n")
    verdict = fresh.gate(item["id"], before, after, noise, "mean", generic=True)
    assert verdict["verdict"] == "reject"
    assert "Instance-literal" in json.dumps(verdict["reasons"])


def test_direct_command_config_is_copied_and_anchored(tmp_path, record_backend):
    (tmp_path / "rule.txt").write_text("0")
    config = {
        "editable": ["rule.txt"],
        "tasks": [{"id": f"command-{i:03d}", "payload": {}} for i in range(15)],
        "command": [sys.executable, "score.py", "strict"],
    }
    scenario = CommandScenario(tmp_path, config)
    workspace = Workspace(tmp_path, scenario, storage=record_backend)
    workspace.initialize()
    config["command"][-1] = "lenient"
    assert scenario.config["command"][-1] == "strict"
    with pytest.raises(ValueError, match="policy changed"):
        Workspace(tmp_path, CommandScenario(tmp_path, config), storage=record_backend).initialize()
    scenario.config["command"][-1] = "lenient"
    with pytest.raises(ValueError, match="policy changed"):
        workspace.launch(workspace.current(), wait=True)


def test_binary_guard_preserves_plaintext_unicode_spans():
    content = {"derived.bin": b"\xff" + "public-résumé-marker".encode()}
    assert introduced_literal({}, content, [], ["public-résumé-marker"])
    assert introduced_literal({}, content, ["public-résumé-marker"], [])


def test_scorer_fingerprint_accepts_infinite_and_complex_constants(workspace):
    class Bounded(Minimal):
        def run_and_score(self, version_dir, task):
            return Result(task.payload["value"] < 1e309, "bounded")

    def complex_value():
        return 1e309j

    assert code_fingerprint(complex_value.__code__)
    fresh = Workspace(
        workspace.root,
        Bounded(),
        workspace.root.parent / "bounded",
        storage={"backend": workspace.store.backend},
    )
    fresh.initialize()
    assert fresh.launch(fresh.current(), smoke=True, wait=True)["errors"] == 0
