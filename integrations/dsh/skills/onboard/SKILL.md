---
name: onboard
description: Onboard an existing repository as an evokit scenario by finding editable artifacts, stable tasks and a run-and-score entrypoint, then scaffolding and checking the integration.
---

# Onboard a system

Help the operator connect an existing system to evokit. Keep its language,
runtime, scorer and deployment conventions. The three required pieces are
editable file globs, stable JSON tasks and a runner that returns a measured
outcome, reason and cost. No model-provider or database interface is required.

Inspect the repository's instructions and existing test/evaluation commands.
Identify the run command and the independent scoring boundary. Ask the operator
only for missing decisions: editable scope, task source, success criterion and
execution budget. Propose concrete globs that exclude scorers, labels, task data,
credentials and frozen environment configuration. Do not invent a success score.

Run `evokit init --help`, then scaffold in a small scenario directory. For example:

```console
evokit init ./evolution --editable "prompts/*.md" --tasks ./public-tasks.json --command-argv node run-and-score.mjs
```

Use `--adapter python` before `--command-argv` for a Python SDK skeleton. A blank
interactive command creates an unfinished runner that refuses execution until
implemented. Init never overwrites existing files. Put executable/frozen support
inside the scenario root, or pin external dependencies explicitly. Verify that
the runner reads EVOKIT_TASK_FILE, uses EVOKIT_VERSION_DIR and writes one Result
JSON to EVOKIT_RESULT_FILE. Trace files must remain inside its isolated checkout.

Run `evokit doctor --config ./evolution/scenario.yaml` first. This freezes/checks
normal epoch anchors but does not execute the runner. Review counts, edit scope,
public-literal leak scans and confirmation/final isolation. Do not treat skipped checks as
passes. Keep labels and confirmation/final sources outside the agent's filesystem access
when confidentiality is required; evokit is not an OS sandbox.

With the operator's existing execution authorization and budget, use `doctor
--run --labels ./calibration.json` for two public diagnosis tasks twice plus any
additional explicitly labeled public tasks once. Labels map public ids to human
verified expected scores. Never supply or query confirmation/final labels. Inspect scorer
agreement, outcome stability, reported cost units and task time. Missing cost
units remain unknown. Stop on execution/accounting errors; do not retry paid work
or weaken the scorer to make readiness pass.

Run a two-task smoke through the normal tools: obtain `version_current`, then
`run_launch` with that version, `split=diagnosis`, `smoke=true`, `workers=1`,
`wait=true`; read `run_results`. Use an args file for PowerShell. Keep the run id
after uncertain transport and resume the same run instead of launching a duplicate.
Do not start provider-backed work without existing authorization.

Hand off the config path, editable scope, task/split counts, scorer identity,
calibration result, observed costs and unresolved checks. Suggest repeated
incumbent selection controls and `noise_estimate` before using `evolve`.
Holdout remains operator-only post-adoption measurement. Onboarding does not
authorize evolution, promotion, deployment or additional spending.
