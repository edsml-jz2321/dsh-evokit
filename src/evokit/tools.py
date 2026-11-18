"""Stable transport-neutral tools. Tool names are the CLI commands and MCP names."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

from .coordinator import CONTRACT_SCHEMA
from .workspace import Workspace

# MCP tool annotations. Read-only tools change no workspace state; write tools
# only append records (never rewrite history) unless marked destructive.
READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


def writes(
    *, idempotent: bool = False, open_world: bool = False, destructive: bool = False
) -> dict[str, bool]:
    """Annotations for a tool that changes workspace state.

    Args:
        idempotent: Repeating the call with the same arguments changes nothing more.
        open_world: Executes scenario tasks, which may call external services.
        destructive: Makes an irreversible change beyond appending records.

    Returns:
        dict[str, bool]: The four MCP annotation hints.
    """
    return {
        "readOnlyHint": False,
        "destructiveHint": destructive,
        "idempotentHint": idempotent,
        "openWorldHint": open_world,
    }


@dataclass(frozen=True)
class Tool:
    """Tool documentation, closed JSON input schema and MCP annotations."""

    name: str
    group: str
    description: str
    parameters: dict[str, dict[str, Any]]
    required: tuple[str, ...] = ()
    annotations: dict[str, bool] = field(default_factory=lambda: dict(READ_ONLY))

    @property
    def schema(self) -> dict[str, Any]:
        """JSON Schema accepted identically by CLI, SDK dispatcher and MCP."""
        return {
            "type": "object",
            "properties": {"config": CONFIG, **self.parameters},
            "required": ["config", *self.required],
            "additionalProperties": False,
        }


def string(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def integer(description: str, minimum: int | None = None, **extra: Any) -> dict[str, Any]:
    bounds = {} if minimum is None else {"minimum": minimum}
    return {"type": "integer", **bounds, **extra, "description": description}


def boolean(description: str, default: bool) -> dict[str, Any]:
    return {"type": "boolean", "default": default, "description": description}


def split(default: str = "selection") -> dict[str, Any]:
    return {
        "type": "string",
        "enum": ["diagnosis", "selection"],
        "default": default,
        "description": "Task split: `diagnosis` to explore and read failures, `selection` for "
        "gate evidence. Default `selection`. Confirmation and final splits are never available.",
    }


def workers(default: int) -> dict[str, Any]:
    return integer(
        f"Parallel task workers, 1 to 256. Default {default}.", 1, maximum=256, default=default
    )


def aggregation(description: str, default: str | None = None) -> dict[str, Any]:
    extra = {} if default is None else {"default": default}
    return {"type": "string", "enum": ["mean", "majority"], **extra, "description": description}


CONFIG = string(
    "Path of the scenario YAML file on the server host, absolute or relative to the server's "
    "working directory, for example `scenario.yaml`. It selects the workspace; a server "
    "started with --config rejects any other path."
)
VERSION = "64-character lowercase SHA-256 version id, as returned by version_create"
RUN_ID = "Run ids are 1 to 80 letters, digits, `_` or `-`, starting with a letter or digit"
REPEATS = integer("Times each task runs, at least 1. Default 1.", 1, default=1)
OFFSET = integer("Zero-based index of the first item to return. Default 0.", 0, default=0)
LIMIT = integer("Maximum items to return, at least 1. Omit to return all remaining items.", 1)
PAIR_AGGREGATION = (
    "How repeats combine per task: `majority` for pass/fail outcomes (ties count as failure) "
    "or `mean` for numeric scores."
)
PROPOSAL = {
    **json.loads(Path(__file__).with_name("proposal.schema.json").read_text()),
    "description": "Proposal object with exactly five fields: targets, mechanism, "
    "predicted_effect, falsification and prior {attempt, difference}.",
}

TOOLS = (
    Tool(
        "scenario_describe",
        "scenario",
        "Describe the bound scenario: epoch id, editable file globs, frozen scorer version, "
        "task counts per split, primer, budgets, replay support and readiness flags. Call it "
        "first to learn what may be edited and how large each split is; use version_current "
        "for the current version id and ledger_history for lineage. Read-only; never reveals "
        "confirmation or final task ids. Returns one JSON object.",
        {},
    ),
    Tool(
        "version_current",
        "versions",
        "Return the id of the current base version: the latest adopted or reverted-to "
        "snapshot, or the initial snapshot before any adoption. Use it to get the parent for "
        "version_create, run_launch and noise_estimate; use ledger_history for the whole base "
        'chain. Read-only. Returns {"version": "<sha256>"}.',
        {},
    ),
    Tool(
        "version_create",
        "versions",
        "Store a candidate version of the editable files, either by snapshotting a complete "
        "scenario checkout or by applying a unified patch to a parent version. Use it after "
        "editing and before proposal_create and run_launch; use version_diff to review a stored "
        "change. Writes content-addressed snapshot files and appends a ledger event; it never "
        "adopts or changes the current base. Rejects edits outside the editable globs, unsafe "
        "paths and patches that do not apply. Returns {version, parent, changed}; identical "
        "content always gets the same version id.",
        {
            "directory": string(
                "Path of a complete scenario checkout on the server host to snapshot. "
                "Defaults to the original scenario directory. Do not combine with patch."
            ),
            "patch": string(
                "Unified Git-style patch text, as printed by `git diff`, applied to a "
                "temporary checkout of parent. Do not combine with directory."
            ),
            "parent": string(f"Base version of the change: {VERSION}. Default: current base."),
        },
        annotations=writes(),
    ),
    Tool(
        "version_diff",
        "versions",
        "Show the unified text diff of editable files between two stored versions, after "
        "verifying both snapshots' content digests. Use it to review a candidate before "
        "proposing or gating; use compare_paired to compare measured outcomes instead. "
        "Read-only. Returns {parent, child, changed, diff}; binary files are named, not diffed.",
        {
            "parent": string(f"Version before the change: {VERSION}."),
            "child": string(f"Version after the change: {VERSION}."),
        },
        ("parent", "child"),
    ),
    Tool(
        "run_launch",
        "runs",
        "Start or resume one arm: a run of one version over the tasks of the diagnosis or "
        "selection split, scored by the scenario. Use it to measure a single version; use "
        "run_interleaved to run a parent and candidates side by side. Detached by default: it "
        "returns at once while a background worker runs, so poll run_status, then read "
        "run_results. Writes run records, results and traces, and costs whatever the "
        "scenario's tasks cost (for example model calls). Resuming a run_id with the same "
        "parameters only fills missing task slots; changed parameters are refused. Returns "
        "the run status object.",
        {
            "version": string(f"Version to run: {VERSION}."),
            "split": split(),
            "repeats": REPEATS,
            "workers": workers(1),
            "smoke": boolean(
                "Run only the first two tasks of the split as a quick check. Smoke arms "
                "cannot be gated or used for noise. Default false.",
                False,
            ),
            "run_id": string(
                "Arm name. Omit for a new random id; pass an existing id with identical "
                f"parameters to resume that arm. {RUN_ID}."
            ),
            "wait": boolean(
                "Block until every task finishes instead of returning at once. Default false.",
                False,
            ),
        },
        ("version",),
        writes(open_world=True),
    ),
    Tool(
        "run_interleaved",
        "runs",
        "Run two or more versions as matched arms in rotating task blocks, so each arm sees "
        "every task at nearly the same time. Use it for parent-versus-candidate measurements "
        "when latency or cost may drift; use run_launch for one arm or a detached run. Blocks "
        "until all arms finish. Writes run records, results and traces, and costs whatever the "
        "scenario's tasks cost. Repeating the call with identical arms and parameters resumes "
        "and skips committed slots; a new plan needs fresh run ids. Returns each arm's run "
        "status, keyed by run id.",
        {
            "arms": {
                "type": "object",
                "minProperties": 2,
                "additionalProperties": {"type": "string"},
                "description": f"Map of new run id to version id, at least two entries, for "
                f'example {{"parent-1": "<sha256>", "child-1": "<sha256>"}}. {RUN_ID}.',
            },
            "split": split(),
            "repeats": REPEATS,
            "workers": workers(4),
            "seed": integer(
                "Integer that rotates which arm starts first in each block. Default 0.",
                default=0,
            ),
        },
        ("arms",),
        writes(idempotent=True, open_world=True),
    ),
    Tool(
        "run_status",
        "runs",
        "Report one arm's progress: state (pending, running, completed or interrupted), "
        "completed and expected result counts, and execution errors. Use it to poll a detached "
        "run_launch; use run_results for the task rows. Read-only. Returns {run_id, version, "
        "split, state, completed, expected, errors}.",
        {"run_id": string("Run id, as returned by run_launch or named in run_interleaved.")},
        ("run_id",),
    ),
    Tool(
        "run_results",
        "runs",
        "List one arm's committed task results: task_id, group, repeat, outcome, reason, cost, "
        "seconds, output and archived trace path. Use it for raw evidence; use failure_cards "
        "for failures only and compare_paired for parent-versus-child deltas. Read-only; "
        "partial while the arm is running. Returns an array in stable order.",
        {
            "run_id": string("Run id of a diagnosis or selection arm."),
            "offset": OFFSET,
            "limit": LIMIT,
        },
        ("run_id",),
    ),
    Tool(
        "compare_paired",
        "analysis",
        "Compare two complete arms task by task: fixes, breaks, net score change, per-task "
        "deltas, per-group tables, and cost and latency ratios. Use it to judge a candidate "
        "against its parent; use gate_check to record a decision with noise and budget checks. "
        "Both arms must be complete, error-free and match on tasks, split, repeats and anchors. "
        "Read-only. Returns the comparison object.",
        {
            "parent_run": string("Run id of the parent (incumbent) arm."),
            "child_run": string("Run id of the candidate arm."),
            "aggregation": aggregation(PAIR_AGGREGATION),
        },
        ("parent_run", "child_run", "aggregation"),
    ),
    Tool(
        "noise_estimate",
        "analysis",
        "Measure the current base's run-to-run noise from one repeated arm and record the "
        "estimate that gate_check requires. Use it once per base, on a complete, non-smoke "
        "selection arm of the current version with at least two repeats; use compare_paired "
        "for candidate effects. Appends a noise record to the ledger. Returns noise_id, "
        "per-task flip rates, net spread, threshold and method; pass noise_id to gate_check.",
        {"run_id": string("Run id of a complete selection arm of the current base.")},
        ("run_id",),
        writes(),
    ),
    Tool(
        "failure_cards",
        "analysis",
        "List failure cards for one arm: every task with outcome below 1, or, with parent_run "
        "and change, the tasks this arm fixed or broke against the parent. Use it to diagnose "
        "before proposing; use run_results for all rows, and failures_similar (HB workspaces) "
        "to search failures across runs. A card has task_id, group, outcome, reason, payload "
        "summary, output and trace pointer unless the scenario renders its own. Also saves the "
        "cards to the record store for later search, overwriting identical entries; nothing "
        "else changes. Returns an array.",
        {
            "run_id": string("Run id of the arm to read."),
            "group": string("Return only cards of this exact task group."),
            "parent_run": string("Run id of the parent arm to pair with; required by change."),
            "change": {
                "type": "string",
                "enum": ["fix", "break"],
                "description": "`fix` for tasks that improved against parent_run, `break` for "
                "tasks that got worse. Omit to list all failures.",
            },
            "aggregation": aggregation(
                "Repeat aggregation for the paired comparison: `mean` (default) or `majority`.",
                "mean",
            ),
            "offset": OFFSET,
            "limit": LIMIT,
            "details": boolean(
                "Include payload summaries, outputs and trace pointers. False returns a compact "
                "index of task_id, group, outcome and reason; start with false on large runs. "
                "Default true.",
                True,
            ),
        },
        ("run_id",),
        writes(idempotent=True),
    ),
    Tool(
        "proposal_create",
        "proposals",
        "Record a falsifiable proposal for a candidate version before testing it: targets, "
        "mechanism, predicted effect, falsification criterion and prior attempt. Use it after "
        "version_create and before running and gating the candidate; use proposal_list to read "
        "earlier proposals. Checks that targets are editable and cover every changed file, pins "
        "the current base as parent and snapshots the improver playbook. Appends to the "
        "ledger; runs and adopts nothing. Returns the stored proposal with its new id.",
        {
            "version": string(f"Candidate version, different from the current base: {VERSION}."),
            "proposal": PROPOSAL,
        },
        ("version", "proposal"),
        writes(),
    ),
    Tool(
        "proposal_list",
        "proposals",
        "List every recorded proposal in creation order, rejected ones included, with id, "
        "parent, version, prediction, playbook version and a not_confirmed flag. Use it to find "
        "a proposal_id for gate_check or a prior attempt; use ledger_history for adoptions and "
        "the base chain. Read-only. Returns an array.",
        {},
    ),
    Tool(
        "gate_check",
        "decisions",
        "Judge whether a proposal's candidate may be adopted and record the verdict. It checks "
        "frozen anchors, that the proposal's parent is still the current base, an editable-only "
        "diff, a scan for task-specific literals, complete non-smoke selection arms of parent "
        "and candidate, cost and latency budgets, scenario quality guards and the recorded "
        "noise threshold. Use it after compare_paired looks promising; use ledger_adopt "
        "(operator profile) to act on a passing verdict, because this tool never changes the "
        "current base. Appends one verdict record per call. Returns {id, verdict, reasons, "
        "threshold, comparison, secondary_guards} with "
        "verdict `promote` (net above noise), `adopt-no-harm` (net >= 0 with a rationale) or "
        "`reject`.",
        {
            "proposal_id": string("Proposal id from proposal_create for the candidate."),
            "parent_run": string(
                "Run id of a complete, non-smoke selection arm of the proposal's parent."
            ),
            "child_run": string(
                "Run id of a complete, non-smoke selection arm of the proposal's candidate, "
                "on the same tasks and repeats."
            ),
            "noise_id": string("noise_id from noise_estimate on this parent in this epoch."),
            "aggregation": aggregation(PAIR_AGGREGATION),
            "rationale": {
                "type": "string",
                "default": "",
                "description": "Why the change is generic and safe. Required for an "
                "adopt-no-harm verdict. Default empty.",
            },
            "generic": boolean(
                "Attest that the mechanism generalizes beyond the observed tasks. Any passing "
                "verdict requires true. Default false.",
                False,
            ),
        },
        ("proposal_id", "parent_run", "child_run", "noise_id", "aggregation"),
        writes(),
    ),
    Tool(
        "ledger_adopt",
        "decisions",
        "Adopt a passing gate verdict, making its candidate the current base, or, with "
        "queue=true, record it as a runner-up for later. Re-runs every gate check first and "
        "refuses rejected or stale verdicts. Use it after gate_check passes; use ledger_revert "
        "to undo an adoption. Appends to the ledger and moves the current pointer; history is "
        "never rewritten. Returns the adoption or runner-up record.",
        {
            "verdict_id": string("Verdict id returned by gate_check."),
            "rationale": string("Non-empty reason for adopting or queueing."),
            "queue": boolean(
                "Record a runner-up instead of adopting; current stays unchanged. Default false.",
                False,
            ),
        },
        ("verdict_id", "rationale"),
        writes(),
    ),
    Tool(
        "ledger_history",
        "lineage",
        "Read the public lineage: current base, base chain (initial version plus every adoption "
        "and revert), queued runner-ups and the append-only public event list. Use it to audit "
        "what was adopted and when; use meta_metrics for aggregate rates. Read-only; private "
        "confirmation and final events are hidden. Returns {current, base_chain, runner_ups, "
        "events}.",
        {},
    ),
    Tool(
        "meta_metrics",
        "lineage",
        "Report aggregate improvement metrics: proposal and adoption counts, acceptance rate, "
        "mean absolute prediction error, gain per cost unit, total experiment cost and "
        "confirmation measurement counts. Use it to judge how well proposals predict and pay "
        "off; use ledger_history for individual events. Read-only. Returns one JSON object.",
        {},
    ),
)

OPERATOR_TOOLS = tuple(t for t in TOOLS if t.name == "ledger_adopt") + (
    Tool(
        "contract_record",
        "meta",
        "Anchor the epoch contract (objective, guards, confirmation rule, noise plan, budget, "
        "stagnation window, wear budget) before work starts. Use it once at the start of an "
        "epoch; use contract_read to read it back. Refused once any arm has been prepared, "
        "including a smoke arm. Appends to the ledger. Returns the anchored contract with "
        "epoch, split hash, scorer version and split counts.",
        {"contract": {**CONTRACT_SCHEMA, "description": "Contract object; no task ids."}},
        ("contract",),
        writes(),
    ),
    Tool(
        "contract_read",
        "meta",
        "Read the latest anchored epoch contract. Use it to check the confirmation rule and "
        "budgets before confirm_adoption; use contract_record to set it. Read-only. Returns the "
        "contract object, or null when none was recorded.",
        {},
    ),
    Tool(
        "confirm_adoption",
        "meta",
        "Measure the current adoption against its parent on the private confirmation split and "
        "apply the contract's keep rule. Use it once after each adoption; use ledger_revert "
        "when the verdict is not_confirmed. Runs tasks at the scenario's cost unless reuse_only "
        "is true, and spends one confirmation wear slot per adoption; a completed decision is "
        "returned again without spending. Returns aggregates only: fixes, breaks, net, cost and "
        "latency ratios, secondary guards and verdict.",
        {
            "version": string(
                f"The current adopted version: {VERSION}. A historical adoption is accepted only "
                "under a retrospective contract with reuse_only."
            ),
            "workers": workers(1),
            "reuse_only": boolean(
                "Use retained measurements only and refuse to execute tasks. Default false.",
                False,
            ),
        },
        ("version",),
        writes(idempotent=True, open_world=True),
    ),
    Tool(
        "ledger_revert",
        "meta",
        "Move the current base back to the parent of its adoption, recording a reason. Use it "
        "when confirm_adoption returns not_confirmed or an adoption must be undone; use "
        "ledger_adopt to move forward. Appends a revert event and moves the current pointer; "
        "earlier history stays. Each call reverts one more adoption, so never repeat it as a "
        "retry. Refused for the initial version and after final_measure. Returns {parent, "
        "version, reason, proposal_id, adoption, not_confirmed}, where version is the new current.",
        {"reason": string("Non-empty reason, kept in the operator-only ledger.")},
        ("reason",),
        writes(),
    ),
    Tool(
        "final_measure",
        "meta",
        "Run the one-time final measurement: pair the current base with the epoch's starting "
        "version on the separate final split. Use it only at the end of an epoch, after asking "
        "the human; use confirm_adoption for per-adoption checks. Irreversible: the opening is "
        "recorded before execution, counts even if execution fails, and closes the epoch to "
        "further arms, adoptions and reverts. Runs tasks at the scenario's cost. Returns "
        "aggregates only: fixes, breaks, net, cost and latency ratios.",
        {"workers": workers(1)},
        annotations=writes(destructive=True, open_world=True),
    ),
)
TOOLS = tuple(t for t in TOOLS if t.name != "ledger_adopt")

HB_TOOLS = (
    Tool(
        "records_query",
        "records",
        "Query stored public task results across runs, versions and generations, with exact "
        "filters combined by AND. Use it for exploratory questions, such as which tasks flipped "
        "between two versions; use compare_paired for gate evidence. HB workspaces only. "
        "Read-only. Returns result rows with run_id, ordered by stable record key.",
        {
            "task": string("Exact task id."),
            "group": string("Exact task group."),
            "outcome": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Exact outcome score, 0 to 1.",
            },
            "version": string(f"Version that produced the result: {VERSION}."),
            "run_id": string("Exact run id."),
            "epoch": string("Epoch id, as reported by scenario_describe."),
            "proposal": string("Proposal id; restricts to that proposal's candidate version."),
            "generation": integer(
                "Zero-based index into the base chain: 0 is the initial version.", 0
            ),
            "flipped_between": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
                "maxItems": 2,
                "description": "Two version ids; return their results for tasks whose mean "
                "outcome differs between them.",
            },
            "limit": integer("Maximum rows, 1 to 1000. Default 100.", 1, maximum=1000, default=100),
        },
    ),
    Tool(
        "failures_similar",
        "records",
        "Find stored failure cards most similar to a text query, across all public runs. Use "
        "it to check whether a failure pattern was seen before; use failure_cards for one "
        "run's complete list. HB workspaces only. Makes no model calls: it scores local word "
        "overlap unless an embedding function was injected through the Python SDK. Refreshes "
        "the stored card index first, an idempotent overwrite. Returns {method, matches}, each "
        "match with score, run_id, version and card.",
        {
            "text": {
                "type": "string",
                "minLength": 1,
                "description": "Text to match, for example an error message or failure reason.",
            },
            "limit": integer("Maximum matches, 1 to 100. Default 10.", 1, maximum=100, default=10),
        },
        ("text",),
        writes(idempotent=True),
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
