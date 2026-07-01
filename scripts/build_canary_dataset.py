"""Build a LoCoMo-compatible canary dataset from a regression flip set.

The canary keeps full conversations for ingest but filters each conversation's
QA list to the diagnostic questions we want to score cheaply before another
full Pro run.

Usage:
  .venv/bin/python scripts/build_canary_dataset.py \
      --source-data data/locomo10.json \
      --regression-set results/regression/regression_set_upg8_vs_upg11.json \
      --output data/regression_canary_upg8_vs_upg11.json
"""
from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
QID_RE = re.compile(r"^(?P<conversation_id>.+)_q(?P<index>\d+)$")


def _resolve(path: str) -> Path:
    src = Path(path)
    if not src.is_absolute():
        src = REPO_ROOT / src
    return src


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise SystemExit(f"Missing input file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc


def _wanted_question_ids(regression: dict[str, Any], stable_wrong_sample: int, seed: int) -> set[str]:
    entries = regression.get("entries")
    if not isinstance(entries, list):
        raise SystemExit("Regression set must contain entries[]")

    wanted: set[str] = set()
    stable_by_category: dict[str, list[str]] = defaultdict(list)
    for entry in entries:
        qid = (entry.get("join_key") or {}).get("question_id")
        if not qid:
            continue
        bucket = entry.get("bucket")
        if bucket in {"lost", "gained"}:
            wanted.add(str(qid))
        elif bucket == "stable_wrong":
            stable_by_category[str(entry.get("category") or "unknown")].append(str(qid))

    rng = random.Random(seed)
    if stable_wrong_sample > 0:
        categories = sorted(stable_by_category)
        base = stable_wrong_sample // max(len(categories), 1)
        remainder = stable_wrong_sample % max(len(categories), 1)
        for i, category in enumerate(categories):
            ids = stable_by_category[category]
            rng.shuffle(ids)
            take = min(len(ids), base + (1 if i < remainder else 0))
            wanted.update(ids[:take])
    return wanted


def _parse_qid(qid: str) -> tuple[str, int]:
    match = QID_RE.match(qid)
    if not match:
        raise SystemExit(f"Question id is not in <conversation>_q<index> form: {qid}")
    return match.group("conversation_id"), int(match.group("index"))


def build(args: argparse.Namespace) -> dict[str, Any]:
    source_path = _resolve(args.source_data)
    regression_path = _resolve(args.regression_set)
    source = _load_json(source_path)
    regression = _load_json(regression_path)
    if not isinstance(source, list):
        raise SystemExit("Source LoCoMo data must be a JSON list")

    wanted = _wanted_question_ids(regression, args.stable_wrong_sample, args.seed)
    by_conv: dict[str, set[int]] = defaultdict(set)
    for qid in wanted:
        conv_id, idx = _parse_qid(qid)
        by_conv[conv_id].add(idx)

    out: list[dict[str, Any]] = []
    category_counts: Counter[str] = Counter()
    bucket_counts: Counter[str] = Counter()
    bucket_by_qid = {
        (entry.get("join_key") or {}).get("question_id"): entry.get("bucket")
        for entry in regression.get("entries", [])
    }

    for conv in source:
        conv_id = str(conv.get("sample_id", ""))
        indices = by_conv.get(conv_id)
        if not indices:
            continue
        qa = conv.get("qa") or []
        filtered = []
        for idx in sorted(indices):
            if idx < 0 or idx >= len(qa):
                raise SystemExit(f"{conv_id}_q{idx} is out of range for {conv_id}")
            item = dict(qa[idx])
            qid = f"{conv_id}_q{idx}"
            item["question_id"] = qid
            item["original_q_index"] = idx
            item["regression_bucket"] = bucket_by_qid.get(qid)
            filtered.append(item)
            category_counts[str(item.get("category"))] += 1
            bucket_counts[str(item.get("regression_bucket"))] += 1
        copied = dict(conv)
        copied["qa"] = filtered
        copied["canary_metadata"] = {
            "source_data": str(source_path.relative_to(REPO_ROOT) if source_path.is_relative_to(REPO_ROOT) else source_path),
            "regression_set": str(regression_path.relative_to(REPO_ROOT) if regression_path.is_relative_to(REPO_ROOT) else regression_path),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        out.append(copied)

    found = {qa["question_id"] for conv in out for qa in conv["qa"]}
    missing = sorted(wanted - found)
    if missing:
        raise SystemExit(f"Could not map {len(missing)} wanted question ids; sample: {missing[:5]}")

    return {
        "data": out,
        "summary": {
            "conversation_count": len(out),
            "question_count": sum(len(conv["qa"]) for conv in out),
            "bucket_counts": dict(sorted(bucket_counts.items())),
            "category_counts": dict(sorted(category_counts.items())),
        },
    }


def _write(path: Path, data: list[dict[str, Any]], force: bool) -> None:
    if path.exists() and not force:
        raise SystemExit(f"Refusing to overwrite existing output without --force: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a diagnostic LoCoMo canary dataset")
    parser.add_argument("--source-data", default="data/locomo10.json")
    parser.add_argument("--regression-set", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--stable-wrong-sample",
        type=int,
        default=60,
        help="stratified stable_wrong sample count to add beside all lost/gained",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--force", action="store_true", help="Overwrite output if it already exists")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = _resolve(args.output)
    built = build(args)
    _write(output, built["data"], args.force)
    summary = built["summary"]
    print("=== Canary Dataset Built ===")
    print(f"output: {output}")
    print(f"conversations: {summary['conversation_count']}  questions: {summary['question_count']}")
    print(f"buckets: {summary['bucket_counts']}")
    print(f"categories: {summary['category_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
