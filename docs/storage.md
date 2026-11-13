---
description: Select a record backend, migrate records, and inspect an HB workspace while isolating confirmation/final data.
---

# Record storage

evokit stores artifact content in snapshots and evolution records through
`RecordStore`. Choose `files` (the default) or install `evokit[hb]` and choose
`hb`. The selected backend owns records. HB does not maintain a JSONL mirror.
No provider credentials or model calls are required by either backend.

## Select a backend

The three-piece scenario contract does not change. Optional YAML settings select
record storage before the workspace is initialized:

```yaml
storage:
  backend: hb
  workspace: my-evolution
```

The SDK accepts `Workspace(root, scenario, storage={"backend": "hb"})`.
Custom SDK backends implement `RecordStore` and use `Workspace(..., store=store)`;
they do not need a separate onboarding contract.
Mutable keyed records use complete replacement. Task/repeat slots are immutable:
an identical retry succeeds, while a conflicting payload fails. Ledger events use
`append`, `history`, and the operator-only `restore_events` contract. Restore
preserves timestamps, hashes and order, and requires empty history.
Omitting storage reuses `storage.json` in the state directory; an old workspace
without that selector uses files. The selector is deployment configuration, not
an experiment record. A conflicting selection fails instead of switching stores.
Use export/import into a fresh state directory to change backends.

