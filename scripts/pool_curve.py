"""LLM-free raw-recall curve: how much gold sits in the raw top-N retrieval
(rerank=False) on the LIVE store, swept over N. Pinpoints the retrieve-limit
headroom that the reranker's top-10 cut is currently throwing away.

One /context call per question (pull top-50 once, slice at each N) — no LLM,
no answerer, no judge. Reuses funnel_probe's exact coverage helpers so numbers
are directly comparable to the funnel's in_pool_25 / in_pool_50 stages.

Usage: .venv/bin/python scripts/pool_curve.py [max_limit] [concurrency]
"""
import asyncio, importlib.util, sys
from collections import defaultdict
from pathlib import Path

REPO = Path("/Users/kingjames/Projects/ironmem-locomo-benchmark")
sys.path.insert(0, str(REPO))
spec = importlib.util.spec_from_file_location("funnel_probe", REPO / "scripts/funnel_probe.py")
fp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fp)

from benchmark.config import Config
from benchmark.ingest import load_conversations
from benchmark.ironmem_client import IronMemClient
import httpx

LIMITS = [10, 15, 20, 25, 30, 40, 50]
COV = 0.5


async def main():
    cfg = Config()
    max_limit = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    conc = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    convs = load_conversations(str(REPO / "data/locomo10.json"))
    limits = [n for n in LIMITS if n <= max_limit]
    sem = asyncio.Semaphore(conc)

    # tallies: overall + per-category, count of gold-covered at each N
    n = 0
    hit = {N: 0 for N in limits}
    cat_n = defaultdict(int)
    cat_hit = defaultdict(lambda: {N: 0 for N in limits})

    async def one(project, transcript_text, qa):
        nonlocal n
        category = int(qa.get("category", 0))
        if category == 5:
            return
        gold = fp._tokens(fp._ground_truth(qa, category))
        question = str(qa.get("question", ""))
        async with sem:
            mems = await client.get_context(project, query=question, limit=max_limit, rerank=False)
        n += 1
        cat = fp.CATEGORY_MAP.get(category, str(category))
        cat_n[cat] += 1
        for N in limits:
            if fp._covered(gold, fp._memories_text(mems[:N]), COV):
                hit[N] += 1
                cat_hit[cat][N] += 1

    async with httpx.AsyncClient(timeout=cfg.request_timeout) as http:
        client = IronMemClient(cfg, http)
        await client.status()
        for conv in convs:
            project = cfg.project_for(conv.sample_id, "hybrid")
            transcript_text = fp._transcript_text(conv)
            await asyncio.gather(*(one(project, transcript_text, qa) for qa in conv.qa))
            print(f"  probed {conv.sample_id}: cumulative n={n}", flush=True)

    print(f"\nRAW recall@N curve (rerank=False), coverage store, cats 1-4, n={n}")
    print("  (reranked top-10 was 72.6% in funnel_hybrid — this is the raw ceiling per N)")
    hdr = "  N:      " + "".join(f"{N:>7}" for N in limits)
    print(hdr)
    print("  overall " + "".join(f"{100*hit[N]/n:>6.1f}%" for N in limits))
    for cat in sorted(cat_n):
        cn = cat_n[cat]
        print(f"  {cat:<8}" + "".join(f"{100*cat_hit[cat][N]/cn:>6.1f}%" for N in limits) + f"   (n={cn})")


asyncio.run(main())
