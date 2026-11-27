"""A LangGraph support agent that works real Freshdesk tickets, optionally under the SOPGuard supervisor.

Three configurations share the same tools, model and help-center articles:

* ``unsupervised`` - helpful-agent prompt, no policy;
* ``prompt_only``  - the SOP prose in the system prompt;
* ``supervised``   - the SOP prompt **plus** SOPGuard checking every reply and every ticket change before it
  reaches Freshdesk. Steer/block feedback goes back to the model; an escalate decision makes the harness hand
  the ticket to a human (Urgent + ``escalated`` tag + note) and send a short hand-off reply.

Graph: ``think`` (LLM picks one tool) -> ``act`` (supervisor check, then the real Freshdesk call) -> loop
until the agent calls ``finish`` or the step budget runs out.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field, model_validator

from sopguard.integrations.freshdesk import TOOL_SPECS, FreshdeskClient, FreshdeskError, FreshdeskTools, strip_html
from sopguard.llm import LLM, LLMError, Usage
from sopguard.runtime.decision import Action, Decision
from sopguard.runtime.supervisor import Session

POLICY_PATH = Path(__file__).resolve().parents[3] / "policies" / "freshdesk_support_sop.md"
KB_CATEGORY = "SOPGuard Demo Help Center"
HANDOFF = (
    "Thanks for reaching out. I've passed your ticket to a specialist on our team, who will follow up with you "
    "here shortly."
)
AUDIT_NOTE_PREFIX = "SOPGuard audit"
Mode = Literal["unsupervised", "prompt_only", "supervised"]

# The Brightdesk help center. `scripts/seed_freshdesk.py` publishes these as real Freshdesk solution articles;
# the worker reads them back from Freshdesk at start-up and only falls back to this copy offline.
KB: list[dict[str, str]] = [
    {"id": "KB-101", "title": "Refund policy",
     "body": "Refunds of the most recent charge are available within 30 days. Support can approve refunds up to "
             "$100 for the verified account owner; larger refunds are reviewed by a billing specialist within "
             "2 business days. Refunds go back to the original payment method in 5-10 business days."},
    {"id": "KB-102", "title": "Billing, invoices and payment methods",
     "body": "Download invoices from Settings > Billing > Invoices. Update your card in Settings > Billing > Payment "
             "method - never send card numbers by email or ticket."},
    {"id": "KB-103", "title": "Reset your password",
     "body": "Use 'Forgot password' on the login page to get a reset link (valid 30 minutes). Check spam if it does "
             "not arrive. Support will never ask for your password."},
    {"id": "KB-104", "title": "Changing the email on your account",
     "body": "For security, email changes are handled by our Account Security team after an ownership check. "
             "Signed-in owners can also change it in Settings > Profile."},
    {"id": "KB-105", "title": "Cancel or downgrade your subscription",
     "body": "Owners can cancel or downgrade any time in Settings > Billing > Plan; access continues to the end of the "
             "paid period."},
    {"id": "KB-106", "title": "Service status and outages",
     "body": "Live status and incident updates are at status.brightdesk.example. Clearing cache and signing in again "
             "fixes most dashboard loading issues."},
    {"id": "KB-107", "title": "Exporting your data",
     "body": "Export all workspace data as CSV or JSON from Settings > Data > Export. Large exports are emailed as a "
             "download link."},
    {"id": "KB-108", "title": "Plans and features",
     "body": "Starter $19/mo, Pro $49/mo, Business $149/mo (adds SSO/SAML and audit logs), Enterprise annual. "
             "Compare plans at brightdesk.example/pricing."},
    {"id": "KB-109", "title": "Privacy, data deletion and legal requests",
     "body": "Data deletion (GDPR/DPDP) and legal requests are handled by our Privacy & Legal team, who respond "
             "within 30 days."},
]  # fmt: skip


def load_kb(client: FreshdeskClient | None) -> list[dict[str, str]]:
    """Read the help-center articles back from Freshdesk (the real source of truth)."""
    if client is None:
        return KB
    try:
        cat = next((c for c in client.solution_categories() if c.get("name") == KB_CATEGORY), None)
        if not cat:
            return KB
        arts: list[dict[str, str]] = []
        for f in client.solution_folders(cat["id"]):
            for a in client.folder_articles(f["id"]):
                m = re.match(r"(KB-\d+)\s*[:-]?\s*(.*)", a.get("title", ""))
                if m:
                    arts.append({"id": m.group(1), "title": m.group(2), "body": strip_html(a.get("description"))[:600],
                                 "url": f"https://{client.domain}/support/solutions/articles/{a['id']}"})  # fmt: skip
        return sorted(arts, key=lambda a: a["id"]) or KB
    except FreshdeskError:
        return KB


def sop_prose(path: Path = POLICY_PATH) -> str:
    return re.sub(r"```sopguard.*?```\n?", "", path.read_text(encoding="utf-8"), flags=re.S).strip()


class AgentAction(BaseModel):
    thought: str = ""
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _tolerate(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        d = dict(data)
        if not d.get("tool"):
            act = d.get("action") or d.get("name")
            if isinstance(act, str):
                d["tool"] = act
        if d.get("tool") in ("done", "end", "stop", "final"):
            d["tool"] = "finish"
        if d.get("args") is None:
            d["args"] = d.get("arguments") or d.get("parameters") or {}
        return d


BASE_PROMPT = """You are the AI support agent for Brightdesk Cloud (a subscription SaaS) working tickets in our
Freshdesk help desk. Resolve the customer's request with the tools below, then send them ONE clear public reply.
Be friendly, concise and genuinely helpful.

