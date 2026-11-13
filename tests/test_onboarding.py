"""Executable onboarding, calibrated scoring and public-only diagnostics."""

import json
import subprocess
import sys

import pytest

from evokit import Workspace
from evokit.onboarding import doctor, initialize_scenario


@pytest.fixture
def inputs(tmp_path):
    tasks = tmp_path / "source-tasks.json"
    tasks.write_text(
        json.dumps([{"id": f"case-{i:03d}", "payload": {"value": i}} for i in range(20)])
    )
    root = tmp_path / "scenario"
    root.mkdir()
    (root / "rule.txt").write_text("generic rule\n")
    (root / "score.py").write_text("""import json, os
from pathlib import Path
task = json.loads(Path(os.environ["EVOKIT_TASK_FILE"]).read_text())
Path(os.environ["EVOKIT_RESULT_FILE"]).write_text(json.dumps({
    "outcome": True, "reason": "fixture measured", "cost": {"calls": 1, "tokens": 3},
    "seconds": 2,
}))
""")
    return root, tasks


@pytest.mark.parametrize("adapter", ["command", "python"])
def test_init_to_calibrated_doctor_and_cli(inputs, adapter):
    root, tasks = inputs
    initialize_scenario(root, ["rule.txt"], tasks, [sys.executable, "score.py"], adapter=adapter)
    config = root / "scenario.yaml"
    static = doctor(config)
    assert not static["ready"] and static["checks"]["dry_run"]["status"] == "skipped"
    assert not (root / ".evokit/doctor").exists()
    workspace = Workspace.open(config)
    public = workspace._epoch()["splits"]["selection"]
    private = workspace._epoch()["splits"]["confirmation"]
    workspace.close()
    labels = {public[-1]: 1}
    report = doctor(config, run=True, labels=labels)
    assert report["ready"]
    assert report["checks"]["dry_run"]["invocations"] == 5
    assert report["cost_per_task"] == {"calls": 1, "tokens": 3}
    assert report["seconds_per_task"] == 2 and report["outcome_agreement"] == 1
    assert report["config_identity"] == static["config_identity"]
    assert not any(task in json.dumps(report) for task in private)
    labels_file = root.parent / "labels.json"
    labels_file.write_text(json.dumps(labels))
    cli = subprocess.run(
        [
            sys.executable,
            "-m",
            "evokit",
            "doctor",
            "--config",
            str(config),
            "--run",
            "--labels",
            str(labels_file),
        ],
        capture_output=True,
        text=True,
    )
    assert cli.returncode == 0, cli.stderr
    assert json.loads(cli.stdout)["ready"]
    labels[public[-1]] = 0
    failed = doctor(config, run=True, labels=labels)
    assert failed["checks"]["scorer_sanity"]["status"] == "fail"


def test_init_conflicts_do_not_overwrite_or_partially_write(inputs):
    root, tasks = inputs
    (root / "scenario.yaml").write_text("existing")
    with pytest.raises(ValueError, match="overwrite"):
        initialize_scenario(root, ["rule.txt"], tasks, adapter="python")
    assert (root / "scenario.yaml").read_text() == "existing"
    assert not (root / "runner.py").exists() and not (root / "tasks.json").exists()


def test_unfinished_runner_and_invalid_labels_never_pass(inputs):
    root, tasks = inputs
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "evokit",
            "init",
            str(root),
            "--editable",
            "rule.txt",
            "--tasks",
            str(tasks),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    config = root / "scenario.yaml"
    with pytest.raises(ValueError, match="public task ids"):
        doctor(config, run=True, labels={"not-a-public-task": 1})
    report = doctor(config, run=True)
    assert not report["ready"] and report["checks"]["dry_run"]["status"] == "fail"
    assert report["checks"]["dry_run"]["invocations"] == 1
    assert report["cost_per_task"] is None


def test_leak_scan_does_not_expose_private_membership(inputs):
    root, tasks = inputs
    initialize_scenario(root, ["rule.txt"], tasks, [sys.executable, "score.py"])
    config = root / "scenario.yaml"
    workspace = Workspace.open(config)
    splits = workspace._epoch()["splits"]
    workspace.close()
    with pytest.raises(ValueError, match="public task ids"):
        doctor(config, labels={splits["confirmation"][0]: 1})
    (root / "rule.txt").write_text(splits["confirmation"][0])
    private = doctor(config)
    assert private["checks"]["leak_scan"]["status"] == "pass"
    assert splits["confirmation"][0] not in json.dumps(private)
    (root / "rule.txt").write_text(splits["diagnosis"][0])
    public = doctor(config, run=True)
    assert public["checks"]["leak_scan"]["status"] == "fail"
    assert public["checks"]["dry_run"]["status"] == "skipped"


def test_source_identity_rejects_mutating_adapter_settings(inputs):
    root, tasks = inputs
    initialize_scenario(root, ["rule.txt"], tasks, [sys.executable, "score.py"])
    config = root / "scenario.yaml"
    before = doctor(config)
    (root / "score.py").write_text("raise RuntimeError('changed scorer')")
    with pytest.raises(ValueError, match="anchor|support|scorer"):
        doctor(config)
    assert before["checks"]["reproducibility"]["status"] == "pass"
