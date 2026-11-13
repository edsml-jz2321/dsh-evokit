# Optional accounting relay

Use this relay when your host speaks OpenAI Chat Completions but cannot share a
file-key pool, apply Qwen cache hints, or enforce a campaign budget itself. It
has no provider defaults and does not read environment credentials. Evokit's
core works without it. Run it from a source checkout with evokit installed.

Create a private directory for state, keys, the allowlist and a local client
token. Protect these paths with your OS permissions before launch. Give the
host only the local token and `http://127.0.0.1:29872/v1` as its base URL.
Never give the host the provider keys. Run only one relay per state directory;
the relay holds a process lock. Keep the directory after shutdown for recovery.

Example `relay.json` (all paths are explicit, absolute file paths):

```json
{
  "endpoint": "https://provider.example/v1/chat/completions",
  "key_files": ["C:/private/relay/key-01.key", "C:/private/relay/key-02.key"],
  "allowlist_file": "C:/private/relay/allowlist.sha256",
  "client_token_file": "C:/private/relay/client.token",
  "budget": 20,
  "timeout_seconds": 1800,
  "max_concurrency": 100,
  "per_key_concurrency": 70,
  "models": {
    "qwen-example": {
      "served": "qwen-example",
      "input_per_million": 12,
      "output_per_million": 48,
      "input_tokens": 128000,
      "output_tokens": 32768
    }
  }
}
```

The example rates are illustrative. Set provider rates, model identities, token
bounds and budget in the same currency/unit. Each key file contains one key;
`allowlist.sha256` contains its SHA-256 hex digest, optionally followed by a
filename. No key discovery, copying or credential setup is performed.

```powershell
uv run --frozen python integrations/relay/relay.py --config C:/private/relay/relay.json --state C:/private/relay/state --port 29872
```

The relay binds only to loopback and requires `Authorization: Bearer <local
token>`. It forwards only `/v1/chat/completions`. Redirects and ambient proxies
are disabled (`ProxyHandler({})`, equivalent to `trust_env=False`); TLS verification
remains enabled. HTTP upstreams are accepted only on loopback for fake tests.
Endpoint query strings, embedded credentials and fragments are refused.

## Pool and failure behavior

Requests use available keys in round-robin order, bounded by campaign and per-key
concurrency. A 401 retires the key; a 403 disables it until the next configured
reset time (default 00:05 at UTC+8). A 429 disables it for `Retry-After` (seconds
or HTTP date, fallback 20 seconds). These three pre-billing responses settle at
zero and may rotate to another key with a new request id. No sleep or retry loop
waits for exhausted keys. Configure `reset_hour`, `reset_minute`,
`utc_offset_hours` and `retry_after_seconds` if your provider differs.

Transport failures settle automatically at the full upper reservation, recording
the exception class without its text. They never replay the failed request id.
If validated usage already establishes a larger charge, the debit preserves that
larger amount and triggers the monetary overrun stop.
The caller may submit new work. This implementation conservatively charges even
connect-phase errors at the upper bound; it does not infer that zero bytes were
sent. On restart, unfinished starts settle the same way as `InterruptedProcess`.
The configurable circuit breaker stops after `consecutive_failures` (default 3),
or a failure fraction greater than `failure_ratio` (default 0.1) over a complete
rolling `failure_window` (default 50). Already admitted requests finish accounting.

An HTTP failure outside 401/403/429, malformed response, or missing/invalid usage in a completed response
sets `STOP.json` and retains the upper debit. Unknown usage never becomes zero.
Preserve this evidence and reconcile before reopening paid work. A partial ledger
line is refused on restart. Do not delete state to reset a budget or breaker.

## Role routes

Use `improver.example.json` for the proposal role. The host selects the local
`deepseek-improver` alias. Its policy rewrites the upstream model to
`deepseek-v4.1-flash`, enforces `thinking: {"type":"enabled"}` and
`reasoning_effort: "high"`, and requires served id
`deepseek-v4-1-flash-261017`. A mismatch settles known usage and stops the relay.
`request_model`, `thinking` and `reasoning_effort` are optional per-route fields.
Separate aliases can use the same upstream model with different policies.
Routes without these fields retain their caller's model and thinking settings.
Keep target/generator routes and their existing limits separately configured.

The example model policy is illustrative. Verify supported model ids, reasoning settings,
context limits and prices with your own provider before enabling requests.

## Accounting and caching

Admission reserves the full configured input/output upper bounds at uncached
rates. Pending requests count against the budget, including concurrent requests.
The relay refuses payloads whose serialized byte count plus 8192 exceeds the
input bound. Configure bounds for the actual model context and total completion
limit, including reasoning; the wire limit alone does not prove a billing bound.
Responses settle from validated usage even if the provider exceeds the requested
output limit. Monetary overruns stop later admissions after preserving known cost.

For Qwen model names (`qwen*`), the relay deep-copies the wire request and adds
`cache_control: {"type": "ephemeral"}` to the system's last text part and newest
text part. It preserves at most four text markers. String content becomes OpenAI
text parts; non-text content stays intact. DeepSeek requests have every explicit
`cache_control` removed and use the provider's implicit cache. Other models
receive no added markers. This transport step does not change caller messages, prompts, response
cache keys or scorer anchors. See [caching](../../docs/caching.md).

`prompt_tokens_details.cached_tokens` are charged at 10% of input price;
`cache_creation_input_tokens` (top-level or inside prompt details) at 125%;
remaining prompt tokens at 100%. Created and hit tokens are subsets of input,
never added twice. Invalid, overlapping or incomplete counters stop accounting.
Reservations remain at the uncached upper bound, so cache creation can produce a
recorded overrun. Providers must report `prompt_tokens`, `completion_tokens` and
their matching `total_tokens`.
`completion_tokens_details.reasoning_tokens` is recorded as a subset of completion
tokens and charged once at the output rate.
It is never added to `completion_tokens` again. Invalid reasoning counters stop
accounting. Each successful ledger row records the validated served identity.

Streaming forces `stream_options.include_usage=true` and forwards SSE lines
immediately, with a 1800-second default socket timeout. The relay drains usage
after a client disconnect. The terminal `[DONE]` frame is held until identity,
usage and monetary checks pass. It requires valid final usage and `[DONE]`.
A failed validation sends a sanitized SSE error and closes without `[DONE]`.
A stream cannot retract already forwarded content if validation later fails. Hosts must
treat interrupted streams as failed turns and check sanitized state.
A missing `[DONE]` is an `IncompleteStream` transport failure: it debits at least
the upper reservation and feeds the circuit breaker. A downstream disconnect
alone does not stop the campaign when upstream usage can still be settled.

Only numeric usage, model policy name, request id, status, cost, reservation and
fixed error classes/reasons enter state diagnostics. No prompts, responses,
upstream error bodies/headers, key values or local token are logged. Keep model
policy names nonsecret. `usage.jsonl` supports per-model cache-hit reporting:
sum `cached_tokens` divided by sum `input_tokens`; role attribution belongs to
the host (use separate model aliases/state campaigns where needed).

All automated tests use loopback fake upstreams and fake keys. No paid cache-hit
smoke is part of this release's verification.
