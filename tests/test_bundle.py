"""The public bundle's native metadata and skill copies stay consistent."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


def test_bundle_metadata_and_skills():
    root = Path(__file__).resolve().parents[1]
    bundle = root / "integrations/dsh"
    package = json.loads((bundle / "package.json").read_text())
    assert package["name"] == "dsh-evokit" and package["version"] == "0.1.7"
    assert not package.get("private", False)
    assert package["dsh"]["bundle"]["patch"] == "./cordis.patch.yml"
    assert package["engines"]["dsh"] == ">=0.2.2-rc.1 <0.3.0-0"
    for name in ("plugin.json", "mcp.json"):
        assert json.loads((bundle / name).read_text()) == json.loads((root / name).read_text())
    for name in ("onboard", "evolve", "meta"):
        relative = f"skills/{name}/SKILL.md"
        assert (bundle / relative).read_bytes() == (root / relative).read_bytes()


def _bundle_expressions():
    """Read the bundle patch, keeping each `!!js` value as its expression text."""

    class Loader(yaml.SafeLoader):
        pass

    Loader.add_constructor("tag:yaml.org,2002:js", lambda loader, node: {"js": node.value})
    root = Path(__file__).resolve().parents[1]
    patch = yaml.load((root / "integrations/dsh/cordis.patch.yml").read_text(), Loader=Loader)
    rows = {row["id"]: row["config"] for row in patch[0]["insert"]}
    return {
        "command": rows["evokit-mcp"]["command"]["js"],
        "cwd": rows["evokit-mcp"]["cwd"]["js"],
        "skills": rows["evokit-skills"]["customSkillDirs"]["js"],
    }


@pytest.mark.parametrize("overridden", [False, True])
def test_bundle_defaults_evaluate_like_dsh(tmp_path, overridden):
    """Evaluate the patch expressions the way DSH's loader does: `with (ctx) eval(expr)`."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to evaluate the DSH patch expressions")
    profile = tmp_path / "profiles" / "evokit"
    workspace = tmp_path / "work"
    workspace.mkdir()
    script = """
        const { pathToFileURL } = require('node:url');
        const expressions = JSON.parse(process.argv[1]);
        const ctx = { baseUrl: pathToFileURL(process.argv[2]).href + '/' };
        const evaluate = new Function('ctx', 'expr', 'with (ctx) { return eval(expr) }');
        const values = Object.fromEntries(
          Object.entries(expressions).map(([key, expr]) => [key, evaluate(ctx, expr)]));
        console.log(JSON.stringify(values));
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("EVOKIT_")}
    if overridden:
        env.update(
            EVOKIT_MCP_COMMAND=str(tmp_path / "bin" / "evokit"),
            EVOKIT_WORKSPACE=str(tmp_path / "scenario"),
            EVOKIT_SKILL_ROOT=str(tmp_path / "skills"),
        )
    result = subprocess.run(
        [node, "-e", script, json.dumps(_bundle_expressions()), str(profile)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=workspace,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    values = json.loads(result.stdout)
    if overridden:
        assert values == {
            "command": str(tmp_path / "bin" / "evokit"),
            "cwd": str(tmp_path / "scenario"),
            "skills": [str(tmp_path / "skills")],
        }
    else:
        assert values["command"] == "evokit"
        assert Path(values["cwd"]).resolve() == workspace.resolve()
        assert values["skills"] == [str(profile / "node_modules" / "dsh-evokit" / "skills")]
