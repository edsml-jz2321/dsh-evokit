# evokit for DeepSeek Harness

evokit improves the editable parts of an AI system (prompts, skills, code,
configuration) and keeps a change only when measurements show it helps.

**How it differs.** A proposed change is adopted only after a paired experiment
against the current version, a noise-aware gate derived from measured
run-to-run variation, and a sealed holdout confirmation; when confirmation
fails, the operator workflow reverts the adoption. Each experiment also records
cost and latency.

**Documented result.** In one public BFCL pilot, held-out accuracy went from
**41 to 47 of 50 after confirmation**. This is one small example; it does not
establish general gains.

**What you supply.** Editable file globs, stable tasks, and a runner that
executes and scores each task. A keyless offline example in the
[repository](https://github.com/edsml-jz2321/dsh-evokit#minimal-example) runs
without a model or provider key.

This bundle adds evokit's stdio MCP tools and the `onboard`, `evolve` and `meta`
skills to DSH. The Python engine is installed separately; the bundle does not
install Python.

## Quick install

Requires Python 3.11+, uv, Node 24, DSH `>=0.2.2-rc.1` or later (no upper bound;
checked on 0.2.2-rc.1 and 0.2.2-rc.4, later releases unverified) and DSH's pnpm prerequisite. Windows is the verified
platform; Linux and macOS runtime integration remains unverified.

```console
dsh plugin --profile evokit add dsh-evokit
uv tool install dsh-evokit
dsh --profile evokit
```

Run the last command from your scenario directory. `EVOKIT_MCP_COMMAND`, `EVOKIT_WORKSPACE` and `EVOKIT_SKILL_ROOT` are optional: they default to `evokit` on PATH, the directory where DSH starts, and the bundle's own `skills` folder. Set them to override these defaults.

## Install in an isolated profile

Verified alternative from release files: download and verify
`dsh_evokit-0.1.10-py3-none-any.whl`, `dsh-evokit-0.1.10.tgz` and `SHA256SUMS` from the
[release page](https://github.com/edsml-jz2321/dsh-evokit/releases).
From PowerShell, with an existing scenario at `./my-system`:

```powershell
uv tool install ./dsh_evokit-0.1.10-py3-none-any.whl
$env:EVOKIT_WORKSPACE = (Resolve-Path ./my-system).Path  # optional; defaults to the directory where dsh starts
$env:DSH_HOME = Join-Path (Get-Location) 'private-dsh/home'
$env:DSH_AGENTS_HOME = Join-Path (Get-Location) 'private-dsh/agents'
$env:HOME = $env:DSH_HOME
$env:USERPROFILE = $env:DSH_HOME
$env:DSH_TELEMETRY_DISABLED = '1'
$env:DSH_TELEMETRY_MODE = 'DISABLED'
dsh plugin --profile evokit add ./dsh-evokit-0.1.10.tgz
dsh plugin --profile evokit list
dsh --profile evokit --dump-config
```

Keep these variables for each launch. On other shells, set the same absolute
paths using the shell's environment syntax. Package listing checks installation;
`--dump-config` checks configuration without starting an agent or making model
requests.

## Configure your improver

Use your own provider key in DSH's local credential settings or a process-local
environment variable. Set the provider URL and model in your private DSH profile;
do not commit credentials. No particular gateway is required. For optional
file-key accounting, adapt
[the relay example](https://github.com/edsml-jz2321/dsh-evokit/blob/main/integrations/relay/README.md),
set `EVOKIT_IMPROVER_BASE_URL` to its loopback `/v1` endpoint and
`EVOKIT_RELAY_CLIENT_TOKEN` to its local client token, then apply
`improver.patch.yml` with `--patch`. Its model policy is an example, not a
universal provider default. Confirm model support and pricing before execution.

Run the `onboard` skill with the scenario's config path. The `evolve` skill
diagnoses and proposes; `meta` owns adoption, sealed confirmation and revert.
The default MCP profile excludes operator decisions. Configure a separate
`evokit mcp --profile operator` server for those decisions and protect its files
from the improver's direct access.

## Inspect tools without a model

The source includes `list-tools.mjs` and `list-tools.patch.yml`. Copy the overlay
to a private directory, replace `EVOKIT_TOOL_INSPECTOR_URI` with the module's file
URI, and pass it with `--patch`. It disables model adapters and credential services
and exits after comparing registered MCP tools against `evokit tools` and
checking that the `onboard`, `evolve` and `meta` skills are registered.

## Add the market index

DSH ships no built-in plugin index. Enable **Custom plugin indexes**, open
**Discover → Add index source**, and enter:
`https://edsml-jz2321.github.io/dsh-evokit/index.json`.
Its entries install the `dsh-evokit` package from npm.

MIT; see [LICENSE](LICENSE).
