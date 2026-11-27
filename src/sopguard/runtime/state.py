"""Per-conversation task state the supervisor tracks across steps, plus the helper functions policy
expressions can call (``called``, ``verified``, ``last_result`` ...)."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from sopguard.policy.schema import Policy
from sopguard.runtime import pii


class ToolRecord(BaseModel):
    step_index: int
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    ok: bool = True


def _dig(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return _MISSING
    return cur


_MISSING = object()


def result_ok(result: Any) -> bool:
    if isinstance(result, dict):
        if result.get("error") or result.get("ok") is False or result.get("status") == "error":
            return False
    return True


class SessionState(BaseModel):
    session_id: str
    tool_calls: list[ToolRecord] = Field(default_factory=list)
    user_messages: list[str] = Field(default_factory=list)
    agent_messages: list[str] = Field(default_factory=list)
    approvals: list[str] = Field(default_factory=list)  # rule ids (optionally rule_id:key) approved by a human
    step_index: int = 0
    steer_streak: int = 0
    judge_tokens: int = 0
    escalated: bool = False
    events: list[dict[str, str]] = Field(default_factory=list)  # ordered transcript (customer/agent/tool)

    # ---- mutation -------------------------------------------------------------------------------------
    def record_tool(self, name: str, args: dict[str, Any], result: Any) -> ToolRecord:
        rec = ToolRecord(step_index=self.step_index, name=name, args=dict(args), result=result, ok=result_ok(result))
        self.tool_calls.append(rec)
        self.events.append({"role": "tool", "text": f"{name}({args}) -> {str(result)[:300]}"})
        return rec

    def record_user(self, text: str) -> None:
        self.user_messages.append(text)
        self.events.append({"role": "customer", "text": text})

    def record_agent(self, text: str) -> None:
        self.agent_messages.append(text)
        self.events.append({"role": "agent", "text": text})

    # ---- queries used by policy helpers -----------------------------------------------------------------
    def _match(self, rec: ToolRecord, where: dict[str, Any] | None) -> bool:
        if not where:
            return True
        view = {"args": rec.args, "result": rec.result}
        for path, want in where.items():
            got = _dig(view, path)
            if got is _MISSING:
                return False
            if isinstance(want, str) and isinstance(got, str):
                if want.strip().lower() != got.strip().lower():
                    return False
            elif got != want:
                return False
        return True

    def successful(self, tool: str, where: dict[str, Any] | None = None) -> list[ToolRecord]:
        return [r for r in self.tool_calls if r.name == tool and r.ok and self._match(r, where)]

    def helpers(self, policy: Policy) -> dict[str, Any]:
        ident = policy.identity

        def called(tool: str, where: dict[str, Any] | None = None) -> bool:
            return bool(self.successful(tool, where))

        def count(tool: str, where: dict[str, Any] | None = None) -> int:
            return len(self.successful(tool, where))

        def last_result(tool: str, where: dict[str, Any] | None = None) -> Any:
            recs = self.successful(tool, where)
            return recs[-1].result if recs else None

        def verified(subject: Any) -> bool:
            if subject is None:
                return False
            return bool(
                self.successful(
                    ident.tool, {f"result.{ident.success_field}": True, f"result.{ident.subject_field}": subject}
                )
            )

        def user_said(value: Any) -> bool:
            v = str(value).strip().lower()
            return bool(v) and any(v in m.lower() for m in self.user_messages)

        def sum_args(tool: str, field: str, where: dict[str, Any] | None = None) -> float:
            return float(sum(float(r.args.get(field) or 0) for r in self.successful(tool, where)))

        def approved(rule_id: str) -> bool:
            return rule_id in self.approvals

        def contains_any(hay: Any, needles: Any) -> bool:
            h = str(hay or "").lower()
            return any(str(n).lower() in h for n in (needles or []))

        def days_between(a: str, b: str) -> int:
            return (date.fromisoformat(str(b)[:10]) - date.fromisoformat(str(a)[:10])).days

        return {
            "called": called,
            "count": count,
            "last_result": last_result,
            "verified": verified,
            "user_said": user_said,
            "sum_args": sum_args,
            "approved": approved,
            "lower": lambda s: str(s).lower(),
            "len": len,
            "abs": abs,
            "min": min,
            "max": max,
            "contains": lambda hay, needle: str(needle).lower() in str(hay).lower(),
            "days_between": days_between,
            "contains_any": contains_any,
            "matches": lambda text, pattern: bool(re.search(str(pattern), str(text or ""), re.I)),
            "has_pii": lambda text, kind="card_number": bool(pii.detect(str(text or ""), [kind])),
        }

    def transcript(self, limit: int = 12) -> list[dict[str, str]]:
        """Compact recent context for the semantic judge."""
        return [{"role": e["role"], "text": e["text"][:500]} for e in self.events[-limit:]]
