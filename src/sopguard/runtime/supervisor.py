"""The supervisor runtime.

Every agent step - a planned tool call or an outgoing message - goes through :meth:`Session.check_tool_call`
or :meth:`Session.check_message` *before* it takes effect. The supervisor:

1. runs deterministic checks compiled from the SOP (ordering, preconditions, argument constraints,
   approval thresholds, limits, forbidden claims, PII) against the tracked task state;
2. only if those pass, asks the LLM judge about the *semantic* rules whose trigger prefilter fired,
   under a latency and token budget with a per-rule fail mode;
3. decides allow / steer / block / escalate, converts repeated steering into escalation, and writes an
   audit record citing the SOP sections it enforced.
"""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from sopguard.llm import LLMError
from sopguard.policy import expr
from sopguard.policy.schema import (
    ConditionRule,
    ForbiddenClaimRule,
    LimitRule,
    PIIRule,
    Policy,
    SemanticRule,
    SequenceRule,
    ThresholdRule,
)
from sopguard.runtime import pii
from sopguard.runtime.audit import AuditLog
from sopguard.runtime.decision import Action, Decision, StepKind, Violation
from sopguard.runtime.judge import Judge, triggered
from sopguard.runtime.state import SessionState

_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="sopguard-judge")


class Supervisor:
    def __init__(
        self,
        policy: Policy,
        judge: Judge | None = None,
        audit: AuditLog | None = None,
        use_judge: bool = True,
    ) -> None:
        self.policy = policy
        self.judge = judge or Judge()
        self.audit = audit or AuditLog(policy_hash=policy.source_hash)
        self.use_judge = use_judge
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def session(self, session_id: str) -> Session:
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = Session(self, session_id)
            return self._sessions[session_id]

    def drop(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)


