"""Stable transport-neutral tools. Tool names are the CLI commands and MCP names."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema

from .coordinator import CONTRACT_SCHEMA
from .workspace import Workspace


@dataclass(frozen=True)
class Tool:
    """Tool documentation and closed JSON input schema."""

    name: str
    group: str
    description: str
    parameters: dict[str, dict[str, Any]]
    required: tuple[str, ...] = ()

    @property
    def schema(self) -> dict[str, Any]:
        """JSON Schema accepted identically by CLI, SDK dispatcher and MCP."""
        return {
            "type": "object",
            "properties": {"config": {"type": "string"}, **self.parameters},
            "required": ["config", *self.required],
            "additionalProperties": False,
        }


STRING = {"type": "string"}
POSITIVE_INTEGER = {"type": "integer", "minimum": 1}
BOOLEAN = {"type": "boolean"}
AGGREGATION = {"type": "string", "enum": ["mean", "majority"]}
PROPOSAL = json.loads(Path(__file__).with_name("proposal.schema.json").read_text())

TOOLS = (
    Tool("scenario_describe", "scenario", "Describe the contract, anchors and split counts.", {}),
    Tool("version_current", "versions", "Read the current artifact version.", {}),
    Tool(
        "version_create",
        "versions",
        "Snapshot a checkout or apply a patch; reject out-of-scope edits.",
        {"directory": STRING, "patch": STRING, "parent": STRING},
    ),
    Tool(
        "version_diff",
        "versions",
        "Diff two artifact snapshots.",
        {"parent": STRING, "child": STRING},
        ("parent", "child"),
    ),
    Tool(
        "run_launch",
        "runs",
        "Launch/resume an arm; detached by default. Holdout is unavailable.",
        {
            "version": STRING,
            "split": {"enum": ["diagnosis", "selection"], "type": "string"},
            "repeats": POSITIVE_INTEGER,
            "workers": {"type": "integer", "minimum": 1, "maximum": 256},
            "smoke": BOOLEAN,
            "run_id": STRING,
            "wait": BOOLEAN,
        },
        ("version",),
    ),
    Tool(
        "run_interleaved",
        "runs",
        "Complete matched public arms in rotating task blocks; resume exact names and parameters.",
        {
            "arms": {"type": "object", "minProperties": 2, "additionalProperties": STRING},
            "split": {"enum": ["diagnosis", "selection"], "type": "string"},
            "repeats": POSITIVE_INTEGER,
            "workers": {"type": "integer", "minimum": 1, "maximum": 256},
            "seed": {"type": "integer"},
        },
        ("arms",),
    ),
    Tool(
        "run_status",
        "runs",
        "Read completed/expected counts and execution state.",
        {"run_id": STRING},
        ("run_id",),
    ),
    Tool(
        "run_results",
        "runs",
        "Read public task outcomes, costs, latency and archived traces.",
        {
            "run_id": STRING,
            "offset": {"type": "integer", "minimum": 0},
            "limit": POSITIVE_INTEGER,
        },
        ("run_id",),
    ),
    Tool(
        "compare_paired",
        "analysis",
        "Compare identical tasks with explicit repeat aggregation.",
        {"parent_run": STRING, "child_run": STRING, "aggregation": AGGREGATION},
        ("parent_run", "child_run", "aggregation"),
    ),
    Tool(
        "noise_estimate",
        "analysis",
        "Measure incumbent flip rates and record a threshold method.",
        {"run_id": STRING},
        ("run_id",),
    ),
    Tool(
        "failure_cards",
        "analysis",
        "Read the full failure index, or paired fixes/breaks.",
        {
            "run_id": STRING,
            "group": STRING,
            "parent_run": STRING,
            "change": {"type": "string", "enum": ["fix", "break"]},
            "aggregation": AGGREGATION,
            "offset": {"type": "integer", "minimum": 0},
            "limit": POSITIVE_INTEGER,
            "details": BOOLEAN,
        },
        ("run_id",),
    ),
    Tool(
        "proposal_create",
        "proposals",
        "Validate and append a five-field falsifiable proposal.",
        {"version": STRING, "proposal": PROPOSAL},
        ("version", "proposal"),
    ),
    Tool("proposal_list", "proposals", "List all recorded proposals with playbook versions.", {}),
    Tool(
        "gate_check",
        "decisions",
        "Check anchors, literals, budgets and measured noise.",
        {
            "proposal_id": STRING,
            "parent_run": STRING,
            "child_run": STRING,
            "noise_id": STRING,
            "aggregation": AGGREGATION,
            "rationale": STRING,
            "generic": BOOLEAN,
        },
        ("proposal_id", "parent_run", "child_run", "noise_id", "aggregation"),
    ),
    Tool(
        "ledger_adopt",
        "decisions",
        "Recheck and adopt a passing verdict, or queue a runner-up.",
        {"verdict_id": STRING, "rationale": STRING, "queue": BOOLEAN},
        ("verdict_id", "rationale"),
    ),
    Tool(
        "ledger_history",
        "lineage",
        "Read append-only public history, base chain and runner-ups.",
        {},
    ),
    Tool(
        "meta_metrics",
        "lineage",
        "Read acceptance, prediction error, gain/cost and holdout counts.",
        {},
    ),
)

OPERATOR_TOOLS = tuple(t for t in TOOLS if t.name == "ledger_adopt") + (
    Tool(
        "contract_record",
        "meta",
        "Anchor the epoch contract before work starts.",
        {"contract": CONTRACT_SCHEMA},
        ("contract",),
    ),
    Tool("contract_read", "meta", "Read the anchored epoch contract.", {}),
    Tool(
        "confirm_adoption",
        "meta",
        "Apply the confirmation rule; aggregate only; consumes wear.",
        {
            "version": STRING,
            "workers": {"type": "integer", "minimum": 1, "maximum": 256},
            "reuse_only": BOOLEAN,
        },
        ("version",),
    ),
    Tool(
        "ledger_revert",
        "meta",
        "Append a revert from current to its parent, with a reason.",
        {"reason": STRING},
        ("reason",),
    ),
    Tool(
        "final_measure",
        "meta",
        "Open final once; pair current with the epoch starting version.",
        {"workers": {"type": "integer", "minimum": 1, "maximum": 256}},
    ),
)
TOOLS = tuple(t for t in TOOLS if t.name != "ledger_adopt")

HB_TOOLS = (
    Tool(
        "records_query",
        "records",
        "Query public HB results across runs and generations.",
        {
            "task": STRING,
            "group": STRING,
            "outcome": {"type": "number", "minimum": 0, "maximum": 1},
            "version": STRING,
            "run_id": STRING,
            "epoch": STRING,
            "proposal": STRING,
            "generation": {"type": "integer", "minimum": 0},
            "flipped_between": {"type": "array", "items": STRING, "minItems": 2, "maxItems": 2},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
        },
    ),
    Tool(
        "failures_similar",
        "records",
        "Search public HB failure cards; no implicit model calls.",
        {
            "text": {"type": "string", "minLength": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        ("text",),
    ),
)


def available_tools(config: str | None = None, profile: str = "improver") -> tuple[Tool, ...]:
    """Return base tools plus HB capabilities only for a selected HB workspace."""
    if profile not in {"improver", "operator"}:
        raise ValueError("profile must be improver or operator")
    selected = TOOLS + (OPERATOR_TOOLS if profile == "operator" else ())
    if config:
        workspace = Workspace.open(config)
        try:
            if workspace.store.backend == "hb":
                return selected + HB_TOOLS
        finally:
            workspace.close()
    return selected


def call_tool(name: str, arguments: dict[str, Any], *, profile: str = "improver") -> Any:
    """Validate and invoke one stable tool through the owning Workspace API.

    Args:
        name (str): A name from TOOLS, documented in docs/tools.md.
        arguments (dict[str, Any]): Closed-schema JSON arguments, including config.

    Returns:
        Any: JSON result, identical across transports.

    Raises:
        ValueError: For an unknown tool or domain contract violation.
        jsonschema.ValidationError: For invalid tool input.
    """
    if profile not in {"improver", "operator"}:
        raise ValueError("profile must be improver or operator")
    selected = TOOLS + HB_TOOLS + (OPERATOR_TOOLS if profile == "operator" else ())
    tool = next((tool for tool in selected if tool.name == name), None)
    if tool is None:
        raise ValueError(f"Unknown tool: {name}")
    jsonschema.validate(arguments, tool.schema)
    arguments = dict(arguments)
    workspace = Workspace.open(arguments.pop("config"))
    if tool in HB_TOOLS and workspace.store.backend != "hb":
        workspace.close()
        raise ValueError(f"Unknown tool for files backend: {name}")
    methods = {
        "contract_record": workspace.contract_record,
        "contract_read": workspace.contract_read,
        "confirm_adoption": workspace.confirm_adoption,
        "ledger_revert": workspace.ledger_revert,
        "final_measure": workspace.final_measure,
        "scenario_describe": workspace.describe,
        "version_current": workspace.current,
        "version_create": workspace.create_version,
        "version_diff": workspace.diff,
        "run_launch": workspace.launch,
        "run_interleaved": workspace.interleaved,
        "run_status": workspace.status,
        "run_results": workspace.results,
        "compare_paired": workspace.paired,
        "noise_estimate": workspace.noise,
        "failure_cards": workspace.cards,
        "proposal_create": workspace.propose,
        "proposal_list": workspace.proposals,
        "gate_check": workspace.gate,
        "ledger_adopt": workspace.adopt,
        "ledger_history": workspace.history,
        "meta_metrics": workspace.metrics,
        "records_query": workspace.records_query,
        "failures_similar": workspace.failures_similar,
    }
    try:
        result = methods[name](**arguments)
    finally:
        workspace.close()
    if name == "meta_metrics" and profile == "improver":
        result.pop("confirmation", None)
    return {"version": result} if name == "version_current" else result
