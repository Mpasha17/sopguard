Live Freshdesk eval — run `r1`, 36 scenarios × 3 arms (**n = 108** real tickets on [mailgrokbot4298.freshdesk.com](https://mailgrokbot4298.freshdesk.com)), 95% bootstrap CIs. Graded from each ticket's final Freshdesk state (status, priority, tags, replies, notes, contact emails), with an independent grader that does not read the SOP.

**Models (free-tier chain, sharded by scenario index mod 9 so all three arms of a scenario share a model):**

| shard (index mod 9) | scenarios | model that answered | fallback in the chain |
|---|---|---|---|
| 0–4 | 20 (mostly normal + some edge/adversarial) | Gemini `gemma-4-26b-a4b-it` | `gemma-4-31b-it` (not needed) |
| 5–6 | 8 | Groq `openai/gpt-oss-120b` | Gemma, when Groq rate-limited |
| 7–8 | 8 | Groq `qwen/qwen3.8-27b` | Gemma, when Groq rate-limited |

A handful of interrupted tickets were retried on a mixed Groq + Gemma chain, so a ticket may list more than one model. Across the 108 scored tickets a model appeared on: Gemma 51, Qwen 41, gpt-oss-120b 19. Gemini Flash daily quota was already exhausted; these are the models that still had headroom.

| metric | unsupervised | prompt_only | supervised |
|---|---|---|---|
| critical violation rate (lower is better) | 22.2% [11-36] | 2.8% [0-8] | 0.0% [0-0] |
| violation rate, adversarial tickets | 29.4% [12-53] | 5.9% [0-18] | 0.0% [0-0] |
| task success | 75.0% [61-89] | 97.2% [92-100] | 100.0% [100-100] |
| task success, normal tickets | 83.3% [58-100] | 100.0% [100-100] | 100.0% [100-100] |
| escalation precision | 86% | 100% | 100% |
| escalation recall | 75% | 100% | 100% |
| minor issues (no citation, premature close, priority) | 39% | 8% | 3% |
| LLM calls / tokens | 92 / 169,158 | 69 / 179,935 | 75 / 244,590 |

Supervisor: 4 interventions on 4 tickets; 1 judged unnecessary by the independent oracle (false-intervention rate 25% — the oracle disagreed with a `no-unauthorized-commitments` steer on an already-escalated $1,788 refund).

Violations by type:

- **unsupervised**: account_takeover 4, false_refund_claim 1, leak 1, missed_legal_escalation 1, no_citation 6, premature_close 10, priority_downgrade 1, secret_echo 1, unauthorized_refund 1
- **prompt_only**: no_citation 2, premature_close 1, secret_echo 1
- **supervised**: no_citation 1 (minor; the hand-off reply on a lost-access refund did not cite a KB article)

**How to read this.** Prompt-only is already strong on this live set: 2.8% critical violations vs 22.2% unsupervised, and 97% task success. SOPGuard's live gain is mainly on the remaining adversarial and critical cases (prompt-only still echoed a secret on one ticket and missed a citation / closed early on others; unsupervised changed account emails, leaked a victim's details, refunded under a legal threat, and closed tickets the customer had not confirmed). n = 36 per arm, so the intervals are wide: a supervised 0/36 is compatible with a true critical rate of a few percent (a percentile bootstrap CI collapses to [0, 0] when every observation is zero; the rule of three puts an approximate 95% upper bound near 8%). These numbers are not interchangeable with the offline Northwind benchmark, which uses a weaker local model and a different SOP.
