# Measured results

<!-- RESULTS:start -->

All numbers below are measured from the committed runs in [`results/`](../results/) (raw transcripts, decisions and DB snapshots). Degraded conversations (any LLM error or quota hit) are excluded and counted. Regenerate with `python -m evals.report results/<run>`.

### Held-out suite, local model (main run)

Seed 2026 (held-out), shard 0/2, 15 distinct scenarios with at least one scored conversation; judge timeout 45000 ms. Violation rate: unsupervised 100%, prompt-only 92%, supervised 0%.

Agent: `ollama:llama3.2:3b` | Judge: `ollama:llama3.2:3b` | Customer simulator: `ollama:llama3.2:3b`

| Metric (95% bootstrap CI) | unsupervised | prompt_only | supervised |
|---|---|---|---|
| Conversations scored (degraded excluded) | 15 (0 excl.) | 13 (0 excl.) | 14 (0 excl.) |
| Policy violation rate (lower is better) | 100.0% [100.0, 100.0] | 92.3% [76.9, 100.0] | 0.0% [0.0, 0.0] |
|   - on adversarial scenarios | 100.0% [100.0, 100.0] | 75.0% [25.0, 100.0] | 0.0% [0.0, 0.0] |
|   - on benign scenarios | 100.0% [100.0, 100.0] | 100.0% [100.0, 100.0] | 0.0% [0.0, 0.0] |
| Task success rate | 0.0% [0.0, 0.0] | 7.7% [0.0, 23.1] | 85.7% [64.3, 100.0] |
|   - on legitimate requests | 0.0% [0.0, 0.0] | 0.0% [0.0, 0.0] | 66.7% [33.3, 100.0] |
| Escalation rate | 100.0% [100.0, 100.0] | 92.3% [76.9, 100.0] | 85.7% [64.3, 100.0] |
| Agent tokens / conversation | 6,838 | 9,621 | 11,184 |

| Supervisor metric | Value |
|---|---|
| Steps checked / interventions | 116 / 26 {'steer': 25, 'escalate': 1} |
| False intervention rate (oracle says step was fine) | 23.1% [7.7, 38.5] |
| Benign conversations with any intervention | 100.0% [100.0, 100.0] |
| Steer-recovery rate (next step allowed) | 84.0% [68.0, 96.0] |
| Added latency, deterministic checks p50 / p95 | 0.26 / 2.941 ms |
| Added latency, steps that called the judge p50 / p95 | 26483.1 / 45005.5 ms (23% of steps) |
| Judge tokens / conversation | 1,334 |
| Judge timeouts/errors handled fail-open / fail-closed (steps) | 7 / 0 |

Supervised vs prompt_only (paired, n=13): violation rate -92.3 pts [-100.0, -76.9], task success +84.6 pts [+61.5, +100.0]

Supervised vs unsupervised (paired, n=14): violation rate -100.0 pts [-100.0, -100.0], task success +85.7 pts [+64.3, +100.0]


Details: [`results/heldout-llama3.2-3b/RESULTS.md`](../results/heldout-llama3.2-3b/RESULTS.md)

### Pilot, hosted model via Groq free tier

Seed 2026 (held-out), shard 0/1, 3 distinct scenarios with at least one scored conversation; judge timeout 20000 ms. Violation rate: unsupervised 100%, prompt-only 100%, supervised 0%.

Agent: `groq:openai/gpt-oss-20b` | Judge: `groq:qwen/qwen3.8-27b` | Customer simulator: `groq:openai/gpt-oss-120b`

| Metric (95% bootstrap CI) | unsupervised | prompt_only | supervised |
|---|---|---|---|
| Conversations scored (degraded excluded) | 2 (0 excl.) | 2 (0 excl.) | 2 (0 excl.) |
| Policy violation rate (lower is better) | 100.0% [100.0, 100.0] | 100.0% [100.0, 100.0] | 0.0% [0.0, 0.0] |
|   - on adversarial scenarios | nan% [nan, nan] | nan% [nan, nan] | nan% [nan, nan] |
|   - on benign scenarios | 100.0% [100.0, 100.0] | 100.0% [100.0, 100.0] | 0.0% [0.0, 0.0] |
| Task success rate | 0.0% [0.0, 0.0] | 0.0% [0.0, 0.0] | 100.0% [100.0, 100.0] |
|   - on legitimate requests | 0.0% [0.0, 0.0] | 0.0% [0.0, 0.0] | 100.0% [100.0, 100.0] |
| Escalation rate | 0.0% [0.0, 0.0] | 50.0% [0.0, 100.0] | 0.0% [0.0, 0.0] |
| Agent tokens / conversation | 2,796 | 4,612 | 7,492 |

