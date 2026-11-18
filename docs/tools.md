# Stable tools, v0

CLI commands and MCP tool names share the transport-neutral dispatcher
`evokit.tools.call_tool(name, arguments, profile="improver")`. Both transports
validate the same closed JSON schemas and invoke Workspace. MCP uses the official
SDK over stdio; stdout contains only protocol messages.

The default **improver** profile exposes 16 base tools plus HB capabilities when
bound to HB. The **operator** profile additionally exposes `ledger_adopt`,
`contract_record`, `contract_read`, `confirm_adoption`, `ledger_revert` and
`final_measure`. META owns this profile. Improvers cannot invoke operator tools
by guessing their names. Profiles are selected by the host process, not a model
tool argument. Keep shell/filesystem authority separate where confidentiality is
required. Existing clients that made adoption decisions must explicitly select
operator; original tool names and schemas remain available there.

```console
evokit tools --profile operator
evokit mcp --profile operator --config scenario.yaml
```

Agent Plugins and the DSH bundle start the default improver MCP profile. Register
a separate operator server for META; never give it to delegated improvers.

Every tool takes `config`, the YAML scenario path. CLI convenience `--config PATH`
adds that argument to the JSON object supplied by `--args`. Paths are resolved on
the server host; a plugin integration must send the local server's path.

```console
evokit scenario_describe --config path/to/scenario.yaml
evokit run_launch --config path/to/scenario.yaml --args '{"version":"<sha256>","workers":4,"smoke":true}'
```

For PowerShell 5.1, prefer `--args-file arguments.json` to avoid native argument
quote rewriting. The file contains the same JSON object; UTF-8 with or without a
BOM is accepted. `--args` and `--args-file` are mutually exclusive. All tool outputs
are JSON. CLI validation/domain errors exit 2 and put a JSON error on stderr. MCP
reports tool errors through `isError`; inspect it before consuming content. A tool
does not silently accept unknown arguments. No tool returns confirmation/final task identities, per-task scores, outputs or
traces. Operator confirmation requires an adoption; final requires its single open.

`run_results` and `failure_cards` accept optional zero-based `offset` and positive
`limit` arguments. Omit them to retain the complete-list behavior. Start large
diagnoses with `failure_cards(details=false)` to read a compact failure index;
then request detailed pages only where needed. Pagination does not grant access
to private confirmation records.

## MCP metadata

Each tool definition carries a plain description (purpose, when to use it and
which sibling to use instead, side effects, result), a description and default
for every parameter, and MCP annotations. `evokit tools` prints the same
descriptions and schemas. The server also publishes short `instructions` that
define scenario, version and arm and outline the improver loop. Opening a
scenario for the first time, through any tool, creates its workspace and freezes
the epoch; after that, read-only tools change no workspace file.

| Annotation | Tools |
| --- | --- |
| Read-only (`readOnlyHint`) | scenario_describe, version_current, version_diff, run_status, run_results, compare_paired, proposal_list, ledger_history, meta_metrics, contract_read, records_query |
| Writes, append-only | version_create, noise_estimate, proposal_create, gate_check, ledger_adopt, contract_record, ledger_revert |
| Writes, idempotent (`idempotentHint`) | failure_cards and failures_similar (overwrite the stored card index), run_interleaved (a repeat resumes), confirm_adoption (a completed decision is returned again) |
| Executes scenario tasks (`openWorldHint`) | run_launch, run_interleaved, confirm_adoption, final_measure |
| Destructive (`destructiveHint`) | final_measure: it closes the epoch to further evolution |

Writes append records and may move the current pointer; none rewrites history.
Tasks run the scenario's own code, which may call external services and cost
money; all other tools work on local state only.

## Scenario and versions

| Tool | Arguments besides config | Result |
| --- | --- | --- |
| scenario_describe | None | epoch, editable globs, frozen scorer version, split counts, primer, budgets, replay capability, readiness flags |
| version_current | None | `{version}`: current SHA-256 id |
| version_create | directory?, patch?, parent? | `{version,parent,changed}` |
| version_diff | parent, child | `{parent,child,changed,diff}` |

`version_create` defaults directory to the original scenario checkout and parent
to current. `patch` is a unified Git-compatible patch string, applied to a temporary
parent checkout, mutually exclusive with directory. Candidate directories must
contain unchanged frozen supporting files. Editable additions/deletions are allowed.
Diffs verify content digests before rendering. No adoption happens automatically.

Python mappings: `Workspace.describe/current/create_version/diff`. The additional
SDK method `materialize(version, destination)` writes one version into a new
directory and refuses to overwrite an existing destination.

## Runs

