"""Representation-fidelity suite for IronMem on LoCoMo (paper addition #2).

arXiv 2606.24775 module M1 (Memory Representation & Storage): a memory system is
only as good as what survives *ingest*. The retrieval funnel (scripts/funnel_probe.py)
answers a binary "did the gold fact survive compression?" (gold_in_memory). This
suite grades that survival and isolates the loss attributable to the
representation/governance pipeline (compression + governed write + consolidation),
independent of retrieval ranking.

For each question it measures, against the STORED memories only (one empty-query,
big-limit pull per conversation — no per-question retrieval, so ranking cannot
confound the result):

  - answer EM   : the gold answer is recoverable verbatim from some stored memory
  - answer F1   : token-F1 of the gold answer vs. its best-matching stored memory
                  (SQuAD-style best-match)
  - answer recall (store)   : graded version of the funnel's gold_in_memory
  - evidence preservation    : token-recall of the gold *evidence turns* (qa.evidence
                  → dia_id text) that survived into storage
  - representation loss      : 1 − (evidence recall in store / evidence recall in
                  transcript). The transcript term is the ceiling of what was
                  extractable, so the ratio isolates the degradation the storage
                  pipeline introduced — "degradation attributable to governance ops,
                  not just write success."

No Vertex / answerer / judge: this is a local fidelity measurement, safe to run
without an LLM call. Use the retrieval funnel for "where is it lost on the way to
the answerer" and this for "how faithfully is it stored at all."

Usage:
  .venv/bin/python -m scripts.fidelity_probe --strategy hybrid --store-limit 2000 \
      --output fidelity_hybrid.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmark.config import CATEGORY_MAP, Config  # noqa: E402
from benchmark.ironmem_client import IronMemClient  # noqa: E402
from scripts.funnel_probe import _ground_truth, _tokens  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = REPO_ROOT / "data" / "locomo10.json"


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — for the EM check."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())).strip()


def _f1(gold: set[str], other: set[str]) -> float:
    """SQuAD-style token F1 between two token sets."""
    if not gold or not other:
        return 0.0
    overlap = len(gold & other)
    if overlap == 0:
        return 0.0
    precision = overlap / len(other)
    recall = overlap / len(gold)
    return 2 * precision * recall / (precision + recall)


def _recall(gold: set[str], other: set[str]) -> float:
    if not gold:
        return 0.0
    return len(gold & other) / len(gold)


def _dia_map(conversation: dict) -> dict[str, str]:
    """Map every dia_id (e.g. 'D1:3') to its turn text across all sessions."""
    out: dict[str, str] = {}
    for key, val in conversation.items():
        if not (isinstance(val, list) and val and isinstance(val[0], dict)):
            continue
        for turn in val:
            did = turn.get("dia_id")
            if did:
                out[did] = turn.get("text", "")
    return out


def _evidence_text(qa: dict, dia: dict[str, str]) -> str:
    ev = qa.get("evidence") or []
    if isinstance(ev, str):
        ev = [ev]
    parts = []
    for e in ev:
        # evidence ids are usually dia_ids; tolerate non-matching entries.
        if isinstance(e, str) and e in dia:
            parts.append(dia[e])
    return " ".join(parts)


# --- per-question fidelity ---------------------------------------------------

CATS = ("multi_hop", "temporal", "open_domain", "single_hop")


def fidelity_one(
    qa: dict,
    mem_texts: list[str],
    mem_token_sets: list[set[str]],
    store_tokens: set[str],
    store_norm: str,
    transcript_tokens: set[str],
    dia: dict[str, str],
) -> dict:
    category = int(qa.get("category", 0))
    gold_answer = _ground_truth(qa, category)
    gold_tokens = _tokens(gold_answer)

    ev_text = _evidence_text(qa, dia)
    ev_tokens = _tokens(ev_text)

    # Answer-level representation fidelity (against the store).
    answer_recall = _recall(gold_tokens, store_tokens)
    best_f1 = max((_f1(gold_tokens, m) for m in mem_token_sets), default=0.0)
    gold_norm = _normalize(gold_answer)
    answer_em = 1 if gold_norm and gold_norm in store_norm else 0

    # Evidence preservation + degradation attributable to the storage pipeline.
    ev_recall_store = _recall(ev_tokens, store_tokens)
    ev_recall_transcript = _recall(ev_tokens, transcript_tokens)  # ceiling (≈1.0)
    if ev_recall_transcript > 0:
        representation_loss = max(0.0, 1.0 - ev_recall_store / ev_recall_transcript)
    else:
        representation_loss = 0.0  # no recoverable evidence text to grade

    return {
        "question": str(qa.get("question", "")),
        "category": CATEGORY_MAP.get(category, str(category)),
        "category_int": category,
        "ground_truth": gold_answer,
        "answer_em": answer_em,
        "answer_f1": round(best_f1, 4),
        "answer_recall_store": round(answer_recall, 4),
        "evidence_recall_store": round(ev_recall_store, 4),
        "evidence_recall_transcript": round(ev_recall_transcript, 4),
        "representation_loss": round(representation_loss, 4),
        "has_evidence": bool(ev_tokens),
    }


def _mean(xs: list[float]) -> float:
    return round(sum(xs) / len(xs), 4) if xs else 0.0


def aggregate(rows: list[dict]) -> dict:
    rows = [r for r in rows if r["category_int"] != 5]  # exclude adversarial
    ev_rows = [r for r in rows if r["has_evidence"]]

    def block(rs: list[dict]) -> dict:
        ev = [r for r in rs if r["has_evidence"]]
        return {
            "n": len(rs),
            "answer_em_pct": round(100.0 * sum(r["answer_em"] for r in rs) / len(rs), 1) if rs else 0.0,
            "answer_f1": _mean([r["answer_f1"] for r in rs]),
            "answer_recall_store": _mean([r["answer_recall_store"] for r in rs]),
            "evidence_preservation": _mean([r["evidence_recall_store"] for r in ev]),
            "representation_loss": _mean([r["representation_loss"] for r in ev]),
        }

    by_cat = {}
    for cat in CATS:
        crows = [r for r in rows if r["category"] == cat]
        if crows:
            by_cat[cat] = block(crows)

    overall = block(rows)
    overall["evidence_questions"] = len(ev_rows)
    overall["by_category"] = by_cat
    return overall


# --- driver ------------------------------------------------------------------

async def pull_store(client: IronMemClient, project: str, store_limit: int) -> list[str]:
    mems = await client.get_context(project, query="", limit=store_limit, rerank=False)
    out = []
    for m in mems:
        s = (m.get("summary") or "").strip()
        tags = (m.get("tags") or "").strip()
        if s or tags:
            out.append((s + " " + tags).strip())
    return out


def transcript_tokens_for(conversation: dict) -> set[str]:
    toks: set[str] = set()
    for key, val in conversation.items():
        if isinstance(val, list) and val and isinstance(val[0], dict):
            for turn in val:
                toks |= _tokens(turn.get("text", ""))
    return toks


async def amain(args) -> int:
    cfg = Config()
    if args.concurrency:
        cfg.max_concurrency = args.concurrency

    data = json.loads(Path(args.data).read_text())
    if args.limit_convs:
        data = data[: args.limit_convs]

    async with httpx.AsyncClient(timeout=cfg.request_timeout) as http:
        client = IronMemClient(cfg, http)
        try:
            await client.status()
        except Exception as exc:
            print(f"Cannot reach IronMem at {cfg.ironmem_url}: {exc}")
            return 2

        all_rows: list[dict] = []
        for sample in data:
            sample_id = sample["sample_id"]
            project = cfg.project_for(sample_id, args.strategy)
            conversation = sample["conversation"]
            dia = _dia_map(conversation)
            transcript_tokens = transcript_tokens_for(conversation)

            mem_texts = await pull_store(client, project, args.store_limit)
            mem_token_sets = [_tokens(t) for t in mem_texts]
            store_tokens: set[str] = set().union(*mem_token_sets) if mem_token_sets else set()
            store_norm = _normalize(" ".join(mem_texts))

            rows = [
                fidelity_one(
                    qa, mem_texts, mem_token_sets, store_tokens, store_norm,
                    transcript_tokens, dia,
                )
                for qa in sample.get("qa", [])
            ]
            all_rows.extend(rows)
            print(f"  probed {sample_id}: {len(rows)} questions, {len(mem_texts)} memories")

    summary = aggregate(all_rows)

    out = {
        "benchmark": "LoCoMo",
        "system": "IronMem",
        "analysis": "representation_fidelity",
        "paper_addition": 2,
        "strategy": args.strategy,
        "store_limit": args.store_limit,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "per_question": all_rows,
    }
    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = REPO_ROOT / "results" / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2))

    s = summary
    print("\n=== REPRESENTATION FIDELITY (cats 1-4, M1 / paper #2) ===")
    print(f"questions: {s['n']}  (with gold evidence: {s['evidence_questions']})")
    print(f"  answer EM            {s['answer_em_pct']:5.1f}%")
    print(f"  answer F1            {s['answer_f1']:.3f}")
    print(f"  answer recall@store  {s['answer_recall_store']:.3f}")
    print(f"  evidence preservation{s['evidence_preservation']:6.3f}")
    print(f"  representation loss  {s['representation_loss']:.3f}  (degradation attributable to storage)")
    print("  by category:")
    for cat, b in s["by_category"].items():
        print(
            f"    {cat:12s} n={b['n']:<4d} EM={b['answer_em_pct']:5.1f}%  F1={b['answer_f1']:.3f}  "
            f"evid={b['evidence_preservation']:.3f}  loss={b['representation_loss']:.3f}"
        )
    print(f"\n→ {out_path}")
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="IronMem LoCoMo representation-fidelity suite (paper #2)")
    p.add_argument("--strategy", choices=["session", "hybrid"], default="hybrid")
    p.add_argument("--data", default=str(DEFAULT_DATA))
    p.add_argument("--limit-convs", type=int, default=None)
    p.add_argument("--store-limit", type=int, default=2000, help="big limit to pull ~all memories")
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--output", default="fidelity_hybrid.json")
    return p.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain(parse_args())))
