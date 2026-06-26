"""Judge-robustness check: re-judge a random sample with a SECOND judge model
(default Gemini 2.5 Flash) and measure agreement with the headline Pro judge.

This bounds the "Gemini graded Gemini" self-preference concern while staying
100% first-party (everything bills the GCP trial credit — no third-party model).
High agreement (typical for factual matching) means the headline score is not an
artifact of one judge's idiosyncrasies.

Usage:
  .venv/bin/python scripts/judge_agreement.py \
      results/upg_hybrid_C_rerank_pool50.json \
      --judge-model gemini-2.5-flash --sample 200 --output agreement_C.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from benchmark.config import Config  # noqa: E402
from benchmark.gemini import GeminiClient  # noqa: E402
from benchmark.judge import judge_answer  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


class _Clients:
    def __init__(self, gemini):
        self.gemini = gemini


def _kappa(a: int, b: int, c: int, d: int) -> float:
    """Cohen's kappa for a 2x2 table. a=both1, d=both0, b=pro1/2nd0, c=pro0/2nd1."""
    n = a + b + c + d
    if n == 0:
        return 0.0
    po = (a + d) / n
    p_pro1 = (a + b) / n
    p_2nd1 = (a + c) / n
    pe = p_pro1 * p_2nd1 + (1 - p_pro1) * (1 - p_2nd1)
    return round((po - pe) / (1 - pe), 4) if pe != 1 else 1.0


async def amain(args) -> int:
    cfg = Config()
    cfg.judge_model = args.judge_model
    # Flash supports disabling thinking → fast/cheap second-pass judging.
    cfg.judge_thinking_budget = 0
    if args.concurrency:
        cfg.max_concurrency = args.concurrency

    src = Path(args.results)
    if not src.is_absolute():
        src = REPO_ROOT / src
    data = json.loads(src.read_text())
    rows = [
        r for r in data.get("per_question", [])
        if r.get("score") is not None and r.get("category_int") in (1, 2, 3, 4)
    ]
    random.seed(args.seed)
    if args.sample and len(rows) > args.sample:
        rows = random.sample(rows, args.sample)

    gemini = GeminiClient(cfg)
    clients = _Clients(gemini)
    sem = asyncio.Semaphore(cfg.max_concurrency)

    async def rejudge(r: dict) -> dict:
        async with sem:
            try:
                s2 = await judge_answer(
                    cfg, clients,
                    r["question"], r["ground_truth"], r["generated_answer"],
                    int(r["category_int"]),
                )
            except Exception as exc:
                return {**r, "second_score": None, "second_error": f"{type(exc).__name__}: {exc}"}
        return {**r, "second_score": s2}

    judged = await asyncio.gather(*(rejudge(r) for r in rows))
    judged = [r for r in judged if r.get("second_score") is not None]

    a = sum(1 for r in judged if r["score"] == 1 and r["second_score"] == 1)
    b = sum(1 for r in judged if r["score"] == 1 and r["second_score"] == 0)
    c = sum(1 for r in judged if r["score"] == 0 and r["second_score"] == 1)
    d = sum(1 for r in judged if r["score"] == 0 and r["second_score"] == 0)
    n = a + b + c + d
    agree = round(100.0 * (a + d) / n, 1) if n else 0.0
    kappa = _kappa(a, b, c, d)
    pro_acc = round(100.0 * (a + b) / n, 1) if n else 0.0
    second_acc = round(100.0 * (a + c) / n, 1) if n else 0.0

    out = {
        "source": str(src),
        "headline_judge": data.get("judge_model"),
        "second_judge": args.judge_model,
        "sample_n": n,
        "raw_agreement_pct": agree,
        "cohens_kappa": kappa,
        "pro_accuracy_pct_on_sample": pro_acc,
        "second_accuracy_pct_on_sample": second_acc,
        "confusion": {"both_correct": a, "pro1_second0": b, "pro0_second1": c, "both_wrong": d},
    }
    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = REPO_ROOT / "results" / out_path
    out_path.write_text(json.dumps(out, indent=2))

    print("\n=== JUDGE AGREEMENT (headline Pro vs second judge) ===")
    print(f"  source        : {src.name}")
    print(f"  second judge  : {args.judge_model}")
    print(f"  sample n      : {n}")
    print(f"  raw agreement : {agree}%")
    print(f"  Cohen's kappa : {kappa}")
    print(f"  accuracy on sample — Pro: {pro_acc}%  |  {args.judge_model}: {second_acc}%")
    print(f"  confusion     : both✓={a}  pro✓/2nd✗={b}  pro✗/2nd✓={c}  both✗={d}")
    print(f"\n→ {out_path}")
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Second-judge agreement check")
    p.add_argument("results", help="scored results JSON (the headline config)")
    p.add_argument("--judge-model", default="gemini-2.5-flash")
    p.add_argument("--sample", type=int, default=200, help="0 = all scored questions")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--concurrency", type=int, default=10)
    p.add_argument("--output", default="judge_agreement.json")
    return p.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain(parse_args())))
