"""Typed policy model. A compiled SOP is a :class:`Policy` made of typed rules."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from sopguard.policy import expr
from sopguard.runtime.decision import Action

Target = Literal["tool_call", "message"]


class _Rule(BaseModel):
    id: str
    description: str = ""
    on_violation: Action = Action.STEER
    guidance: str = ""
    severity: Literal["low", "medium", "high", "critical"] = "high"
    citation: str = ""  # filled by the compiler from the SOP heading the rule sits under
    tools: list[str] = Field(default_factory=list)  # tool-call rules: which tools ("*" = all)
    when: str | None = None  # optional guard expression; rule only applies when it is truthy

    model_config = {"extra": "forbid"}

    @property
    def target(self) -> Target:
        return "tool_call"

    def applies_to_tool(self, name: str) -> bool:
        return "*" in self.tools or name in self.tools

    def expressions(self) -> list[str]:
        return [self.when] if self.when else []


class SequenceRule(_Rule):
    """``then`` tools may only run after a successful prior call to ``first`` (optionally on the same key)."""

    type: Literal["sequence"]
    first: str
    key: str | None = None  # argument name that must match between the two calls (e.g. order_id)
    first_result: dict[str, object] = Field(default_factory=dict)  # required fields of the first call's result


class ConditionRule(_Rule):
    """``require`` must evaluate truthy before the tool call runs. Covers preconditions and argument constraints."""

    type: Literal["precondition", "arg_constraint"]
    require: str

    def expressions(self) -> list[str]:
        return super().expressions() + [self.require]


class ThresholdRule(_Rule):
    """Numeric approval limit, e.g. refunds above $200 need a manager."""

    type: Literal["threshold"]
    value: str  # expression, e.g. args.amount
    max: float
    approver: str = "manager"
    on_violation: Action = Action.ESCALATE

    def expressions(self) -> list[str]:
        return super().expressions() + [self.value]


class LimitRule(_Rule):
    """At most ``max_count`` successful calls of the tools per session (optionally per key)."""

    type: Literal["limit"]
    max_count: int = 1
    key: str | None = None
    on_violation: Action = Action.BLOCK


class _MessageRule(_Rule):
    @property
    def target(self) -> Target:
        return "message"


class ForbiddenClaimRule(_MessageRule):
    """Outgoing messages must not match ``patterns`` (regex, case-insensitive) unless ``unless`` holds."""

    type: Literal["forbidden_claim"]
    patterns: list[str]
    unless: str | None = None

    def expressions(self) -> list[str]:
        return super().expressions() + ([self.unless] if self.unless else [])

    @field_validator("patterns")
    @classmethod
    def _compile(cls, v: list[str]) -> list[str]:
        import re

        for p in v:
            re.compile(p)
        return v


PIIDetector = Literal["card_number", "email", "phone", "street_address"]


class PIIRule(_MessageRule):
    """No personal data in outgoing messages unless the owner is verified or the customer said it first."""

    type: Literal["pii"]
    detectors: list[PIIDetector] = Field(default_factory=lambda: ["card_number", "email", "phone"])
    sensitive_fields: dict[str, list[str]] = Field(default_factory=dict)  # tool -> result fields holding PII
    owner_field: str = "customer_id"
    allowed_if: str | None = None  # expression with ``owner`` bound, e.g. verified(owner)
    always_block: list[PIIDetector] = Field(default_factory=lambda: ["card_number"])
    on_violation: Action = Action.STEER

    def expressions(self) -> list[str]:
        return super().expressions() + ([self.allowed_if] if self.allowed_if else [])


class SemanticRule(_Rule):
    """Judged by an LLM. Only for rules that cannot be decided deterministically."""

    type: Literal["semantic"]
    applies_to: Target = "message"
    rule: str  # the natural-language rule given to the judge
    triggers: list[str] = Field(default_factory=list)  # cheap prefilter (substring/regex); empty = always judge
    violation_examples: list[str] = Field(default_factory=list)
    ok_examples: list[str] = Field(default_factory=list)
    fail_mode: Literal["open", "closed"] = "open"  # what to do if the judge errors or exceeds budget

    @property
    def target(self) -> Target:
        return self.applies_to


Rule = Annotated[
    SequenceRule | ConditionRule | ThresholdRule | LimitRule | ForbiddenClaimRule | PIIRule | SemanticRule,
    Field(discriminator="type"),
]


class Budgets(BaseModel):
    max_steers_per_step: int = 2  # consecutive steers before the supervisor escalates
    judge_timeout_ms: int = 4000
    judge_max_tokens_per_session: int = 60_000
    model_config = {"extra": "forbid"}


class IdentityConfig(BaseModel):
    """How the runtime knows a subject (customer) is verified: a successful call to ``tool`` whose result has
    ``success_field`` truthy and names the subject in ``subject_field``."""

    tool: str = "verify_identity"
    success_field: str = "verified"
    subject_field: str = "customer_id"
    model_config = {"extra": "forbid"}


class Policy(BaseModel):
    name: str
    version: str = "1"
    description: str = ""
    tools: dict[str, str] = Field(default_factory=dict)  # declared tool catalogue (name -> description)
    lets: dict[str, str] = Field(default_factory=dict)  # reusable named expressions
    rules: list[Rule]
    budgets: Budgets = Field(default_factory=Budgets)
    identity: IdentityConfig = Field(default_factory=IdentityConfig)
    source_hash: str = ""

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _validate(self) -> Policy:
        seen: set[str] = set()
        for r in self.rules:
            if r.id in seen:
                raise ValueError(f"duplicate rule id {r.id!r}")
            seen.add(r.id)
            for e in r.expressions():
                expr.parse(e)
            if self.tools:
                unknown = [t for t in r.tools if t != "*" and t not in self.tools]
                if isinstance(r, SequenceRule) and r.first not in self.tools:
                    unknown.append(r.first)
                if unknown:
                    raise ValueError(f"rule {r.id!r} references undeclared tools {unknown}")
        for name, e in self.lets.items():
            expr.parse(e)
            if not name.isidentifier():
                raise ValueError(f"let name {name!r} is not an identifier")
        return self

    def rules_for_tool(self, tool: str) -> list[Rule]:
        return [r for r in self.rules if r.target == "tool_call" and r.applies_to_tool(tool)]

    def message_rules(self) -> list[Rule]:
        return [r for r in self.rules if r.target == "message"]
