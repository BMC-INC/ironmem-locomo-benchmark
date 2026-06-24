"""Combine the session-strategy and hybrid-strategy LoCoMo result files into a
single record with a per-category delta (hybrid - session).

The delta is the actual finding: how much an explicit LLM fact-extraction layer
(/remember) adds on top of IronMem's session compression alone.

Usage:
    python scripts/combine_results.py SESSION.json HYBRID.json OUT.json
"""
from __future__ import annotations

import json
import sys


def _summary(d: dict) -> dict:
    return {
        "ingest_strategy": d.get("ingest_strategy"),
        "question_count": d.get("question_count"),
        "error_count": d.get("error_count"),
        "category_counts": d.get("category_counts"),
        "results": d.get("results"),
        "timestamp": d.get("timestamp"),
    }


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    sess_path, hyb_path, out_path = sys.argv[1:4]
    sess = json.load(open(sess_path))
    hyb = json.load(open(hyb_path))

    cats = sorted(set(sess.get("results", {})) | set(hyb.get("results", {})))
    delta = {
        c: round(hyb["results"].get(c, 0.0) - sess["results"].get(c, 0.0), 4)
        for c in cats
    }

    out = {
        "benchmark": "LoCoMo",
        "system": str(sess.get("system", "IronMem")).split(" (")[0],
        "answerer_model": sess.get("answerer_model"),
        "judge_model": sess.get("judge_model"),
        "harness_version": sess.get("harness_version"),
        "overall_scope": sess.get("overall_scope"),
        "dataset_version": sess.get("dataset_version"),
        "runs": {"session": _summary(sess), "hybrid": _summary(hyb)},
        "delta_hybrid_minus_session": delta,
        "per_question": {
            "session": sess.get("per_question", []),
            "hybrid": hyb.get("per_question", []),
        },
    }
    json.dump(out, open(out_path, "w"), indent=2)
    print("WROTE", out_path)
    print("session overall:", sess["results"].get("overall"),
          "| hybrid overall:", hyb["results"].get("overall"))
    print("delta(hybrid-session):", delta)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