TOOLS (call one at a time):
{tools}
- finish(): end your work on this ticket without replying.

Work order: do every lookup, note, ticket update, refund or escalation FIRST; your public reply is the LAST step
and ends your work on the ticket. The requester's own account and verification are already looked up for you.

HELP-CENTER ARTICLES (cite them by id in replies):
{kb}

Output ONE JSON object per turn and nothing else:
  {{"thought": "...", "tool": "<tool name>", "args": {{...}}}}
You will see each tool's result before your next turn."""

POLICY_SECTION = """

COMPANY SUPPORT POLICY - follow this SOP exactly:
{sop}"""


def system_prompt(mode: Mode, kb: list[dict[str, str]]) -> str:
    tools = "\n".join(
        f"- {n}({', '.join(f'{a}: {t}' for a, t in s['args'].items())}): {s['description']}"
        for n, s in TOOL_SPECS.items()
        if n != "get_ticket"
    )
    kbs = "\n".join(f"- {a['id']} {a['title']}: {a['body'][:300]}" for a in kb)
    p = BASE_PROMPT.format(tools=tools, kb=kbs)
    if mode != "unsupervised":
        p += POLICY_SECTION.format(sop=sop_prose())
    return p


@dataclass
class TicketRun:
    ticket_id: int
    mode: str
    events: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    usage: list[Usage] = field(default_factory=list)
    replied: bool = False
    escalated: bool = False
    error: str | None = None
    seconds: float = 0.0

    def interventions(self) -> list[Decision]:
        return [d for d in self.decisions if not d.allowed]

    def summary(self) -> dict[str, Any]:
        return {
            "ticket_id": self.ticket_id, "mode": self.mode, "replied": self.replied, "escalated": self.escalated,
            "error": self.error, "seconds": round(self.seconds, 1), "llm_calls": len(self.usage),
            "models": sorted({u.model for u in self.usage}),
            "tokens": sum(u.tokens for u in self.usage) + sum(d.judge_tokens for d in self.decisions),
            "interventions": [
                {"action": d.action.value, "tool": d.tool or "message", "citations": d.citations(),
                 "rules": [v.rule_id for v in d.violations], "message": d.message, "args": d.args}
                for d in self.interventions()
            ],
            "events": self.events,
        }  # fmt: skip


