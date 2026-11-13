# Concepts

## System and scenario

A system is a fixed model/environment plus editable artifacts: prompts, skills,
knowledge, tool code, control flow or configuration. A scenario supplies editable
globs, tasks and a scorer. The framework owns experiments and lineage. The host
owns proposing and reviewing mechanisms. META coordinates and decides; analysts
and improvers propose, and executors run arms. The `meta` and `evolve` skills
define those responsibilities. There is no fixed candidate/judge/critic
topology and no assumption that a task has a reference answer.

## Versions and epochs

Each version is a manifest mapping editable relative paths to SHA-256 file bytes.
The SHA-256 of its canonical manifest is its id. Blobs are verified before use.
Unchanged supporting files are snapshotted at epoch creation; a version materializes
those files plus its editable manifest. A candidate directory must retain exactly
the frozen support files. New, removed and changed editable files are allowed.
Symlinks, junctions and case collisions are rejected for portability. Snapshots
track file content; executable permission bits and external dependencies are not
versioned. Invoke scripts through their runtime and pin dependencies separately.

An epoch freezes the task source, seeded split, final scorer version, located
adapter/config source digests and Python scorer bytecode, noneditable support,
editable scope, literal guards and budget policy. Diagnosis explains
failures; selection decides adoption; confirmation decides only keep-or-revert
after adoption; final is opened once and decides nothing. `holdout` remains a
configuration/data alias for confirmation, including existing epoch assignments.
Largest-remainder allocation within each optional group preserves proportions.
Group stratification distributes each group across splits; it does not keep entire
groups in one split. Tiny groups may have empty partitions; readiness counts reveal
that limitation. The installed evokit scenario module may relocate only with identical bytes.
User adapter paths, callable fingerprints and scenario-relative anchors still
match exactly. A new state directory starts a new epoch. Optional YAML `splits`
sets diagnosis, selection, confirmation (or holdout) and final fractions summing to
1. Defaults are 0.4/0.4/0.2/0. Final defaults to empty; adding it needs a new epoch.
Neither tools nor public queries reveal confirmation/final identities, per-task
scores, outputs or traces. Only counts are visible before opening final.

Before the first arm (including a keyless smoke), META records an epoch contract:
objective, cost/latency ratio guards, split/scorer anchors, confirmation `min_net`
rule, repeated-incumbent noise plan, total/per-generation budget and unit,
stagnation window and confirmation wear budget (default 4). The min_net form
represents either zero or a pre-chosen negative noise threshold. The tool refuses
changes once work has started; changing anchors then requires a new epoch. META
owns enforcing the campaign's budget and stopping plan. Existing scenario gate
budgets remain enforced by the gate.

`confirm_adoption` pairs the adopted current child against its parent on
confirmation, reusing complete retained measurements. It returns aggregate fixes,
breaks, net, cost/latency ratios and the contract's verdict. Rule or ratio-guard
failure marks the proposal not confirmed; META uses `ledger_revert` with a reason.
Confirmation never selects among candidates. Each opening consumes a wear slot,
even on execution failure; resuming the same adoption reuses its slot. Completed
decisions are idempotent. Exhaustion requires a new epoch with fresh tasks. These
reused confirmation results become optimistic estimates; report them separately
from final measurements. Improvers receive only the `not_confirmed` proposal bit.

At campaign end, with human approval, `final_measure` opens a separate final split
once and pairs current against the epoch's initial version. It returns aggregates
and changes no pointer. The opening is durable before execution, so a failed
measurement still consumes it. Opening final closes evolution in that epoch.
Without final tasks, use a new epoch with fresh tasks; confirmation must never
serve as the final measurement. A final result cannot justify adoption or tuning.

## Runs and replay

An arm is version × split × repeat count. `run_launch` returns immediately by
default; a detached process works through task slots with a bounded thread pool.
`wait=true` executes synchronously. Distinct arm ids can run concurrently. A run
has one executor at a time, and completed task/repeat records are never rerun.
An interrupted slot without a committed newline is retried. Scenarios with external
side effects must make those operations idempotent; this is not an exactly-once
provider transaction. Resume with the same run id and the same parameters.

Every task/repeat has its own materialized directory under its arm. Results record
outcome, reason, numeric costs, seconds, output, trace path, version, epoch, repeat
and scorer version. Traces are archived before the result commits. Execution errors
are explicit failed rows and block a gate. Fix execution defects and use a new run
to retry scored errors; resume preserves them as evidence.

