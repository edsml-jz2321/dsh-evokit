"""Official MCP SDK stdio transport over the shared tool dispatcher."""

from __future__ import annotations

import asyncio
import json
from importlib.metadata import version
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent
from mcp.types import Tool as MCPTool

from .tools import available_tools, call_tool


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
                description=f"[{tool.group}] {tool.description}",
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
            "evokit", version="0.1.0", on_list_tools=list_modern, on_call_tool=invoke_modern
        )
    else:
        server = Server("evokit", version="0.1.0")
        server.list_tools()(list_tools)
        server.call_tool()(invoke)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