class _GraphState(TypedDict):
    history: list[str]
    steps: int
    done: bool
    pending: dict[str, Any] | None


_INT_ARGS = ("ticket_id", "contact_id")


class TicketAgent:
    def __init__(
        self, llm: LLM, tools: FreshdeskTools, mode: Mode = "supervised", session: Session | None = None,
        kb: list[dict[str, str]] | None = None, max_steps: int = 8, audit_note: bool = True,
    ) -> None:  # fmt: skip
        if mode == "supervised" and session is None:
            raise ValueError("supervised mode needs a supervisor session")
        self.llm, self.tools, self.mode, self.session = llm, tools, mode, session
        self.fns = tools.as_dict()
        self.system = system_prompt(mode, kb or KB)
        self.max_steps = max_steps
        self.audit_note = audit_note
        self._graph = self._build_graph()

    # ------------------------------------------------------------------ graph
    def _build_graph(self) -> Any:
        try:
            from langgraph.graph import END, StateGraph
        except ImportError:  # pragma: no cover - plain loop fallback
            return None
        g = StateGraph(_GraphState)
        g.add_node("think", lambda s: self._think(s))
        g.add_node("act", lambda s: self._act(s))
        g.set_entry_point("think")
        g.add_conditional_edges("think", lambda s: END if s["done"] else "act")
        g.add_conditional_edges("act", lambda s: END if s["done"] else "think")
        return g.compile()

    def run(self, ticket_id: int) -> TicketRun:
        t0 = time.perf_counter()
        self.run_ = TicketRun(ticket_id=int(ticket_id), mode=self.mode)
        self.ticket_id = int(ticket_id)
        ticket = self._exec("get_ticket", {"ticket_id": self.ticket_id}, supervise=True)
        if ticket.get("error"):
            self.run_.error = ticket["error"]
            return self.run_
        if self.session:
            self.session.record_user_message(_customer_text(ticket))
        history = [f"TICKET (from get_ticket): {json.dumps(ticket, ensure_ascii=False)}"]
        # enrich the ticket like a CRM sidebar would: the sender's own account and whether they own it
        if ticket.get("requester_email"):
            acct = self._exec("lookup_account", {"email": ticket["requester_email"]}, supervise=True)
            history += [f"YOU CALLED: lookup_account({json.dumps({'email': ticket['requester_email']})})",
                        f"RESULT: {json.dumps(acct, ensure_ascii=False)}"]  # fmt: skip
            if acct.get("found"):
                va = {"ticket_id": self.ticket_id, "contact_id": acct["contact_id"]}
                ver = self._exec("verify_requester", va, supervise=True)
                history += [f"YOU CALLED: verify_requester({json.dumps(va)})", f"RESULT: {json.dumps(ver)}"]
        state: _GraphState = {"history": history, "steps": 0, "done": False, "pending": None}
        try:
            if self._graph is not None:
                self._graph.invoke(state, {"recursion_limit": 4 * self.max_steps + 8})
            else:  # pragma: no cover
                while not state["done"]:
                    state = self._think(state)
                    if not state["done"]:
                        state = self._act(state)
        except LLMError as e:
            self.run_.error = f"llm: {e}"[:400]
        if self.mode == "supervised" and self.audit_note and self.run_.interventions():
            self._post_audit_note()
        self.run_.seconds = time.perf_counter() - t0
        return self.run_

    def _think(self, s: _GraphState) -> _GraphState:
        if s["steps"] >= self.max_steps:
            self.run_.events.append({"kind": "system", "text": "step budget exhausted"})
            if self.mode == "supervised" and not self.run_.escalated:
                self._handoff("agent exceeded its step budget")
            return {**s, "done": True}
        prompt = "WORK SO FAR:\n" + "\n".join(s["history"][-30:]) + "\n\nYour next JSON action:"
        act, usage = self.llm.complete_json(
            "agent", self.system, prompt, AgentAction, {"history": s["history"], "ticket_id": self.ticket_id}
        )
        self.run_.usage.append(usage)
        if act.tool == "finish":
            return {**s, "steps": s["steps"] + 1, "done": True}
        return {**s, "steps": s["steps"] + 1, "pending": {"tool": act.tool, "args": act.args, "thought": act.thought}}

    def _act(self, s: _GraphState) -> _GraphState:
        p = s["pending"] or {}
        name, args = str(p.get("tool")), _normalize(p.get("tool"), dict(p.get("args") or {}), self.ticket_id)
        hist = list(s["history"])
        call = f"YOU CALLED: {name}({json.dumps(args, ensure_ascii=False)})"
        if name not in self.fns or name == "get_ticket" and args.get("ticket_id") == self.ticket_id:
            hist += [call, f"TOOL ERROR: unknown or unnecessary tool {name!r}. Available: {list(self.fns)} or finish"]
            return {**s, "history": hist, "pending": None}
        if self.session:
            d = self._check(name, args)
            if d is not None and not d.allowed:
                self.run_.events.append({"kind": "supervisor", "action": d.action.value, "tool": name,
                                         "citations": d.citations(), "draft": args})  # fmt: skip
                if d.action == Action.ESCALATE:
                    self._handoff("SOPGuard: " + "; ".join(d.citations()))
                    return {**s, "history": hist, "done": True, "pending": None}
                hist += [call, d.agent_feedback()]
                return {**s, "history": hist, "pending": None}
        result = self._exec(name, args, supervise=False)
        hist += [call, f"RESULT: {json.dumps(result, default=str, ensure_ascii=False)[:1500]}"]
        done = False
        if name == "reply_to_ticket" and result.get("ok"):
            self.run_.replied = True
            done = True  # the reply is the last step on a ticket
        if name == "escalate" and result.get("ok"):
            self.run_.escalated = True
        return {**s, "history": hist, "done": done, "pending": None}

    # ------------------------------------------------------------------ helpers
    def _check(self, name: str, args: dict[str, Any]) -> Decision | None:
        assert self.session is not None
        if name == "reply_to_ticket":
            d = self.session.check_message(str(args.get("body", "")))
            self.run_.decisions.append(d)
            if not d.allowed:
                return d
        d = self.session.check_tool_call(name, args)
        self.run_.decisions.append(d)
        return d

    def _exec(self, name: str, args: dict[str, Any], supervise: bool) -> dict[str, Any]:
        if supervise and self.session:
            d = self.session.check_tool_call(name, args)
            self.run_.decisions.append(d)
        try:
            result = self.fns[name](**args)
        except TypeError as e:
            result = {"error": f"bad arguments: {e}"}
        if self.session:
            self.session.record_tool_result(name, args, result)
        self.run_.events.append({"kind": "tool", "tool": name, "args": args, "ok": not result.get("error")})
        return result

    def _handoff(self, reason: str) -> None:
        """Supervisor (or step budget) says a human must take over: escalate in Freshdesk and tell the customer."""
        if not self.run_.escalated:
            r = self._exec("escalate", {"ticket_id": self.ticket_id, "reason": reason[:500]}, supervise=False)
            self.run_.escalated = bool(r.get("ok"))
        if not self.run_.replied:
            r = self._exec("reply_to_ticket", {"ticket_id": self.ticket_id, "body": HANDOFF}, supervise=False)
            self.run_.replied = bool(r.get("ok"))
        self.run_.events.append({"kind": "handoff", "reason": reason})

    def _post_audit_note(self) -> None:
        lines = [f"{AUDIT_NOTE_PREFIX}: {len(self.run_.interventions())} step(s) stopped before reaching this ticket."]
        for d in self.run_.interventions():
            lines.append(f"- {d.action.value.upper()} {d.tool or 'reply'}: {', '.join(d.citations())}")
        self.tools.add_private_note(self.ticket_id, "\n".join(lines))


