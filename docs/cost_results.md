# Second Look: measured AI costs

Run costs-20261017-204105 (2026-10-18T00:47:59+00:00), models: fast = gpt-6-luna, smart = gpt-6-luna. App response cache off and Portkey gateway cache bypassed; gateway cache hits during the run: 0.

Total spent: $0.0343 on 87 calls (27 analyses, 5 pause checks).

| Unit | n | Mean input tokens | Mean output tokens | Mean $ | Mean $ with caching | p95 latency (s) |
|---|---:|---:|---:|---:|---:|---:|
| analyst | 27 | 2,085 | 369 | $0.000393 | $0.000363 | 5.04 |
| explainer | 28 | 2,601 | 518 | $0.000519 | $0.000472 | 6.38 |
| pause | 5 | 753 | 435 | $0.000293 | $0.000263 | 4.92 |
| verifier | 27 | 2,334 | 105 | $0.000286 | $0.000258 | 3.23 |
| **Full analysis** | 27 | 7,116 | 1,010 | $0.001217 | $0.001111 | 17.12 |
| **Pause check** | 5 | 753 | 435 | $0.000293 | $0.000263 | 6.01 |

## Monthly projection (2 analyses per user per month)

| Users | Analyses / month | Without caching | With caching |
|---:|---:|---:|---:|
| 1,000 | 2,000 | $2.43 | $2.22 |
| 10,000 | 20,000 | $24.34 | $22.21 |
| 50,000 | 100,000 | $121.68 | $111.06 |

With caching: each agent's system prompt (about analyst 334, explainer 519, pause 334, verifier 308 tokens) billed at 10% of the input price; Facts JSON and output billed in full. OpenAI only caches prompt prefixes of 1,024 tokens or more, so this saving needs the system prompts to grow past that size or be combined with a longer shared prefix.

Analysis sources: {'ai': 31, 'ai_rewrite': 1}. Latency for a full analysis is end-to-end wall time.

## Reasoning effort: default vs low

gpt-6-luna reasons before it answers. At the default effort the hidden reasoning sometimes used the whole
`max_completion_tokens` allowance and returned empty text, which forced retries and template fallbacks.
`REASONING_EFFORT=low` (now the default) removed that.

| Setting | Run | Mean $ per analysis | Mean output tokens | p95 analysis latency (s) | Template fallbacks | 50,000 users / month |
|---|---|---:|---:|---:|---:|---:|
| Default effort | costs-20261017-200015 | $0.001572 | 1,696 | 27.6 | 3 of 32 | $157.16 |
| Low effort (current) | costs-20261017-204105 | $0.001217 | 1,010 | 17.1 | 0 of 32 | $121.68 |

Change with low effort: -23% cost per analysis,
-38% p95 latency.

## Portkey check

Portkey's log export API is not available to the course key ("not enough permissions"), so compare by hand in the
Portkey dashboard: filter Logs by trace id **costs-20261017-204105-00-analyst** (sent 2026-10-18T00:41:11 UTC).
Our ledger recorded 1,802 input and 338 output tokens on gpt-6-luna,
costing **$0.00034920** at $0.10 / $0.50 per 1M tokens.

Measurement note: an earlier run at default effort without bypassing the Portkey gateway cache was discarded; its
repeats were cache hits (about 0.15 s each), not real model calls.
