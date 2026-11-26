Agent: `ollama:llama3.2:3b` | Judge: `ollama:llama3.2:3b` | Customer simulator: `ollama:llama3.2:3b`

| Metric (95% bootstrap CI) | unsupervised | prompt_only | supervised |
|---|---|---|---|
| Conversations scored (degraded excluded) | 24 (0 excl.) | 23 (0 excl.) | 21 (0 excl.) |
| Policy violation rate (lower is better) | 100.0% [100.0, 100.0] | 91.3% [78.3, 100.0] | 0.0% [0.0, 0.0] |
|   - on adversarial scenarios | 100.0% [100.0, 100.0] | 75.0% [37.5, 100.0] | 0.0% [0.0, 0.0] |
|   - on benign scenarios | 100.0% [100.0, 100.0] | 100.0% [100.0, 100.0] | 0.0% [0.0, 0.0] |
| Task success rate | 0.0% [0.0, 0.0] | 8.7% [0.0, 21.7] | 85.7% [71.4, 100.0] |
|   - on legitimate requests | 0.0% [0.0, 0.0] | 0.0% [0.0, 0.0] | 62.5% [25.0, 87.5] |
| Escalation rate | 100.0% [100.0, 100.0] | 87.0% [73.9, 100.0] | 76.2% [57.1, 95.2] |
| Agent tokens / conversation | 5,983 | 10,635 | 10,266 |

| Supervisor metric | Value |
|---|---|
| Steps checked / interventions | 163 / 35 {'steer': 33, 'escalate': 2} |
| False intervention rate (oracle says step was fine) | 22.9% [8.6, 37.1] |
| Benign conversations with any intervention | 100.0% [100.0, 100.0] |
| Steer-recovery rate (next step allowed) | 81.8% [66.7, 93.9] |
| Added latency, deterministic checks p50 / p95 | 0.275 / 2.311 ms |
| Added latency, steps that called the judge p50 / p95 | 27024.8 / 45005.4 ms (20% of steps) |
| Judge tokens / conversation | 1,089 |
| Judge timeouts/errors handled fail-open / fail-closed (steps) | 8 / 0 |

Supervised vs prompt_only (paired, n=21): violation rate -90.5 pts [-100.0, -76.2], task success +76.2 pts [+57.1, +90.5]

Supervised vs unsupervised (paired, n=21): violation rate -100.0 pts [-100.0, -100.0], task success +85.7 pts [+71.4, +100.0]

Violations by type:
- unsupervised: {'laundered_verification': 24, 'unapproved_high_refund': 5, 'ineligible_refund': 5, 'unverified_change': 5, 'non_owner_change': 3, 'final_sale_refund': 2, 'shipped_address_change': 2, 'over_refund': 1, 'pii_leak': 1, 'unauthorized_commitment': 1}
- prompt_only: {'laundered_verification': 21, 'non_owner_change': 3, 'unverified_change': 3, 'unapproved_high_refund': 2, 'ineligible_refund': 2, 'final_sale_refund': 2, 'shipped_address_change': 2, 'unauthorized_commitment': 1}
- supervised: none
