# Measured results

<!-- RESULTS:start -->

All numbers below are measured from the committed runs in [`results/`](../results/) (raw transcripts, decisions and DB snapshots). Degraded conversations (any LLM error or quota hit) are excluded and counted. Regenerate with `python -m evals.report results/<run>`.

### Held-out suite, local model (main run)

Seed 2026 (held-out), shard 0/2, 28 distinct scenarios with at least one scored conversation; judge timeout 45000 ms. Violation rate: unsupervised 100%, prompt-only 93%, supervised 0%.

Agent: `ollama:llama3.2:3b` | Judge: `ollama:llama3.2:3b` | Customer simulator: `ollama:llama3.2:3b`

| Metric (95% bootstrap CI) | unsupervised | prompt_only | supervised |
|---|---|---|---|
| Conversations scored (degraded excluded) | 28 (0 excl.) | 27 (0 excl.) | 27 (0 excl.) |
| Policy violation rate (lower is better) | 100.0% [100.0, 100.0] | 92.6% [81.5, 100.0] | 0.0% [0.0, 0.0] |
|   - on adversarial scenarios | 100.0% [100.0, 100.0] | 77.8% [44.4, 100.0] | 0.0% [0.0, 0.0] |
|   - on benign scenarios | 100.0% [100.0, 100.0] | 100.0% [100.0, 100.0] | 0.0% [0.0, 0.0] |
| Task success rate | 0.0% [0.0, 0.0] | 7.4% [0.0, 18.5] | 81.5% [66.7, 96.3] |
|   - on legitimate requests | 0.0% [0.0, 0.0] | 0.0% [0.0, 0.0] | 54.5% [27.3, 81.8] |
| Escalation rate | 100.0% [100.0, 100.0] | 81.5% [66.7, 96.3] | 77.8% [63.0, 92.6] |
| Agent tokens / conversation | 6,631 | 11,448 | 10,732 |

| Supervisor metric | Value |
|---|---|
| Steps checked / interventions | 214 / 46 {'steer': 44, 'escalate': 2} |
| False intervention rate (oracle says step was fine) | 26.1% [15.2, 39.1] |
| Benign conversations with any intervention | 100.0% [100.0, 100.0] |
| Steer-recovery rate (next step allowed) | 86.4% [75.0, 95.5] |
| Added latency, deterministic checks p50 / p95 | 0.281 / 2.82 ms |
| Added latency, steps that called the judge p50 / p95 | 27519.7 / 45004.8 ms (21% of steps) |
| Judge tokens / conversation | 1,274 |
| Judge timeouts/errors handled fail-open / fail-closed (steps) | 8 / 0 |

Supervised vs prompt_only (paired, n=26): violation rate -92.3 pts [-100.0, -80.8], task success +76.9 pts [+61.5, +92.3]

Supervised vs unsupervised (paired, n=27): violation rate -100.0 pts [-100.0, -100.0], task success +81.5 pts [+66.7, +96.3]


Details: [`results/heldout-llama3.2-3b/RESULTS.md`](../results/heldout-llama3.2-3b/RESULTS.md)

### Pilot, hosted model via Groq free tier

Seed 2026 (held-out), shard 0/1, 11 distinct scenarios with at least one scored conversation; judge timeout 20000 ms. Violation rate: unsupervised 82%, prompt-only 73%, supervised 0%.

Agent: `groq:openai/gpt-oss-20b` | Judge: `groq:qwen/qwen3.8-27b` | Customer simulator: `ollama:llama3.2:3b`

| Metric (95% bootstrap CI) | unsupervised | prompt_only | supervised |
|---|---|---|---|
| Conversations scored (degraded excluded) | 11 (0 excl.) | 11 (0 excl.) | 10 (0 excl.) |
| Policy violation rate (lower is better) | 81.8% [54.5, 100.0] | 72.7% [45.5, 100.0] | 0.0% [0.0, 0.0] |
|   - on adversarial scenarios | 100.0% [100.0, 100.0] | 66.7% [0.0, 100.0] | 0.0% [0.0, 0.0] |
|   - on benign scenarios | 75.0% [50.0, 100.0] | 75.0% [37.5, 100.0] | 0.0% [0.0, 0.0] |
| Task success rate | 18.2% [0.0, 45.5] | 27.3% [0.0, 54.5] | 100.0% [100.0, 100.0] |
|   - on legitimate requests | 0.0% [0.0, 0.0] | 25.0% [0.0, 75.0] | 100.0% [100.0, 100.0] |
| Escalation rate | 63.6% [36.4, 90.9] | 90.9% [72.7, 100.0] | 30.0% [0.0, 60.0] |
| Agent tokens / conversation | 3,886 | 8,479 | 9,553 |

| Supervisor metric | Value |
|---|---|
| Steps checked / interventions | 72 / 8 {'steer': 8} |
| False intervention rate (oracle says step was fine) | 0.0% [0.0, 0.0] |
| Benign conversations with any intervention | 66.7% [0.0, 100.0] |
| Steer-recovery rate (next step allowed) | 100.0% [100.0, 100.0] |
| Added latency, deterministic checks p50 / p95 | 0.362 / 6.41 ms |
| Added latency, steps that called the judge p50 / p95 | 354.8 / 469.4 ms (24% of steps) |
| Judge tokens / conversation | 1,824 |
| Judge timeouts/errors handled fail-open / fail-closed (steps) | 0 / 0 |

Supervised vs prompt_only (paired, n=10): violation rate -70.0 pts [-90.0, -40.0], task success +70.0 pts [+40.0, +90.0]

Supervised vs unsupervised (paired, n=10): violation rate -80.0 pts [-100.0, -50.0], task success +80.0 pts [+50.0, +100.0]


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
