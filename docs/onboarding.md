# Onboard a scenario

## Scaffold and check an existing system

Create a small directory containing editable artifacts and frozen execution
support. Supply an existing JSON task array and your trusted run-and-score command:

```console
evokit init ./my-scenario --editable "prompts/*.md" --tasks ./public-tasks.json --command-argv node run-and-score.mjs
evokit doctor --config ./my-scenario/scenario.yaml
```

`--editable` can be repeated. Put `--command-argv` last: every following token is
part of the command. `--command "shell command"` supports platform shell syntax.
Add `--adapter python` before the command option for a delegating SDK skeleton.
When a terminal prompts for missing required inputs, it also asks for the command.
A blank answer or complete flags without a command creates runner.py. That scaffold deliberately raises
NotImplementedError until you implement real execution and scoring. Init copies
an external task file to tasks.json, preserves an existing internal task file,
and refuses to overwrite any generated destination. It does not create fake
editable artifacts or invent passing results. A terminal can prompt for missing
globs and task-file paths; automation supplies those flags explicitly.

Doctor initializes/checks the normal workspace epoch. It snapshots the same
version twice and checks stable config identity, frozen scorer/support anchors,
nonempty disjoint splits and the public tool boundary. It scans editable files
for public task ids and declared public literals/gold strings. It does not scan
private confirmation/final strings or reveal secret membership. These checks do not replace
OS permissions or review of executable adapters.

Static doctor does not invoke the runner. Scorer/dry-run checks are skipped,
`ready` is false and cost is null. After reviewing execution authorization and
budget, supply public human-verified calibration labels and enable the dry run:

```json
{"public-case-001": 1, "public-case-002": 0}
```

```console
evokit doctor --config ./my-scenario/scenario.yaml --run --labels ./calibration.json
```

Doctor executes the first two diagnosis tasks twice, then additional labeled
public tasks once. It stops at the first execution error; it does not retry a
failed slot or evaluate confirmation/final. Explicit labels must refer to public tasks only.
Without a labels file, it uses public entries in the adapter's optional
calibration_labels mapping. An empty label set leaves scorer sanity unverified.
The report gives label agreement, repeat outcome agreement, reported cost per
invocation and measured seconds. Missing cost units remain unknown; an empty cost
map is not proof of free model execution. Failed execution makes cost null.
Results remain under the state's doctor/ subdirectory. Stochastic outcomes are
reported for noise planning, not treated as config drift.

Failed checks exit 2. A static/incomplete report exits 0 with `ready: false`;
automation must inspect `ready`. A complete calibrated pass sets `ready: true`.
The suggested next step is repeated incumbent selection controls followed by
noise_estimate. The two-task check is not a measured promotion threshold.

The bundled `onboard` skill finds an existing run/scoring boundary, proposes edit
scope, runs init/doctor and a normal two-task smoke under existing execution
authorization. Both onboard and evolve ship in the wheel, Agent Plugins package
and DSH bundle. Init and doctor are CLI commands, not new improver MCP tools.

## Three required pieces

The Python adapter requires only `editable`, `tasks()` and `run_and_score()`.
Its no-argument constructor is sufficient. A scorer version defaults to `1`,
groups default to `default`, and replay is off. There is no mandatory model client,
gold-answer table, role decomposition, calibration dataset or domain primer.

```python
from pathlib import Path
from evokit import Result, Scenario, Task, Workspace

class Rules(Scenario):
    editable = ("settings.txt",)

    def tasks(self):
        return [Task(f"case-{i:03d}", {"input": i}) for i in range(30)]

    def run_and_score(self, version_dir: Path, task: Task) -> Result:
        setting = (version_dir / "settings.txt").read_text().strip()
        output = task.payload["input"] + int(setting)
        return Result(output >= 0, "nonnegative output", {"calls": 1}, output=output)

ws = Workspace(Path("my-system"), Rules())
ws.initialize(seed=17)
arm = ws.launch(ws.current(), split="diagnosis", workers=2, wait=True)
cards = ws.cards(arm["run_id"])
```

