import copy

import pytest
import yaml

from evokit import Workspace
from evokit.config import validate_config

VALID = {
    "editable": ["rule.txt"],
    "tasks": [{"id": "case", "payload": {}}],
    "command": ["never-execute"],
}


@pytest.mark.parametrize(
    "edit",
    [
        {"planning": True},
        {"options": {"unknown": True}},
        {"timeout_seconds": -1},
        {"budgets": {"latency_ratios": 1.2}},
        {"tasks": [{"id": "a", "payload": {}, "gold": "x"}]},
        {"splits": {"diagnosis": 0.4, "selection": 0.4, "holdout": 0.2, "confirmation": 0.2}},
        {"storage": {"backend": "files", "workspace": "ignored"}},
    ],
)
def test_unknown_or_unsupported_config_refused_before_state(tmp_path, edit):
    (tmp_path / "rule.txt").write_text("0")
    config = tmp_path / "scenario.yaml"
    config.write_text(yaml.safe_dump({**VALID, **edit}))
    with pytest.raises(ValueError, match="config|splits|storage"):
        Workspace.open(config)
    assert not (tmp_path / ".evokit").exists()


def test_valid_v012_command_config_keeps_defaults():
    original = copy.deepcopy(VALID)
    assert validate_config(original) == VALID
    assert original == VALID


def test_python_mode_rejects_ignored_command_keys():
    with pytest.raises(ValueError, match="config"):
        validate_config({"adapter": "adapter.py:Scenario", "editable": ["ignored"]})


def test_python_unknown_options_refused_before_constructor(tmp_path):
    (tmp_path / "adapter.py").write_text(
        "from evokit import Scenario\n"
        "class Adapter(Scenario):\n"
        " def __init__(self, *, supported=1):\n"
        "  raise AssertionError('constructor must not run')\n"
        " def tasks(self): return []\n"
        " def run_and_score(self, version_dir, task): pass\n"
    )
    config = tmp_path / "scenario.yaml"
    config.write_text(
        yaml.safe_dump({"adapter": "adapter.py:Adapter", "options": {"invented": True}})
    )
    with pytest.raises(ValueError, match="Unsupported Python adapter options"):
        Workspace.open(config)
    assert not (tmp_path / ".evokit").exists()
