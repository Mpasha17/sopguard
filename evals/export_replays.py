"""Pick recorded conversations where the three systems behaved differently and bundle them for the demo app.

python -m evals.export_replays results/local-llama --out space/replays.json --max 14
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evals.report import SYSTEMS, load


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dirs", nargs="+")
    ap.add_argument("--out", default="space/replays.json")
    ap.add_argument("--max", type=int, default=14)
    a = ap.parse_args(argv)
    data = load([Path(d) for d in a.run_dirs])
    by = {s: {r["scenario"]: r for r in data.get(s, []) if not r.get("degraded")} for s in SYSTEMS}
    common = set.intersection(*(set(v) for v in by.values())) if all(by.values()) else set()
    scored = []
    for k in sorted(common):
        u, p, s = by["unsupervised"][k], by["prompt_only"][k], by["supervised"][k]
        interventions = sum(d["action"] != "allow" for d in s["decisions"])
        score = (
            2 * bool(p["violations"])
            + bool(u["violations"])
            + (s["success"])
            + min(interventions, 2)
            - 3 * bool(s["violations"])
        )
        scored.append((score, k))
    seen_cat: dict[str, int] = {}
    out = {}
    for _score, k in sorted(scored, reverse=True):
        cat = by["supervised"][k]["category"]
        if seen_cat.get(cat, 0) >= 2:
            continue
        seen_cat[cat] = seen_cat.get(cat, 0) + 1
        item = {"summary": f"Category `{cat}`, expected outcome: **{by['supervised'][k]['expected']}**.", "systems": {}}
        for s in SYSTEMS:
            r = by[s][k]
            item["systems"][s] = {kk: r[kk] for kk in ("success", "violations", "events", "decisions", "outcome")}
        out[f"{k} ({cat})"] = item
        if len(out) >= a.max:
            break
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1, default=str))
    print(f"wrote {len(out)} replays to {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