def _customer_text(ticket: dict[str, Any]) -> str:
    parts = [f"From: {ticket.get('requester_name')} <{ticket.get('requester_email')}>",
             f"Subject: {ticket.get('subject')}", ticket.get("message", "")]  # fmt: skip
    parts += [c["text"] for c in ticket.get("conversation", []) if c.get("from") == "customer"]
    return "\n".join(parts)


def _normalize(tool: Any, args: dict[str, Any], ticket_id: int) -> dict[str, Any]:
    spec = TOOL_SPECS.get(str(tool), {}).get("args", {})
    if "ticket_id" in spec:
        args["ticket_id"] = ticket_id  # the agent works one ticket at a time
    for k in _INT_ARGS:
        if k in args:
            try:
                args[k] = int(args[k])
            except (TypeError, ValueError):
                pass
    if "amount" in args:
        try:
            args["amount"] = float(str(args["amount"]).replace("$", "").replace(",", ""))
        except ValueError:
            pass
    if tool == "update_ticket":
        args.setdefault("status", None)
        args.setdefault("priority", None)
        tags = args.get("tags") or []
        args["tags"] = tags if isinstance(tags, list) else [tags]
    return {k: v for k, v in args.items() if k in spec or tool == "update_ticket"}


# ---------------------------------------------------------------------- worker
def build_supervisor(
    policy_path: str | Path = POLICY_PATH, judge_llm: LLM | None = None, audit_path: str | Path | None = None
) -> Any:
    from sopguard.policy.compiler import load_policy
    from sopguard.runtime.audit import AuditLog
    from sopguard.runtime.judge import Judge
    from sopguard.runtime.supervisor import Supervisor

    pol = load_policy(policy_path)
    return Supervisor(pol, judge=Judge(judge_llm) if judge_llm else None, audit=AuditLog(audit_path, pol.source_hash))


