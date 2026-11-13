import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from evokit.tools import available_tools


def test_native_inspector_derives_improver_inventory():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the DSH inspector contract")
    executable = Path(sys.executable).parent / ("evokit.exe" if os.name == "nt" else "evokit")
    module = Path(__file__).resolve().parents[1] / "integrations/dsh/list-tools.mjs"
    expected = ["mcp__evokit__" + tool.name for tool in available_tools(profile="improver")]
    script = f"""
        import {{apply}} from {json.dumps(module.as_uri())};
        const names = {json.dumps(expected)};
        apply({{
          get: name => name === 'appExit' ? code => process.exit(code) : undefined,
          tools: {{ definitions: () => names.map(name => ({{ name }})) }},
          effect: () => {{}}
        }});
    """
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=35,
        env={**os.environ, "EVOKIT_MCP_COMMAND": str(executable)},
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt == {"tools": sorted(expected), "modelRequests": 0}
    assert "mcp__evokit__ledger_adopt" not in expected