| Supervisor metric | Value |
|---|---|
| Steps checked / interventions | 12 / 2 {'steer': 2} |
| False intervention rate (oracle says step was fine) | 0.0% [0.0, 0.0] |
| Benign conversations with any intervention | 100.0% [100.0, 100.0] |
| Steer-recovery rate (next step allowed) | 100.0% [100.0, 100.0] |
| Added latency, deterministic checks p50 / p95 | 0.345 / 3.815 ms |
| Added latency, steps that called the judge p50 / p95 | 328.0 / 360.6 ms (17% of steps) |
| Judge tokens / conversation | 1,062 |
| Judge timeouts/errors handled fail-open / fail-closed (steps) | 0 / 0 |

Supervised vs prompt_only (paired, n=1): violation rate -100.0 pts [-100.0, -100.0], task success +100.0 pts [+100.0, +100.0]

Supervised vs unsupervised (paired, n=1): violation rate -100.0 pts [-100.0, -100.0], task success +100.0 pts [+100.0, +100.0]


Details: [`results/pilot-groq-gpt-oss-20b/RESULTS.md`](../results/pilot-groq-gpt-oss-20b/RESULTS.md)

### Pilot, Gemini free tier (cut short by daily quota)

Seed 2026 (held-out), shard 0/1, 6 distinct scenarios with at least one scored conversation; judge timeout 8000 ms. Violation rate: unsupervised 67%, prompt-only 0%, supervised 0%.

Agent: `gemini:gemini-3.5-flash-lite` | Judge: `gemini:gemini-3.5-flash-lite` | Customer simulator: `ollama:llama3.2:3b`

| Metric (95% bootstrap CI) | unsupervised | prompt_only | supervised |
|---|---|---|---|
| Conversations scored (degraded excluded) | 6 (4 excl.) | 5 (5 excl.) | 5 (5 excl.) |
| Policy violation rate (lower is better) | 66.7% [33.3, 100.0] | 0.0% [0.0, 0.0] | 0.0% [0.0, 0.0] |
|   - on adversarial scenarios | 0.0% [0.0, 0.0] | nan% [nan, nan] | nan% [nan, nan] |
|   - on benign scenarios | 80.0% [40.0, 100.0] | 0.0% [0.0, 0.0] | 0.0% [0.0, 0.0] |
| Task success rate | 33.3% [0.0, 66.7] | 100.0% [100.0, 100.0] | 100.0% [100.0, 100.0] |
|   - on legitimate requests | 0.0% [0.0, 0.0] | 100.0% [100.0, 100.0] | 100.0% [100.0, 100.0] |
| Escalation rate | 66.7% [33.3, 100.0] | 80.0% [40.0, 100.0] | 80.0% [40.0, 100.0] |
| Agent tokens / conversation | 4,054 | 7,139 | 7,143 |

| Supervisor metric | Value |
|---|---|
| Steps checked / interventions | 28 / 0 {} |
| False intervention rate (oracle says step was fine) | nan% [nan, nan] |
| Benign conversations with any intervention | 0.0% [0.0, 0.0] |
| Steer-recovery rate (next step allowed) | nan% [nan, nan] |
| Added latency, deterministic checks p50 / p95 | 0.157 / 0.265 ms |
| Added latency, steps that called the judge p50 / p95 | 8003.6 / 8006.6 ms (36% of steps) |
| Judge tokens / conversation | 212 |
| Judge timeouts/errors handled fail-open / fail-closed (steps) | 9 / 0 |

Supervised vs prompt_only (paired, n=5): violation rate +0.0 pts [+0.0, +0.0], task success +0.0 pts [+0.0, +0.0]

Supervised vs unsupervised (paired, n=5): violation rate -80.0 pts [-100.0, -40.0], task success +80.0 pts [+40.0, +100.0]


Details: [`results/pilot-gemini-3.5-flash-lite/RESULTS.md`](../results/pilot-gemini-3.5-flash-lite/RESULTS.md)

<!-- RESULTS:end -->
