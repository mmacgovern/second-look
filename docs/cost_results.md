# Second Look: measured AI costs

Run costs-20261018-103313 (2026-10-18T14:45:47+00:00), models: fast = gpt-6-luna, smart = gpt-6-luna. Main pass: app response cache off and Portkey gateway cache bypassed; gateway cache hits: 0.

Main pass: $0.0521 on 118 calls (27 analyses, 5 pause checks).

| Unit | n | Mean input tokens | Mean cached input tokens | Mean output tokens | Mean $ | p95 latency (s) |
|---|---:|---:|---:|---:|---:|---:|
| analyst | 29 | 2,334 | 2,022 | 384 | $0.000243 | 5.52 |
| explainer | 37 | 3,062 | 0 | 892 | $0.000752 | 9.62 |
| pause | 5 | 882 | 0 | 371 | $0.000274 | 3.89 |
| sandbox | 9 | 882 | 0 | 329 | $0.000253 | 5.09 |
| shifts | 9 | 765 | 0 | 350 | $0.000252 | 4.34 |
| verifier | 29 | 2,652 | 163 | 280 | $0.000390 | 4.85 |
| **Full analysis** | 27 | 9,551 | 2,347 | 1,935 | $0.001711 | 29.39 |
| **Pause check** | 5 | 882 | 0 | 371 | $0.000274 | 4.49 |
| **Possible Shifts** | 9 | 765 | 0 | 350 | $0.000252 | 4.86 |
| **Try a Shift** | 9 | 882 | 0 | 329 | $0.000253 | 5.61 |

A full visit (analysis + Possible Shifts + Try a Shift) costs $0.002216.

## Monthly cost (2 full visits per user per month)

With the app's response cache, the same 27 analyses cost $0.0154 ($0.000571 each) because repeats and steps shared across fluency levels are served from the cache. That applies when people re-run a portfolio or switch fluency; a brand-new portfolio always costs the full amount. Possible Shifts and Try a Shift are counted at full price.

| | Per user per month | 100 users | 10,000 users |
|---|---:|---:|---:|
| Without response cache | $0.0044 | $0.44 | $44.31 |
| With response cache | $0.0022 | $0.22 | $21.51 |

Analysis sources (main pass): {'ai': 40, 'ai_rewrite': 10}. Latency for a full analysis is end-to-end wall time.