Create settings.txt before initialization. Task payloads must be JSON, ids must be
unique nonempty strings, and outcomes must be bool or a finite score in [0,1].
Costs are nonnegative numbers keyed by scenario-defined units. `Result` defaults
cost to `{}` and trace to null. When seconds is omitted, evokit measures wall time.
Trace files must live inside the isolated version directory. Scenarios must be
thread-safe; use `workers=1` if their runtime is not safe for concurrent tasks.

For CLI/MCP, save the class in adapter.py beside the scenario YAML:

```yaml
adapter: ./adapter.py:Rules
seed: 17
# Optional: constructor keyword arguments.
options: {}
```

This config loads a trusted local module; it is not a code-install mechanism.
The scenario root is the config directory. Keep it small and self-contained,
including frozen execution support. State defaults to `.evokit/` beside it.
`workspace: ../private-state` changes the state path. `playbook: ./custom-skill.md`
selects an optional level-1 source; otherwise the bundled evolve skill is tracked.
For direct Python use, pass `playbook=Path(...)` to Workspace.

## Command adapter

The same contract can use YAML and any executable. `command` plus a task source
implements `run_and_score`; no Python import is required in the target system.

```yaml
editable: [prompts/*.txt]
tasks: tasks.json
command: [node, harness.mjs]
# A shell string is also accepted, using the platform's default shell.
```

tasks.json is an array of `{"id":"case-001","payload":{},"group":"optional"}`.
Tasks can instead be inline under `tasks`. The command runs inside the isolated
version checkout and receives absolute paths through:

| Variable | Meaning |
| --- | --- |
| EVOKIT_TASK_FILE | Input JSON object with id, group, payload |
| EVOKIT_RESULT_FILE | Destination for one JSON result |
| EVOKIT_VERSION_DIR | Isolated checkout path |

Write `{"outcome":true,"reason":"measured success","cost":{"calls":1},
"trace_path":"trace.txt","output":"optional"}` to the result file. Optional
`seconds` overrides wall time; optional `roles` reports cost maps by role. Exit zero
only after the file is complete. `timeout_seconds` defaults to 60. Nonzero exits,
timeout, malformed results and invalid traces are execution errors. Prefer argv
arrays to avoid shell syntax differences. For PowerShell use an argv array such as
`[powershell, -NoProfile, -File, harness.ps1]`, reading `$env:EVOKIT_TASK_FILE` and
writing UTF-8 JSON to `$env:EVOKIT_RESULT_FILE`.

## Optional capabilities and defaults

Configuration is checked before constructing an adapter or preparing a run.
Unknown keys are errors. Common keys are `adapter`/`options` (Python mode),
`workspace`, `storage`, `playbook`, `seed`, `stratified`, `splits`, and
`split_assignments`. Command mode instead requires `editable`, `tasks`, and
`command`, and accepts `timeout_seconds`, `scorer_version`, `primer`, `literals`,
`gold_strings`, `private_literals`, and `budgets`. Python scenario settings belong
in its constructor's `options`; command-only keys beside `adapter` are refused.
Do not invent host planning flags in scenario YAML. Put generic reasoning
procedures in editable prompt/skill text, or first implement and validate an
explicit adapter option. Existing v0.1.2 command and Python configs retain their
defaults; `holdout` remains a split-fraction alias for `confirmation`.

If your host needs file-key rotation, prefix caching and a budget relay, use the
optional [relay](../integrations/relay/README.md). No relay or provider key is
needed for onboarding or keyless tests. See [caching](caching.md) for the
difference between stage replay and provider prefix caching.

Since v0.1.3, evokit accepts
`split_assignments` in YAML: an object with `diagnosis`, `selection`,
`confirmation`, and `final` task-id lists. The first three must be nonempty;
`final` may be empty. Lists must partition every task exactly once. This
preserves repository-level holdouts from an already frozen dataset and is
anchored with the epoch; changing it requires a new epoch. It supersedes seeded
allocation, while retaining the seed as epoch metadata.

Python scenarios can override
`secondary_guards(parent, child, noise)`. Both selection and operator
confirmation invoke this optional callback; adoption rechecks it. The default
returns `{"passed": true, "metrics": {}}`. The callback must return a boolean
`passed` and aggregate-only metrics, using repeated public incumbent `noise`
rows. Parent/child rows can be private during confirmation: do not return task
ids, outputs, labels, examples or trace paths. Callback source and code are
anchored. A failed guard rejects selection or marks confirmation not confirmed;
META then records a revert. New callbacks require new epochs. Existing v0.1.2 epochs reopen with the default
no-op guard without rewriting data or frozen splits.

