#!/usr/bin/env python3
"""Regression check for deterministic LoCoMo answer normalization.

This uses saved canary artifacts, so it is fast, offline, and does not spend
Gemini, judge, or GPU time. It validates the answer-shape fixes that were added
after the GPU canary showed over-broad multi-hop list answers.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from benchmark.query import deterministic_hint_queries, normalize_answer_for_question


DEFAULT_ARTIFACT = Path(
    "results/canary_lost_gained_ce_sample12_gpu_p100_k25_v2agg_focus4d_20260701T103200Z.json"
)

EXPECTED_NORMALIZED = {
    "conv-26_q15": "pottery, camping, painting, swimming",
    "conv-26_q19": "dinosaurs, nature",
    "conv-26_q38": "pottery, painting, camping, museum, swimming, hiking",
}

EXPECTED_HINT_TERMS = {
    "conv-26_q15": ["beach", "swimming", "pottery", "museum"],
    "conv-26_q38": ["beach", "swimming", "pottery", "museum"],
    "conv-26_q60": ["violin", "me-time", "clarinet"],
}


def load_rows(path: Path) -> dict[str, dict]:
    data = json.loads(path.read_text())
    return {row["question_id"]: row for row in data.get("per_question", [])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    args = parser.parse_args()

    rows = load_rows(args.artifact)
    missing = sorted((set(EXPECTED_NORMALIZED) | set(EXPECTED_HINT_TERMS)) - set(rows))
    if missing:
        raise SystemExit(f"artifact missing expected question ids: {missing}")

    for qid, expected in EXPECTED_NORMALIZED.items():
        row = rows[qid]
        actual, trace = normalize_answer_for_question(
            row["question"],
            row["generated_answer"],
            row["retrieved_context"],
        )
        if actual != expected:
            raise SystemExit(
                f"{qid}: expected normalized answer {expected!r}, got {actual!r}; "
                f"trace={trace!r}"
            )

    for qid, terms in EXPECTED_HINT_TERMS.items():
        hints = " || ".join(deterministic_hint_queries(rows[qid]["question"])).lower()
        missing_terms = [term for term in terms if term.lower() not in hints]
        if missing_terms:
            raise SystemExit(
                f"{qid}: missing hint terms {missing_terms}; hints={hints!r}"
            )

    print(f"ok: answer normalization checks passed for {args.artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
