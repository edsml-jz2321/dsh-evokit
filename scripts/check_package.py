"""Check built wheel resources and optional published Agent Plugins schemas.

Run after `uv build`. Schema paths are acquired by the caller from the versioned
official Agent Plugins website, so ordinary checks require no network access.
"""

import argparse
import json
import tarfile
import tomllib
import zipfile
from pathlib import Path

import jsonschema
import yaml

parser = argparse.ArgumentParser()
parser.add_argument("--plugin-schema", type=Path)
parser.add_argument("--mcp-schema", type=Path)
options = parser.parse_args()
root = Path(__file__).resolve().parents[1]
version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
for name in (
    "plugin.json",
    "package.json",
    "integrations/dsh/plugin.json",
    "integrations/dsh/package.json",
):
    assert json.loads((root / name).read_text())["version"] == version, name
server = json.loads((root / "server.json").read_text())
assert server["version"] == version, "server.json"
assert [package["version"] for package in server["packages"]] == [version], "server.json packages"
wheel = next((root / "dist").glob("dsh_evokit-*.whl"))
with zipfile.ZipFile(wheel) as archive:
    entry_points = next(name for name in archive.namelist() if name.endswith("entry_points.txt"))
    console = archive.read(entry_points).decode()
    for script in ("evokit", "dsh-evokit"):
        assert f"{script} = evokit.cli:main" in console, script
    schema = json.loads(archive.read("evokit/proposal.schema.json"))
    jsonschema.Draft202012Validator.check_schema(schema)
    for name in ("evolve", "onboard", "meta"):
        skill = archive.read(f"evokit/data/{name}.md")
        metadata = yaml.safe_load(skill.decode().split("---", 2)[1])
        assert metadata["name"] == name and metadata["description"]
        assert skill == (root / f"skills/{name}/SKILL.md").read_bytes()
sdist = root / "dist" / f"dsh_evokit-{version}.tar.gz"
with tarfile.open(sdist) as archive:
    names = archive.getnames()
    assert not any(
        "/.dp/" in name or name.endswith(("runtime.json", "runtime-files.json", ".pyc"))
        for name in names
    ), "Local runtime/evidence must not ship in the sdist"
for artifact, schema_path in [
    ("plugin.json", options.plugin_schema),
    ("mcp.json", options.mcp_schema),
]:
    if schema_path:
        jsonschema.validate(
            json.loads((root / artifact).read_text()), json.loads(schema_path.read_text())
        )
        print(f"{artifact}: validated against {schema_path.name}")
print(f"{wheel.name}: proposal schema and exact playbook included")