Optional `run_replay` uses `StageCache.replay(stage, key, compute)`. The scenario
owns cache-key completeness: include stage implementation/version, relevant config,
input, model/environment identities and randomness when applicable. The cache
serializes one producer per key and persists JSON outputs. The toy's stage 2 edit
leaves the stage-1 key unchanged, so stage-1 cost becomes zero. Confirmation and final caches use
separate private namespaces. Cache speed does not establish behavior improvement.

## Paired decisions

Both arms evaluate identical tasks, groups and repeat counts. Mean aggregation
preserves scores in [0,1]. Majority aggregation accepts binary outcomes and treats
a tied vote as failure. Net is the sum of child-minus-parent per-task aggregated
scores, not a percentage. Fixes and breaks name tasks with positive/negative deltas.
Cost and latency ratios normalize each task over repeats, then sum tasks. Absolute
cost and task-seconds budgets count the complete candidate arm, including all repeats.
A positive child cost over a zero parent cost has an unbounded ratio, represented
as JSON null, and fails a ratio budget. Missing cost units count as zero.

Repeated incumbent selection runs estimate each task's pairwise flip rate and
paired net spread. The recorded threshold is the maximum of the largest observed
absolute repeat net and 1.96 times the square root of twice the summed per-task
sample variances. It is a conservative single-repeat reference, not a confidence
guarantee, and does not model correlated provider drift. Use same-window controls
and more repeats when the margin is small. Gates using mean aggregation divide this
single-repeat reference by sqrt(R); majority aggregation retains it unchanged.

The public gate verifies editable scope, scans introduced content for public task
ids, declared public literals and public gold strings, checks epoch/scorer/private split isolation and
budgets, and requires generic-mechanism attestation. Literal scans cannot prove
the absence of semantic or encoded leaks. Default cost and latency ratio ceilings
are 1.2; scenarios may declare alternatives. Gain strictly above the measured
threshold promotes. Nonnegative gain can adopt-no-harm with a substantive rationale.
All hard checks apply to both verdicts. Adoption rechecks evidence against current
before appending an event. Promising runner-ups remain queued for a new proposal
and fresh testing on the next base.

Private confirmation/final ids and `private_literals` are checked only in the operator's
post-adoption audit, with no match status returned to the improver. A public
pass/fail response that depends on a secret literal is a membership oracle even
when it hides the matched text. This privacy boundary takes precedence over using
secret membership as an adoption signal. Public literals/gold_strings must never
contain private confirmation/final labels. Guard matching uses word boundaries and newly
introduced lines; removal of a pre-existing literal is allowed. Numeric or one/two
character task ids count only when quoted, to avoid treating generic scalar
changes as instance patches. Use descriptive task ids and explicitly declared
public literals when a stricter policy is appropriate. This heuristic remains a
review backstop, not proof against arbitrary task-specific behavior.

## Lineage and recursion

The append-only event ledger records proposals, runs, verdicts, adoptions, noise
estimates, queue entries and rationale. Events form a hash chain. The ledger is
authoritative for the current base and base chain. A revert appends a new
pointer event back to the current adoption's parent; failed proposals, adoptions
and their evidence remain in history. The stored current pointer is
a convenience cache. Files uses JSONL; HB stores the events natively. See
[storage](storage.md) for backend selection and holdout placement. Never edit or truncate history to hide a failed experiment. A damaged
ledger fails loudly and needs explicit recovery preserving the original bytes.

Level 0 evolves system artifacts. Level 1 evolves the improver playbook and proposal
practice. Each proposal snapshots the exact playbook bytes and records its digest.
Metrics report proposal acceptance rate, absolute prediction error and gain/cost.
Gain/cost includes every committed public experiment result, including controls,
smokes and rejected candidates, so failed experiments remain part of the expense.
META uses the operator tool profile to record contracts, adopt, confirm, revert and
open final. The default improver profile omits those tools and refuses guessed
operator tool names at dispatch. An operator reads confirmation rate through
`meta_metrics`; the improver profile removes that rate. `Workspace.measure_holdout`
is a legacy SDK alias: contracted epochs use the confirmation rule and wear;
uncontracted legacy clients retain count-only measurement behavior. This is a
capability boundary, not a sandbox for an improver with arbitrary shell access.
Use separate host permissions for private state. Level 2 can change scoring or
gate policy only at an epoch boundary, with calibration and fresh noise.
