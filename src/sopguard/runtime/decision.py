"""Decision types returned by the supervisor for every intercepted step."""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Action(str, Enum):
    ALLOW = "allow"
    STEER = "steer"  # do not execute; tell the agent what is wrong and let it retry
    BLOCK = "block"  # do not execute; the agent may continue but must not retry this step as-is
    ESCALATE = "escalate"  # hand the conversation to a human (or park the step for approval)

    @property
    def rank(self) -> int:
        return {"allow": 0, "steer": 1, "block": 2, "escalate": 3}[self.value]


class Violation(BaseModel):
    rule_id: str
    rule_type: str
    action: Action
    message: str
    citation: str = ""
    guidance: str = ""
    severity: str = "high"
    source: Literal["deterministic", "judge", "budget"] = "deterministic"
    evidence: dict[str, Any] = Field(default_factory=dict)


class StepKind(str, Enum):
    TOOL_CALL = "tool_call"
    MESSAGE = "message"


class Decision(BaseModel):
    id: str = Field(default_factory=lambda: "dec_" + uuid.uuid4().hex[:12])
    session_id: str
    step_index: int
    kind: StepKind
    action: Action
    tool: str | None = None
    args: dict[str, Any] | None = None
    message: str | None = None
    violations: list[Violation] = Field(default_factory=list)
    guidance: str = ""
    steer_count: int = 0
    latency_ms: float = 0.0
    judge_calls: int = 0
    judge_tokens: int = 0
    ts: float = Field(default_factory=time.time)
    note: str = ""

    @property
    def allowed(self) -> bool:
        return self.action == Action.ALLOW

    def citations(self) -> list[str]:
        return [f"{v.rule_id} ({v.citation})" if v.citation else v.rule_id for v in self.violations]

    def agent_feedback(self) -> str:
        """The text injected back into the agent's context when a step is not allowed."""
        if self.action == Action.ALLOW:
            return ""
        head = {
            Action.STEER: "SUPERVISOR: your last step was NOT executed because it breaks company policy.",
            Action.BLOCK: "SUPERVISOR: this action is blocked by company policy and must not be retried.",
            Action.ESCALATE: "SUPERVISOR: this needs a human. Tell the customer a specialist will follow up; do not act further.",
        }[self.action]
        lines = [head]
        for v in self.violations:
            cite = f" [{v.citation}]" if v.citation else ""
            lines.append(f"- {v.rule_id}{cite}: {v.message}")
        if self.guidance:
            lines.append(f"What to do instead: {self.guidance}")
        return "\n".join(lines)
