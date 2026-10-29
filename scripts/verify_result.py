#!/usr/bin/env python3
"""Verify completed LoCoMo result arithmetic without credentials or model calls."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

CATEGORIES = {1: "multi_hop", 2: "temporal", 3: "open_domain", 4: "single_hop", 5: "adversarial"}
SCOPES = {"categories 1-4 (adversarial excluded)": {1, 2, 3, 4}, "categories 1-5": {1, 2, 3, 4, 5}}


def verify(result: dict, expected_questions: int | None = None,
           expected_scored: int | None = None) -> dict:
    """Reject incomplete/error-bearing artifacts and inconsistent stored totals."""
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    require(isinstance(result, dict), "result must be an object")
    require(result.get("benchmark") == "LoCoMo", "expected a LoCoMo result")
    scope = result.get("overall_scope")
    require(scope in SCOPES, "unknown overall_scope")
    included = SCOPES[scope]
    rows = result.get("per_question")
    require(isinstance(rows, list) and rows, "per_question must be a nonempty list")
    require(result.get("question_count") == len(rows), "question_count does not match rows")
    require(result.get("error_count") == 0, "completed evaluation must have zero errors")
    if expected_questions is not None:
        require(len(rows) == expected_questions, "unexpected question coverage")

    ids = set()
    counts, correct, scored = Counter(), Counter(), Counter()
    for index, row in enumerate(rows):
        require(isinstance(row, dict), f"row {index}: expected an object")
        question_id = row.get("question_id")
        require(isinstance(question_id, str) and question_id, f"row {index}: missing question_id")
        require(question_id not in ids, f"duplicate question_id: {question_id}")
        ids.add(question_id)
        category = row.get("category_int")
        require(type(category) is int and category in CATEGORIES, f"{question_id}: unknown category")
        name = CATEGORIES[category]
        require(row.get("category") == name, f"{question_id}: category label mismatch")
        require("error" in row and row["error"] is None, f"{question_id}: missing or nonempty error")
        counts[name] += 1
        require("score" in row, f"{question_id}: missing score")
        score = row["score"]
        if category in included:
            require(type(score) is int and score in (0, 1), f"{question_id}: expected a binary score")
            scored[name] += 1
            correct[name] += score
        else:
            require(score is None, f"{question_id}: excluded category must have null score")

    total_scored = sum(scored.values())
    require(total_scored > 0, "no scored questions")
    if expected_scored is not None:
        require(total_scored == expected_scored, "unexpected scored coverage")
    require(result.get("category_counts") == dict(counts), "category_counts do not match rows")
    computed = {name: round(correct[name] / count, 4) for name, count in scored.items()}
    computed["overall"] = round(sum(correct.values()) / total_scored, 4)
    require(result.get("results") == computed, "stored scores do not match per-question scores")
    return {"questions": len(rows), "scored": total_scored, "correct": sum(correct.values()),
            "excluded": len(rows) - total_scored, "category_counts": dict(counts), "results": computed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path)
    parser.add_argument("--expected-questions", type=int)
    parser.add_argument("--expected-scored", type=int)
    args = parser.parse_args()
    try:
        raw = args.result.read_bytes()
        summary = verify(json.loads(raw), args.expected_questions, args.expected_scored)
    except (OSError, ValueError, TypeError) as exc:
        print(f"Verification failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"file": str(args.result), "sha256": hashlib.sha256(raw).hexdigest(),
                      **summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