Install with `uv sync --frozen --extra hb --group dev` in this repository or
`pip install 'evokit[hb]'` from a built package. The extra pins the unmodified
PyPI `heavenbase==0.1.2.2` wheel (SHA-256
`09113b8714fdcc054ad268735e64c9bb273411e6cfe94a395ba4ce1ef91e9d71`).
HB supports Python 3.11–3.13 in evokit. The extra also pins FastMCP `4.0.0b1`;
evokit's MCP dependency accepts SDK 2, as required by this FastMCP release.
The transport supports both [official SDK server APIs](https://py.sdk.modelcontextprotocol.io/v2/advanced/low-level-server/)
and preserves the same tool schemas and stdio wire names.
Package provenance: [PyPI release metadata](https://pypi.org/pypi/heavenbase/0.1.2.2/json),
inspected 2026-11-10. Patterns from a private downstream adopter informed the adapter.
No downstream checkout or patched wheel is a runtime dependency.

Before any HB import, give the process a private home. In PowerShell:

```powershell
$env:EVOKIT_HB_HOME = Join-Path (Get-Location) 'private-hb-home'
$env:HOME = $env:EVOKIT_HB_HOME
$env:USERPROFILE = $env:EVOKIT_HB_HOME
New-Item -ItemType Directory -Force $env:HOME | Out-Null
hb setup
evokit scenario_describe --config ./my-scenario/scenario.yaml
```

Run `hb setup` once for that home. Never use `--reset`. Keep the same private
home when reopening the workspace, including detached workers. On POSIX, set
all three variables to the same private absolute directory before `hb setup`.
MCP clients can filter inherited environment variables. Put `HOME`, `USERPROFILE`
and `EVOKIT_HB_HOME` explicitly in the MCP server launch configuration's `env`.
The adapter refuses an unset or mismatched home and never configures the user's
machine-wide `.heavenbase`. Call `Workspace.close()` when an SDK caller finishes
using an HB workspace. The tool dispatcher closes its backend after each call.

## What each backend owns

Both backends persist epoch anchors and task payloads, current pointer metadata,
version digests, run specifications, execution status, per-task results, failure
cards, proposals, verdicts, adoptions, noise estimates and metric inputs. Artifact
blobs, version manifests, playbook snapshots, archived traces and working checkouts
remain on the filesystem. A run resumes only uncommitted task/repeat slots.

Files mode preserves the original `epoch.json`, `current.json`, `ledger.jsonl`
and `runs/<id>/{run,status}.json` / `results.jsonl` layout. The ledger remains
append-only and hash chained. Incomplete result suffixes are discarded on resume.
Rendered failure cards and version metadata use `records/`.

HB uses six entities with the original JSON payload retained for schema evolution:

| Entity | Contents |
| --- | --- |
| evokit-version | Version digest, current pointer, operator epoch anchors |
| evokit-run | Run specification and execution status records |
| evokit-task-result | Original result plus typed run/task/group/version/epoch/outcome dimensions |
| evokit-failure-card | Original rendered card, text and optional vector |
| evokit-proposal | Original proposal and candidate version |
| evokit-ledger-event | Ordered event envelope, including verdicts and adoptions |

No built-in HB entity expresses these experiment semantics without changing its
meaning, so the adapter registers these six entities. It uses native HB schema,
CRUD and queries. New rows use `upsert`; updates use `set` because HB upsert
replaces a whole row. HB ledger events preserve existing event hashes on import.

## Backup and migration

Export/import are operator CLI operations. They are absent from MCP because the
backup contains confirmation/final payloads and results. Stop writers for an export or import;
export is not a transaction spanning concurrent experiments.

```console
evokit export --config scenario.yaml --output operator-records.jsonl
evokit import --config scenario.yaml --state .evokit-hb --backend hb --input operator-records.jsonl
```

The destination state is relative to the operator's current directory when passed
with `--state`. Import requires an empty record store. It checks the backup's
format, count, digest, duplicate keys, event hashes, anchors, run identities and
visibility before writing. Existing backup files are not overwritten. A storage
failure during restore can leave a partial target; preserve it for diagnosis and
retry in a new target. Import publishes the epoch last.

Copy `blobs/`, `versions/`, and `playbooks/` from the old state into the new state.
Copy archived traces and any replay caches separately if needed. Trace paths are
recorded paths, not portable file attachments; moving machines can require keeping
the same layout or explicitly relocating trace files. Keep the original backup
private and preserve the old store until the restored workspace is verified.
Then set `workspace: .evokit-hb` and `storage: {backend: hb}` in scenario.yaml.
For epochs created with this storage format, these deployment settings are excluded
from semantic config anchor comparisons. Snapshots retain the original config
bytes. Task, scorer and execution changes still fail anchors.
Older v0 epochs retain their original byte anchors. To migrate one, export while
writers are stopped, move its old state directory outside the scenario checkout,
and import HB records into a fresh directory at the **original state path**. Copy
the saved snapshots to that same path and leave the scenario YAML unchanged. The
persisted storage selector chooses HB. This preserves the original config bytes,
source paths, epoch and lineage. A changed scenario/deployment layout needs a new epoch.
New-format epochs remember the original state path and exclude it from support
scans, so the old store can remain in place during migration.

## Queries and similarity

`evokit tools --config scenario.yaml` adds `records_query` and `failures_similar`
only for HB. Start `evokit mcp --config scenario.yaml` for the same bound MCP
surface. An unbound `evokit mcp` retains the 16 improver tools, and rejects
unadvertised HB calls. A bound server rejects calls naming a different config.
See [tools](tools.md) for filters and exact result contracts.

`failures_similar` refreshes cards from public runs. Without an embedding provider,
it ranks cards by deterministic Unicode word-set Jaccard overlap. Ties use stable
card keys. This local fallback scans HB card text; it does not call a model.
An SDK operator can explicitly set `workspace.store.embedding = callable` on an
HB store; the callable accepts text and returns a nonzero finite vector of fixed
dimension. evokit stores it in the native HB Vector field and uses HB cosine-near
queries. No provider or key is auto-discovered. CLI/MCP defaults to the deterministic
fallback; an embedding provider is a process-local SDK dependency.

## Holdout access

The public HB workspace contains only public runs, results, cards, proposals and
lineage. A second workspace named `<workspace>-operator` holds the full epoch
(including confirmation/final tasks), confirmation/final runs, results and measurement events. Default
SQLite files are separate. Holdout execution directories and traces are below
`operator/` in evokit's state. Improver tools return only split counts and
measured/pending version counts. Neither new query tool accepts a private-domain
selector. Private measurement scores never become adoption feedback.

Restrict the operator workspace, its private home, scenario benchmark sources and
snapshot files with OS permissions or service credentials. The SDK is an operator
API. Trusted adapters can read their inputs; evokit does not sandbox executable
code or grant security to an improver with operator filesystem access. Do not
serve the operator workspace through HB MCP. This release exposes the safe evokit
MCP surface, without an additional ad hoc HB MCP profile.

## Dashboard and multiple hosts

In the same private home, run `hb dashboard --no-open` and open the printed
loopback URL. Select the public workspace id from `storage.workspace`, or the
generated `evokit-<digest>` id shown by `hb ws list`. Use the dashboard's workspace
inspector to browse the six entities. Only an operator should use that home or
select the operator workspace. Dashboard assistants are outside the test workflow;
do not configure providers when you want inspection without paid calls.

The default SQLite placement is local durable storage. For multiple hosts, supply
HB `backends` and separate `operator_backends` mappings in storage settings,
using the same workspace id and suitably restricted service credentials. HB owns
the placement and storage access. Snapshot files and lifecycle/run locks must
also be available at a shared filesystem path if several processes drive the
same experiment. Local OS file locks do not coordinate unrelated hosts with
independent state directories. Use one orchestrator in that configuration.
No Git authority, CAS, claims, receipts or projection checks are added by evokit.
