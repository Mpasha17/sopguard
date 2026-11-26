import pytest
from conftest import SOP, pick

from sopguard.sdk import Escalated, Guard, Steered


def test_decorated_tool_is_supervised(shop):
    guard = Guard(SOP)
    s = guard.session("sdk")
    calls = []

    @s.tool
    def issue_refund(order_id: str, amount: float, reason: str = ""):
        calls.append(order_id)
        return {"refund_id": "RF-1"}

    lo = pick(shop)
    out = issue_refund(lo["order_id"], 10)
    assert calls == [] and out["error"].startswith("not executed") and "verify" in out["supervisor"]
    assert guard.audit.summary() == {"steer": 1}


def test_raise_mode_and_reply():
    guard = Guard(SOP, on_intervention="raise")
    s = guard.session("r")
    with pytest.raises(Steered) as ei:
        s.call("issue_refund", lambda **k: None, {"order_id": "NW-10001", "amount": 5})
    assert "verify-before-change" in ei.value.feedback
    assert s.reply("How can I help?").allowed
    with pytest.raises(Steered):
        s.reply("Your refund has been processed.")


def test_reply_escalates_after_budget():
    guard = Guard(SOP)
    s = guard.session("e")
    for _ in range(2):
        assert not s.reply("I've refunded you.").allowed
    r = s.reply("I've refunded you.")
    assert r.escalated and "specialist" in r.text


def test_guard_accepts_inline_policy_text():
    g = Guard("name: inline\nrules:\n  - {id: only-one, type: limit, tools: [ping], max_count: 1}\n")
    s = g.session("i")
    assert s.call("ping", lambda: "pong", {}) == "pong"
    assert "error" in s.call("ping", lambda: "pong", {})
    with pytest.raises(Escalated):
        g2 = Guard(
            "name: x\nrules:\n  - {id: cap, type: threshold, tools: [pay], value: args.n, max: 1}\n",
            on_intervention="raise",
        )
        g2.session("z").call("pay", lambda n: n, {"n": 5})
