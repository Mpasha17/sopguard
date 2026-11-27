"""Freshdesk automation webhook: Freshdesk calls us when a ticket is created; we work it in the background.

In Freshdesk: Admin > Automations > Ticket creation > new rule > Action "Trigger webhook":
POST ``https://<your-host>/freshdesk/webhook``, header ``X-SOPGuard-Token: <SOPGUARD_WEBHOOK_TOKEN>``,
JSON body ``{"ticket_id": {{ticket.id}}}``.
"""

from __future__ import annotations

import hmac
import threading
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel

from sopguard.integrations.freshdesk import FreshdeskClient
from sopguard.integrations.freshdesk_agent import Mode, load_kb, process_ticket
from sopguard.llm import LLM


class WebhookIn(BaseModel):
    ticket_id: int


def freshdesk_router(
    fd: FreshdeskClient, llm: LLM, supervisor: Any, token: str, mode: Mode = "supervised", dry_run: bool = False
) -> APIRouter:
    if not token:
        raise ValueError("a webhook token is required")
    router = APIRouter()
    lock = threading.Lock()
    seen: set[int] = set()
    kb = load_kb(fd)
    results: dict[int, dict[str, Any]] = {}

    def work(tid: int) -> None:
        r = process_ticket(fd, llm, tid, mode, supervisor, dry_run=dry_run, kb=kb, session_prefix="webhook")
        results[tid] = {k: v for k, v in r.summary().items() if k != "events"}

    @router.post("/freshdesk/webhook", status_code=202)
    def webhook(body: WebhookIn, bg: BackgroundTasks, x_sopguard_token: str = Header(default="")) -> dict[str, Any]:
        if not hmac.compare_digest(x_sopguard_token, token):
            raise HTTPException(401, "bad token")
        with lock:
            if body.ticket_id in seen:
                return {"accepted": False, "reason": "already processed", "ticket_id": body.ticket_id}
            seen.add(body.ticket_id)
        bg.add_task(work, body.ticket_id)
        return {"accepted": True, "ticket_id": body.ticket_id, "url": fd.ticket_url(body.ticket_id)}

    @router.get("/freshdesk/tickets/{tid}")
    def status(tid: int) -> dict[str, Any]:
        if tid not in results:
            raise HTTPException(404, "not processed yet")
        return results[tid]

    return router
