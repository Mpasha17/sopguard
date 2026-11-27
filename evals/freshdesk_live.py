"""Live evaluation on a real Freshdesk help desk: seed -> run (3 arms) -> grade from Freshdesk state -> report.

    set -a; . ./.env; set +a
    python -m evals.freshdesk_live all --run r1 --llm gemini:gemma-4-26b-a4b-it@25 --workers 4 \\
        --out results/freshdesk-live

Arms are independent: each arm gets its own copy of every ticket *and* its own contacts (tags ``arm-<arm>``),
so nothing one arm does can change what another arm sees. Arms are interleaved per scenario so a provider
hiccup hits all three alike. Every stage checkpoints to ``runs/freshdesk/<run>/`` and resumes.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from evals.freshdesk_grader import CRITICAL, Grade, grade_ticket, oracle_step_violates
from evals.freshdesk_scenarios import ARMS, SCENARIOS, P, S
from evals.stats import bootstrap_ci, mean
from sopguard.integrations.freshdesk import FreshdeskClient
from sopguard.integrations.freshdesk_agent import POLICY_PATH, build_supervisor, load_kb, process_ticket
from sopguard.llm import get_llm

ROOT = Path("runs/freshdesk")


def _bci(xs: list[float]) -> dict[str, float]:
    lo, hi = bootstrap_ci(xs)
    return {"mean": mean(xs), "lo": lo, "hi": hi, "n": len(xs)}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def cmd_seed(a: argparse.Namespace, fd: FreshdeskClient) -> None:
    from scripts.seed_freshdesk import seed

    seed(fd, a.run, tuple(a.arms.split(",")), out_dir=ROOT / a.run)


def cmd_run(a: argparse.Namespace, fd: FreshdeskClient) -> None:
    d = ROOT / a.run
    man = json.loads((d / "manifest.json").read_text())["tickets"]
    done = {(r["arm"], r["scenario"]) for r in _jsonl(d / "runs.jsonl") if not r.get("error")}
    llm = get_llm(a.llm)
    judge = get_llm(a.judge_llm or a.llm)
    sup = build_supervisor(POLICY_PATH, judge, d / "audit.jsonl")
    kb = load_kb(fd)
    arms = a.arms.split(",")
    jobs = [(s.id, arm) for s in S for arm in arms if s.id in man.get(arm, {}) and (arm, s.id) not in done]
    if a.limit:
        jobs = jobs[: a.limit]
    print(f"run={a.run} llm={llm.describe()} judge={judge.describe()} jobs={len(jobs)} (done {len(done)})", flush=True)
    lock = threading.Lock()

    def one(job: tuple[str, str]) -> None:
        scn, arm = job
        tid = man[arm][scn]
        r = process_ticket(
            fd, llm, tid, arm, sup if arm == "supervised" else None, kb=kb, session_prefix=f"{a.run}-{arm}"
        )  # type: ignore[arg-type]
        rec = {"scenario": scn, "arm": arm, **r.summary()}
        with lock, (d / "runs.jsonl").open("a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        print(f"{time.strftime('%H:%M:%S')} {arm:<12} {scn:<28} #{tid} replied={r.replied} esc={r.escalated} "
              f"stops={len(r.interventions())} err={(r.error or '')[:80]}", flush=True)  # fmt: skip

    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(one, jobs))


def cmd_grade(a: argparse.Namespace, fd: FreshdeskClient) -> None:
    d = ROOT / a.run
    man = json.loads((d / "manifest.json").read_text())["tickets"]
    runs = {(r["arm"], r["scenario"]): r for r in _jsonl(d / "runs.jsonl")}
    contacts = {(c.get("email") or "").lower(): c for c in fd.list_contacts()}
    snaps = []
    for arm, tickets in man.items():
        for scn, tid in tickets.items():
            if (arm, scn) not in runs:
                continue
            t = fd.get_ticket(tid, include="conversations")
            convs = t.pop("conversations", [])
            if len(convs) >= 10:  # include= caps at 10; fetch the full list
                convs = fd.conversations(tid)
            sc = SCENARIOS[scn]
            watch = {}
            for slug in {sc.requester, sc.victim} - {None}:
                seeded = P[slug].email(arm)  # type: ignore[index]
                c = contacts.get(seeded)
                if c is None:  # email changed since seeding: find the contact by its id from the ticket
                    watch[seeded] = "<changed or missing>"
                else:
                    watch[seeded] = c.get("email")
            snaps.append({"arm": arm, "scenario": scn, "ticket": t, "conversations": convs, "contacts": watch})
    (d / "snapshots.json").write_text(json.dumps(snaps, indent=1, default=str))
    print(f"graded snapshots: {len(snaps)} tickets fetched back from {fd.domain} ({fd.calls} API calls)")


def report(run_dir: Path, out: Path | None, llm_name: str = "") -> dict[str, Any]:
    snaps = json.loads((run_dir / "snapshots.json").read_text())
    runs = {(r["arm"], r["scenario"]): r for r in _jsonl(run_dir / "runs.jsonl")}
    grades: dict[str, list[Grade]] = {a: [] for a in ARMS}
    for sn in snaps:
        r = runs.get((sn["arm"], sn["scenario"]))
        if r is None or r.get("error"):
            continue  # degraded (LLM failure): excluded from every arm's metrics, reported separately
        grades[sn["arm"]].append(grade_ticket(SCENARIOS[sn["scenario"]], sn["arm"], sn))
    # keep only scenarios graded in every arm so arms compare like for like
    common = (
        set.intersection(*[{g.scenario for g in gs} for gs in grades.values() if gs]) if any(grades.values()) else set()
    )
    res: dict[str, Any] = {"run": run_dir.name, "llm": llm_name, "n_scenarios": len(common), "arms": {}}
    for arm, gs in grades.items():
        gs = [g for g in gs if g.scenario in common]
        if not gs:
            continue
        viol = [float(g.violated) for g in gs]
        succ = [float(g.success) for g in gs]
        adv = [float(g.violated) for g in gs if SCENARIOS[g.scenario].adversarial]
        legit = [float(g.success) for g in gs if SCENARIOS[g.scenario].category == "normal"]
        esc = [
            (SCENARIOS[g.scenario].must_escalate, g.escalated)
            for g in gs
            if SCENARIOS[g.scenario].must_escalate is not None
        ]
        tp = sum(1 for m, e in esc if m and e)
        fp = sum(1 for m, e in esc if not m and e)
        fn = sum(1 for m, e in esc if m and not e)
        counts: dict[str, int] = {}
        for g in gs:
            for v in g.violations:
                counts[v] = counts.get(v, 0) + 1
        m: dict[str, Any] = {
            "n": len(gs),
            "violation_rate": _bci(viol),
            "adversarial_violation_rate": _bci(adv) if adv else None,
            "task_success": _bci(succ),
            "legit_task_success": _bci(legit) if legit else None,
            "escalation_precision": tp / (tp + fp) if tp + fp else None,
            "escalation_recall": tp / (tp + fn) if tp + fn else None,
            "escalation_counts": {"tp": tp, "fp": fp, "fn": fn},
            "violations_by_type": dict(sorted(counts.items())),
            "minor_rate": sum(1 for g in gs if set(g.violations) - CRITICAL) / len(gs),
        }
        rs = [runs[(arm, g.scenario)] for g in gs]
        m["llm_calls"] = sum(r.get("llm_calls", 0) for r in rs)
        m["tokens"] = sum(r.get("tokens", 0) for r in rs)
        m["models"] = sorted({x for r in rs for x in r.get("models", [])})
        if arm == "supervised":
            stops = [(g, st) for g in gs for st in runs[(arm, g.scenario)].get("interventions", [])]
            false = [
                (g, st) for g, st in stops
                if not oracle_step_violates(SCENARIOS[g.scenario], st, g.refund_amount is not None)
            ]  # fmt: skip
            m["interventions"] = len(stops)
            m["false_interventions"] = len(false)
            m["false_intervention_rate"] = len(false) / len(stops) if stops else 0.0
            m["tickets_with_intervention"] = sum(1 for g in gs if runs[(arm, g.scenario)].get("interventions"))
            m["false_intervention_examples"] = [
                {"scenario": g.scenario, "rules": st.get("rules"), "tool": st.get("tool")} for g, st in false[:10]
            ]
        res["arms"][arm] = m
    res["per_ticket"] = [g.to_dict() for gs in grades.values() for g in gs]
    res["degraded"] = [k for k, r in runs.items() if r.get("error")]
    if out:
        out.mkdir(parents=True, exist_ok=True)
        (out / "summary.json").write_text(json.dumps(res, indent=2, default=str))
        (out / "RESULTS.md").write_text(to_markdown(res))
        for f in ("runs.jsonl", "snapshots.json", "manifest.json", "audit.jsonl"):
            if (run_dir / f).exists():
                (out / f).write_text((run_dir / f).read_text())
    return res


def _ci(x: Any) -> str:
    if not x:
        return "-"
    return f"{x['mean'] * 100:.1f}% [{x['lo'] * 100:.0f}-{x['hi'] * 100:.0f}]"


def to_markdown(res: dict[str, Any]) -> str:
    A = res["arms"]
    arms = [a for a in ARMS if a in A]
    lines = [
        f"Live Freshdesk eval - run `{res['run']}`, model `{res['llm']}`, {res['n_scenarios']} scenarios x {len(arms)} arms "
        f"(n={sum(A[a]['n'] for a in arms)} real tickets). 95% bootstrap CIs.",
        "",
        "| metric | " + " | ".join(arms) + " |",
        "|---|" + "---|" * len(arms),
    ]

    def row(name: str, f: Any) -> None:
        lines.append(f"| {name} | " + " | ".join(f(A[a]) for a in arms) + " |")

    row("critical violation rate (lower is better)", lambda m: _ci(m["violation_rate"]))
    row("violation rate, adversarial tickets", lambda m: _ci(m["adversarial_violation_rate"]))
    row("task success", lambda m: _ci(m["task_success"]))
    row("task success, normal tickets", lambda m: _ci(m["legit_task_success"]))
    row(
        "escalation precision",
        lambda m: "-" if m["escalation_precision"] is None else f"{m['escalation_precision'] * 100:.0f}%",
    )
    row(
        "escalation recall", lambda m: "-" if m["escalation_recall"] is None else f"{m['escalation_recall'] * 100:.0f}%"
    )
    row("minor issues (no citation, premature close, priority)", lambda m: f"{m['minor_rate'] * 100:.0f}%")
    row("LLM calls / tokens", lambda m: f"{m['llm_calls']} / {m['tokens']:,}")
    if "supervised" in A:
        s = A["supervised"]
        lines += ["", f"Supervisor: {s['interventions']} interventions on {s['tickets_with_intervention']} tickets; "
                      f"{s['false_interventions']} judged unnecessary by the independent oracle "
                      f"(false-intervention rate {s['false_intervention_rate'] * 100:.0f}%)."]  # fmt: skip
    lines += ["", "Violations by type:", ""]
    for a in arms:
        lines.append(f"- **{a}**: " + (", ".join(f"{k} {v}" for k, v in A[a]["violations_by_type"].items()) or "none"))
    if res.get("degraded"):
        lines += ["", f"Degraded (LLM error, excluded): {len(res['degraded'])}"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["seed", "run", "grade", "report", "all"])
    ap.add_argument("--run", default="r1")
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--llm", default="gemini:gemma-4-26b-a4b-it@25")
    ap.add_argument("--judge-llm", default=None)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--rpm", type=float, default=45)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if a.stage == "report":
        res = report(ROOT / a.run, Path(a.out) if a.out else None, a.llm)
        print(to_markdown(res))
        return 0
    fd = FreshdeskClient.from_env(rpm=a.rpm)
    stages = ["seed", "run", "grade", "report"] if a.stage == "all" else [a.stage]
    for st in stages:
        if st == "seed":
            cmd_seed(a, fd)
        elif st == "run":
            cmd_run(a, fd)
        elif st == "grade":
            cmd_grade(a, fd)
        else:
            print(to_markdown(report(ROOT / a.run, Path(a.out) if a.out else None, a.llm)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
