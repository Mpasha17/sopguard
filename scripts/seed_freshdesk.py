"""Seed a real Freshdesk help desk with the SOPGuard evaluation tickets, contacts and help-center articles.

    set -a; . ./.env; set +a          # FRESHDESK_DOMAIN, FRESHDESK_API_KEY
    python scripts/seed_freshdesk.py --run r1 --arms unsupervised,prompt_only,supervised

Idempotent: contacts are matched by email, articles by title, tickets by their ``scn-*``/``arm-*``/``run-*``
tags, so re-running only creates what is missing. Every request is paced under the account's per-minute
limit and 429s are retried. Tickets that are not tagged ``sopguard-eval`` (e.g. Freshdesk's sample tickets) are
never touched. Writes the ticket manifest to ``runs/freshdesk/<run>/manifest.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.freshdesk_scenarios import ARMS, P, S, Scenario  # noqa: E402
from sopguard.integrations.freshdesk import FreshdeskClient, FreshdeskError, html_body  # noqa: E402
from sopguard.integrations.freshdesk_agent import KB, KB_CATEGORY  # noqa: E402

EVAL_TAG = "sopguard-eval"


def scenario_tags(s: Scenario, arm: str, run: str) -> list[str]:
    return [EVAL_TAG, f"scn-{s.id}", f"arm-{arm}", f"run-{run}"]


def seed_kb(fd: FreshdeskClient, log: Any = print) -> dict[str, int]:
    cat = next((c for c in fd.solution_categories() if c.get("name") == KB_CATEGORY), None)
    if cat is None:
        cat = fd.create_category(name=KB_CATEGORY, description="Help articles cited by the supervised support agent")
    folder = next((f for f in fd.solution_folders(cat["id"]) if f.get("name") == "Brightdesk Help Center"), None)
    if folder is None:
        folder = fd.create_folder(cat["id"], name="Brightdesk Help Center", visibility=1,
                                  description="Billing, account and product help")  # fmt: skip
    have = {a.get("title", "").split(" ")[0]: a["id"] for a in fd.folder_articles(folder["id"])}
    out = {}
    author = int(os.environ["FRESHDESK_KB_AUTHOR_ID"]) if os.getenv("FRESHDESK_KB_AUTHOR_ID") else None
    for a in KB:
        if a["id"] in have:
            out[a["id"]] = have[a["id"]]
            continue
        fields = {"title": f"{a['id']} {a['title']}", "description": html_body(a["body"]), "status": 2}
        if author:
            fields["agent_id"] = author
        art = fd.create_article(folder["id"], **fields)
        out[a["id"]] = art["id"]
        log(f"  article {a['id']} -> {art['id']}")
    return out


def seed(
    fd: FreshdeskClient, run: str, arms: tuple[str, ...] = ARMS, scenarios: list[Scenario] | None = None,
    out_dir: Path | None = None, log: Any = print,
) -> dict[str, dict[str, int]]:  # fmt: skip
    scenarios = scenarios or S
    log(f"seeding run={run} arms={arms} scenarios={len(scenarios)} on {fd.domain}")
    try:
        seed_kb(fd, log)
    except FreshdeskError as e:  # service-account keys cannot author articles; the agent then uses the built-in KB copy
        log(f"  ! help-center articles not published ({str(e)[:120]}); using the built-in KB")
    contacts = {(c.get("email") or "").lower(): c for c in fd.list_contacts()}
    existing: dict[tuple[str, str], int] = {}
    for t in fd.list_tickets():
        tags = t.get("tags") or []
        if EVAL_TAG in tags and f"run-{run}" in tags:
            scn = next((x[4:] for x in tags if x.startswith("scn-")), None)
            arm = next((x[4:] for x in tags if x.startswith("arm-")), None)
            if scn and arm:
                existing[(arm, scn)] = t["id"]
    manifest: dict[str, dict[str, int]] = {a: {} for a in arms}
    needed = {s.requester for s in scenarios} | {s.victim for s in scenarios if s.victim}
    for arm in arms:
        for slug in sorted(needed):
            p = P[slug]
            email = p.email(arm)
            if email in contacts:
                continue
            fields: dict[str, Any] = {"name": p.name, "email": email, "description": p.description() if p.plan else ""}
            if p.phone:
                fields["phone"] = p.phone
            contacts[email] = fd.create_contact(**fields)
            log(f"  contact {email}")
        for s in scenarios:
            if (arm, s.id) in existing:
                manifest[arm][s.id] = existing[(arm, s.id)]
                continue
            req = contacts[P[s.requester].email(arm)]
            body = s.body.replace("{arm}", arm)
            if s.followup:  # API keys cannot post as the customer, so the follow-up is part of the message
                body += "\n\n" + s.followup
            t = fd.create_ticket(requester_id=req["id"], subject=s.subject, description=html_body(body), status=2,
                                 priority=2, source=1, tags=scenario_tags(s, arm, run))  # fmt: skip
            manifest[arm][s.id] = t["id"]
            log(f"  ticket {arm}/{s.id} -> #{t['id']}")
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "manifest.json").write_text(
            json.dumps({"run": run, "domain": fd.domain, "tickets": manifest}, indent=2)
        )
    log(f"done: {sum(len(v) for v in manifest.values())} tickets, {fd.calls} API calls")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", default="r1", help="run id; tickets are tagged run-<id>")
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--only", default="", help="comma-separated scenario ids (default: all)")
    ap.add_argument("--rpm", type=float, default=45)
    a = ap.parse_args(argv)
    fd = FreshdeskClient.from_env(rpm=a.rpm)
    only = {x for x in a.only.split(",") if x}
    seed(fd, a.run, tuple(x for x in a.arms.split(",") if x), [s for s in S if not only or s.id in only],
         Path("runs/freshdesk") / a.run)  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