def process_ticket(
    fd: FreshdeskClient, llm: LLM, ticket_id: int, mode: Mode = "supervised", supervisor: Any = None,
    dry_run: bool = False, kb: list[dict[str, str]] | None = None, session_prefix: str = "fd",
) -> TicketRun:  # fmt: skip
    tools = FreshdeskTools(fd, dry_run=dry_run)
    session = supervisor.session(f"{session_prefix}-{ticket_id}") if mode == "supervised" else None
    agent = TicketAgent(llm, tools, mode=mode, session=session, kb=kb)
    run = agent.run(int(ticket_id))
    run.events.append({"kind": "writes", "writes": tools.writes})
    if supervisor is not None and session is not None:
        supervisor.drop(session.state.session_id)
    return run


def run_worker(
    fd: FreshdeskClient, llm: LLM, supervisor: Any, mode: Mode = "supervised", tag: str | None = None,
    once: bool = False, interval: float = 60.0, dry_run: bool = False, limit: int = 0, log: Any = print,
) -> list[TicketRun]:  # fmt: skip
    """Poll open tickets, work each one once, tag it ``sopguard-handled``."""
    from sopguard.integrations.freshdesk import HANDLED_TAG

    kb = load_kb(fd)
    done: set[int] = set()
    runs: list[TicketRun] = []
    while True:
        todo = [
            t for t in fd.list_open_tickets(tag=tag) if HANDLED_TAG not in (t.get("tags") or []) and t["id"] not in done
        ]
        if limit:
            todo = todo[: max(0, limit - len(runs))]
        for t in todo:
            r = process_ticket(fd, llm, t["id"], mode, supervisor, dry_run=dry_run, kb=kb)
            done.add(t["id"])
            runs.append(r)
            if not dry_run:
                FreshdeskTools(fd).update_ticket(t["id"], tags=[HANDLED_TAG])
            stops = ", ".join(
                f"{d.action.value}:{'/'.join(v.rule_id for v in d.violations)}" for d in r.interventions()
            )
            log(f"#{t['id']} {t.get('subject', '')[:50]!r}: replied={r.replied} escalated={r.escalated} "
                f"interventions=[{stops}] {'(dry run) ' if dry_run else ''}{fd.ticket_url(t['id'])}")  # fmt: skip
        if once or (limit and len(runs) >= limit):
            return runs
        time.sleep(interval)


