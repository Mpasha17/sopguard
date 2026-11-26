from __future__ import annotations

from pathlib import Path

import pytest

from sopguard.demo.shop import Shop
from sopguard.policy.compiler import load_policy
from sopguard.runtime.supervisor import Supervisor

SOP = Path(__file__).resolve().parents[1] / "src" / "sopguard" / "demo" / "northwind_sop.md"


@pytest.fixture(scope="session")
def policy():
    return load_policy(SOP)


@pytest.fixture
def shop():
    return Shop()


@pytest.fixture
def sup(policy):
    return Supervisor(policy)


def pick(shop: Shop, *, status="delivered", recent=True, final_sale=False, max_total=200.0, min_total=0.0):
    for o in shop.all_orders():
        lo = shop.lookup_order(o["id"])
        if lo["status"] != status or lo["final_sale"] != final_sale or not (min_total < lo["total"] <= max_total):
            continue
        if status == "delivered" and recent != (lo["days_since_delivery"] <= 30):
            continue
        return lo
    raise LookupError("no matching fixture order")


def verified_session(sup, shop, lo, sid="s1"):
    """A session where the customer gave credentials and the agent looked up + verified the order."""
    s = sup.session(sid)
    c = shop.customer(lo["customer_id"])
    s.record_user_message(f"refund {lo['order_id']} please, my email is {c['email']} and zip {c['zip']}")
    s.record_tool_result("lookup_order", {"order_id": lo["order_id"]}, lo)
    args = {"order_id": lo["order_id"], "email": c["email"], "zip_code": c["zip"]}
    assert s.check_tool_call("verify_identity", args).allowed
    s.record_tool_result("verify_identity", args, shop.verify_identity(**args))
    return s
