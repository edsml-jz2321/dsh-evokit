---
name: evolve
description: Improve editable system artifacts through evokit proposals, paired experiments, noise-aware gates and append-only lineage. Use with an onboarded evokit scenario.
metadata:
  version: 0.1.4
---

# Evolve a system

META decides; the improver analyzes and proposes. Use the `meta` skill for the
epoch contract, generation plans, adoption, confirmation reverts, budget, stopping
and the final measurement. Delegated improvers never make those decisions.

Use the scenario's `config` path with every tool. `scenario_describe` explains
editable scope, task counts, budgets and the domain primer. `version_current`
and `ledger_history` establish the current base and earlier attempts. The host
supplies the improver; these instructions do not require a particular agent.
Inspect the available tools (`evokit tools --config PATH`). For an HB workspace,
use `records_query` to compare tasks, groups, outcomes, proposal versions and
adoption generations, including `flipped_between: [parent, child]`. Use
`failures_similar` to find related earlier mechanisms before proposing. These
capabilities are absent for files storage; the generation loop below works on both.
Neither tool can query private confirmation or final records.

## Generation loop

1. Read the full failure index with `failure_cards` on a diagnosis run. Do not
   reason from a few attractive examples. Cluster failures by mechanism, then
   read original traces through their archived pointers. Keep task identities
   and observed answers out of every editable artifact, including derived files.
2. Read the measured selection noise threshold before proposing. State the
   predicted net score-sum effect and threshold in the same units, and whether
   the effect is detectable on this selection set. If noise is unmeasured, request
   incumbent repeats first. If the effect does not exceed the threshold,
   propose a stronger mechanism or state that more tasks or repeats are needed.
   Write distinct, falsifiable hypotheses. Cite prior attempts only from
   `proposal_list` / `ledger_history` records with their ids, or use null when
   no related record exists. Each proposal names target files, a generic mechanism,
   a falsification criterion and what differs from its cited prior. Verify that
   the failing tasks actually load the artifact, including its language and stage.
   Name the specific public failing tasks expected to flip and the trace changes
   that would demonstrate the mechanism in the proposal record. Edit in a candidate checkout,
   inspect the full diff for task, fixture, repository and expected-output literals,
   and remove them before claiming genericity. Keep task evidence in records.
   then use `version_create` and `proposal_create`. Each proposal pins this
   playbook's content version. Improve the playbook as a separate level-1 artifact
   using the same evidence discipline, preserving system/scorer anchors.
3. Smoke each candidate with `run_launch(smoke=true)` (two tasks when available).
   Fix execution errors before buying a full experiment. A completed result is
   never rerun on resume: pass the original run id and exact arm parameters.
4. Repeat the incumbent on selection with `repeats >= 2`; use `noise_estimate`.
   Measure noise before setting thresholds. Small apparent gains can be noise.
   Run a same-window incumbent control because providers drift over time.
5. Launch candidate arms in parallel using distinct run ids; use task workers
   appropriate to the host and budget. A host can pin one arm per machine when
   orchestration supports it. Keep a launched arm on its original machine and
   inputs. Reuse stage replay only when the scenario declares complete cache keys;
   a changed dependency must invalidate its stage. Cached upstream cost is zero.
6. Wait for completion via `run_status`; inspect `run_results` and original traces.
   Use `compare_paired` on identical selection tasks and repeats. Choose `majority`
   for binary votes (ties fail), or `mean` for scores, explicitly. Review fixes,
   breaks, group tables, costs and latency, including failures of the hypothesis.
   Check the predicted task flips against original traces for the stated mechanism.
7. `gate_check` enforces anchors, public instance guards, holdout isolation, budgets and
   a measured threshold. Attest `generic=true` only after reviewing the mechanism.
   `adopt-no-harm` also requires a concrete rationale. Report a passing verdict to META for
   `ledger_adopt` in the operator profile. Recommend promising runner-ups for META
   to queue with `ledger_adopt(queue=true)` and retest them against the next base
   through a new proposal. Earlier scores do not establish performance on a changed base.

## Constraints and lessons

- Diagnosis supports analysis; selection decides adoption. Confirmation (the
  backward-compatible `holdout` role) is operator-only and decides keep-or-revert
  of the already chosen adoption under the anchored rule. It never chooses
  proposals. Improvers see only the not-confirmed bit, never private task ids,
  scores, outputs or traces. META owns its wear budget (default 4 decisions).
- Final is a separate optional split opened once, with human approval, by META
  at the end. It decides nothing. Without final tasks, use a fresh-task epoch.
  Split assignments, scorer and contract stay anchored within the epoch.
- Prefer a generic mechanism over an instance patch even when scores tie. Leak
  checks cover derived examples, memories, lookup tables and other artifacts.
  Literal scans are a backstop, not a proof against encoded or semantic leaks.
- Separate bug fixes from behavior changes. A defect in implementation can merge
  after review and focused tests; a change to system behavior needs paired
  evidence. If it changes scoring or frozen support, begin a new epoch.
- Keep records minimal and sufficient: proposals, arms, outcomes, verdicts and
  rationale. Do not add reservations or elaborate approval machinery.
- Replay, caching and cheap counterfactuals multiply iteration capacity. Verify
  cache identity and cost attribution; do not count stale results as an improvement.
- Use `meta_metrics` to examine acceptance rate, prediction error and gain per
  cost. META reads the operator-only confirmation rate; the improver receives
  only each proposal's `not_confirmed` bit. Avoid optimizing proposal count alone.

## Stop rules

Set an experiment budget and a stagnation window before a generation. Stop when
the remaining budget cannot fund a smoke plus a matched comparison, when the
latency/cost ceiling is reached, or when successive generations yield no generic
gain above measured noise. Record why, leave runner-ups queued, and preserve
failed proposals. Do not lower the gate merely to force another adoption.