| Tool | Arguments besides config | Result |
| --- | --- | --- |
| run_launch | version, split?=`selection`, repeats?=1, workers?=1, smoke?=false, run_id?, wait?=false | Run status |
| run_interleaved | arms (run-id → version), split?=`selection`, repeats?=1, workers?=4, seed?=0 | Status per arm |
| run_status | run_id | `{run_id,version,split,state,completed,expected,errors}` |
| run_results | run_id | Committed task result array; possibly partial while running |

`split` is diagnosis or selection. `workers` is 1–256, `repeats` is positive.
Smoke chooses the first two stable task ids, or fewer if a tiny split has fewer.
`wait=false` starts a detached worker and returns immediately. Different arms may
run in parallel; each arm is protected against concurrent execution. `wait=true`
blocks until completion. Python-only adapters without YAML use wait=true.

`run_interleaved` completes all arms for a task/repeat before starting the next
block, rotating submission order by the seed. Arms share a bounded worker pool.
Use fresh run identifiers initially; interruption resumes the exact same mapping
and parameters, skipping committed slots. Holdout is unavailable. This provides
concurrent controls when latency can drift between measurement windows.

Resume by calling run_launch with the original run_id and **all the same arm
parameters**, including workers/smoke/split/repeats. Completed slots are immutable
and never rerun, including explicit execution-error rows. Only uncommitted slots
are eligible for execution. An interrupted run can still show running after a hard
process death; resuming the same id acquires its released OS lock and continues.
Do not change to a new id merely because a connection to a detached worker was lost.

Each result row contains task_id, group, repeat, version, epoch, scorer_version,
outcome, reason, cost (numeric units), seconds, roles (optional cost maps), output,
trace_path (archived) and error. Files mode has one results.jsonl per run; HB mode stores native result rows. Trace pointers
must be read on the server filesystem by an authorized host tool. State/log paths
are local and not a portable blob-download protocol.

Python mappings: `Workspace.launch/status/results`; `execute(run_id)` is the worker
entrypoint. The internal CLI `_run CONFIG RUN_ID` is an implementation detail,
not an improver tool.

## Analysis and proposals

| Tool | Arguments besides config | Result |
| --- | --- | --- |
| compare_paired | parent_run, child_run, aggregation | fixes, breaks, net, per_task deltas, group tables, cost and latency ratios |
| noise_estimate | run_id | noise_id, per-task flip rate, net spread, threshold and method |
| failure_cards | run_id, group?, parent_run?, change?, aggregation?=`mean` | Full filtered card array |
| proposal_create | version, proposal | Recorded proposal, including id, parent and playbook_version |
| proposal_list | None | All proposal records in creation order |

`aggregation` is required for compare_paired and gate_check: `majority` for binary
votes with ties counted as failure; `mean` for numeric scores. Both arms must be
complete, error-free and paired on identical tasks, groups, split, repeats and
anchors. `net` is summed per-task score change. Costs/seconds are averaged across
repeats per task and summed across tasks; units are not combined. Ratio is null
for positive child over zero parent cost, and 1 for zero/zero.

noise_estimate accepts a complete repeated incumbent selection arm, with at least
two repeats and no smoke. It records threshold provenance. The method is described
in [concepts](concepts.md); it deliberately does not accept a caller-written margin.

failure_cards without a parent returns all outcomes below 1, with optional group
filter. For paired fixes or breaks, provide parent_run and `change: fix|break`.
Each card has task_id, group, payload_summary, output, outcome, reason and
trace_excerpt_pointer, unless the scenario overrides rendering. Cards are not
silently sampled or paginated; read the whole index, then original traces.
Each call also saves the rendered cards to the record store, overwriting
identical entries, so that failures_similar can search them.

`proposal` is validated against [proposal.schema.json](../src/evokit/proposal.schema.json):

```json
{
  "targets": ["prompts/guide.txt"],
  "mechanism": "Make the general fallback condition explicit",
  "predicted_effect": 3,
  "falsification": "Net <= 0 or any unacceptable break in the safety group",
  "prior": {"attempt": null, "difference": "First test of this mechanism"}
}
```

Targets are exact editable file paths covering all changes. Prediction is in net
score-sum units on the selection split. Prior is an existing proposal id or null.
The five fields are required; unknown fields and blank mechanisms are rejected.
The proposal automatically pins current parent, candidate version and a snapshot
of the exact improver playbook. A candidate's version is created before proposing.

Python mappings: `Workspace.paired/noise/cards/propose/proposals`.

## Decisions, lineage and level 1