| Capability | Default | Extension |
| --- | --- | --- |
| Groups | `default` | Task.group; seeded stratification per group |
| Scorer anchor | `1` | Scenario.scorer_version / YAML scorer_version |
| Stage replay | Execute the full task | Override run_replay; use StageCache |
| Domain primer | Empty | Scenario.primer / YAML primer |
| Failure cards | Bounded payload summary, output, reason, trace pointer | Override render_failure |
| Guards | Public task ids, no optional literals | Public literals/gold_strings; operator-only private_literals |
| Calibration labels | Empty mapping | calibration_labels; scenario-owned calibration in v0 |
| Budgets | Cost/latency ratios <=1.2 | budgets.latency_ratio, cost_ratio by unit, max_cost by unit, max_seconds |

Python capabilities are class/instance attributes or ordinary methods. Command
adapters support primer, guards, scorer version and budgets in YAML; a custom card
renderer or stage replay requires a Python wrapper around the executable. No HB
workspace or provider credentials are required by the framework.

Absolute max_cost and max_seconds limits cover all task/repeat results in the
candidate arm. Ratios compare per-task averages over repeats. Budget values must
be finite and nonnegative; they are frozen with the epoch.

## Readiness and limits

Inspect `scenario_describe` for matched edit scope and nonempty task/split counts.
Then run a two-task smoke, inspect traces and cost units, repeat the incumbent on
selection, and check noise against the achievable margin. Confirm the scorer is
outside editable scope, all dependencies are reproducible, task identities stay
stable, and every sensitive reference string is in the scenario's guards when
available. Never declare secret confirmation/final labels as public guard strings: use
private_literals for the operator-only post-adoption audit. Returning whether a
guessed secret matched would disclose confirmation/final membership. Numeric/short ids are
checked only as quoted references; descriptive ids avoid that ambiguity.
Keep confirmation/final labels in operator-only storage for a real experiment;
the toy data is synthetic and its executable adapter is trusted.

A scenario does not fit when it has no measurable signal, feedback is too slow
for iteration, too few tasks relative to noise, irreversible side effects without
a sandbox, behavior outside editable control, or an irreproducible environment.
Do not paper over those problems with a more permissive gate.

Known adapter/config source files are automatically anchored, including Python
adapter source outside the scenario root for direct SDK use. A known scorer or
config source cannot overlap editable globs. Keep system implementation in separate
editable files and final scoring in the fixed adapter. Scorer version still
defaults to `1`; source anchoring adds no fourth required scenario capability.
External packages, environment configuration and model identities must be pinned
by the scenario's environment; v0 does not package that environment automatically.

### Paired secondary guards and noise policy (0.1.5 branch)

Use `paired_defined_guard(parent, child, extract)` to compare a secondary metric
only where both paired values are defined. It returns aggregate counts for both
undefined and one-sided undefined observations. If all pairs are undefined on
both sides, `not_applicable_both_sides=true`; no artificial means are returned.
Protect newly missing values with a separate count guard (`aggregation="sum"`,
`higher_is_better=false`). `one_observation_margin=true` floors a mean margin at
`1 / paired_defined_tasks`. Repeat observations first form task/group means,
so sum guards and their margins remain in task-count units. `paired_quality_noise(rows, extract)` measures a macro
paired-delta spread from complete defined incumbent repeats. Both helpers reject
duplicated, incomplete, errored or non-finite input and expose no row identities.

Set `Scenario.noise_policy` (or command YAML `noise_policy`) before recording an
epoch contract. Defaults preserve `multiplier=1.96, observed_floor=true`. A pure
one-sigma policy uses `multiplier=1.0, observed_floor=false`. The policy is
anchored; changing it requires a new epoch. A gate on a mean of R paired repeats
uses the recorded single-repeat threshold divided by sqrt(R). Majority aggregation
keeps the single-repeat threshold. This is a declared
noise reference, not a confidence guarantee. Changes to this policy or secondary
quality definitions require new incumbent repeats; old private confirmation
rows must not select their new definitions.
