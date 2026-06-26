"""Retrieval funnel probe for IronMem on LoCoMo.

Measures, per question, WHERE the gold fact is lost on its way from the raw
transcript to the answerer's context — without any server-side instrumentation.
It re-uses the existing /context endpoint at different limit/pool/rerank settings
and token-matches the gold answer against the returned memory summaries.

Funnel stages (each a yes/no per question):
  1. gold_in_transcript  — gold content present in the raw conversation text
  2. gold_in_memory      — gold content present in ANY stored memory
                           (empty-query, big-limit pull ≈ the whole project store)
                           → "compression kept the fact"
  3. in_pool@25 / @50    — gold present in top-25 / top-50 hybrid candidates
                           (rerank OFF) → "candidate pool has the fact"
  4. reranker_kept       — gold present in the final top-`limit` after LLM rerank
                           over a `pool`-sized candidate set → "reranker kept it"
  5. answerer_used       — joined from a scored results file (score==1), optional
                           → "answerer used it"

"Present" = content-token coverage >= --coverage (default 0.5), the SAME rule
scripts/analyze_failures.py uses, so the two analyses are directly comparable.

Usage:
  .venv/bin/python -m scripts.funnel_probe --strategy hybrid --pool 50 \
      --scored results/<the matching limit=10 rerank pool=50 run>.json \
      --output results/funnel_hybrid.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmark.config import CATEGORY_MAP, Config  # noqa: E402
from benchmark.ingest import load_conversations  # noqa: E402
from benchmark.ironmem_client import IronMemClient  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = REPO_ROOT / "data" / "locomo10.json"

# --- tokenization (kept identical to scripts/analyze_failures.py) -----------
_MONTHS = {
    "january": "1", "february": "2", "march": "3", "april": "4", "may": "5",
    "june": "6", "july": "7", "august": "8", "september": "9", "october": "10",
    "november": "11", "december": "12",
}
_STOP = {
    "the", "a", "an", "is", "was", "were", "are", "be", "been", "to", "of", "in",
    "on", "at", "and", "or", "for", "with", "as", "by", "that", "this", "it",
    "he", "she", "they", "them", "his", "her", "their", "i", "you", "we", "do",
    "did", "does", "had", "has", "have", "from", "but", "not", "what", "when",
    "who", "which", "there", "here", "about",
}


def _tokens(text: str) -> set[str]:
    out: set[str] = set()
    for tok in re.findall(r"[a-z0-9]+", (text or "").lower()):
        tok = _MONTHS.get(tok, tok)
        if len(tok) >= 2 and tok not in _STOP:
            out.add(tok)
    return out


def _covered(gold: set[str], ctx_text: str, threshold: float) -> bool:
    """True if >= threshold of gold content tokens appear in ctx_text."""
    if not gold:
        return False
    ctx = _tokens(ctx_text)
    return (len(gold & ctx) / len(gold)) >= threshold


def _coverage(gold: set[str], ctx_text: str) -> float:
    if not gold:
        return 0.0
    ctx = _tokens(ctx_text)
    return round(len(gold & ctx) / len(gold), 4)


# --- transcript rendering (mirrors ingest Turn.as_line) ---------------------

def _transcript_text(conv) -> str:
    lines: list[str] = []
    for s in conv.sessions:
        if s.date_time:
            lines.append(f"[Conversation took place on {s.date_time}]")
        for t in s.turns:
            lines.append(t.as_line())
    return "\n".join(lines)


def _ground_truth(qa: dict, category: int) -> str:
    if category == 5:
        return str(qa.get("adversarial_answer") or qa.get("answer") or "")
    return str(qa.get("answer") or "")


def _memories_text(memories: list[dict]) -> str:
    parts: list[str] = []
    for m in memories:
        parts.append((m.get("summary") or "").strip())
        tags = (m.get("tags") or "").strip()
        if tags:
            parts.append(tags)
    return "\n".join(p for p in parts if p)


# --- probe ------------------------------------------------------------------

async def probe_project_store(client: IronMemClient, project: str, store_limit: int) -> str:
    """Pull ~all memories for a project (empty query → recent, big limit) so we
    can test store-presence independent of per-question retrieval ranking."""
    memories = await client.get_context(project, query="", limit=store_limit, rerank=False)
    return _memories_text(memories)


async def funnel_one(
    client: IronMemClient,
    cfg: Config,
    sem: asyncio.Semaphore,
    project: str,
    store_text: str,
    transcript_text: str,
    conv_id: str,
    q_index: int,
    qa: dict,
    pool: int,
    final_limit: int,
    coverage: float,
) -> dict:
    category = int(qa.get("category", 0))
    question = str(qa.get("question", ""))
    gold = _tokens(_ground_truth(qa, category))

    in_transcript = _covered(gold, transcript_text, coverage)
    in_memory = _covered(gold, store_text, coverage)

    async with sem:
        pool25 = await client.get_context(project, query=question, limit=25, rerank=False)
        pool50 = await client.get_context(project, query=question, limit=50, rerank=False)
        final = await client.get_context(
            project, query=question, limit=final_limit, rerank=True, pool=pool
        )

    in_pool25 = _covered(gold, _memories_text(pool25), coverage)
    in_pool50 = _covered(gold, _memories_text(pool50), coverage)
    reranked_text = _memories_text(final)
    reranker_kept = _covered(gold, reranked_text, coverage)

    return {
        "question_id": f"{conv_id}_q{q_index}",
        "conversation_id": conv_id,
        "category": CATEGORY_MAP.get(category, str(category)),
        "category_int": category,
        "question": question,
        "ground_truth": _ground_truth(qa, category),
        "gold_token_count": len(gold),
        "funnel": {
            "gold_in_transcript": in_transcript,
            "gold_in_memory": in_memory,
            "in_pool_25": in_pool25,
            "in_pool_50": in_pool50,
            "reranker_kept": reranker_kept,
        },
        "coverage": {
            "final_reranked": _coverage(gold, reranked_text),
        },
        "num_final": len(final),
    }


# --- aggregation ------------------------------------------------------------

STAGES = ["gold_in_transcript", "gold_in_memory", "in_pool_25", "in_pool_50", "reranker_kept"]


def aggregate(rows: list[dict], scored_lookup: dict[str, int | None]) -> dict:
    # Exclude adversarial (cat 5) from the headline funnel, matching the score.
    rows = [r for r in rows if r["category_int"] != 5]
    n = len(rows)
    totals = {s: sum(1 for r in rows if r["funnel"][s]) for s in STAGES}

    # answerer_used: joined from the scored run (score==1). Only meaningful where
    # the fact survived to the reranked context.
    used = 0
    used_denom = 0
    for r in rows:
        sc = scored_lookup.get(r["question_id"])
        if sc is not None:
            used_denom += 1
            if sc == 1:
                used += 1

    def pct(x: int, d: int) -> float:
        return round(100.0 * x / d, 1) if d else 0.0

    # Stage-to-stage leaks (conditional on having reached the prior stage).
    in_t = [r for r in rows if r["funnel"]["gold_in_transcript"]]
    kept_compression = [r for r in in_t if r["funnel"]["gold_in_memory"]]
    in_pool = [r for r in kept_compression if r["funnel"]["in_pool_50"]]
    survived_rerank = [r for r in in_pool if r["funnel"]["reranker_kept"]]

    by_cat: dict[str, dict] = {}
    for cat in ("multi_hop", "temporal", "open_domain", "single_hop"):
        crows = [r for r in rows if r["category"] == cat]
        if not crows:
            continue
        by_cat[cat] = {
            "n": len(crows),
            **{s: pct(sum(1 for r in crows if r["funnel"][s]), len(crows)) for s in STAGES},
        }

    return {
        "n_questions": n,
        "stage_pct": {s: pct(totals[s], n) for s in STAGES},
        "stage_counts": totals,
        "leaks": {
            "transcript_to_memory_lost": len(in_t) - len(kept_compression),
            "memory_to_pool50_lost": len(kept_compression) - len(in_pool),
            "pool50_to_reranked_lost": len(in_pool) - len(survived_rerank),
        },
        "conditional_retention_pct": {
            "compression_kept_given_in_transcript": pct(len(kept_compression), len(in_t)),
            "pool50_kept_given_in_memory": pct(len(in_pool), len(kept_compression)),
            "rerank_kept_given_in_pool50": pct(len(survived_rerank), len(in_pool)),
        },
        "answerer_used": {
            "scored_questions": used_denom,
            "correct": used,
            "accuracy_pct": pct(used, used_denom),
        },
        "by_category": by_cat,
    }


def _load_scored(path: str | None) -> dict[str, int | None]:
    if not path:
        return {}
    p = Path(path)
    if not p.is_absolute():
        p = REPO_ROOT / p
    data = json.loads(p.read_text())
    out: dict[str, int | None] = {}
    for r in data.get("per_question", []):
        out[r.get("question_id")] = r.get("score")
    return out


async def amain(args) -> int:
    cfg = Config()
    if args.concurrency:
        cfg.max_concurrency = args.concurrency

    conversations = load_conversations(args.data)
    if args.conv_index is not None:
        conversations = [conversations[args.conv_index]]
    elif args.limit_convs:
        conversations = conversations[: args.limit_convs]

    scored_lookup = _load_scored(args.scored)

    async with httpx.AsyncClient(timeout=cfg.request_timeout) as http:
        client = IronMemClient(cfg, http)
        try:
            await client.status()
        except Exception as exc:
            print(f"Cannot reach IronMem at {cfg.ironmem_url}: {exc}")
            return 2

        sem = asyncio.Semaphore(cfg.max_concurrency)
        all_rows: list[dict] = []
        for conv in conversations:
            project = cfg.project_for(conv.sample_id, args.strategy)
            store_text = await probe_project_store(client, project, args.store_limit)
            transcript_text = _transcript_text(conv)
            tasks = [
                funnel_one(
                    client, cfg, sem, project, store_text, transcript_text,
                    conv.sample_id, i, qa, args.pool, args.final_limit, args.coverage,
                )
                for i, qa in enumerate(conv.qa)
            ]
            rows = await asyncio.gather(*tasks)
            all_rows.extend(rows)
            print(f"  probed {conv.sample_id}: {len(rows)} questions")

    summary = aggregate(all_rows, scored_lookup)

    out = {
        "benchmark": "LoCoMo",
        "system": "IronMem",
        "analysis": "retrieval_funnel",
        "strategy": args.strategy,
        "pool": args.pool,
        "final_limit": args.final_limit,
        "coverage_threshold": args.coverage,
        "scored_source": args.scored,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "per_question": all_rows,
    }
    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = REPO_ROOT / "results" / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2))

    # Pretty print the funnel.
    s = summary
    print("\n=== RETRIEVAL FUNNEL (cats 1-4) ===")
    print(f"questions: {s['n_questions']}")
    for stage in STAGES:
        print(f"  {stage:22s} {s['stage_pct'][stage]:5.1f}%  ({s['stage_counts'][stage]})")
    print("  conditional retention:")
    for k, v in s["conditional_retention_pct"].items():
        print(f"    {k:42s} {v:5.1f}%")
    print("  leaks (absolute questions lost at each hop):")
    for k, v in s["leaks"].items():
        print(f"    {k:32s} {v}")
    if s["answerer_used"]["scored_questions"]:
        a = s["answerer_used"]
        print(f"  answerer accuracy (joined): {a['accuracy_pct']}%  ({a['correct']}/{a['scored_questions']})")
    print(f"\n→ {out_path}")
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="IronMem LoCoMo retrieval funnel probe")
    p.add_argument("--strategy", choices=["session", "hybrid"], default="hybrid")
    p.add_argument("--data", default=str(DEFAULT_DATA))
    p.add_argument("--conv-index", type=int, default=None, help="probe a single conversation (smoke test)")
    p.add_argument("--limit-convs", type=int, default=None)
    p.add_argument("--pool", type=int, default=50, help="rerank candidate pool size")
    p.add_argument("--final-limit", type=int, default=10, help="final top-k the answerer sees")
    p.add_argument("--store-limit", type=int, default=500, help="big limit to pull ~all memories for store-presence")
    p.add_argument("--coverage", type=float, default=0.5, help="gold token coverage threshold for 'present'")
    p.add_argument("--scored", default=None, help="scored results JSON to join answerer_used (score==1)")
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--output", default="funnel_hybrid.json")
    return p.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain(parse_args())))
