# Reuse stages and provider prompt prefixes

## Validated responses and host recovery (v0.1.6)

`evokit.responses.ResponseCache` owns reusable structured responses. Callers
supply a request fingerprint, accounted dispatch callback, decoder and current
domain validator. Reuse requires `finish_reason="stop"` and successful current
validation, including for legacy entries. Invalid or incomplete responses go to
`rejected/`; complete replies without an owning validator go to `unvalidated/`.
They never become success-cache hits. Dispatch must settle usage before returning
its sanitized envelope. Keys and authorization headers must never enter it.

`evokit.runtime.publish_with_retry` retries publication callbacks after OS errors;
`watch_updates` keeps periodic publication alive. Callbacks publish already
computed state and must not execute an evaluation. Atomic file replacement
preserves the old value while a Windows deny-delete handle is open.

`RecoveryAccounting` restores prior settled usage once. New signed leases cover
only incremental requests. Supply prior full wall time when recorded; otherwise
restored latency is explicitly a model-time lower bound. A scorer that already
included seeded history uses `usage_already_included=True`. `TransportCircuit`
archives an explained operator reset only after idle broker state, no pending
accounting and fresh successful direct probes. It never cancels reservations or
changes thresholds. `Relay.reset_circuit` applies the same prerequisites to relay
ledger state. Neither operation is an improver MCP tool.

## Computation and prompt-prefix reuse

Evokit has two independent forms of reuse. A scenario's `StageCache` replays
JSON results for a stage whose full dependencies are unchanged. Its key must
include code, configuration and task inputs. Keep public and private measurement
cache roots separate; cache only valid reusable computation. A fresh stage-2
sample can share a frozen stage-1 result without reusing its own model response.

Provider prompt caching reuses input prefixes at the provider. It still makes
a model request and needs normal admission/accounting. The optional
[relay](../integrations/relay/README.md) applies explicit Qwen hints at the wire
boundary on a deep copy, targeting system and newest text with at most four
markers. DeepSeek receives no added hints. Scoring prompts, caller data and
response-cache identities remain unchanged.

Price cache creation at 125% of the input rate, hits at 10%, and remaining input
at 100%. Both counters are subsets of prompt tokens. Reserve at uncached upper
bounds and settle reported usage; a missing usage report never implies zero.
Report hit/input tokens per model or host role, including zero/unknown states.
Fake-upstream tests verify hint shape and arithmetic. A real repeated-prefix
cache-hit measurement needs separately authorized provider execution and is not
claimed by the keyless v0.1.3 release.
