import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from evokit.tools import available_tools


@pytest.mark.parametrize("explicit_command", [True, False])
def test_native_inspector_derives_improver_inventory(explicit_command):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the DSH inspector contract")
    executable = Path(sys.executable).parent / ("evokit.exe" if os.name == "nt" else "evokit")
    module = Path(__file__).resolve().parents[1] / "integrations/dsh/list-tools.mjs"
    expected = ["mcp__evokit__" + tool.name for tool in available_tools(profile="improver")]
    script = f"""
        import {{apply}} from {json.dumps(module.as_uri())};
        const names = {json.dumps(expected)};
        const ctx = {{
          get: name => name === 'appExit' ? code => process.exit(code) : name === 'skills' ? ctx.skills : undefined,
          tools: {{ definitions: () => names.map(name => ({{ name }})) }},
          skills: {{ list: async () => [
            ...['onboard', 'meta', 'evolve'].map(name => ({{
              name, provider: 'evokit', path: `/bundle/skills/${{name}}/SKILL.md` }})),
            {{ name: 'other', provider: 'filesystem' }},
          ] }},
          effect: () => {{}}
        }};
        apply(ctx);
    """
    env = {key: value for key, value in os.environ.items() if key != "EVOKIT_MCP_COMMAND"}
    if explicit_command:
        env["EVOKIT_MCP_COMMAND"] = str(executable)
    else:
        env["PATH"] = str(executable.parent) + os.pathsep + env.get("PATH", "")
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=35,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt == {
        "tools": sorted(expected),
        "skills": ["evolve", "meta", "onboard"],
        "skillRoots": ["/bundle/skills"],
        "modelRequests": 0,
    }
    assert "mcp__evokit__ledger_adopt" not in expected
