"""Build a reusable regression flip set from two LoCoMo result files.

The output is intentionally rich: every paired question keeps both full source
rows, including retrieved contexts, answers, scores, and errors. That makes the
artifact useful both for fast canary runs and later training/evaluation work.

Usage:
  .venv/bin/python scripts/build_flip_set.py \
      --baseline results/upg8_PRO_p100_k25_v2.json \
      --candidate results/upg11_PRO_rerank_p100_k25_v2_d86f70f_20260701.json \
      --output results/regression/regression_set_upg8_vs_upg11.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
JOIN_KEY = ("conversation_id", "question_id")


def _resolve(path: str) -> Path:
    src = Path(path)
    if not src.is_absolute():
        src = REPO_ROOT / src
    return src


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise SystemExit(f"Missing input file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("per_question"), list):
        raise SystemExit(f"{path} is not a LoCoMo result file with a per_question list")
    return data


def _run_metadata(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    keep = [
        "timestamp",
        "benchmark",
        "dataset_version",
        "harness_version",
        "ingest_strategy",
        "answerer_model",
        "judge_model",
        "answer_prompt_version",
        "synthesize",
        "synthesis_model",
        "overall_scope",
        "question_count",
        "error_count",
        "results",
        "category_counts",
        "system",
    ]
    meta = {k: data.get(k) for k in keep if k in data}
    meta["path"] = str(path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path)
    meta["sha256"] = _sha256(path)
    meta["per_question_count"] = len(data.get("per_question", []))
    return meta


def _key(row: dict[str, Any]) -> tuple[str, str]:
    missing = [field for field in JOIN_KEY if row.get(field) in (None, "")]
    if missing:
        raise ValueError(f"row is missing join fields {missing}: {row!r}")
    return str(row["conversation_id"]), str(row["question_id"])


def _index_rows(rows: list[dict[str, Any]], label: str) -> dict[tuple[str, str], dict[str, Any]]:
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    duplicates: list[tuple[str, str]] = []
    for row in rows:
        key = _key(row)
        if key in indexed:
            duplicates.append(key)
        indexed[key] = row
    if duplicates:
        sample = ", ".join(f"{c}/{q}" for c, q in duplicates[:5])
        raise SystemExit(f"{label} has duplicate question keys; sample: {sample}")
    return indexed


def _score_state(score: Any) -> str:
    if score == 1:
        return "correct"
    if score == 0:
        return "wrong"
    return "unscored"


def _bucket(base_score: Any, cand_score: Any) -> str:
    base = _score_state(base_score)
    cand = _score_state(cand_score)
    if base == "correct" and cand == "wrong":
        return "lost"
    if base == "wrong" and cand == "correct":
        return "gained"
    if base == "wrong" and cand == "wrong":
        return "stable_wrong"
    if base == "correct" and cand == "correct":
        return "stable_correct"
    return "unscored_or_mixed"


def _pct(correct: int, total: int) -> float | None:
    if total == 0:
        return None
    return round(100.0 * correct / total, 2)


def _summarize(entries: list[dict[str, Any]]) -> dict[str, Any]:
    bucket_counts = Counter(e["bucket"] for e in entries)
    by_category: dict[str, dict[str, Any]] = {}
    by_category_bucket: dict[str, Counter[str]] = defaultdict(Counter)
    by_category_scored: dict[str, dict[str, int]] = defaultdict(lambda: {
        "baseline_scored": 0,
        "baseline_correct": 0,
        "candidate_scored": 0,
        "candidate_correct": 0,
    })

    for entry in entries:
        cat = entry["category"]
        by_category_bucket[cat][entry["bucket"]] += 1
        scored = by_category_scored[cat]
        if entry["baseline_score"] in (0, 1):
            scored["baseline_scored"] += 1
            scored["baseline_correct"] += int(entry["baseline_score"] == 1)
        if entry["candidate_score"] in (0, 1):
            scored["candidate_scored"] += 1
            scored["candidate_correct"] += int(entry["candidate_score"] == 1)

    for cat in sorted(by_category_bucket):
        scored = by_category_scored[cat]
        baseline_acc = _pct(scored["baseline_correct"], scored["baseline_scored"])
        candidate_acc = _pct(scored["candidate_correct"], scored["candidate_scored"])
        by_category[cat] = {
            "buckets": dict(sorted(by_category_bucket[cat].items())),
            **scored,
            "baseline_accuracy_pct": baseline_acc,
            "candidate_accuracy_pct": candidate_acc,
            "delta_pct_points": (
                round(candidate_acc - baseline_acc, 2)
                if baseline_acc is not None and candidate_acc is not None
                else None
            ),
        }

    scored_entries = [e for e in entries if e["baseline_score"] in (0, 1) and e["candidate_score"] in (0, 1)]
    baseline_correct = sum(1 for e in scored_entries if e["baseline_score"] == 1)
    candidate_correct = sum(1 for e in scored_entries if e["candidate_score"] == 1)
    baseline_acc = _pct(baseline_correct, len(scored_entries))
    candidate_acc = _pct(candidate_correct, len(scored_entries))

    return {
        "total_paired": len(entries),
        "scored_paired": len(scored_entries),
        "buckets": dict(sorted(bucket_counts.items())),
        "baseline_correct": baseline_correct,
        "candidate_correct": candidate_correct,
        "net_correct_delta": candidate_correct - baseline_correct,
        "baseline_accuracy_pct": baseline_acc,
        "candidate_accuracy_pct": candidate_acc,
        "delta_pct_points": (
            round(candidate_acc - baseline_acc, 2)
            if baseline_acc is not None and candidate_acc is not None
            else None
        ),
        "by_category": by_category,
    }


def _entry(base: dict[str, Any], cand: dict[str, Any]) -> dict[str, Any]:
    base_score = base.get("score")
    cand_score = cand.get("score")
    category = cand.get("category") or base.get("category") or "unknown"
    return {
        "join_key": {
            "conversation_id": str(base.get("conversation_id")),
            "question_id": str(base.get("question_id")),
        },
        "category": category,
        "category_int": cand.get("category_int", base.get("category_int")),
        "question": cand.get("question") or base.get("question"),
        "ground_truth": cand.get("ground_truth") or base.get("ground_truth"),
        "bucket": _bucket(base_score, cand_score),
        "baseline_score": base_score,
        "candidate_score": cand_score,
        "baseline_answer": base.get("generated_answer"),
        "candidate_answer": cand.get("generated_answer"),
        "baseline_num_retrieved": base.get("num_retrieved"),
        "candidate_num_retrieved": cand.get("num_retrieved"),
        "baseline_error": base.get("error"),
        "candidate_error": cand.get("error"),
        "baseline": base,
        "candidate": cand,
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    baseline_path = _resolve(args.baseline)
    candidate_path = _resolve(args.candidate)
    baseline_data = _load_json(baseline_path)
    candidate_data = _load_json(candidate_path)

    baseline = _index_rows(baseline_data["per_question"], "baseline")
    candidate = _index_rows(candidate_data["per_question"], "candidate")
    baseline_keys = set(baseline)
    candidate_keys = set(candidate)
    common_keys = sorted(baseline_keys & candidate_keys)
    missing_from_candidate = sorted(baseline_keys - candidate_keys)
    missing_from_baseline = sorted(candidate_keys - baseline_keys)

    entries = [_entry(baseline[key], candidate[key]) for key in common_keys]
    entries.sort(key=lambda e: (
        str(e.get("category")),
        str(e["join_key"]["conversation_id"]),
        str(e["join_key"]["question_id"]),
    ))

    return {
        "artifact": "ironmem_locomo_regression_flip_set",
        "artifact_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "join_key": list(JOIN_KEY),
        "baseline_run": _run_metadata(baseline_path, baseline_data),
        "candidate_run": _run_metadata(candidate_path, candidate_data),
        "join_summary": {
            "baseline_rows": len(baseline),
            "candidate_rows": len(candidate),
            "common_rows": len(common_keys),
            "missing_from_candidate": [
                {"conversation_id": c, "question_id": q}
                for c, q in missing_from_candidate
            ],
            "missing_from_baseline": [
                {"conversation_id": c, "question_id": q}
                for c, q in missing_from_baseline
            ],
        },
        "summary": _summarize(entries),
        "entries": entries,
    }


def _write_json(path: Path, data: dict[str, Any], force: bool) -> None:
    if path.exists() and not force:
        raise SystemExit(f"Refusing to overwrite existing output without --force: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a paired LoCoMo regression flip set")
    parser.add_argument("--baseline", required=True, help="Baseline result JSON")
    parser.add_argument("--candidate", required=True, help="Candidate result JSON")
    parser.add_argument("--output", required=True, help="Output regression JSON")
    parser.add_argument("--force", action="store_true", help="Overwrite output if it already exists")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = _resolve(args.output)
    data = build(args)
    _write_json(output, data, args.force)

    summary = data["summary"]
    print("=== Flip Set Built ===")
    print(f"output: {output}")
    print(f"paired: {summary['total_paired']}  scored: {summary['scored_paired']}")
    print(f"baseline: {summary['baseline_accuracy_pct']}%  candidate: {summary['candidate_accuracy_pct']}%")
    print(f"delta: {summary['delta_pct_points']}pp  net_correct_delta: {summary['net_correct_delta']}")
    print("buckets:")
    for bucket, count in summary["buckets"].items():
        print(f"  {bucket}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
