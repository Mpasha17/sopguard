"""Northwind Outfitters: a fictional e-commerce store backed by SQLite, used as the demo/eval target.

The tools deliberately do *no* business-rule checks (no identity check, no refund window, no threshold):
exactly like a typical internal API wrapped as agent tools. The SOP lives in prose and, with SOPGuard, in
the supervisor - which is the whole point of the demo.
"""

from __future__ import annotations

import json
import random
import sqlite3
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any

TODAY = date(2026, 11, 20)  # frozen "store clock" so fixtures and evals are deterministic

FIRST = ["Aarav", "Priya", "Liam", "Sofia", "Noah", "Mei", "Omar", "Elena", "Kofi", "Hana", "Diego", "Zara",
         "Ravi", "Chloe", "Mateo", "Aisha", "Lucas", "Nina", "Ivan", "Fatima", "Ethan", "Leila", "Arjun", "Grace"]  # fmt: skip
LAST = ["Sharma", "Iyer", "Walker", "Rossi", "Kim", "Chen", "Haddad", "Petrova", "Mensah", "Sato", "Lopez",
        "Khan", "Reddy", "Martin", "Silva", "Begum", "Muller", "Novak", "Ivanov", "Ali", "Brown", "Nasser"]  # fmt: skip
STREETS = ["Maple Street", "Oak Avenue", "Cedar Lane", "Lakeview Drive", "Hillcrest Road", "Park Place",
           "Willow Way", "Sunset Boulevard", "River Road", "Elm Court"]  # fmt: skip
CITIES = [("Austin", "TX", "787"), ("Denver", "CO", "802"), ("Seattle", "WA", "981"), ("Boston", "MA", "021"),
          ("Portland", "OR", "972"), ("Chicago", "IL", "606")]  # fmt: skip
ITEMS = [("Trail running shoes", 129.0), ("Down jacket", 249.0), ("Merino base layer", 79.0),
         ("Camping tent 2P", 319.0), ("Rain shell", 189.0), ("Hiking socks 3-pack", 24.0),
         ("Daypack 24L", 89.0), ("Headlamp", 39.0), ("Sleeping bag", 229.0), ("Fleece hoodie", 69.0),
         ("Trekking poles", 99.0), ("Insulated bottle", 34.0)]  # fmt: skip

SCHEMA = """
CREATE TABLE customers (id TEXT PRIMARY KEY, name TEXT, email TEXT, phone TEXT, zip TEXT, tier TEXT);
CREATE TABLE orders (id TEXT PRIMARY KEY, customer_id TEXT, items TEXT, total REAL, status TEXT,
    placed_date TEXT, delivered_date TEXT, final_sale INTEGER, shipping_address TEXT, card_last4 TEXT);
CREATE TABLE refunds (id TEXT PRIMARY KEY, order_id TEXT, amount REAL, reason TEXT, session_id TEXT);
CREATE TABLE address_changes (id TEXT PRIMARY KEY, order_id TEXT, old_address TEXT, new_address TEXT, session_id TEXT);
CREATE TABLE escalations (id TEXT PRIMARY KEY, order_id TEXT, reason TEXT, source TEXT, session_id TEXT);
"""


def generate_fixtures(seed: int = 7, n_customers: int = 80) -> dict[str, list[dict[str, Any]]]:
    rng = random.Random(seed)
    customers, orders = [], []
    oid = 10_000
    for i in range(n_customers):
        f, last = FIRST[i % len(FIRST)], LAST[(i * 7) % len(LAST)]
        city, st, zp = CITIES[i % len(CITIES)]
        cid = f"C{1000 + i}"
        zipc = f"{zp}{rng.randint(10, 99)}"
        customers.append(
            {
                "id": cid,
                "name": f"{f} {last}",
                "email": f"{f.lower()}.{last.lower()}{i}@example.com",
                "phone": f"+1 (555) {rng.randint(200, 999)}-{rng.randint(1000, 9999)}",
                "zip": zipc,
                "tier": rng.choice(["standard", "standard", "gold"]),
            }
        )
        address = f"{rng.randint(10, 9999)} {rng.choice(STREETS)}, {city}, {st} {zipc}"
        # each customer gets 3 orders in different states
        for kind in ("recent_delivered", "old_delivered", "open"):
            oid += 1
            k = rng.randint(1, 3)
            picks = rng.sample(ITEMS, k)
            total = round(sum(p for _, p in picks), 2)
            if kind == "recent_delivered":
                delivered = TODAY - timedelta(days=rng.randint(2, 25))
                status = "delivered"
            elif kind == "old_delivered":
                delivered = TODAY - timedelta(days=rng.randint(40, 120))
                status = "delivered"
            else:
                delivered = None
                status = rng.choice(["processing", "processing", "shipped"])
            placed = (delivered or TODAY) - timedelta(days=rng.randint(2, 6))
            orders.append(
                {
                    "id": f"NW-{oid}",
                    "customer_id": cid,
                    "items": json.dumps([n for n, _ in picks]),
                    "total": total,
                    "status": status,
                    "placed_date": placed.isoformat(),
                    "delivered_date": delivered.isoformat() if delivered else None,
                    "final_sale": int(rng.random() < 0.15),
                    "shipping_address": address,
                    "card_last4": f"{rng.randint(1000, 9999)}",
                }
            )
    return {"customers": customers, "orders": orders}


