---
description: Measured self-improvement with paired experiments and a DSH plugin.
---

<!-- mcp-name: io.github.edsml-jz2321/dsh-evokit -->

# evokit

evokit improves the editable parts of an AI system (prompts, skills, code, configuration) and keeps a change only when measurements show it helps.

**How it differs.** A proposed change is adopted only after:

- a **paired experiment** against the current version on the same tasks;
- a **noise-aware gate**, with a threshold derived from measured run-to-run variation;
- a **sealed holdout confirmation**; when confirmation fails, the operator workflow reverts the adoption.

Each experiment also records cost and latency, and every version records its parent, so lineage is traceable.

**Documented result.** In one public BFCL pilot, held-out accuracy went from **41 to 47 of 50 after confirmation**. This is one small example; it does not establish general gains.

**What you supply.** Editable file globs, stable tasks, and a runner that executes and scores each task. The task source and scorer stay frozen. A [keyless offline example](#minimal-example) runs without a model or provider key.

Use it as a DeepSeek Harness (DSH) plugin, or through its Python SDK, CLI and MCP server.

## Install

Requires Python 3.11+, uv, Node 24, DSH `>=0.2.2-rc.1 <0.3.0-0` (0.2.2-rc.1 and later 0.2.x releases, not 0.3) and DSH's pnpm prerequisite. Windows is the verified platform; Linux and macOS runtime integration remains unverified.

```console
dsh plugin --profile evokit add dsh-evokit
uv tool install dsh-evokit
dsh --profile evokit
```

Run the last command from your scenario directory. `EVOKIT_MCP_COMMAND`, `EVOKIT_WORKSPACE` and `EVOKIT_SKILL_ROOT` are optional: they default to `evokit` on PATH, the directory where DSH starts, and the bundle's own `skills` folder. Set them to override these defaults.

Verified alternative: download the wheel, bundle and `SHA256SUMS` from [Releases](https://github.com/edsml-jz2321/dsh-evokit/releases), compare their SHA-256 hashes, then install the files:

```console
uv tool install ./dsh_evokit-0.1.7-py3-none-any.whl
dsh plugin --profile evokit add ./dsh-evokit-0.1.7.tgz
dsh plugin --profile evokit list
```

See the [DSH guide](integrations/dsh/README.md) for an isolated profile and exact PowerShell commands.

Configure the improver model in your DSH profile using **your own provider key** and your provider's supported model name. Keep the key in DSH's local credential settings or a process environment variable, never in this repository. The optional [relay](integrations/relay/README.md) supports file-based keys and accounting; adapt its example endpoint, model identity and illustrative prices before use. No key is included or required for the offline example. Installing or listing the plugin makes no model requests; starting evolution may incur provider charges.

## How the loop works

The loop is **diagnose → propose → paired experiment → noise-aware gate → holdout confirmation with automatic revert in the operator workflow**. The improver sees diagnosis and selection evidence. A separate operator adopts a candidate, checks sealed confirmation aggregates, and calls `ledger_revert` when confirmation fails; `confirm_adoption` alone does not move the pointer. An optional final split opens once, with human approval, and never selects changes.

## Minimal example

From a source checkout, the Node.js command example needs no model or key:

```console
uv sync --frozen --group dev
uv run python examples/scripted_improver.py command-echo
```

Its `scenario.yaml` supplies the three pieces:

```yaml
editable: [prefix.txt]
tasks: tasks.json
command: [node, runner.mjs]
```

The runner reads `EVOKIT_TASK_FILE`, executes against `EVOKIT_VERSION_DIR`, and writes an outcome and cost to `EVOKIT_RESULT_FILE`. The scripted example works in a temporary copy. To connect your own system, follow [onboarding](docs/onboarding.md), then use the bundled `onboard`, `evolve` and `meta` skills.

## Safety and limits

Set budgets, anchors, noise policy and a stopping rule before experiments. Repeated mean gates use threshold/√R; majority gates retain the single-repeat threshold. Confirmation has a limited wear budget and is not an untouched final benchmark. The BFCL pilot above is a confirmation result from one small task set.

Scenarios run trusted executable code. Per-task directories are not a security sandbox. Protect sealed tasks and records with OS permissions; MCP filtering cannot hide files an improver can read directly. Review proposed changes for task literals and leakage. The relay accounts reported usage conservatively; it cannot guarantee provider billing or prevent every external cost.

See [concepts](docs/concepts.md), [tools](docs/tools.md), [storage](docs/storage.md), [caching and recovery](docs/caching.md) and [contributing](CONTRIBUTING.md). This project uses the [MIT license](LICENSE), Copyright (c) 2026 quei.
