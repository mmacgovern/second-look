# Second Look: quality vs cost

3 personas x 3 fluency levels per routing (costs-20261018-072604). Response cache off, gateway cache bypassed.

| Routing | Models | First-draft verifier pass rate | Rewrites | Template fallbacks | Mean $ per analysis | Total $ |
|---|---|---:|---:|---:|---:|---:|
| Normal routing | fast gpt-6-luna, smart gpt-6-luna | 78% | 2 | 0 | $0.001545 | $0.0139 |
| MODEL_SMART for every agent | all gpt-6-sol | 11% | 8 | 2 | $0.037269 | $0.3354 |