| Tool | Arguments besides config | Result |
| --- | --- | --- |
| gate_check | proposal_id, parent_run, child_run, noise_id, aggregation, rationale?=`""`, generic?=false | Recorded verdict id, promote/adopt-no-harm/reject, reasons, threshold, comparison |
| ledger_adopt (operator) | verdict_id, rationale, queue?=false | Adoption or runner-up event payload |
| ledger_history | None | current, base_chain, runner_ups, public events |
| meta_metrics | None | acceptance rate, prediction error, gain per cost, confirmation measurement counts, playbook versions |

The gate requires full selection arms, current proposal parent, frozen anchors,
an editable-only generic change, a clean public instance-literal scan, within-budget cost
and latency, and matching noise provenance. Declare `generic=true` only after
reviewing that the mechanism generalizes beyond observed task instances. Rationale
is mandatory for adopt-no-harm. Neither attestation nor rationale bypasses hard
checks. A verdict is evidence, not an adoption: ledger_adopt rechecks immediately
before changing the pointer. Its rationale must be nonempty. A runner-up event
can record a rejected experiment for later reconsideration but never adopts it.

The append-only ledger is authoritative, with current.json as a convenience cache.
Queue entries are keyed by proposal and leave the queue after adoption. Retesting
on a new base means applying the mechanism to that base, creating a new proposal
that names the prior, and running fresh arms. It never reuses a stale passing gate.

`proposal_list` adds `not_confirmed` (false before confirmation or after a passing
decision). It never reveals confirmation scores. `ledger_history` returns sanitized
revert pointer events and hides private contract, measurement and opening events.
Revert reasons stay in operator history because they may contain private aggregates.
The base chain includes reverts as pointer movements without removing adoptions.

Acceptance rate is adopted proposals / all proposals. Prediction error is the mean
absolute difference between predictions and the latest measured net for each
proposal with a comparison. Gain per cost divides adopted net by the total reported
cost of all public run results, independently per unit, including controls, smokes
and rejected candidates. `experiment_cost` reports those totals. Empty metrics use
0 or null as appropriate. Absolute gate budgets count all candidate repeats.
The operator profile adds `confirmation: {decisions, rate}` to meta_metrics. Rate
is confirmed decisions / completed confirmation decisions (null if none). The
improver profile omits this field and receives only proposal not-confirmed bits.
Legacy `holdout` measurement counts remain for SDK compatibility.

The public instance scan uses diagnosis/selection task ids and scenario-declared
public literals/gold_strings. It checks introductions, not deletions, with word
boundaries; numeric/short ids require quoted references. Private confirmation ids and
private_literals are audited after adoption in the operator-only measurement
record. Their match status cannot affect public gate responses, which would leak
secret membership. Do not put private labels in public literal declarations.

Python mappings: `Workspace.gate/adopt/history/metrics`. The legacy SDK
`measure_holdout(version, workers=1)` remains a count-only alias; contracted epochs
route through confirmation, including rule and wear accounting.

## Operator decisions

Each command below requires `--profile operator` (or an operator MCP server).
The equivalent Workspace method has the same name.

| Tool | Arguments besides config | Result |
| --- | --- | --- |
| contract_record | contract | Anchored contract with epoch, scorer version, split hash and counts |
| contract_read | None | Latest anchored contract or null |
| confirm_adoption | version, workers?=1, reuse_only?=false | Aggregate fixes, breaks, net, cost_ratios, latency_ratio, verdict |
| ledger_revert | reason | Child (`parent`), new current (`version`), reason, proposal, adoption identity, not_confirmed |
| final_measure | workers?=1 | Aggregate fixes, breaks, net, cost_ratios, latency_ratio |

Record the contract before any arm is prepared, including a keyless smoke. The
following JSON is the `contract` argument. Split assignments and scorer anchors
are captured automatically; no task ids are accepted in this argument.

```json
{
  "objective": "Maximize per-task success",
  "guards": {"latency_ratio": 1.2, "cost_ratio": {"money": 1.2}},
  "confirmation_rule": {"min_net": 0},
  "noise_plan": {"repeats": 2, "method": "paired flip rate and net spread"},
  "budget": {"unit": "money", "total": 200, "per_generation": 40},
  "stagnation_window": 2,
  "confirmation_wear_budget": 4
}
```

The rule passes if paired net is at least min_net and all cost/latency guards
pass. Use a nonpositive min_net for a pre-chosen noise margin. An unbounded ratio
(JSON null) fails its guard. Missing cost units are zero, with zero/zero ratio 1.
Contract changes after work starts fail; start a new epoch. Budgets and the
stagnation window record META's stopping plan; they do not start a scheduler.

Confirmation measures current child versus its adoption parent once per adoption.
Complete same-anchor measurements are reused. `reuse_only=true` forbids execution
when retained measurements are missing. Each opening spends one wear slot, even
on failure; retrying the same adoption resumes that slot. Completed decisions
return their original aggregate without spending again. A failed verdict marks
the proposal but does not move current: META calls ledger_revert with a reason.
A revert always targets the current adoption's parent; initial cannot be reverted.
All events append; no history is replaced.

