"""Run the scenario suite against the three systems and write per-scenario JSONL results.

    python -m evals.run --llm fake                                  # offline, CI
    python -m evals.run --llm gemini:gemini-3.1-flash-lite \\
        --sim-llm gemini:gemini-3.5-flash-lite --out runs/gemini     # real run (resumable)
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from evals.grader import grade, intervention_needed, snapshot
from evals.scenarios import Scenario, build_scenarios
from evals.simulator import CustomerSimulator, make_fake_sim_llm
from sopguard.demo.agent import HANDOFF, SupportAgent, fake_agent_responder
from sopguard.demo.shop import Shop
from sopguard.llm import LLM, LLMError, get_llm
from sopguard.policy.compiler import load_policy
from sopguard.runtime.audit import AuditLog
from sopguard.runtime.judge import Judge, fake_judge_responder
from sopguard.runtime.supervisor import Supervisor

SYSTEMS = ("unsupervised", "prompt_only", "supervised")
SOP = Path(__file__).resolve().parents[1] / "src" / "sopguard" / "demo" / "northwind_sop.md"
_write_lock = threading.Lock()


def run_one(sc: Scenario, system: str, agent_llm: LLM, sim_llm: LLM, sup: Supervisor | None) -> dict[str, Any]:
    t0 = time.perf_counter()
    sid = f"{system}:{sc.id}"
    shop, base = Shop(session_id=sid), Shop()
    session = sup.session(sid) if sup else None
    agent = SupportAgent(agent_llm, shop, system, session)  # type: ignore[arg-type]
    sim = CustomerSimulator(sim_llm, sc)
    events: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    agent_tokens = agent_calls = sim_tokens = 0
    agent_cost = 0.0
    degraded: str | None = None
    msg = sim.opening()
    turns = 0
    try:
        for turns in range(1, sc.max_turns + 1):
            events.append({"role": "customer", "text": msg})
            r = agent.respond(msg)
            agent_calls += len(r.usage)
            agent_tokens += sum(u.tokens for u in r.usage)
            agent_cost += sum(u.cost_usd for u in r.usage)
            for ev in r.events:
                if ev.kind == "tool":
                    events.append({"role": "tool", **ev.data})
                elif ev.kind == "agent":
                    events.append({"role": "agent", "text": ev.text})
                elif ev.kind == "system" and ev.text == HANDOFF:
                    events.append({"role": "agent", "text": HANDOFF})
                elif ev.kind == "supervisor":
                    d = ev.data["decision"]
                    d["_events_before"] = len(events)
                    events.append({"role": "supervisor", "decision_id": d["id"]})
            for d in r.decisions:
                dd = d.model_dump(mode="json")
                decisions.append(dd)
            # attach the event index for interventions
            idx = {e.get("decision_id"): i for i, e in enumerate(events) if e["role"] == "supervisor"}
            for dd in decisions:
                if dd["id"] in idx:
                    dd["_events_before"] = idx[dd["id"]]
            if r.error:
                degraded = f"agent LLM error: {r.error[:200]}"
                break
            if agent.done:
                break
            st, u = sim.next(r.reply)
            sim_tokens += u.tokens
            if st.done or not st.message.strip():
                break
            msg = st.message
    except LLMError as e:
        degraded = f"simulator LLM error: {str(e)[:200]}"
    except Exception as e:  # noqa: BLE001 - any crash marks the run degraded, never silently passes
        degraded = f"crash: {e!r} {traceback.format_exc()[-400:]}"
    if session is not None:
        judge_fail = [e["text"] for e in session.state.events if e["role"] == "supervisor" and "judge unavailable" in e["text"]]
        if judge_fail and not degraded:
            degraded = f"judge LLM error: {judge_fail[0][:200]}"
    db = snapshot(shop)
    g = grade(sc, [e for e in events if e["role"] != "supervisor"], db, base)
    # interventions + counterfactual necessity
    sup_events = [e for e in events]
    for dd in decisions:
        if dd["action"] != "allow":
            before = [e for e in sup_events[: dd.get("_events_before", len(sup_events))] if e["role"] != "supervisor"]
            dd["needed"] = intervention_needed(sc, base, before, dd)
    return {
        "scenario": sc.id,
        "category": sc.category,
        "expected": sc.expected,
        "adversarial": sc.adversarial,
        "system": system,
        "violations": g.violations,
        "success": g.success,
        "escalated": g.escalated,
        "outcome": g.outcome,
        "turns": turns,
        "agent_calls": agent_calls,
        "agent_tokens": agent_tokens,
        "agent_cost_usd": round(agent_cost, 6),
        "sim_tokens": sim_tokens,
        "decisions": [
            {k: dd.get(k) for k in ("id", "kind", "action", "tool", "args", "message", "latency_ms", "judge_calls", "judge_tokens", "steer_count", "needed", "note")}
            | {"rules": [v["rule_id"] for v in dd["violations"]], "sources": [v["source"] for v in dd["violations"]]}
            for dd in decisions
        ],
        "events": events,
        "db": db,
        "degraded": degraded,
        "wall_s": round(time.perf_counter() - t0, 2),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--llm", default="fake", help="agent LLM: fake | gemini[:model] | groq[:model] | ollama[:model]")
    ap.add_argument("--judge-llm", default=None, help="supervisor judge LLM (default: same as --llm)")
    ap.add_argument("--sim-llm", default=None, help="customer simulator LLM (default: same as --llm)")
    ap.add_argument("--systems", default=",".join(SYSTEMS))
    ap.add_argument("--out", default="runs/fake")
    ap.add_argument("--limit", type=int, default=0, help="only the first N scenarios (smoke tests)")
    ap.add_argument("--only", default="", help="comma-separated scenario ids or categories")
    ap.add_argument("--seed", type=int, default=11, help="scenario seed: 11 = dev suite (used while building), 2026 = held-out")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--rpm", type=float, default=None, help="client-side requests/minute per LLM client")
    ap.add_argument("--fresh", action="store_true", help="ignore existing results instead of resuming")
    ap.add_argument("--retry-degraded", action="store_true", help="re-run scenarios previously marked degraded")
    a = ap.parse_args(argv)

    fake = a.llm == "fake"
    agent_llm = get_llm(a.llm, fake=fake_agent_responder, rpm=a.rpm)  # type: ignore[arg-type]
    judge_spec = a.judge_llm or a.llm
    judge_llm = get_llm(judge_spec, fake=fake_judge_responder, rpm=a.rpm)  # type: ignore[arg-type]
    sim_spec = a.sim_llm or a.llm
    sim_llm = make_fake_sim_llm() if sim_spec == "fake" else get_llm(sim_spec, rpm=a.rpm)

    scenarios = build_scenarios(a.seed)
    if a.only:
        keys = set(a.only.split(","))
        scenarios = [s for s in scenarios if s.id in keys or s.category in keys]
    if a.limit:
        scenarios = scenarios[: a.limit]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    meta = {
        "agent_llm": agent_llm.describe(), "judge_llm": judge_llm.describe(), "sim_llm": sim_llm.describe(),
        "n_scenarios": len(scenarios), "seed": a.seed, "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "policy": str(SOP.name),
    }  # fmt: skip
    (out / "meta.json").write_text(json.dumps(meta, indent=2))
    policy = load_policy(SOP)
    for system in a.systems.split(","):
        path = out / f"{system}.jsonl"
        done: dict[str, dict[str, Any]] = {}
        if path.exists() and not a.fresh:
            for line in path.read_text().splitlines():
                rec = json.loads(line)
                if a.retry_degraded and rec.get("degraded"):
                    continue
                done[rec["scenario"]] = rec
        if a.fresh or a.retry_degraded:
            path.write_text("".join(json.dumps(r) + "\n" for r in done.values()))
        todo = [s for s in scenarios if s.id not in done]
        sup = None
        if system == "supervised":
            sup = Supervisor(policy, judge=Judge(judge_llm), audit=AuditLog(out / "audit.jsonl", policy.source_hash))
        print(f"[{system}] {len(done)} done, {len(todo)} to run", file=sys.stderr)
        with ThreadPoolExecutor(max_workers=max(1, a.workers)) as ex:
            futs = {ex.submit(run_one, s, system, agent_llm, sim_llm, sup): s for s in todo}
            for i, f in enumerate(as_completed(futs), 1):
                rec = f.result()
                with _write_lock, path.open("a") as fh:
                    fh.write(json.dumps(rec, default=str) + "\n")
                flag = "DEGRADED " + rec["degraded"][:80] if rec["degraded"] else ""
                print(
                    f"[{system}] {i}/{len(todo)} {rec['scenario']:<24} success={rec['success']!s:<5} "
                    f"viol={rec['violations']} {rec['wall_s']}s {flag}",
                    file=sys.stderr,
                    flush=True,
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
