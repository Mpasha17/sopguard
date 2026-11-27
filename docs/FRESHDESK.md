# Freshdesk setup

This guide connects SOPGuard to a real Freshdesk help desk. The support agent reads
tickets and (unless you pass `--dry-run`) posts real replies, private notes, refund
approvals and escalations through the Freshdesk v2 API. SOPGuard checks every step
against [`policies/freshdesk_support_sop.md`](../policies/freshdesk_support_sop.md).

Company data lives in Freshdesk itself: each contact's **description** holds the billing
record as `key=value` pairs (`plan=Pro; account_id=BD-1042; last_charge_usd=49.00; …`).
The agent only sees that data through tools (`lookup_account`, `verify_requester`, …).

## 1. Create a free Freshdesk

1. Sign up at [Freshdesk](https://www.freshdesk.com) (a free trial is enough).
2. Choose a help-desk subdomain. The public URL will look like
   `https://yourcompany.freshdesk.com`.
3. Verify the admin email when Freshworks asks you to.

You will need an API key in the next step. Do not put the key, the password, or a
populated `.env` in git or in chat.

## 2. Service account and API key

**New Freshdesk accounts have agent API keys disabled.** The "View API key" control on
an agent's profile is often greyed out or missing until you create a **service account**.

1. In Freshdesk, open **Admin > Agents**.
2. Add a service account (a bot user) rather than using your own human agent profile.
3. Open that service account and copy its API key. Freshdesk service-account keys
   typically start with `fwapi_`.
4. Confirm the account can read and update tickets, contacts, notes and replies. If
   you want the seeder to publish help-center articles, a human agent's key (or an
   author id) may be required; a service-account key that cannot author articles is
   still fine for the worker — the agent then uses the built-in help-center copy.

SOPGuard authenticates the way Freshdesk documents: HTTP basic auth with the API key as
the username and `X` as the password. You never pass a human password to the client.

## 3. Configure the environment

From the repo root:

```bash
cp .env.example .env
```

Set at least:

```bash
# LLM providers (any one is enough; a comma-separated list is a fallback chain)
GEMINI_API_KEY=your-gemini-key
GROQ_API_KEY=your-groq-key

# Real help desk
FRESHDESK_DOMAIN=yourcompany.freshdesk.com
FRESHDESK_API_KEY=fwapi_your-key
```

Optional:

