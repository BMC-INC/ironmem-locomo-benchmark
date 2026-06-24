"""Failure analysis for a LoCoMo result file.

Splits every wrong answer (score == 0) into one of three buckets so we know
where to aim IronMem upgrades:

  * retrieval_gap   -- the ground-truth content is largely ABSENT from the
                       retrieved context. IronMem didn't surface the memory.
                       -> fix retrieval (edges/multi-hop, reranking, recall).
  * answerer_gap    -- the ground-truth content IS in the retrieved context but
                       the answer was still scored wrong. -> answerer/judge, not
                       memory. (Often the strict judge penalizing extra detail.)
  * abstained       -- the answerer said "I don't have enough information".
                       -> usually a retrieval gap the model was honest about.

The retrieval signal is a heuristic: token overlap between the normalized
ground-truth and the retrieved context (month names + digits normalized so
"19 January, 2023" matches "January 19, 2023").

Usage:
    python scripts/analyze_failures.py RESULT.json [RESULT2.json ...]
A combined file (gemini_locomo_full_run.json) is auto-expanded into its
per-strategy runs.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict

_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "with",
    "is", "are", "was", "were", "by", "as", "that", "this", "it", "be", "has",
    "have", "had", "did", "do", "does", "his", "her", "their", "they", "he",
    "she", "based", "context", "provided", "answer", "question", "about",
}
_MONTHS = {
    "january": "1", "february": "2", "march": "3", "april": "4", "may": "5",
    "june": "6", "july": "7", "august": "8", "september": "9", "october": "10",
    "november": "11", "december": "12",
}


def _tokens(text: str) -> set[str]:
    text = (text or "").lower()
    raw = re.findall(r"[a-z0-9]+", text)
    out: set[str] = set()
    for w in raw:
        w = _MONTHS.get(w, w)
        if w in _STOP or len(w) < 2:
            continue
        out.add(w)
    return out


def _classify(row: dict) -> str:
    ans = (row.get("generated_answer") or "").lower()
    if "don't have enough information" in ans or "do not have enough information" in ans:
        return "abstained"
    gt = _tokens(row.get("ground_truth", ""))
    if not gt:
        return "answerer_gap"
    ctx = _tokens(row.get("retrieved_context", ""))
    coverage = len(gt & ctx) / len(gt)
    return "retrieval_gap" if coverage < 0.5 else "answerer_gap"


def _runs(path: str):
    d = json.load(open(path))
    if "per_question" in d and isinstance(d["per_question"], dict):
        for strat, rows in d["per_question"].items():
            yield f"{path}:{strat}", rows
    else:
        yield path, d.get("per_question", [])


def analyze(label: str, rows: list[dict]) -> None:
    scored = [r for r in rows if r.get("score") is not None]
    wrong = [r for r in scored if not r.get("score")]
    errored = [r for r in rows if r.get("error")]
    print(f"\n=== {label} ===")
    print(f"scored={len(scored)}  correct={len(scored)-len(wrong)}  "
          f"wrong={len(wrong)}  errored={len(errored)}")

    by_cat_total: dict[str, int] = defaultdict(int)
    by_cat_wrong: dict[str, int] = defaultdict(int)
    buckets: dict[str, int] = defaultdict(int)
    bucket_by_cat: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in scored:
        by_cat_total[r["category"]] += 1
    for r in wrong:
        cat = r["category"]
        by_cat_wrong[cat] += 1
        b = _classify(r)
        buckets[b] += 1
        bucket_by_cat[cat][b] += 1

    print("failure buckets:", dict(buckets))
    print(f"{'category':<14}{'acc':>7}{'n':>6}{'wrong':>7}   "
          f"{'retrieval_gap':>14}{'answerer_gap':>14}{'abstained':>11}")
    for cat in sorted(by_cat_total):
        n = by_cat_total[cat]
        w = by_cat_wrong.get(cat, 0)
        acc = (n - w) / n if n else 0.0
        bc = bucket_by_cat[cat]
        print(f"{cat:<14}{acc*100:>6.1f}%{n:>6}{w:>7}   "
              f"{bc.get('retrieval_gap',0):>14}{bc.get('answerer_gap',0):>14}"
              f"{bc.get('abstained',0):>11}")

    print("--- sample retrieval_gap failures (memory not surfaced) ---")
    shown = 0
    for r in wrong:
        if _classify(r) == "retrieval_gap" and shown < 5:
            print(f"  [{r['category']}] Q: {r['question'][:80]}")
            print(f"            GT: {str(r['ground_truth'])[:70]}")
            shown += 1


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    for path in sys.argv[1:]:
        for label, rows in _runs(path):
            analyze(label, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