class Session:
    def __init__(self, sup: Supervisor, session_id: str) -> None:
        self.sup = sup
        self.policy = sup.policy
        self.state = SessionState(session_id=session_id)
        self.decisions: list[Decision] = []
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ recording
    def record_user_message(self, text: str) -> None:
        with self._lock:
            self.state.record_user(text)

    def record_tool_result(self, tool: str, args: dict[str, Any], result: Any) -> None:
        with self._lock:
            self.state.record_tool(tool, args, result)

    def approve(self, rule_id: str, approver: str = "human") -> None:
        """A human approved the parked action (e.g. a refund above the threshold)."""
        with self._lock:
            self.state.approvals.append(rule_id)
            self.state.escalated = False
            self.state.events.append({"role": "supervisor", "text": f"{approver} approved {rule_id}"})

    # ------------------------------------------------------------------ checks
    def check_tool_call(self, tool: str, args: dict[str, Any]) -> Decision:
        with self._lock:
            t0 = time.perf_counter()
            self.state.step_index += 1
            env = self._env({"args": args or {}, "tool": tool})
            violations: list[Violation] = []
            for rule in self.policy.rules_for_tool(tool):
                if isinstance(rule, SemanticRule):
                    continue
                if not self._when(rule, env):
                    continue
                v = self._check_tool_rule(rule, tool, args or {}, env)
                if v:
                    violations.append(v)
            judge_calls = judge_tokens = 0
            if not violations:
                sem = [
                    r
                    for r in self.policy.rules_for_tool(tool)
                    if isinstance(r, SemanticRule) and self._when(r, env) and triggered(r, str(args))
                ]
                if sem:
                    vs, judge_calls, judge_tokens = self._judge(sem, {"type": "tool_call", "tool": tool, "args": args})
                    violations.extend(vs)
            d = self._decide(StepKind.TOOL_CALL, violations, t0, tool=tool, args=args)
            d.judge_calls, d.judge_tokens = judge_calls, judge_tokens
            self._attach_judge_note(d)
            if not d.allowed:
                self.state.events.append(
                    {"role": "supervisor", "text": f"{d.action.value} {tool}({args}): {', '.join(d.citations())}"}
                )
            self._log(d)
            return d

    def check_message(self, text: str) -> Decision:
        with self._lock:
            t0 = time.perf_counter()
            self.state.step_index += 1
            env = self._env({"message": text})
            violations: list[Violation] = []
            for rule in self.policy.message_rules():
                if isinstance(rule, SemanticRule) or not self._when(rule, env):
                    continue
                if isinstance(rule, ForbiddenClaimRule):
                    v = self._check_claim(rule, text, env)
                elif isinstance(rule, PIIRule):
                    v = self._check_pii(rule, text)
                else:  # pragma: no cover - schema prevents this
                    continue
                if v:
                    violations.append(v)
            judge_calls = judge_tokens = 0
            if not violations:
                sem = [
                    r
                    for r in self.policy.message_rules()
                    if isinstance(r, SemanticRule) and self._when(r, env) and triggered(r, text)
                ]
                if sem:
                    vs, judge_calls, judge_tokens = self._judge(sem, {"type": "message", "message": text})
                    violations.extend(vs)
            d = self._decide(StepKind.MESSAGE, violations, t0, message=text)
            d.judge_calls, d.judge_tokens = judge_calls, judge_tokens
            self._attach_judge_note(d)
            if d.allowed:
                self.state.record_agent(text)
            else:
                self.state.events.append(
                    {"role": "supervisor", "text": f"{d.action.value} message: {', '.join(d.citations())}"}
                )
            self._log(d)
            return d

    # ------------------------------------------------------------------ internals
    _judge_note: str = ""

    def _attach_judge_note(self, d: Decision) -> None:
        if self._judge_note:
            d.note = (d.note + "; " if d.note else "") + self._judge_note
            self._judge_note = ""

    def _env(self, values: dict[str, Any]) -> expr.Env:
        return expr.Env(
            {"session": {"id": self.state.session_id}, **values},
            self.state.helpers(self.policy),
            self.policy.lets,
        )

    def _when(self, rule: Any, env: expr.Env) -> bool:
        if not rule.when:
            return True
        try:
            return bool(expr.evaluate(rule.when, env))
        except expr.MissingFact:
            return True  # conservative: if we cannot tell, the rule applies

    def _viol(self, rule: Any, message: str, **evidence: Any) -> Violation:
        return Violation(
            rule_id=rule.id,
            rule_type=rule.type,
            action=rule.on_violation,
            message=message,
            citation=rule.citation,
            guidance=rule.guidance,
            severity=rule.severity,
            evidence=evidence,
        )

    def _check_tool_rule(self, rule: Any, tool: str, args: dict[str, Any], env: expr.Env) -> Violation | None:
        try:
            if isinstance(rule, SequenceRule):
                where: dict[str, Any] = {f"result.{k}": v for k, v in rule.first_result.items()}
                if rule.key:
                    if rule.key not in args:
                        return self._viol(rule, f"{tool} is missing argument {rule.key!r}")
                    where[f"args.{rule.key}"] = args[rule.key]
                if not self.state.successful(rule.first, where):
                    on = f" for {rule.key}={args.get(rule.key)!r}" if rule.key else ""
                    return self._viol(rule, f"{tool} requires a successful {rule.first}{on} first")
                return None
            if isinstance(rule, ConditionRule):
                if not expr.evaluate(rule.require, env):
                    return self._viol(rule, rule.description or f"condition failed: {rule.require}")
                return None
            if isinstance(rule, ThresholdRule):
                val = float(expr.evaluate(rule.value, env))
                if val > rule.max and rule.id not in self.state.approvals:
                    return self._viol(
                        rule, f"{rule.value}={val:g} exceeds {rule.max:g}; needs {rule.approver} approval", value=val
                    )
                return None
            if isinstance(rule, LimitRule):
                lim_where = {f"args.{rule.key}": args.get(rule.key)} if rule.key else None
                n = sum(len(self.state.successful(t, lim_where)) for t in (rule.tools if "*" not in rule.tools else [tool]))
                if n >= rule.max_count:
                    return self._viol(rule, f"limit reached: {n} prior successful {'/'.join(rule.tools)} call(s)")
                return None
        except expr.MissingFact as e:
            return self._viol(rule, f"cannot verify policy: {e}", missing=True)
        except (expr.ExprError, TypeError, ValueError) as e:
            v = self._viol(rule, f"policy evaluation error (fail-closed): {e}")
            v.action = Action.ESCALATE
            return v
        return None

    def _check_claim(self, rule: ForbiddenClaimRule, text: str, env: expr.Env) -> Violation | None:
        for p in rule.patterns:
            m = re.search(p, text, re.I)
            if m:
                if rule.unless:
                    try:
                        if expr.evaluate(rule.unless, env):
                            return None
                    except expr.MissingFact:
                        pass
                return self._viol(rule, f"message contains forbidden claim {m.group(0)!r}", match=m.group(0))
        return None

    def _check_pii(self, rule: PIIRule, text: str) -> Violation | None:
        low = text.lower()
        norm_text = pii.normalize(text)
        # 1. values from tool results flagged as sensitive, with their owner
        owned: list[tuple[str, str, Any]] = []
        for tool, fields in rule.sensitive_fields.items():
            for rec in self.state.successful(tool):
                res = rec.result if isinstance(rec.result, dict) else {}
                for f in fields:
                    val = res.get(f)
                    if isinstance(val, str) and len(val) >= 5 and (val.lower() in low or pii.normalize(val) in norm_text):
                        owned.append((f, val, res.get(rule.owner_field)))
        # 2. pattern detectors (anything not traced to an owner)
        hits = pii.detect(text, rule.detectors)
        for kind, val in hits:
            if kind in rule.always_block:
                return self._viol(rule, f"message contains a full {kind.replace('_', ' ')}", kind=kind)
        helpers = self.state.helpers(self.policy)
        for field, val, owner in owned:
            if helpers["user_said"](val):
                continue
            if rule.allowed_if:
                env = self._env({"owner": owner})
                try:
                    if expr.evaluate(rule.allowed_if, env):
                        continue
                except expr.MissingFact:
                    pass
            return self._viol(rule, f"discloses {field} belonging to {owner!r} who is not verified", field=field)
        owned_vals = {pii.normalize(v) for _f, v, _o in owned}
        for kind, val in hits:
            if pii.normalize(val) in owned_vals or helpers["user_said"](val):
                continue
            if any(pii.normalize(val) in pii.normalize(m) for m in self.state.user_messages):
                continue
            return self._viol(rule, f"message contains an untraced {kind.replace('_', ' ')}", kind=kind)
        return None

    def _judge(self, rules: list[SemanticRule], step: dict[str, Any]) -> tuple[list[Violation], int, int]:
        b = self.policy.budgets
        self._judge_note = ""
        if not self.sup.use_judge:
            return [], 0, 0
        if self.state.judge_tokens >= b.judge_max_tokens_per_session:
            return self._fail(rules, "judge token budget exhausted for this session", "budget"), 0, 0
        fut = _POOL.submit(self.sup.judge.judge, rules, step, self.state.transcript())
        try:
            out, usage = fut.result(timeout=b.judge_timeout_ms / 1000)
        except FutureTimeout:
            return self._fail(rules, f"judge exceeded {b.judge_timeout_ms} ms latency budget", "budget"), 1, 0
        except LLMError as e:
            return self._fail(rules, f"judge unavailable: {str(e)[:120]}", "judge"), 1, 0
        self.state.judge_tokens += usage.tokens
        by_id = {r.id: r for r in rules}
        vs = []
        for v in out.verdicts:
            if v.violated:
                r = by_id[v.rule_id]
                viol = self._viol(r, v.reason or r.description or r.rule)
                viol.source = "judge"
                viol.guidance = v.guidance or r.guidance
                vs.append(viol)
        return vs, 1, usage.tokens

    def _fail(self, rules: list[SemanticRule], why: str, source: str) -> list[Violation]:
        out = []
        for r in rules:
            if r.fail_mode == "closed":
                v = self._viol(r, f"{why}; rule is fail-closed")
                v.action = Action.ESCALATE
                v.source = "budget"
                out.append(v)
        if not out:
            self.state.events.append({"role": "supervisor", "text": f"fail-open: {why}"})
        self._judge_note = f"judge fail-{'closed' if out else 'open'}: {why}"
        return out

    def _decide(self, kind: StepKind, violations: list[Violation], t0: float, **step: Any) -> Decision:
        action = max((v.action for v in violations), key=lambda a: a.rank, default=Action.ALLOW)
        note = ""
        if action == Action.STEER:
            self.state.steer_streak += 1
            if self.state.steer_streak > self.policy.budgets.max_steers_per_step:
                action = Action.ESCALATE
                note = f"steer budget exhausted after {self.state.steer_streak - 1} attempts"
        elif action == Action.ALLOW:
            self.state.steer_streak = 0
        if action == Action.ESCALATE:
            self.state.escalated = True
        seen: set[str] = set()
        guidance = " ".join(
            g for g in (v.guidance for v in violations) if g and not (g in seen or seen.add(g))  # type: ignore[func-returns-value]
        )
        return Decision(
            session_id=self.state.session_id,
            step_index=self.state.step_index,
            kind=kind,
            action=action,
            violations=violations,
            guidance=guidance,
            steer_count=self.state.steer_streak,
            latency_ms=(time.perf_counter() - t0) * 1000,
            note=note,
            **step,
        )

    def _log(self, d: Decision) -> None:
        self.decisions.append(d)
        self.sup.audit.write(d)
