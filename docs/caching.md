# Reuse stages and provider prompt prefixes

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