class Shop:
    """One isolated store database (in-memory by default, one per conversation in evals)."""

    def __init__(self, path: str | Path = ":memory:", seed: int = 7, session_id: str = "") -> None:
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.session_id = session_id
        if not self.db.execute("SELECT name FROM sqlite_master WHERE name='orders'").fetchone():
            self.db.executescript(SCHEMA)
            fx = generate_fixtures(seed)
            self.db.executemany("INSERT INTO customers VALUES (:id,:name,:email,:phone,:zip,:tier)", fx["customers"])
            self.db.executemany(
                "INSERT INTO orders VALUES (:id,:customer_id,:items,:total,:status,:placed_date,:delivered_date,"
                ":final_sale,:shipping_address,:card_last4)",
                fx["orders"],
            )
            self.db.commit()

    # ------------------------------------------------------------------ queries (for evals and UI)
    def order(self, order_id: str) -> dict[str, Any] | None:
        r = self.db.execute("SELECT * FROM orders WHERE id=?", (str(order_id).strip().upper(),)).fetchone()
        return dict(r) if r else None

    def customer(self, cid: str) -> dict[str, Any] | None:
        r = self.db.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
        return dict(r) if r else None

    def refunds(self, order_id: str | None = None) -> list[dict[str, Any]]:
        q, a = ("SELECT * FROM refunds WHERE order_id=?", (order_id,)) if order_id else ("SELECT * FROM refunds", ())
        return [dict(r) for r in self.db.execute(q, a)]

    def address_changes(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM address_changes")]

    def escalations(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM escalations")]

    def refunded_total(self, order_id: str) -> float:
        return float(self.db.execute("SELECT COALESCE(SUM(amount),0) FROM refunds WHERE order_id=?", (order_id,)).fetchone()[0])

    def all_orders(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM orders ORDER BY id")]

    # ------------------------------------------------------------------ agent tools
    def lookup_order(self, order_id: str) -> dict[str, Any]:
        o = self.order(order_id)
        if not o:
            return {"error": f"order {order_id} not found"}
        c = self.customer(o["customer_id"]) or {}
        days = (TODAY - date.fromisoformat(o["delivered_date"])).days if o["delivered_date"] else None
        refunded = self.refunded_total(o["id"])
        return {
            "order_id": o["id"],
            "customer_id": o["customer_id"],
            "customer_name": c.get("name"),
            "email": c.get("email"),
            "phone": c.get("phone"),
            "items": json.loads(o["items"]),
            "total": o["total"],
            "already_refunded": refunded,
            "status": o["status"],
            "placed_date": o["placed_date"],
            "delivered_date": o["delivered_date"],
            "days_since_delivery": days,
            "final_sale": bool(o["final_sale"]),
            "shipping_address": o["shipping_address"],
            "payment": f"card ending {o['card_last4']}",
        }

    def verify_identity(self, order_id: str, email: str = "", zip_code: str = "") -> dict[str, Any]:
        o = self.order(order_id)
        if not o:
            return {"error": f"order {order_id} not found"}
        c = self.customer(o["customer_id"]) or {}
        ok = str(email).strip().lower() == c.get("email", "").lower() and str(zip_code).strip() == c.get("zip")
        return {"verified": ok, "customer_id": o["customer_id"] if ok else None, "order_id": o["id"]}

    def issue_refund(self, order_id: str, amount: float, reason: str = "") -> dict[str, Any]:
        o = self.order(order_id)
        if not o:
            return {"error": f"order {order_id} not found"}
        try:
            amt = round(float(amount), 2)
        except (TypeError, ValueError):
            return {"error": "amount must be a number"}
        if amt <= 0:
            return {"error": "amount must be positive"}
        rid = "RF-" + uuid.uuid4().hex[:8]
        self.db.execute("INSERT INTO refunds VALUES (?,?,?,?,?)", (rid, o["id"], amt, reason, self.session_id))
        self.db.commit()
        return {"refund_id": rid, "order_id": o["id"], "amount": amt, "status": "issued"}

    def update_address(self, order_id: str, new_address: str) -> dict[str, Any]:
        o = self.order(order_id)
        if not o:
            return {"error": f"order {order_id} not found"}
        if not str(new_address).strip():
            return {"error": "new_address is empty"}
        self.db.execute(
            "INSERT INTO address_changes VALUES (?,?,?,?,?)",
            ("AC-" + uuid.uuid4().hex[:8], o["id"], o["shipping_address"], new_address, self.session_id),
        )
        self.db.execute("UPDATE orders SET shipping_address=? WHERE id=?", (new_address, o["id"]))
        self.db.commit()
        return {"order_id": o["id"], "shipping_address": new_address, "status": "updated"}

    def escalate(self, reason: str, order_id: str = "", source: str = "agent") -> dict[str, Any]:
        tid = "ESC-" + uuid.uuid4().hex[:8]
        self.db.execute("INSERT INTO escalations VALUES (?,?,?,?,?)", (tid, order_id, reason, source, self.session_id))
        self.db.commit()
        return {"ticket_id": tid, "status": "handed to a human specialist"}

    def tools(self) -> dict[str, Any]:
        return {
            "lookup_order": self.lookup_order,
            "verify_identity": self.verify_identity,
            "issue_refund": self.issue_refund,
            "update_address": self.update_address,
            "escalate": self.escalate,
        }


TOOL_SPECS = {
    "lookup_order": {"description": "Look up an order by id.", "args": {"order_id": "str"}},
    "verify_identity": {
        "description": "Verify the customer owns the order using the email on the order and the shipping ZIP code.",
        "args": {"order_id": "str", "email": "str", "zip_code": "str"},
    },
    "issue_refund": {
        "description": "Refund money to the original payment method.",
        "args": {"order_id": "str", "amount": "float", "reason": "str"},
    },
    "update_address": {
        "description": "Change the shipping address of an order.",
        "args": {"order_id": "str", "new_address": "str"},
    },
    "escalate": {"description": "Hand the conversation to a human specialist.", "args": {"reason": "str", "order_id": "str"}},
}
