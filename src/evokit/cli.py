"""CLI parity with the stable MCP tools, plus an internal detached run entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .tools import available_tools, call_tool
from .workspace import Workspace


def main() -> None:
    """Invoke a tool with JSON arguments or start the stdio MCP server.

    Args:
        None: Reads command-line arguments from sys.argv.

    Returns:
        None: Writes JSON; domain/input errors exit 2 with a JSON error on stderr.
    """
    if len(sys.argv) > 1 and sys.argv[1] in {"init", "doctor"}:
        from .onboarding import run_cli

        try:
            result = run_cli(sys.argv[1:])
            print(json.dumps(result, indent=2, allow_nan=False))
            if any(c["status"] == "fail" for c in result.get("checks", {}).values()):
                raise SystemExit(2)
        except Exception as exc:
            print(json.dumps({"error": str(exc), "type": type(exc).__name__}), file=sys.stderr)
            raise SystemExit(2) from exc
        return
    parser = argparse.ArgumentParser(prog="evokit")
    parser.add_argument("command", help="Tool name, tools, mcp, export or import")
    parser.add_argument("internal", nargs="*")
    parser.add_argument("--config")
    parser.add_argument("--profile", choices=["improver", "operator"], default="improver")
    parser.add_argument("--state", type=Path, help="Operator import/export state path")
    parser.add_argument("--backend", choices=["files", "hb"], help="Operator import target backend")
    parser.add_argument("--output", type=Path, help="New operator JSONL export")
    parser.add_argument("--input", type=Path, help="Operator JSONL import")
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--args", default="{}", help="Tool arguments as a JSON object")
    inputs.add_argument(
        "--args-file", type=Path, help="UTF-8 JSON arguments, avoiding shell quoting"
    )
    options = parser.parse_args()
    try:
        if options.command == "mcp":
            from .mcp import serve

            asyncio.run(serve(options.config, profile=options.profile))
            return
        if options.command == "_run":
            config, run_id = options.internal
            Workspace.open(config).execute(run_id)
            return
        if options.command == "tools":
            result = [
                {
                    "name": tool.name,
                    "group": tool.group,
                    "description": tool.description,
                    "inputSchema": tool.schema,
                }
                for tool in available_tools(options.config, profile=options.profile)
            ]
        elif options.command in {"export", "import"}:
            from .portability import export_records, import_records

            if not options.config:
                raise ValueError("Import/export requires --config")
            importing = options.command == "import"
            target = options.input if importing else options.output
            if target is None:
                raise ValueError("Use --input for import or --output for export")
            workspace = Workspace.open(
                options.config,
                initialize=False,
                state=options.state,
                storage={"backend": options.backend} if options.backend else None,
            )
            count = (
                import_records(workspace.store, target)
                if importing
                else export_records(workspace.store, target)
            )
            result = {"records": count, "backend": workspace.store.backend}
        else:
            arguments = json.loads(
                options.args_file.read_text(encoding="utf-8-sig")
                if options.args_file
                else options.args
            )
            if options.config:
                arguments["config"] = options.config
            result = call_tool(options.command, arguments, profile=options.profile)
        print(json.dumps(result, indent=2, allow_nan=False))
    except Exception as exc:
        print(json.dumps({"error": str(exc), "type": type(exc).__name__}), file=sys.stderr)
        raise SystemExit(2) from exc
