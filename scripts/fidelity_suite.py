"""Fidelity suite (#2 diagnostic): split scored errors into RETRIEVAL vs ANSWERER.

We already know extraction is solved and retrieval is the bottleneck — but for any
single wrong answer we cannot currently tell WHETHER the gold fact reached the
answerer's context (so the answerer fumbled it) or NEVER reached it (so retrieval
failed). This script attributes every scored question (cats 1-4) to a failure mode
by JOINING two signals:

  1. the scored correct/incorrect flag from a completed run (`--scored`), and
  2. a fresh retrieval probe using the SAME final retrieval the answerer saw:
     get_context(query=question, limit=final_limit, rerank=True, pool=pool),
     then token-coverage of the gold against that context = "gold_reached_answerer".

Four cells per question:
  CORRECT + gold_reached       -> clean win
  CORRECT + gold_not_reached   -> lucky / parametric-knowledge / coverage-threshold miss
  WRONG   + gold_reached       -> ANSWERER FAILURE  (retrieval did its job)
  WRONG   + gold_not_reached   -> RETRIEVAL FAILURE

Headline per category + overall: of our WRONG answers, X% are retrieval-failures and
Y% are answerer-failures. Among retrieval failures we also report the EARLIEST funnel
stage the gold was lost — store gap (never in memory) vs recall gap (in store, missed
the candidate pool) vs rerank gap (in pool, dropped before the final top-k) — so we
know whether to fix ingestion/store-limit, candidate recall, or the reranker.

"Present" = content-token coverage >= --coverage (default 0.5), the SAME rule
scripts/funnel_probe.py / analyze_failures.py use, so results are directly comparable.

This script does NOT modify any existing harness module; it imports funnel_probe and
reuses its helpers verbatim (the gate_only.py pattern).

Usage:
  .venv/bin/python scripts/fidelity_suite.py \
      --scored results/upg3_PRO_C_rerank_pool50.json \
      --strategy hybrid --pool 50 --final-limit 10 --store-limit 2000 \
      --coverage 0.5 --concurrency 8 --output fidelity_pro_baseline.json
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Reuse funnel_probe's helpers verbatim (gate_only.py pattern) so the coverage /
# tokenization / store-probe logic stays identical and comparable.
_spec = importlib.util.spec_from_file_location(
    "funnel_probe", REPO_ROOT / "scripts" / "funnel_probe.py"
)
fp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fp)

from benchmark.config import Config  # noqa: E402
from benchmark.ingest import load_conversations  # noqa: E402
from benchmark.ironmem_client import IronMemClient  # noqa: E402

DEFAULT_DATA = REPO_ROOT / "data" / "locomo10.json"

CELLS = ["correct_reached", "correct_not_reached", "answerer_failure", "retrieval_failure"]
# Earliest funnel stage the gold was lost (only meaningful for gold_not_reached).
STAGES = ["store", "recall_pool", "rerank"]
CATEGORY_ORDER = ["multi_hop", "temporal", "open_domain", "single_hop"]


def _pct(x: int, d: int) -> float:
    return round(100.0 * x / d, 1) if d else 0.0


def _load_scored(path: str) -> dict[str, int | None]:
    p = Path(path)
    if not p.is_absolute():
        p = REPO_ROOT / p
    data = json.loads(p.read_text())
    return {r.get("question_id"): r.get("score") for r in data.get("per_question", [])}


# --- per-question probe -----------------------------------------------------

async def fidelity_one(
    client: IronMemClient,
    sem: asyncio.Semaphore,
    project: str,
    store_text: str,
    conv_id: str,
    q_index: int,
    qa: dict,
    score: int,
    pool: int,
    final_limit: int,
    coverage: float,
) -> dict:
    category = int(qa.get("category", 0))
    question = str(qa.get("question", ""))
    gold = fp._tokens(fp._ground_truth(qa, category))

    # Funnel stages: store presence (no per-question call), candidate pool
    # (rerank OFF, the recall stage), and the FINAL context the answerer saw
    # (rerank ON over `pool`) — identical settings to the scored run's retrieval.
    gold_in_memory = fp._covered(gold, store_text, coverage)
    async with sem:
        pool_mem = await client.get_context(project, query=question, limit=pool, rerank=False)
        final = await client.get_context(
            project, query=question, limit=final_limit, rerank=True, pool=pool
        )
    in_pool = fp._covered(gold, fp._memories_text(pool_mem), coverage)
    final_text = fp._memories_text(final)
    gold_reached = fp._covered(gold, final_text, coverage)

    correct = score == 1
    if correct and gold_reached:
        cell = "correct_reached"
    elif correct and not gold_reached:
        cell = "correct_not_reached"
    elif (not correct) and gold_reached:
        cell = "answerer_failure"
    else:
        cell = "retrieval_failure"

    # Earliest stage the gold was lost on its way to the answerer's context.
    if gold_reached:
        lost_stage = None
    elif not gold_in_memory:
        lost_stage = "store"        # never compressed into the store (ingest / store-limit gap)
    elif not in_pool:
        lost_stage = "recall_pool"  # in store but BM25+vector missed it in the top-`pool` candidates
    else:
        lost_stage = "rerank"       # was a candidate but the reranker/final-limit dropped it

    return {
        "question_id": f"{conv_id}_q{q_index}",
        "conversation_id": conv_id,
        "category": fp.CATEGORY_MAP.get(category, str(category)),
        "category_int": category,
        "question": question,
        "ground_truth": fp._ground_truth(qa, category),
        "gold_token_count": len(gold),
        "score": score,
        "correct": correct,
        "gold_in_memory": gold_in_memory,
        "in_pool": in_pool,
        "gold_reached_answerer": gold_reached,
        "cell": cell,
        "lost_stage": lost_stage,
        "final_coverage": fp._coverage(gold, final_text),
        "num_final": len(final),
    }


# --- aggregation ------------------------------------------------------------

def _breakdown(rows: list[dict]) -> dict:
    n = len(rows)
    cells = {c: sum(1 for r in rows if r["cell"] == c) for c in CELLS}
    correct = cells["correct_reached"] + cells["correct_not_reached"]
    wrong = cells["answerer_failure"] + cells["retrieval_failure"]
    rf_rows = [r for r in rows if r["cell"] == "retrieval_failure"]
    stage_counts = {s: sum(1 for r in rf_rows if r["lost_stage"] == s) for s in STAGES}
    return {
        "n": n,
        "accuracy_pct": _pct(correct, n),
        "cells": cells,
        "wrong": wrong,
        "of_wrong_retrieval_failure_pct": _pct(cells["retrieval_failure"], wrong),
        "of_wrong_answerer_failure_pct": _pct(cells["answerer_failure"], wrong),
        "retrieval_failure_stage_counts": stage_counts,
        "retrieval_failure_stage_pct": {s: _pct(stage_counts[s], len(rf_rows)) for s in STAGES},
    }


def aggregate(rows: list[dict]) -> dict:
    overall = _breakdown(rows)
    by_cat = {}
    for cat in CATEGORY_ORDER:
        crows = [r for r in rows if r["category"] == cat]
        if crows:
            by_cat[cat] = _breakdown(crows)
    return {"overall": overall, "by_category": by_cat}


# --- pretty printing --------------------------------------------------------

def _print_table(summary: dict) -> None:
    rows = [("OVERALL", summary["overall"])] + list(summary["by_category"].items())

    print("\n=== FIDELITY SUITE — failure-mode breakdown (cats 1-4) ===\n")
    hdr = (
        f"{'category':12s} {'n':>4s} {'acc%':>6s} | "
        f"{'win':>4s} {'lucky':>5s} {'ANSW':>5s} {'RETR':>5s} | "
        f"{'wrong':>5s} {'RETR%':>6s} {'ANSW%':>6s}"
    )
    print(hdr)
    print("-" * len(hdr))
    for name, b in rows:
        c = b["cells"]
        print(
            f"{name:12s} {b['n']:>4d} {b['accuracy_pct']:>6.1f} | "
            f"{c['correct_reached']:>4d} {c['correct_not_reached']:>5d} "
            f"{c['answerer_failure']:>5d} {c['retrieval_failure']:>5d} | "
            f"{b['wrong']:>5d} {b['of_wrong_retrieval_failure_pct']:>6.1f} "
            f"{b['of_wrong_answerer_failure_pct']:>6.1f}"
        )
    print(
        "\nlegend: win=CORRECT+reached  lucky=CORRECT+not_reached  "
        "ANSW=WRONG+reached (answerer fail)  RETR=WRONG+not_reached (retrieval fail)"
    )

    o = summary["overall"]
    print(
        f"\nHEADLINE: of {o['wrong']} wrong answers, "
        f"{o['of_wrong_retrieval_failure_pct']}% are RETRIEVAL failures and "
        f"{o['of_wrong_answerer_failure_pct']}% are ANSWERER failures."
    )

    sc = o["retrieval_failure_stage_counts"]
    sp = o["retrieval_failure_stage_pct"]
    rf_total = sum(sc.values())
    print(f"\nAmong the {rf_total} RETRIEVAL failures, earliest stage the gold was lost:")
    print(f"  store gap   (never in memory)      {sc['store']:>4d}  {sp['store']:>5.1f}%")
    print(f"  recall gap  (in store, missed pool){sc['recall_pool']:>4d}  {sp['recall_pool']:>5.1f}%")
    print(f"  rerank gap  (in pool, dropped)     {sc['rerank']:>4d}  {sp['rerank']:>5.1f}%")


# --- main -------------------------------------------------------------------

async def amain(args) -> int:
    cfg = Config()
    if args.concurrency:
        cfg.max_concurrency = args.concurrency

    scored_lookup = _load_scored(args.scored)
    n_scored = sum(1 for v in scored_lookup.values() if v in (0, 1))

    conversations = load_conversations(args.data)
    if args.conv_index is not None:
        conversations = [conversations[args.conv_index]]
    elif args.limit_convs:
        conversations = conversations[: args.limit_convs]

    async with httpx.AsyncClient(timeout=cfg.request_timeout) as http:
        client = IronMemClient(cfg, http)
        try:
            await client.status()
        except Exception as exc:
            print(f"Cannot reach IronMem at {cfg.ironmem_url}: {exc}")
            return 2

        sem = asyncio.Semaphore(cfg.max_concurrency)
        all_rows: list[dict] = []
        missing = 0
        for conv in conversations:
            project = cfg.project_for(conv.sample_id, args.strategy)
            store_text = await fp.probe_project_store(client, project, args.store_limit)
            tasks = []
            for i, qa in enumerate(conv.qa):
                if int(qa.get("category", 0)) == 5:  # exclude adversarial
                    continue
                score = scored_lookup.get(f"{conv.sample_id}_q{i}")
                if score not in (0, 1):  # only classify questions the scored run graded
                    missing += 1
                    continue
                tasks.append(
                    fidelity_one(
                        client, sem, project, store_text, conv.sample_id, i, qa,
                        int(score), args.pool, args.final_limit, args.coverage,
                    )
                )
            rows = await asyncio.gather(*tasks)
            all_rows.extend(rows)
            print(f"  probed {conv.sample_id}: {len(rows)} questions", flush=True)

    summary = aggregate(all_rows)

    out = {
        "benchmark": "LoCoMo",
        "system": "IronMem",
        "analysis": "fidelity_suite",
        "strategy": args.strategy,
        "pool": args.pool,
        "final_limit": args.final_limit,
        "store_limit": args.store_limit,
        "coverage_threshold": args.coverage,
        "scored_source": args.scored,
        "scored_questions_in_file": n_scored,
        "classified": len(all_rows),
        "unmatched_or_unscored": missing,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "per_question": all_rows,
    }
    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = REPO_ROOT / "results" / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2))

    _print_table(summary)
    print(
        f"\nclassified {len(all_rows)} / {n_scored} scored cats-1-4 questions "
        f"({missing} unmatched/unscored)"
    )
    print(f"\n-> {out_path}")
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="IronMem LoCoMo fidelity suite (retrieval vs answerer)")
    p.add_argument("--scored", required=True, help="completed scored results JSON to join")
    p.add_argument("--strategy", choices=["session", "hybrid"], default="hybrid")
    p.add_argument("--data", default=str(DEFAULT_DATA))
    p.add_argument("--conv-index", type=int, default=None, help="probe a single conversation (smoke test)")
    p.add_argument("--limit-convs", type=int, default=None)
    p.add_argument("--pool", type=int, default=50, help="rerank candidate pool size")
    p.add_argument("--final-limit", type=int, default=10, help="final top-k the answerer saw")
    p.add_argument("--store-limit", type=int, default=2000, help="big limit to pull ~all memories for store-presence")
    p.add_argument("--coverage", type=float, default=0.5, help="gold token coverage threshold for 'present'")
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--output", default="fidelity.json")
    return p.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain(parse_args())))
