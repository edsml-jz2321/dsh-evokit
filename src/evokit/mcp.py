"""Official MCP SDK stdio transport over the shared tool dispatcher."""

from __future__ import annotations

import asyncio
import json
from importlib.metadata import version
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent, ToolAnnotations
from mcp.types import Tool as MCPTool

from .tools import available_tools, call_tool

INSTRUCTIONS = (
    "evokit runs measured self-improvement experiments on one scenario: a YAML file naming "
    "editable files, tasks and a frozen scorer. Every tool takes `config`, the scenario YAML "
    "path on this host; the first call on a new scenario creates its workspace and freezes "
    "the task splits. A version is a SHA-256 snapshot of the editable files; an arm (run) "
    "scores one version on the diagnosis or selection split. Typical loop: scenario_describe "
    "and version_current; run_launch the base on diagnosis and read failure_cards; edit and "
    "version_create; proposal_create; run the base with repeats >= 2 on selection and call "
    "noise_estimate; run parent and candidate (run_interleaved); compare_paired; gate_check. "
    "Runs execute scenario tasks and may cost money; every other tool is local. Records are "
    "append-only. Adoption, confirmation, reverts and final measurement belong to the "
    "separate operator profile."
)


async def serve(config: str | None = None, *, profile: str = "improver") -> None:
    """Serve MCP on stdin/stdout; stdout is reserved for protocol messages.

    Args:
        config: Optional bound workspace. HB tools are advertised only for HB.

    Returns:
        None: Runs until the stdio client disconnects.
    """
    tools = available_tools(config, profile=profile)
    modern = int(version("mcp").split(".")[0]) >= 2

    async def list_tools() -> list[MCPTool]:
        return [
            MCPTool(
                name=tool.name,
                description=tool.description,
                annotations=ToolAnnotations.model_validate(tool.annotations),
                **{("input_schema" if modern else "inputSchema"): tool.schema},
            )
            for tool in tools
        ]

    async def invoke(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        if name not in {tool.name for tool in tools}:
            raise ValueError(f"Unknown tool: {name}")
        if config and Path(arguments.get("config", "")).resolve() != Path(config).resolve():
            raise ValueError("MCP server is bound to a different workspace")
        result = await asyncio.to_thread(call_tool, name, arguments, profile=profile)
        return [TextContent(type="text", text=json.dumps(result, allow_nan=False))]

    if modern:

        async def list_modern(context: Any, params: Any) -> ListToolsResult:
            return ListToolsResult(tools=await list_tools())

        async def invoke_modern(context: Any, params: Any) -> CallToolResult:
            try:
                return CallToolResult(content=await invoke(params.name, params.arguments or {}))
            except Exception as exc:
                return CallToolResult(
                    is_error=True,
                    content=[
                        TextContent(
                            type="text",
                            text=json.dumps({"error": str(exc), "type": type(exc).__name__}),
                        )
                    ],
                )

        server = Server(
            "evokit",
            version="0.1.0",
            instructions=INSTRUCTIONS,
            on_list_tools=list_modern,
            on_call_tool=invoke_modern,
        )
    else:
        server = Server("evokit", version="0.1.0", instructions=INSTRUCTIONS)
        server.list_tools()(list_tools)
        server.call_tool()(invoke)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
