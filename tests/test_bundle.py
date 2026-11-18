"""The public bundle's native metadata and skill copies stay consistent."""

import importlib.util
import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml


def test_bundle_metadata_and_skills():
    root = Path(__file__).resolve().parents[1]
    bundle = root / "integrations/dsh"
    package = json.loads((bundle / "package.json").read_text())
    assert package["name"] == "dsh-evokit" and package["version"] == "0.1.8"
    assert not package.get("private", False)
    assert package["dsh"]["bundle"]["patch"] == "./cordis.patch.yml"
    assert package["engines"]["dsh"] == ">=0.2.2-rc.1 <0.3.0-0"
    for name in ("plugin.json", "mcp.json"):
        assert json.loads((bundle / name).read_text()) == json.loads((root / name).read_text())
    for name in ("onboard", "evolve", "meta"):
        relative = f"skills/{name}/SKILL.md"
        assert (bundle / relative).read_bytes() == (root / relative).read_bytes()


def test_root_manifest_matches_bundle():
    """The repository root is detectable as the same DSH bundle but is never published."""
    root = Path(__file__).resolve().parents[1]
    bundle = root / "integrations/dsh"
    native = json.loads((bundle / "package.json").read_text())
    top = json.loads((root / "package.json").read_text())
    for key in ("name", "version", "engines", "keywords", "license"):
        assert top[key] == native[key], key
    assert not top.get("private", False)
    assert "prepublishOnly" in top["scripts"]
    patch = (root / top["dsh"]["bundle"]["patch"]).resolve()
    assert patch == (bundle / native["dsh"]["bundle"]["patch"]).resolve() and patch.is_file()
    assert {"skills", "integrations/dsh/cordis.patch.yml"} <= set(top["files"])
    for name in ("onboard", "evolve", "meta"):
        assert (root / "skills" / name / "SKILL.md").is_file()


def test_console_scripts_share_the_cli():
    """`uvx dsh-evokit mcp` and `evokit mcp` start the same entry point."""
    root = Path(__file__).resolve().parents[1]
    scripts = tomllib.loads((root / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts == {"evokit": "evokit.cli:main", "dsh-evokit": "evokit.cli:main"}


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


def test_registry_server_matches_package():
    """server.json for the official MCP Registry names this exact package version."""
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    server = json.loads((root / "server.json").read_text())
    assert server["name"] == "io.github.edsml-jz2321/dsh-evokit"
    assert server["version"] == version and len(server["description"]) <= 100
    (package,) = server["packages"]
    assert package["registryType"] == "pypi" and package["identifier"] == "dsh-evokit"
    assert package["version"] == version and package["transport"] == {"type": "stdio"}
    assert package["packageArguments"] == [{"type": "positional", "value": "mcp"}]
    assert f"mcp-name: {server['name']}" in (root / "README.md").read_text()


def test_release_notes_come_from_the_changelog_section():
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts/release_notes.py"
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    spec = importlib.util.spec_from_file_location("release_notes", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    changelog = "# Changelog\n\n## v2.0.0\n\n- new\n\n## v1.0.0\n\n- old\n"
    assert module.section(changelog, "2.0.0") == "- new"
    assert module.section(changelog, "1.0.0") == "- old"
    assert module.section(changelog, "3.0.0") == ""
    assert module.section((root / "CHANGELOG.md").read_text(encoding="utf-8"), version)


def test_release_workflow_publishes_everything_from_one_build():
    root = Path(__file__).resolve().parents[1]
    jobs = yaml.safe_load((root / ".github/workflows/release.yml").read_text())["jobs"]
    needs = {name: job.get("needs") for name, job in jobs.items()}
    assert needs == {
        "build": None,
        "publish-pypi": "build",
        "publish-npm": "build",
        "github-release": "build",
        "publish-mcp-registry": "publish-pypi",
        "update-dsh-index": "publish-npm",
    }
    for name in ("publish-pypi", "publish-npm", "publish-mcp-registry"):
        assert jobs[name]["permissions"]["id-token"] == "write", name
    assert jobs["publish-pypi"]["environment"]["name"] == "pypi"
    assert jobs["publish-npm"]["environment"]["name"] == "npm"
    assert jobs["github-release"]["permissions"] == {"contents": "write"}
    assert jobs["update-dsh-index"]["permissions"] == {"contents": "write", "pages": "write"}
    npm = "\n".join(step.get("run", "") for step in jobs["publish-npm"]["steps"])
    assert "npm publish" in npm and "NODE_AUTH_TOKEN" not in npm


def test_market_update_changes_only_the_version_entry():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("update_market", root / "scripts/update_market.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    template = (root / "market/index.json").read_text(encoding="utf-8")
    engines = json.loads((root / "integrations/dsh/package.json").read_text())["engines"]["dsh"]
    updated = module.update(template, "9.8.7", 1795000000, engines)
    before, after = json.loads(template), json.loads(updated)
    (entry,) = after["plugins"][0].pop("versions")
    before["plugins"][0].pop("versions")
    assert before == after
    assert entry == {
        "version": "9.8.7",
        "publishTimestamp": 1795000000,
        "source": {"type": "npm"},
        "engines": {"dsh": engines},
    }
    assert updated.isascii() and module.update(updated, "9.8.7", 1795000000, engines) == updated
    with pytest.raises(ValueError):
        module.update(template.replace("\n", "\r\n"), "9.8.7", 1795000000, engines)
    with pytest.raises(ValueError):
        module.update(template, "latest", 1795000000, engines)