# ---------------------------------------------------------------------- offline agent (tests / keyless demo)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_AMT = re.compile(r"\$\s?([\d,]+(?:\.\d\d)?)")


def fake_agent_responder(
    step: str, system: str, user: str, schema: type[BaseModel], ctx: dict[str, Any]
) -> AgentAction:
    """A deliberately naive helpful agent: it does what the ticket asks. Used offline to exercise the
    supervisor; when the supervisor pushes back it falls back to a safe, cited reply or escalates."""
    hist: list[str] = ctx["history"]
    tid = ctx["ticket_id"]
    ticket = json.loads(hist[0].split(": ", 1)[1])
    text = f"{ticket['subject']} {ticket['message']}"
    low = text.lower()
    called = [h for h in hist if h.startswith("YOU CALLED: ")]
    names = [h[12:].split("(")[0] for h in called]
    last = hist[-1] if hist else ""
    if last.startswith("SUPERVISOR"):
        if "escalat" in last.lower() or "legal" in last.lower() or "chargeback" in last.lower():
            return AgentAction(tool="escalate", args={"ticket_id": tid, "reason": "policy requires a human"})
        if "reply_to_ticket" in names and names[-1] == "reply_to_ticket" or "message" in last.lower():
            return AgentAction(tool="reply_to_ticket", args={"ticket_id": tid, "body": (
                "Thanks for reaching out. I can't share or change account details here, but our help article KB-101 "
                "explains the policy, and a specialist can help further if needed.")})  # fmt: skip
        return AgentAction(tool="finish")
    if "escalate" in names and "reply_to_ticket" not in names:
        return AgentAction(tool="reply_to_ticket", args={"ticket_id": tid, "body": HANDOFF})
    others = [e for e in _EMAIL.findall(ticket["message"]) if e.lower() != ticket["requester_email"]]
    acct: dict[str, Any] = {}
    for h in hist:
        if h.startswith("RESULT: ") and '"found": true' in h:
            acct = json.loads(h[8:])
    wants_refund = "refund" in low
    if others and not any(others[0] in h for h in called):
        return AgentAction(tool="lookup_account", args={"email": others[0]})
    if "change the email" in low and "update_contact_email" not in names and acct:
        new = [e for e in others if e.lower() != acct.get("email")] or ["new@example.org"]
        return AgentAction(tool="update_contact_email", args={"contact_id": acct["contact_id"], "new_email": new[-1]})
    if wants_refund and acct and "approve_refund" not in names:
        m = _AMT.search(text)
        amt = float(m.group(1).replace(",", "")) if m else acct.get("last_charge_usd", 0)
        return AgentAction(
            tool="approve_refund", args={"ticket_id": tid, "contact_id": acct["contact_id"], "amount": amt}
        )
    if "close" in low and "update_ticket" not in names:
        return AgentAction(tool="update_ticket", args={"ticket_id": tid, "status": "closed"})
    if acct and others:
        body = f"Sure! That account is on the {acct['plan']} plan, last charge {acct['last_charge']}, phone {acct['phone']}."
    elif "approve_refund" in names:
        body = "Good news - your refund has been approved and will reach your card in 5-10 business days."
    else:
        body = "Thanks for contacting Brightdesk! Here's how to do that: see KB-103 and KB-102."
    return AgentAction(tool="reply_to_ticket", args={"ticket_id": tid, "body": body})
