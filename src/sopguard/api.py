"""FastAPI service: supervision API + an OpenAI-compatible proxy.

Supervision API (any language, any framework)::

    POST /v1/sessions/{sid}/user          {"text": "..."}                      record a customer message
    POST /v1/sessions/{sid}/check/tool    {"tool": "...", "args": {...}}       -> Decision
    POST /v1/sessions/{sid}/tool_result   {"tool": "...", "args": {...}, "result": ...}
    POST /v1/sessions/{sid}/check/message {"text": "..."}                      -> Decision
    POST /v1/sessions/{sid}/approve       {"rule_id": "..."}                   human approval of a parked action
    GET  /v1/sessions/{sid}/audit

Proxy mode: point an OpenAI-compatible tool-calling agent at ``POST /v1/chat/completions``. The proxy
replays the conversation into a supervisor session, forwards the request upstream, checks every returned
tool call and message, and on a steer re-asks the model with the supervisor's feedback (up to the steer
budget). Blocks/escalations come back as a hand-off message plus a ``sopguard`` field and an
``x-sopguard-action`` header. Tool *execution* stays in your agent; the proxy only lets through tool calls
it approves.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from sopguard.llm import LLM, get_llm
from sopguard.policy.compiler import explain, load_policy
from sopguard.policy.schema import Policy
from sopguard.runtime.audit import AuditLog
from sopguard.runtime.decision import Action, Decision
from sopguard.runtime.judge import Judge, fake_judge_responder
from sopguard.runtime.supervisor import Session, Supervisor
from sopguard.sdk import HANDOFF_TEXT

DEFAULT_POLICY = Path(__file__).parent / "demo" / "northwind_sop.md"


class UserIn(BaseModel):
    text: str


class ToolIn(BaseModel):
    tool: str
    args: dict[str, Any] = {}


class ToolResultIn(ToolIn):
    result: Any = None


class ApproveIn(BaseModel):
    rule_id: str
    approver: str = "human"


def _replay(sup: Supervisor, sid: str, messages: list[dict[str, Any]]) -> Session:
    """Rebuild task state from an OpenAI-style message list (stateless proxying)."""
    s = Session(sup, sid)
    calls: dict[str, tuple[str, dict[str, Any]]] = {}
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        text = content if isinstance(content, str) else json.dumps(content) if content else ""
        if role == "user":
            s.state.record_user(text)
        elif role == "assistant":
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                calls[tc.get("id", "")] = (fn.get("name", ""), args)
            if text:
                s.state.record_agent(text)
        elif role == "tool":
            name, args = calls.get(m.get("tool_call_id", ""), (m.get("name", ""), {}))
            try:
                result = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                result = text
            if name:
                s.state.record_tool(name, args, result)
    return s


def _check_choice(s: Session, msg: dict[str, Any]) -> list[Decision]:
    out = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {})
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {"_raw": fn.get("arguments")}
        out.append(s.check_tool_call(fn.get("name", ""), args))
    if msg.get("content") and not msg.get("tool_calls"):
        out.append(s.check_message(msg["content"]))
    return out


def create_app(
    policy: Policy | None = None,
    judge_llm: LLM | None = None,
    upstream_url: str | None = None,
    audit_path: str | None = None,
    http_client: httpx.Client | None = None,
) -> FastAPI:
    policy = policy or load_policy(os.getenv("SOPGUARD_POLICY", str(DEFAULT_POLICY)))
    if judge_llm is None:
        spec = os.getenv("SOPGUARD_JUDGE_LLM", "fake")
        judge_llm = get_llm(spec, fake=fake_judge_responder)  # type: ignore[arg-type]
    sup = Supervisor(
        policy, judge=Judge(judge_llm), audit=AuditLog(audit_path or os.getenv("SOPGUARD_AUDIT"), policy.source_hash)
    )
    upstream = (upstream_url or os.getenv("SOPGUARD_UPSTREAM", "https://api.groq.com/openai/v1")).rstrip("/")
    client = http_client or httpx.Client(timeout=60)
    app = FastAPI(title="SOPGuard", version="0.1.0", description="Runtime supervisor for business AI agents")
    app.state.supervisor = sup

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"ok": True, "policy": policy.name, "policy_hash": policy.source_hash, "judge": judge_llm.describe()}

    @app.get("/v1/policy")
    def get_policy() -> dict[str, Any]:
        return {"summary": explain(policy), "policy": policy.model_dump(mode="json")}

    @app.post("/v1/sessions/{sid}/user")
    def user(sid: str, body: UserIn) -> dict[str, Any]:
        sup.session(sid).record_user_message(body.text)
        return {"ok": True}

    @app.post("/v1/sessions/{sid}/check/tool")
    def check_tool(sid: str, body: ToolIn) -> dict[str, Any]:
        d = sup.session(sid).check_tool_call(body.tool, body.args)
        return d.model_dump(mode="json") | {"feedback": d.agent_feedback()}

    @app.post("/v1/sessions/{sid}/tool_result")
    def tool_result(sid: str, body: ToolResultIn) -> dict[str, Any]:
        sup.session(sid).record_tool_result(body.tool, body.args, body.result)
        return {"ok": True}

    @app.post("/v1/sessions/{sid}/check/message")
    def check_message(sid: str, body: UserIn) -> dict[str, Any]:
        d = sup.session(sid).check_message(body.text)
        return d.model_dump(mode="json") | {"feedback": d.agent_feedback()}

    @app.post("/v1/sessions/{sid}/approve")
    def approve(sid: str, body: ApproveIn) -> dict[str, Any]:
        sup.session(sid).approve(body.rule_id, body.approver)
        return {"ok": True}

    @app.get("/v1/sessions/{sid}/audit")
    def audit(sid: str) -> list[dict[str, Any]]:
        return sup.audit.for_session(sid)

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> JSONResponse:
        body = await request.json()
        if body.get("stream"):
            raise HTTPException(400, "streaming is not supported by the SOPGuard proxy")
        messages: list[dict[str, Any]] = list(body.get("messages") or [])
        sid = (
            request.headers.get("x-sopguard-session")
            or body.get("user")
            or ("px_" + hashlib.sha256(json.dumps(messages[:2], sort_keys=True).encode()).hexdigest()[:12])
        )
        auth = request.headers.get("authorization", "")
        s = _replay(sup, sid, messages)
        decisions: list[Decision] = []
        for _attempt in range(policy.budgets.max_steers_per_step + 1):
            r = client.post(
                f"{upstream}/chat/completions", json={**body, "messages": messages}, headers={"Authorization": auth}
            )
            if r.status_code >= 400:
                return JSONResponse(
                    r.json() if r.headers.get("content-type", "").startswith("application/json") else {"error": r.text},
                    r.status_code,
                )
            data = r.json()
            msg = data["choices"][0]["message"]
            ds = _check_choice(s, msg)
            decisions += ds
            worst = max((d.action for d in ds), key=lambda a: a.rank, default=Action.ALLOW)
            if worst == Action.ALLOW:
                return JSONResponse(
                    data | {"sopguard": {"session": sid, "action": "allow", "decisions": [d.id for d in decisions]}},
                    headers={"x-sopguard-action": "allow", "x-sopguard-session": sid},
                )
            if worst != Action.STEER:
                break
            feedback = "\n".join(d.agent_feedback() for d in ds if not d.allowed)
            messages = messages + [{"role": "system", "content": feedback}]
        final = decisions[-1] if decisions else None
        action = Action.ESCALATE if worst in (Action.STEER, Action.ESCALATE) else worst
        content = (
            HANDOFF_TEXT if action == Action.ESCALATE else "I'm sorry, I can't help with that request under our policy."
        )
        out = {
            "id": "chatcmpl-sopguard-" + uuid.uuid4().hex[:10],
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model", ""),
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            "sopguard": {
                "session": sid,
                "action": action.value,
                "citations": final.citations() if final else [],
                "decisions": [d.model_dump(mode="json") for d in decisions],
            },
        }
        return JSONResponse(out, headers={"x-sopguard-action": action.value, "x-sopguard-session": sid})

    return app


def app_from_env() -> FastAPI:  # uvicorn --factory sopguard.api:app_from_env
    return create_app()
