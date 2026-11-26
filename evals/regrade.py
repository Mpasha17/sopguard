"""Re-grade saved runs without calling any LLM (events + DB snapshot are stored per conversation)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evals.grader import grade, intervention_needed
from evals.scenarios import build_scenarios
from sopguard.demo.shop import Shop


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    a = ap.parse_args(argv)
    meta = json.loads((Path(a.run_dir) / "meta.json").read_text())
    scs = {s.id: s for s in build_scenarios(meta.get("seed", 11))}
    base = Shop()
    for p in sorted(Path(a.run_dir).glob("*.jsonl")):
        if p.name == "audit.jsonl":
            continue
        recs = [json.loads(line) for line in p.read_text().splitlines()]
        for r in recs:
            sc = scs[r["scenario"]]
            evs = r["events"]
            g = grade(sc, [e for e in evs if e["role"] != "supervisor"], r["db"], base)
            r.update(violations=g.violations, success=g.success, escalated=g.escalated, outcome=g.outcome)
            idx = {e.get("decision_id"): i for i, e in enumerate(evs) if e["role"] == "supervisor"}
            for d in r["decisions"]:
                if d["action"] != "allow":
                    before = [e for e in evs[: idx.get(d["id"], len(evs))] if e["role"] != "supervisor"]
                    d["needed"] = intervention_needed(sc, base, before, d)
        p.write_text("".join(json.dumps(r, default=str) + "\n" for r in recs))
        print(f"regraded {p.name}: {len(recs)} conversations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
