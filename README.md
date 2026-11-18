---
description: Measured self-improvement with paired experiments and a DSH plugin.
---

# evokit

evokit gives AI systems a measured self-improvement loop. It versions editable artifacts, records paired experiments, and gates changes against measured noise, cost and latency. Use it as a DeepSeek Harness (DSH) plugin or through its Python SDK, CLI and MCP server.

Your system supplies three pieces: **editable file globs**, **stable tasks**, and **a runner that executes and scores each task**. Prompts, skills, code and configuration can all be editable; the task source and scorer stay frozen.

The loop is **diagnose → propose → paired experiment → noise-aware gate → holdout confirmation with automatic revert in the operator workflow**. The improver sees diagnosis and selection evidence. A separate operator adopts a candidate, checks sealed confirmation aggregates, and calls `ledger_revert` when confirmation fails; `confirm_adoption` alone does not move the pointer. An optional final split opens once, with human approval, and never selects changes.

## Quick start

Requires Python 3.11+, uv, DSH `0.2.2-rc.1`, and its pnpm prerequisite. Windows is the verified local platform; Linux/macOS runtime integration remains unverified. Download the wheel, bundle and `SHA256SUMS` from [Releases](https://github.com/edsml-jz2321/dsh-evokit/releases) after publication and compare their SHA-256 hashes before installing.

```console
uv tool install ./dsh_evokit-0.1.6-py3-none-any.whl
dsh plugin --profile evokit add ./dsh-evokit-0.1.6.tgz
dsh plugin --profile evokit list
```

Set `EVOKIT_MCP_COMMAND` to the absolute installed `evokit` executable, `EVOKIT_WORKSPACE` to your scenario directory, and `EVOKIT_SKILL_ROOT` to the installed bundle's `skills` directory. See the [DSH guide](integrations/dsh/README.md) for an isolated profile and exact PowerShell commands.

Configure the improver model in your DSH profile using **your own provider key** and your provider's supported model name. Keep the key in DSH's local credential settings or a process environment variable, never in this repository. The optional [relay](integrations/relay/README.md) supports file-based keys and accounting; adapt its example endpoint, model identity and illustrative prices before use. No key is included or required for the offline example. Installing or listing the plugin makes no model requests; starting evolution may incur provider charges.

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

Set budgets, anchors, noise policy and a stopping rule before experiments. Repeated mean gates use threshold/√R; majority gates retain the single-repeat threshold. Confirmation has a limited wear budget and is not an untouched final benchmark. One public BFCL pilot improved held-out accuracy from **41 to 47 of 50 after confirmation**; this small example does not establish general gains.

Scenarios run trusted executable code. Per-task directories are not a security sandbox. Protect sealed tasks and records with OS permissions; MCP filtering cannot hide files an improver can read directly. Review proposed changes for task literals and leakage. The relay accounts reported usage conservatively; it cannot guarantee provider billing or prevent every external cost.

See [concepts](docs/concepts.md), [tools](docs/tools.md), [storage](docs/storage.md), [caching and recovery](docs/caching.md) and [contributing](CONTRIBUTING.md). This project uses the [MIT license](LICENSE), Copyright (c) 2026 quei.
