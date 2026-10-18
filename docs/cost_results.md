# Second Look: measured AI costs

Run costs-20261017-214350 (2026-10-18T01:55:08+00:00), models: fast = gpt-6-luna, smart = gpt-6-luna. Main pass: app response cache off and Portkey gateway cache bypassed; gateway cache hits: 0.

Main pass: $0.0451 on 95 calls (27 analyses, 5 pause checks).

| Unit | n | Mean input tokens | Mean cached input tokens | Mean output tokens | Mean $ | p95 latency (s) |
|---|---:|---:|---:|---:|---:|---:|
| analyst | 27 | 2,163 | 2,160 | 432 | $0.000238 | 4.95 |
| explainer | 37 | 2,826 | 0 | 889 | $0.000727 | 8.06 |
| pause | 5 | 882 | 0 | 405 | $0.000291 | 4.52 |
| verifier | 26 | 2,504 | 84 | 310 | $0.000398 | 4.32 |
| **Full analysis** | 27 | 8,446 | 2,241 | 1,948 | $0.001617 | 26.05 |
| **Pause check** | 5 | 882 | 0 | 405 | $0.000291 | 5.21 |

## Monthly cost (2 analyses per user per month)

With the app's response cache, the same 27 analyses cost $0.0152 ($0.000563 each) because repeats and steps shared across fluency levels are served from the cache. That applies when people re-run a portfolio or switch fluency; a brand-new portfolio always costs the full amount.

| | Per user per month | 100 users | 10,000 users |
|---|---:|---:|---:|
| Without response cache | $0.0032 | $0.32 | $32.34 |
| With response cache | $0.0011 | $0.11 | $11.26 |

Analysis sources (main pass): {'ai': 22, 'ai_rewrite': 8, 'template': 2}. Latency for a full analysis is end-to-end wall time.
