# Deploying SOPGuard

The production image runs two processes from the same Dockerfile:

* **api** — FastAPI supervision API, OpenAI-compatible proxy (`POST /v1/chat/completions`), and (when configured) a Freshdesk automation webhook at `POST /freshdesk/webhook`.
* **worker** — `sopguard freshdesk run`, a polling loop that picks up open Freshdesk tickets and works them under the supervisor.

The audit log is append-only **JSONL** (not SQLite). Docker Compose, Render, and Fly all write it under `/data`.

Copy [`.env.example`](../.env.example) to `.env` and fill in real values. Never commit `.env` or API keys.

## Local: Docker Compose

```bash
cp .env.example .env
# edit .env: FRESHDESK_DOMAIN, FRESHDESK_API_KEY, GEMINI_API_KEY or GROQ_API_KEY,
# and a long random SOPGUARD_WEBHOOK_TOKEN
docker compose up --build
```

* API: http://localhost:8080/health (and `/healthz`, `/v1/policy`, `/docs`)
* Webhook: `POST http://localhost:8080/freshdesk/webhook` with header `X-SOPGuard-Token: <SOPGUARD_WEBHOOK_TOKEN>` and body `{"ticket_id": 123}`
* Worker: logs each ticket it handles; audit lines land in the `audit` volume at `/data/audit-freshdesk.jsonl`

The webhook is mounted only when **both** `FRESHDESK_DOMAIN` and `SOPGUARD_WEBHOOK_TOKEN` are set. The worker needs `FRESHDESK_DOMAIN` and `FRESHDESK_API_KEY`. You can run just the API (`docker compose up api`) if you only want the webhook, or just the worker if you only want polling. Running both is safe: the worker skips tickets already tagged `sopguard-handled`.

Without LLM keys, set `SOPGUARD_JUDGE_LLM=fake` (the compose default for the API) to exercise the health endpoint and deterministic checks offline.

```bash
curl -fsS http://localhost:8080/health
# {"ok": true, "policy": "...", "policy_hash": "...", "judge": "..."}
```

To run the API without Docker:

```bash
pip install -e ".[langgraph]"
SOPGUARD_JUDGE_LLM=fake uvicorn sopguard.api:app_from_env --factory --host 127.0.0.1 --port 8080
```

The worker is `sopguard freshdesk run` (add `--dry-run` to decide without writing to Freshdesk, `--once` for a single pass).

## Render

This repo includes a [render.yaml](../render.yaml) Blueprint: a free **web** service (`sopguard-api`) and a **background worker** (`sopguard-worker`).

1. In the [Render dashboard](https://dashboard.render.com) choose **New > Blueprint**, point it at `https://github.com/Mpasha17/sopguard`, and apply `render.yaml`.
2. Fill in the secret env vars that Blueprint leaves unset (`GEMINI_API_KEY` and/or `GROQ_API_KEY`, `FRESHDESK_DOMAIN`, `FRESHDESK_API_KEY`). Render generates `SOPGUARD_WEBHOOK_TOKEN` for the web service — copy it into the Freshdesk automation below.
3. The API health check is `GET /health`. After the first deploy the public URL looks like `https://sopguard-api.onrender.com`.

Render's free web instances spin down after idle time (the first webhook after a nap is slow). Free **background workers** may not be available on every account; if the worker plan is rejected, use only the web service and a Freshdesk webhook, or upgrade that service.

Audit files on the free plan are ephemeral (no persistent disk). Add a disk mounted at `/data` if you need the JSONL log to survive a restart.

## Fly.io

[`fly.toml`](../fly.toml) defines two processes, `api` and `worker`, from the same image.

```bash
fly launch --copy-config --no-deploy
# edit the `app` name in fly.toml if launch did not
fly secrets set GEMINI_API_KEY=... GROQ_API_KEY=... \
  FRESHDESK_DOMAIN=yourcompany.freshdesk.com FRESHDESK_API_KEY=... \
  SOPGUARD_WEBHOOK_TOKEN=...
# optional persistent audit log:
# fly volumes create sopguard_data --size 1
# then uncomment the [[mounts]] block in fly.toml
fly deploy
```

Health checks hit `GET /health`. The webhook URL is `https://<app>.fly.dev/freshdesk/webhook`.

## Wiring a Freshdesk automation webhook

Use this when you want tickets worked as soon as they are created, without running the polling worker (or in addition to it).

1. Deploy the **api** service so it is reachable over HTTPS, and set `FRESHDESK_DOMAIN`, `FRESHDESK_API_KEY`, at least one LLM key, and `SOPGUARD_WEBHOOK_TOKEN` on that service.
2. In Freshdesk: **Admin > Automations > Ticket creation > New rule**.
3. Condition: whatever you want to auto-handle (for example, all new tickets, or tickets with a given tag).
4. Action: **Trigger webhook**
    * Method: `POST`
    * URL: `https://<your-host>/freshdesk/webhook`
    * Headers: `X-SOPGuard-Token: <the same SOPGUARD_WEBHOOK_TOKEN>` and `Content-Type: application/json`
    * Body: `{"ticket_id": {{ticket.id}}}`
5. Save and create a test ticket. A 202 response means SOPGuard accepted it; the agent runs in the background. `GET https://<your-host>/freshdesk/tickets/<id>` returns the result once it finishes. A 401 means the token header did not match.

Locally you can expose the compose API with a tunnel (ngrok, Cloudflare Tunnel, etc.) and use that HTTPS URL in the automation.

The polling worker (`sopguard freshdesk run`) is the alternative when you cannot expose a public URL: it lists open tickets every `--interval` seconds (default 60) and writes replies, notes, and escalations back through the Freshdesk API.