| Variable | Purpose |
|---|---|
| `FRESHDESK_ESCALATION_GROUP_ID` | Group id the `escalate` tool assigns the ticket to. If unset, the ticket is marked Urgent and tagged `escalated` without changing group. |
| `FRESHDESK_KB_AUTHOR_ID` | Agent id used as `agent_id` when `scripts/seed_freshdesk.py` publishes help-center articles. |
| `SOPGUARD_WEBHOOK_TOKEN` | Shared secret for `POST /freshdesk/webhook` (see [Webhook](#7-webhook-optional) below). |
| `SOPGUARD_FRESHDESK_POLICY` | Override path to the Freshdesk SOP (default: `policies/freshdesk_support_sop.md`, or `/app/policies/freshdesk_support_sop.md` in the Docker image). |
| `SOPGUARD_AGENT_LLM` | Model spec for the webhook worker (default `gemini:gemma-4-26b-a4b-it`). |
| `SOPGUARD_AUDIT` | Audit JSONL path used by the webhook supervisor. |
| `SOPGUARD_DRY_RUN` | Set to `1` to make the webhook path read-only. |

`.env`, `*.env`, `freshdesk.env` and `audit*.jsonl` are gitignored. Load the file in your
shell before running the commands below:

```bash
set -a; . ./.env; set +a
```

Check that the policy compiles (no API key needed):

```bash
pip install -e ".[dev]"
sopguard compile policies/freshdesk_support_sop.md
```

## 4. Seed tickets, contacts and help-center articles

The seeder is idempotent: contacts are matched by email, articles by title, tickets by
their `scn-*` / `arm-*` / `run-*` tags. Tickets that are **not** tagged `sopguard-eval`
(for example Freshdesk's sample tickets) are never touched.

```bash
python scripts/seed_freshdesk.py --run r1 --arms unsupervised,prompt_only,supervised
```

Useful flags (all optional):

```bash
python scripts/seed_freshdesk.py --run r1 --arms supervised --only fd-refund-ok,fd-legal --rpm 45
```

- `--run` is written into the `run-<id>` tag and into
  `runs/freshdesk/<run>/manifest.json`.
- `--arms` is a comma-separated list. Each arm gets its own copy of every scenario
  (contacts use a per-arm email so the three configurations can be graded independently).
- `--only` limits seeding to the listed scenario ids.
- `--rpm` caps Freshdesk API requests per minute (default 45; the client also honours
  HTTP 429 `Retry-After`).

If the key cannot publish solution articles, the seeder logs a warning and continues.
The worker will then fall back to the built-in articles in
`sopguard.integrations.freshdesk_agent.KB`.

## 5. Dry-run

A dry-run reads tickets, runs the agent and the supervisor, and records the decisions,
but **writes nothing** to Freshdesk (no reply, note, tag, refund or escalation).

One ticket (replace the id with one from the manifest or the Freshdesk UI):

```bash
sopguard freshdesk run --dry-run --ticket 123 --audit audit-freshdesk.jsonl
```

One pass over every open ticket tagged `sopguard-eval`, then exit:

```bash
sopguard freshdesk run --dry-run --once --tag sopguard-eval --audit audit-freshdesk.jsonl
```

The command prints a JSON summary for `--ticket`, or one log line per ticket for the
worker. The summary includes `replied`, `escalated`, `interventions` (action, tool,
citations) and `error` if the agent LLM failed. In dry-run mode those `replied` /
`escalated` flags mean "the agent would have written", not that Freshdesk changed.

Default model spec is a fallback chain
`gemini:gemini-3-flash-preview,groq:openai/gpt-oss-120b`. Override with `--llm` and
`--judge-llm` (same `gemini[:model]`, `groq[:model]`, `ollama[:model]`, or `fake`
forms as the rest of the CLI). Default policy is
`policies/freshdesk_support_sop.md`. Default `--mode` is `supervised`
(`unsupervised` and `prompt_only` are the no-supervisor and SOP-in-the-prompt baselines).

## 6. Live run

The same command without `--dry-run` writes to Freshdesk. After each ticket the worker
adds the tag `sopguard-handled` so a later poll will skip it.

One ticket:

```bash
sopguard freshdesk run --ticket 123 --audit audit-freshdesk.jsonl
```

One pass over open tickets in the supervised arm of run `r1`, then exit:

```bash
sopguard freshdesk run --once --tag arm-supervised --audit audit-freshdesk.jsonl
```

Poll every 60 seconds (the default `--interval`) until you stop the process:

```bash
sopguard freshdesk run --tag sopguard-eval --interval 60 --audit audit-freshdesk.jsonl
```

Other worker flags:

| Flag | Default | Meaning |
|---|---|---|
| `--mode` | `supervised` | `unsupervised`, `prompt_only`, or `supervised`. |
| `--policy` | `policies/freshdesk_support_sop.md` | SOP to compile. |
| `--llm` | Gemini then Groq fallback chain | Agent model spec. |
| `--judge-llm` | same as `--llm` | Semantic-judge model spec. |
| `--rpm` | `45` | Freshdesk client-side requests per minute. |
| `--limit` | `0` (no cap) | Stop after this many tickets in this process. |
| `--audit` | `audit-freshdesk.jsonl` | Append-only JSONL decision log. |

Open the ticket in Freshdesk at `https://<domain>/a/tickets/<id>` (the worker prints this
URL). On a supervised intervention you should see a private note beginning
`SOPGuard audit`. On an escalation the ticket is Urgent, tagged `escalated`, and the
customer gets a short hand-off reply.

## 7. Webhook (optional)

The polling worker is enough to run the demo. If you want Freshdesk to push new tickets
instead:

1. Start the API with the webhook mounted. Both of these do that when
   `FRESHDESK_DOMAIN` and `SOPGUARD_WEBHOOK_TOKEN` are set:

   ```bash
   export SOPGUARD_WEBHOOK_TOKEN=a-long-random-secret
   sopguard serve --port 8080 --audit audit-freshdesk.jsonl
   # or:
   uvicorn --factory sopguard.api:app_from_env --host 0.0.0.0 --port 8080
   ```

   `SOPGUARD_DRY_RUN=1` makes webhook processing read-only.

2. Expose that host on a public HTTPS URL Freshdesk can reach.

3. In Freshdesk: **Admin > Automations > Ticket creation** → new rule → action
   **Trigger webhook**:

   - Method: `POST`
   - URL: `https://<your-host>/freshdesk/webhook`
   - Header: `X-SOPGuard-Token: <SOPGUARD_WEBHOOK_TOKEN>`
   - Body: `{"ticket_id": {{ticket.id}}}`

A matching request returns `202` and works the ticket in the background. A bad token
returns `401`. A ticket id already accepted by this process is ignored
(`{"accepted": false, "reason": "already processed"}`). Poll
`GET /freshdesk/tickets/{id}` for a short status summary once it has finished.

## 8. Read the audit log

Every supervisor decision is appended as one JSON line. Pretty-print the file:

```bash
sopguard audit audit-freshdesk.jsonl
```

Only interventions (steer, block, escalate):

```bash
sopguard audit audit-freshdesk.jsonl --interventions
```

One ticket's session (the worker uses `fd-<ticket_id>`; the webhook uses
`webhook-<ticket_id>`):

```bash
sopguard audit audit-freshdesk.jsonl --session fd-123
```

Each line shows the session id, step index, action, tool or `reply`, latency in
milliseconds, and SOP citations such as `verify-before-refund (SOP § 2. Identity and
account privacy)`. The raw JSON also includes the policy hash, rule ids, guidance,
judge token counts and the proposed arguments or message.

## What the agent is allowed to do

Tools, all defined in `sopguard.integrations.freshdesk.FreshdeskTools`:

| Tool | Effect on Freshdesk |
|---|---|
| `get_ticket` | Read ticket, requester and conversation. |
| `lookup_account` | Look up a contact by email and parse the billing record. |
| `verify_requester` | True only if `requester_id` equals the contact id (ticket sent from the account email). |
| `reply_to_ticket` | Public reply. |
| `add_private_note` | Internal note. |
| `update_ticket` | Status, priority or tags (tags are merged; Freshdesk otherwise replaces the list). |
| `approve_refund` | Private "REFUND APPROVED" note and the `refund-approved` tag. Billing is not charged from this repo; the tag is the approval record. |
| `update_contact_email` | Change the contact's email. The SOP escalates this; agents cannot do it without a prior `approve`. |
| `escalate` | Priority Urgent, tag `escalated`, optional group, private note. |

The supervisor, not these tools, enforces the SOP (read the ticket first, verify before
refund or account disclosure, cap refunds at the last charge and at $100, one refund per
ticket, no false "refund approved" claims, no credentials in notes, legal threats
escalate first, and so on).

## Related docs

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — components, decision semantics, latency, failure modes, security and the threat model.
- [`DEPLOY.md`](DEPLOY.md) — Docker Compose, Render and Fly.io for the API, webhook and worker.
