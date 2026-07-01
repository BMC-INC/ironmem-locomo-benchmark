"""Audit current IronMem retrieval against a regression flip set.

This is the cheap gate before spending another Gemini Pro run. It replays
selected questions from a saved flip set against the currently running IronMem
server and measures whether the gold answer evidence reaches the top-k context.

Usage:
  .venv/bin/python scripts/audit_retrieval_floor.py \
      --regression-set results/regression/regression_set_upg8_vs_upg11.json \
      --buckets lost gained \
      --strategy hybrid --limit 25 --output results/regression/retrieval_audit.json
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from benchmark.config import Config  # noqa: E402
from benchmark.ironmem_client import IronMemClient  # noqa: E402

_classify_spec = importlib.util.spec_from_file_location(
    "classify_flips", REPO_ROOT / "scripts" / "classify_flips.py"
)
classify_flips = importlib.util.module_from_spec(_classify_spec)
assert _classify_spec.loader is not None
_classify_spec.loader.exec_module(classify_flips)


def _resolve(path: str) -> Path:
    src = Path(path)
    if not src.is_absolute():
        src = REPO_ROOT / src
    return src


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise SystemExit(f"Missing input file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise SystemExit(f"{path} is not a regression set with entries[]")
    return data


def _context_from_memories(memories: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for i, memory in enumerate(memories, 1):
        summary = (memory.get("summary") or "").strip()
        tags = (memory.get("tags") or "").strip()
        line = f"[{i}] {summary}" if summary else f"[{i}]"
        if tags:
            line += f"  (tags: {tags})"
        lines.append(line)
    return "\n".join(lines)


def _selected_entries(args: argparse.Namespace, regression: dict[str, Any]) -> list[dict[str, Any]]:
    buckets = set(args.buckets)
    categories = set(args.categories) if args.categories else None
    entries = []
    for entry in regression["entries"]:
        if entry.get("bucket") not in buckets:
            continue
        if categories is not None and entry.get("category") not in categories:
            continue
        if entry.get("category") == "adversarial" and not args.include_adversarial:
            continue
        entries.append(entry)
    if args.max_questions is not None:
        entries = entries[: args.max_questions]
    return entries


async def _audit_one(
    client: IronMemClient,
    cfg: Config,
    sem: asyncio.Semaphore,
    entry: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    join_key = entry.get("join_key") or {}
    conv_id = str(join_key.get("conversation_id") or "")
    qid = str(join_key.get("question_id") or "")
    project = cfg.project_for(conv_id, args.strategy)
    question = str(entry.get("question") or "")
    ground_truth = str(entry.get("ground_truth") or "")

    started = time.monotonic()
    error = None
    memories: list[dict[str, Any]] = []
    try:
        async with sem:
            memories = await client.get_context(
                project,
                query=question,
                limit=args.limit,
                rerank=args.rerank,
                pool=args.pool,
            )
    except Exception as exc:  # keep the artifact complete for interrupted audits
        error = str(exc)
    elapsed_ms = round((time.monotonic() - started) * 1000.0, 1)

    current_context = _context_from_memories(memories)
    current_evidence = classify_flips._evidence_presence(ground_truth, current_context)
    baseline_evidence = classify_flips._evidence_presence(
        ground_truth, (entry.get("baseline") or {}).get("retrieved_context")
    )
    candidate_evidence = classify_flips._evidence_presence(
        ground_truth, (entry.get("candidate") or {}).get("retrieved_context")
    )

    current_rank = current_evidence.get("evidence_rank")
    return {
        "question_id": qid,
        "conversation_id": conv_id,
        "project": project,
        "bucket": entry.get("bucket"),
        "category": entry.get("category"),
        "question": question,
        "ground_truth": ground_truth,
        "baseline_score": entry.get("baseline_score"),
        "candidate_score": entry.get("candidate_score"),
        "baseline_evidence_present": baseline_evidence["evidence_present"],
        "candidate_evidence_present": candidate_evidence["evidence_present"],
        "current_evidence_present": current_evidence["evidence_present"],
        "current_partial_evidence_present": current_evidence["partial_evidence_present"],
        "current_evidence_rank": current_rank,
        "current_evidence_in_top10": current_rank is not None and current_rank <= 10,
        "current_evidence_in_top_limit": current_evidence["evidence_present"],
        "current_context_coverage": current_evidence["ground_truth_context_coverage"],
        "current_exact_ground_truth_match": current_evidence["exact_ground_truth_match"],
        "current_exact_variants": current_evidence["exact_variants"],
        "current_evidence_snippet": current_evidence["evidence_snippet"],
        "num_retrieved": len(memories),
        "elapsed_ms": elapsed_ms,
        "error": error,
        "current_memories": [
            {
                "rank": i,
                "id": memory.get("id"),
                "summary": memory.get("summary"),
                "tags": memory.get("tags"),
            }
            for i, memory in enumerate(memories, 1)
        ],
    }


def _pct(num: int, den: int) -> float:
    return round(100.0 * num / den, 2) if den else 0.0


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def block(subset: list[dict[str, Any]]) -> dict[str, Any]:
        n = len(subset)
        reached = sum(1 for r in subset if r["current_evidence_present"])
        partial = sum(1 for r in subset if r["current_partial_evidence_present"])
        top10 = sum(1 for r in subset if r["current_evidence_in_top10"])
        errors = sum(1 for r in subset if r["error"])
        ranks = [
            r["current_evidence_rank"]
            for r in subset
            if isinstance(r.get("current_evidence_rank"), int)
        ]
        return {
            "n": n,
            "current_evidence_present": reached,
            "current_evidence_present_pct": _pct(reached, n),
            "current_partial_evidence_present": partial,
            "current_partial_evidence_present_pct": _pct(partial, n),
            "current_evidence_in_top10": top10,
            "current_evidence_in_top10_pct": _pct(top10, n),
            "errors": errors,
            "median_evidence_rank": sorted(ranks)[len(ranks) // 2] if ranks else None,
        }

    by_bucket: dict[str, Any] = {}
    for bucket in sorted({str(r["bucket"]) for r in rows}):
        by_bucket[bucket] = block([r for r in rows if str(r["bucket"]) == bucket])

    by_category: dict[str, Any] = {}
    for category in sorted({str(r["category"]) for r in rows}):
        by_category[category] = block([r for r in rows if str(r["category"]) == category])

    by_bucket_category: dict[str, Any] = {}
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["bucket"]), str(row["category"]))].append(row)
    for (bucket, category), subset in sorted(grouped.items()):
        by_bucket_category[f"{bucket}:{category}"] = block(subset)

    deltas = Counter()
    for row in rows:
        if row["baseline_evidence_present"] and not row["candidate_evidence_present"]:
            deltas["saved_baseline_had_candidate_lost"] += 1
            if row["current_evidence_present"]:
                deltas["current_recovers_saved_demotion"] += 1
        if row["candidate_evidence_present"] and not row["current_evidence_present"]:
            deltas["current_lost_candidate_evidence"] += 1

    return {
        "overall": block(rows),
        "by_bucket": by_bucket,
        "by_category": by_category,
        "by_bucket_category": by_bucket_category,
        "saved_context_deltas": dict(sorted(deltas.items())),
    }


def _write_output(path: Path, payload: dict[str, Any], force: bool) -> None:
    if path.exists() and not force:
        raise SystemExit(f"Refusing to overwrite existing output without --force: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def _print_summary(summary: dict[str, Any]) -> None:
    overall = summary["overall"]
    print("\n=== Retrieval Floor Audit ===")
    print(
        f"overall: {overall['current_evidence_present']}/{overall['n']} "
        f"({overall['current_evidence_present_pct']}%) evidence-present; "
        f"top10 {overall['current_evidence_in_top10_pct']}%; errors {overall['errors']}"
    )
    print("\nby bucket:")
    for bucket, block in summary["by_bucket"].items():
        print(
            f"  {bucket:13s} {block['current_evidence_present']:>4d}/{block['n']:<4d} "
            f"{block['current_evidence_present_pct']:>6.2f}% "
            f"top10={block['current_evidence_in_top10_pct']:>6.2f}%"
        )
    print("\nby bucket/category:")
    for key, block in summary["by_bucket_category"].items():
        print(
            f"  {key:25s} {block['current_evidence_present']:>4d}/{block['n']:<4d} "
            f"{block['current_evidence_present_pct']:>6.2f}%"
        )
    print(f"\nsaved-context deltas: {summary['saved_context_deltas']}")


async def amain(args: argparse.Namespace) -> int:
    regression_path = _resolve(args.regression_set)
    output_path = _resolve(args.output)
    regression = _load_json(regression_path)
    entries = _selected_entries(args, regression)
    if not entries:
        raise SystemExit("No entries matched the requested buckets/categories")

    cfg = Config()
    sem = asyncio.Semaphore(args.concurrency)
    timeout = httpx.Timeout(args.request_timeout)
    async with httpx.AsyncClient(timeout=timeout) as http:
        client = IronMemClient(cfg, http)
        status = await client.status()
        tasks = [_audit_one(client, cfg, sem, entry, args) for entry in entries]
        rows = await asyncio.gather(*tasks)

    summary = _summarize(rows)
    payload = {
        "artifact": "ironmem_retrieval_floor_audit",
        "artifact_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "regression_set": str(
            regression_path.relative_to(REPO_ROOT)
            if regression_path.is_relative_to(REPO_ROOT)
            else regression_path
        ),
        "settings": {
            "strategy": args.strategy,
            "limit": args.limit,
            "rerank": args.rerank,
            "pool": args.pool,
            "buckets": args.buckets,
            "categories": args.categories,
            "concurrency": args.concurrency,
        },
        "ironmem_status": status,
        "summary": summary,
        "rows": rows,
    }
    _write_output(output_path, payload, args.force)
    _print_summary(summary)
    print(f"\noutput: {output_path}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit current retrieval over a flip set")
    parser.add_argument(
        "--regression-set",
        default="results/regression/regression_set_upg8_vs_upg11.json",
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--strategy", default="hybrid")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--pool", type=int, default=None)
    parser.add_argument("--rerank", action="store_true")
    parser.add_argument("--buckets", nargs="+", default=["lost"])
    parser.add_argument("--categories", nargs="+", default=None)
    parser.add_argument("--include-adversarial", action="store_true")
    parser.add_argument("--max-questions", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--request-timeout", type=float, default=180.0)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    return asyncio.run(amain(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
