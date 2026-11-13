"""The public bundle's native metadata and skill copies stay consistent."""

import json
from pathlib import Path


def test_bundle_metadata_and_skills():
    root = Path(__file__).resolve().parents[1]
    bundle = root / "integrations/dsh"
    package = json.loads((bundle / "package.json").read_text())
    assert package["name"] == "dsh-evokit" and package["version"] == "0.1.5"
    assert not package.get("private", False)
    assert package["dsh"]["bundle"]["patch"] == "./cordis.patch.yml"
    assert package["engines"]["dsh"] == "0.2.2-rc.1"
    for name in ("plugin.json", "mcp.json"):
        assert json.loads((bundle / name).read_text()) == json.loads((root / name).read_text())
    for name in ("onboard", "evolve", "meta"):
        relative = f"skills/{name}/SKILL.md"
        assert (bundle / relative).read_bytes() == (root / relative).read_bytes()
