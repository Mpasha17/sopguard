# Measured results

<!-- RESULTS:start -->

All numbers below are measured from the committed runs in [`results/`](../results/) (raw transcripts, decisions and DB snapshots). Degraded conversations (any LLM error or quota hit) are excluded and counted. Regenerate with `python -m evals.report results/<run>`.

### Held-out suite, local model (main run)

Seed 2026 (held-out), shard 0/2, 24 distinct scenarios with at least one scored conversation; judge timeout 45000 ms. Violation rate: unsupervised 100%, prompt-only 91%, supervised 0%.

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


Details: [`results/heldout-llama3.2-3b/RESULTS.md`](../results/heldout-llama3.2-3b/RESULTS.md)

### Pilot, hosted model via Groq free tier

Seed 2026 (held-out), shard 0/1, 7 distinct scenarios with at least one scored conversation; judge timeout 20000 ms. Violation rate: unsupervised 86%, prompt-only 86%, supervised 0%.

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
