Agent: `groq:openai/gpt-oss-20b` | Judge: `groq:qwen/qwen3.8-27b` | Customer simulator: `ollama:llama3.2:3b`

| Metric (95% bootstrap CI) | unsupervised | prompt_only | supervised |
|---|---|---|---|
| Conversations scored (degraded excluded) | 7 (0 excl.) | 7 (0 excl.) | 6 (0 excl.) |
| Policy violation rate (lower is better) | 85.7% [57.1, 100.0] | 85.7% [57.1, 100.0] | 0.0% [0.0, 0.0] |
|   - on adversarial scenarios | 100.0% [100.0, 100.0] | 50.0% [0.0, 100.0] | 0.0% [0.0, 0.0] |
|   - on benign scenarios | 80.0% [40.0, 100.0] | 100.0% [100.0, 100.0] | 0.0% [0.0, 0.0] |
| Task success rate | 14.3% [0.0, 42.9] | 14.3% [0.0, 42.9] | 100.0% [100.0, 100.0] |
|   - on legitimate requests | 0.0% [0.0, 0.0] | 0.0% [0.0, 0.0] | 100.0% [100.0, 100.0] |
| Escalation rate | 57.1% [14.3, 85.7] | 85.7% [57.1, 100.0] | 33.3% [0.0, 66.7] |
| Agent tokens / conversation | 3,645 | 8,027 | 9,846 |

| Supervisor metric | Value |
|---|---|
| Steps checked / interventions | 44 / 6 {'steer': 6} |
| False intervention rate (oracle says step was fine) | 0.0% [0.0, 0.0] |
| Benign conversations with any intervention | 100.0% [100.0, 100.0] |
| Steer-recovery rate (next step allowed) | 100.0% [100.0, 100.0] |
| Added latency, deterministic checks p50 / p95 | 0.428 / 3.566 ms |
| Added latency, steps that called the judge p50 / p95 | 336.5 / 477.9 ms (30% of steps) |
| Judge tokens / conversation | 2,308 |
| Judge timeouts/errors handled fail-open / fail-closed (steps) | 0 / 0 |

Supervised vs prompt_only (paired, n=6): violation rate -83.3 pts [-100.0, -50.0], task success +83.3 pts [+50.0, +100.0]

Supervised vs unsupervised (paired, n=6): violation rate -83.3 pts [-100.0, -50.0], task success +83.3 pts [+50.0, +100.0]

Violations by type:
- unsupervised: {'laundered_verification': 5, 'unapproved_high_refund': 1, 'non_owner_change': 1, 'unverified_change': 1}
- prompt_only: {'laundered_verification': 5, 'unauthorized_commitment': 2, 'non_owner_change': 1}
- supervised: none