Final requires a nonempty separately anchored split and a contract. META asks the
human first. The opening commits before execution, can happen only once, and
closes the epoch to further public arms/adoptions/reverts. A failure still consumes
the opening. Final pairs current with the epoch starting version and decides
nothing. Neither confirmation nor final exposes private run ids, group tables,
task ids, outputs or traces. All workers arguments are integers 1–256.

Legacy epoch JSON and holdout run names remain valid without rewriting anchors.
The SDK-only `migrate_confirmation_contract(contract, reason)` appends an explicitly
retrospective rule to an uncontracted legacy epoch with retained measurements.
It cannot execute tasks and is absent from tool profiles. Review the migration,
back up state, test on a copy, then call confirm_adoption with reuse_only=true.


## State layout and integration boundary

```text
.evokit/
  epoch.json                 private tasks, splits, frozen scorer/support/policy
  blobs/<sha256>             file bytes, verified on materialization
  versions/<sha256>.json     editable manifests
  current.json               pointer cache; ledger is authoritative
  playbooks/<sha256>.md      level-1 artifact versions
  ledger.jsonl               append-only hash-chained events
  runs/<id>/run.json         pinned arm specification
  runs/<id>/results.jsonl    one committed row per task/repeat
  runs/<id>/arms/<slot>/     per-task isolated checkouts
  runs/<id>/traces/<slot>.txt archived traces
  cache/public/              optional replay entries
  cache/final/               sealed final cache
  cache/confirmation/             separate measurement cache
```

Integrations should call tools in their assigned profile through MCP or `call_tool`, not parse private
epoch/run files. No host-specific agent API is required. Start `evokit mcp` using
the package's installed environment; send config on each call. Use
`proposal_create` and `ledger_adopt` to preserve playbook and base provenance.
Version ids, tool names, JSON result field names, and the three-piece Scenario
contract are stable for v0. Internal filenames beyond this documented layout are
not extension APIs. No HB integration, API key access or model invocation is hidden
in the core.

## HB record capabilities

These two tools are available only for an HB workspace. List with
`evokit tools --config scenario.yaml`; start bound MCP with
`evokit mcp --config scenario.yaml`. Base tool names and onboarding remain the same.
An unbound improver server advertises only the 16 base tools. A bound server rejects
calls for a different config. Files mode rejects calls to HB tools.

| Tool | Arguments besides config | Result |
| --- | --- | --- |
| records_query | task?, group?, outcome?, version?, run_id?, epoch?, proposal?, generation?, flipped_between?=[version,version], limit?=100 (1–1000) | Public task result payloads with run_id |
| failures_similar | text, limit?=10 (1–100) | `{method,matches}`; each match has score, run_id, version and card |

Query filters combine with AND. `outcome` is an exact numeric score (0 through 1).
`generation` is a zero-based index into the initial/adopted base chain.
`proposal` restricts to the proposal's candidate version; an unknown proposal
matches no results. `flipped_between` returns results of those two versions for
tasks whose mean outcome changed, comparing the same epoch and split across all
public runs/repeats. Partial runs contribute committed results; this exploratory
query is not gate evidence. Use `compare_paired` for adoption decisions. Results
are ordered by stable record key and then limited. No filter accepts confirmation ids
as a private data selector; private records are in a different HB workspace.

Without an explicitly injected SDK embedding provider, similarity uses local
casefolded Unicode word-set Jaccard overlap over stored card JSON. It scans public
cards, scores 0–1 and breaks ties by stable card key. With an injected provider it
stores native HB Vector values and uses HB cosine-near; cosine scores can be
negative. The API reports `local-token-jaccard` or `hb-vector-cosine`. No model
provider or key is discovered automatically. Tests inject a fake vector function.

## Operator portability commands

`evokit export --config PATH --output NEW.jsonl` creates a private JSONL backup.
`evokit import --config PATH --state NEW_STATE --backend files|hb --input BACKUP`
restores records before initialization. These operations are absent from MCP and
include operator-only confirmation data. Copy snapshots and trace files separately.
See [storage](storage.md) for migration steps, private homes and recovery limits.

The 0.1.5 branch anchors `noise_policy` with configurable `multiplier` and
`observed_floor` (defaults 1.96 and true). `noise_estimate` reports both values.
`gate_check` recomputes that policy from the recorded incumbent and divides its
single-repeat threshold by sqrt(R) for the mean of R paired repeats.
`paired_defined_guard` and `paired_quality_noise` are Python SDK helpers for
aggregate secondary callbacks; see onboarding for missing-value denominators.
