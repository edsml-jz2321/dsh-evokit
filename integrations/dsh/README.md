# evokit for DeepSeek Harness

The native DSH bundle adds evokit's stdio MCP tools and the `onboard`, `evolve`
and `meta` skills. Install Python and the wheel first. Compatibility is pinned to
DSH `0.2.2-rc.1`, Node 24 and DSH's pnpm prerequisite. Local runtime acceptance
uses Windows. Other platforms remain unverified.

## Install in an isolated profile

Download and verify `dsh_evokit-0.1.5-py3-none-any.whl`,
`dsh-evokit-0.1.5.tgz` and `SHA256SUMS` from the planned
[release page](https://github.com/edsml-jz2321/dsh-evokit/releases).
From PowerShell, with an existing scenario at `./my-system`:

```powershell
uv tool install ./dsh_evokit-0.1.5-py3-none-any.whl
$env:EVOKIT_MCP_COMMAND = (Get-Command evokit).Source
$env:EVOKIT_WORKSPACE = (Resolve-Path ./my-system).Path
$env:DSH_HOME = Join-Path (Get-Location) 'private-dsh/home'
$env:DSH_AGENTS_HOME = Join-Path (Get-Location) 'private-dsh/agents'
$env:HOME = $env:DSH_HOME
$env:USERPROFILE = $env:DSH_HOME
$env:DSH_TELEMETRY_DISABLED = '1'
$env:DSH_TELEMETRY_MODE = 'DISABLED'
dsh plugin --profile evokit add ./dsh-evokit-0.1.5.tgz
$env:EVOKIT_SKILL_ROOT = Join-Path $env:DSH_HOME 'profiles/evokit/node_modules/dsh-evokit/skills'
dsh plugin --profile evokit list
dsh --profile evokit --dump-config
```

Keep these variables for each launch. On other shells, set the same absolute
paths using the shell's environment syntax. The bundle does not install Python.
Package listing checks installation; `--dump-config` checks configuration without
starting an agent or making model requests.

## Configure your improver

Use your own provider key in DSH's local credential settings or a process-local
environment variable. Set the provider URL and model in your private DSH profile;
do not commit credentials. No particular gateway is required. For optional
file-key accounting, adapt [the relay example](../relay/README.md), set
`EVOKIT_IMPROVER_BASE_URL` to its loopback `/v1` endpoint and
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
and exits after comparing registered MCP tools against `evokit tools`.

## Add the market after publication

DSH ships no built-in plugin index. Enable **Custom plugin indexes**, open
**Discover → Add index source**, and enter:
`https://edsml-jz2321.github.io/dsh-evokit/index.json`.
This URL becomes available only after the publisher deploys market/.
The Git source pins the exported bundle subdirectory and a specific commit.

MIT; see [LICENSE](LICENSE).
