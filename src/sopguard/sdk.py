"""Python SDK: put the supervisor in front of any agent's tools and replies in a few lines.

    guard = Guard("support_sop.md")                 # compile the SOP once
    s = guard.session("ticket-123")                 # one per conversation

    @s.tool                                         # every call is checked before it runs
    def issue_refund(order_id: str, amount: float, reason: str = ""): ...

    s.user("hi, refund NW-10001 please")            # record what the customer said
    out = s.reply("Your refund has been issued!")   # check an outgoing message
    if out.allowed: send(out.text) else: feed out.feedback back to your agent and let it retry

A blocked tool call returns ``{"error": ..., "supervisor": <feedback>}`` by default, which most agent loops
already feed back to the model as the tool result (that *is* the steer). Pass ``on_intervention="raise"`` to
get exceptions instead.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeVar

from sopguard.llm import LLM
from sopguard.policy.compiler import compile_policy, load_policy
from sopguard.policy.schema import Policy
from sopguard.runtime.audit import AuditLog
from sopguard.runtime.decision import Action, Decision
from sopguard.runtime.judge import Judge
from sopguard.runtime.supervisor import Session, Supervisor

F = TypeVar("F", bound=Callable[..., Any])
HANDOFF_TEXT = "I'm connecting you with a human specialist who will follow up shortly."


class SupervisorIntervention(Exception):
    def __init__(self, decision: Decision) -> None:
        super().__init__(decision.agent_feedback())
        self.decision = decision

    @property
    def feedback(self) -> str:
        return self.decision.agent_feedback()


class Steered(SupervisorIntervention):
    pass


class Blocked(SupervisorIntervention):
    pass


class Escalated(SupervisorIntervention):
    pass


_EXC = {Action.STEER: Steered, Action.BLOCK: Blocked, Action.ESCALATE: Escalated}


@dataclass
class ReplyCheck:
    allowed: bool
    text: str  # what to send: the original message if allowed, the hand-off text if escalated, else ""
    feedback: str  # what to give the agent so it can retry
    decision: Decision

    @property
    def escalated(self) -> bool:
        return self.decision.action == Action.ESCALATE


class GuardedSession:
    def __init__(self, session: Session, on_intervention: Literal["return", "raise"] = "return") -> None:
        self.session = session
        self.on_intervention = on_intervention

    @property
    def id(self) -> str:
        return self.session.state.session_id

    @property
    def decisions(self) -> list[Decision]:
        return self.session.decisions

    def user(self, text: str) -> None:
        self.session.record_user_message(text)

    def approve(self, rule_id: str, approver: str = "human") -> None:
        self.session.approve(rule_id, approver)

    def check_tool(self, name: str, args: dict[str, Any]) -> Decision:
        return self.session.check_tool_call(name, args)

    def record(self, name: str, args: dict[str, Any], result: Any) -> None:
        self.session.record_tool_result(name, args, result)

    def call(self, name: str, fn: Callable[..., Any], args: dict[str, Any]) -> Any:
        """Check, then run ``fn(**args)`` only if allowed, record the result."""
        d = self.check_tool(name, args)
        if not d.allowed:
            if self.on_intervention == "raise":
                raise _EXC[d.action](d)
            return {
                "error": f"not executed: {d.action.value} by supervisor",
                "supervisor": d.agent_feedback(),
                "decision_id": d.id,
            }
        result = fn(**args)
        self.record(name, args, result)
        return result

    def tool(self, fn: F | None = None, *, name: str | None = None) -> Any:
        """Decorator wrapping a tool function. Positional args are bound to parameter names."""

        def wrap(f: F) -> F:
            tool_name = name or f.__name__
            sig = inspect.signature(f)

            @functools.wraps(f)
            def inner(*a: Any, **kw: Any) -> Any:
                bound = sig.bind(*a, **kw)
                bound.apply_defaults()
                return self.call(tool_name, lambda **k: f(**k), dict(bound.arguments))

            return inner  # type: ignore[return-value]

        return wrap(fn) if fn is not None else wrap

    def reply(self, text: str) -> ReplyCheck:
        d = self.session.check_message(text)
        if d.allowed:
            return ReplyCheck(True, text, "", d)
        if self.on_intervention == "raise":
            raise _EXC[d.action](d)
        return ReplyCheck(False, HANDOFF_TEXT if d.action == Action.ESCALATE else "", d.agent_feedback(), d)


class Guard:
    """Entry point: compile a policy and hand out per-conversation sessions."""

    def __init__(
        self,
        policy: Policy | str | Path,
        judge_llm: LLM | None = None,
        audit_path: str | Path | None = None,
        use_judge: bool = True,
        on_intervention: Literal["return", "raise"] = "return",
    ) -> None:
        if isinstance(policy, Policy):
            pol = policy
        elif isinstance(policy, Path) or (isinstance(policy, str) and "\n" not in policy and Path(policy).exists()):
            pol = load_policy(policy)
        else:
            pol = compile_policy(str(policy))
        self.policy = pol
        self.supervisor = Supervisor(
            pol,
            judge=Judge(judge_llm) if judge_llm else None,
            audit=AuditLog(audit_path, pol.source_hash),
            use_judge=use_judge,
        )
        self.on_intervention = on_intervention

    def session(self, session_id: str) -> GuardedSession:
        return GuardedSession(self.supervisor.session(session_id), self.on_intervention)

    @property
    def audit(self) -> AuditLog:
        return self.supervisor.audit
