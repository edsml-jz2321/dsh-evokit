"""Portability of the original byte-anchored files workspace format."""

import shutil
from pathlib import Path

import pytest
import yaml

from evokit import Workspace
from evokit.portability import export_records, import_records


def test_original_files_epoch_migrates_at_original_path(tmp_path, monkeypatch):
    pytest.importorskip("heavenbase")
    root = tmp_path / "scenario"
    shutil.copytree(Path(__file__).resolve().parents[1] / "examples/command-echo", root)
    config = root / "scenario.yaml"
    data = yaml.safe_load(config.read_text())
    data["workspace"] = "records-v0"
    config.write_text(yaml.safe_dump(data), encoding="utf-8")
    original_config = config.read_bytes()
    source = Workspace.open(config, initialize=False)
    # The original format froze raw config/source paths and had none of the new
    # format/state hints. Remove those fields before the epoch event is hashed.
    source._normalized_config = False
    save = source.store.put

    def original_epoch(kind, key, value, **options):
        if kind == "epoch":
            value.pop("record_format", None)
            value.pop("state_path", None)
        save(kind, key, value, **options)

    with monkeypatch.context() as context:
        context.setattr(source.store, "put", original_epoch)
        source.initialize(seed=data.get("seed", 0))
    (source.state / "storage.json").unlink()
    shutil.rmtree(source.state / "records")
    arm = source.launch(source.current(), smoke=True, wait=True)
    expected_rows = source.results(arm["run_id"])
    expected_history = source.history()
    expected_epoch = source._epoch()
    assert "record_format" not in expected_epoch
    backup = tmp_path / "operator.jsonl"
    export_records(source.store, backup)
    state = source.state
    source.close()
    saved = tmp_path / "saved-v0"
    state.rename(saved)
    restored = Workspace.open(config, initialize=False, storage={"backend": "hb"})
    import_records(restored.store, backup)
    for name in ("blobs", "versions", "playbooks"):
        if (saved / name).exists():
            shutil.copytree(saved / name, state / name)
    restored.close()
    # YAML bytes and state path stay unchanged; the persisted selector chooses HB.
    reopened = Workspace.open(config)
    assert reopened.store.backend == "hb"
    assert config.read_bytes() == original_config
    assert reopened._epoch() == expected_epoch
    assert reopened.history() == expected_history
    assert sorted(reopened.results(arm["run_id"]), key=lambda r: r["task_id"]) == sorted(
        expected_rows, key=lambda r: r["task_id"]
    )
    assert (
        reopened.launch(reopened.current(), smoke=True, run_id=arm["run_id"], wait=True)[
            "completed"
        ]
        == 2
    )
    reopened.close()
