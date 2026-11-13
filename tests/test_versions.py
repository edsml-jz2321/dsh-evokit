import json

import pytest
from conftest import candidate

from evokit.storage import safe_path


def test_content_address_diff_and_isolated_materialization(workspace, tmp_path):
    before = workspace.current()
    after = candidate(workspace)
    assert before != after
    assert workspace.create_version()["version"] == after
    assert workspace.diff(before, after)["changed"] == ["rule.txt"]
    target = workspace.materialize(before, tmp_path / "v")
    assert (target / "rule.txt").read_text() == "0\n"
    assert (target / "support.txt").read_text() == "frozen\n"
    (target / "rule.txt").write_text("different")
    assert workspace.current() == before
    with pytest.raises(FileExistsError):
        workspace.materialize(after, target)


def test_directory_and_patch_scope(workspace, tmp_path):
    before = workspace.current()
    patch = "--- a/rule.txt\n+++ b/rule.txt\n@@ -1 +1 @@\n-0\n+1\n"
    after = workspace.create_version(patch=patch)["version"]
    assert workspace.diff(before, after)["changed"] == ["rule.txt"]
    target = workspace.materialize(before, tmp_path / "v")
    (target / "support.txt").write_text("changed")
    with pytest.raises(ValueError, match="outside editable"):
        workspace.create_version(target)
    with pytest.raises(ValueError):
        workspace.create_version(patch="--- a/../escape\n+++ b/../escape\n@@ -0,0 +1 @@\n+bad\n")


@pytest.mark.parametrize("value", ["../outside", "/absolute", "C:/x", "x\\y", "NUL.txt", "x./y"])
def test_portable_paths_reject_escape_and_windows_reserved(value):
    with pytest.raises(ValueError):
        safe_path(value)


def test_corrupt_snapshot_rejected(workspace, tmp_path):
    version = workspace.current()
    path = workspace.state / "versions" / f"{version}.json"
    manifest = json.loads(path.read_text())
    manifest["files"]["rule.txt"] = "0" * 64
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="integrity"):
        workspace.materialize(version, tmp_path / "v")


def test_crlf_diff_round_trips_as_patch(workspace, tmp_path):
    (workspace.root / "rule.txt").write_bytes(b"0\r\n")
    before = workspace.create_version()["version"]
    (workspace.root / "rule.txt").write_bytes(b"1\r\n")
    after = workspace.create_version(parent=before)["version"]
    patch = workspace.diff(before, after)["diff"]
    assert workspace.create_version(patch=patch, parent=before)["version"] == after
