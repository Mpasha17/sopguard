import json

import httpx
from conftest import pick
from fastapi.testclient import TestClient

from sopguard.api import create_app


def test_supervision_endpoints(policy, shop):
    c = TestClient(create_app(policy))
    assert c.get("/healthz").json()["ok"] and c.get("/health").json()["ok"]
    assert len(c.get("/v1/policy").json()["policy"]["rules"]) == 14
    lo = pick(shop)
    d = c.post(
        "/v1/sessions/t1/check/tool", json={"tool": "issue_refund", "args": {"order_id": lo["order_id"], "amount": 5}}
    ).json()
    assert d["action"] == "steer" and "verify" in d["feedback"]
    c.post(
        "/v1/sessions/t1/tool_result", json={"tool": "lookup_order", "args": {"order_id": lo["order_id"]}, "result": lo}
    )
    d = c.post("/v1/sessions/t1/check/message", json={"text": f"It ships to {lo['shipping_address']}"}).json()
    assert d["action"] == "steer"
    assert len(c.get("/v1/sessions/t1/audit").json()) == 2


def _upstream(responses):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body)
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": responses[len(seen) - 1]}],
            },
        )

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def tool_call(name, args):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}],
    }


def test_proxy_steers_then_allows(policy, shop):
    lo = pick(shop)
    client, seen = _upstream([
        tool_call("issue_refund", {"order_id": lo["order_id"], "amount": 5}),
        {"role": "assistant", "content": "Could you share the email on the order and your ZIP code?"},
    ])  # fmt: skip
    c = TestClient(create_app(policy, upstream_url="http://up/v1", http_client=client))
    r = c.post(
        "/v1/chat/completions",
        json={"model": "m", "messages": [{"role": "user", "content": f"refund {lo['order_id']}"}]},
        headers={"x-sopguard-session": "p1"},
    )
    assert r.status_code == 200 and r.headers["x-sopguard-action"] == "allow"
    assert "email" in r.json()["choices"][0]["message"]["content"]
    assert (
        len(seen) == 2
        and seen[1]["messages"][-1]["role"] == "system"
        and "SUPERVISOR" in seen[1]["messages"][-1]["content"]
    )


def test_proxy_escalates_and_replays_history(policy, shop):
    lo = pick(shop, min_total=200, max_total=10_000)
    cust = shop.customer(lo["customer_id"])
    history = [
        {"role": "user", "content": f"refund {lo['order_id']}, email {cust['email']} zip {cust['zip']}"},
        tool_call("lookup_order", {"order_id": lo["order_id"]}),
        {"role": "tool", "tool_call_id": "c1", "content": json.dumps(lo)},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c2",
                    "type": "function",
                    "function": {
                        "name": "verify_identity",
                        "arguments": json.dumps(
                            {"order_id": lo["order_id"], "email": cust["email"], "zip_code": cust["zip"]}
                        ),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "c2",
            "content": json.dumps({"verified": True, "customer_id": lo["customer_id"], "order_id": lo["order_id"]}),
        },
    ]
    client, seen = _upstream([tool_call("issue_refund", {"order_id": lo["order_id"], "amount": lo["total"]})])
    c = TestClient(create_app(policy, upstream_url="http://up/v1", http_client=client))
    r = c.post("/v1/chat/completions", json={"model": "m", "messages": history})
    body = r.json()
    assert r.headers["x-sopguard-action"] == "escalate" and len(seen) == 1
    assert "specialist" in body["choices"][0]["message"]["content"]
    assert any("manager-approval-over-200" in cit for cit in body["sopguard"]["citations"])
