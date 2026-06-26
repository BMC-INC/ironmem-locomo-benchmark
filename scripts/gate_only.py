"""Fast gate-only probe: full 10-conv gold_in_memory for the live store,
reusing funnel_probe's exact helpers (so it's comparable to 72.3% / 65.8%).
Skips the slow rerank stage entirely — just the no-rerank store-presence probe."""
import asyncio, importlib.util, sys
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


async def main():
    cfg = Config()
    convs = load_conversations(str(REPO / "data/locomo10.json"))
    cov = 0.5
    store_limit = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    n = n_t = n_m = 0
    async with httpx.AsyncClient(timeout=cfg.request_timeout) as http:
        client = IronMemClient(cfg, http)
        await client.status()
        for conv in convs:
            project = cfg.project_for(conv.sample_id, "hybrid")
            store_text = await fp.probe_project_store(client, project, store_limit)
            transcript_text = fp._transcript_text(conv)
            for qa in conv.qa:
                category = int(qa.get("category", 0))
                if category == 5:
                    continue
                gold = fp._tokens(fp._ground_truth(qa, category))
                n += 1
                if fp._covered(gold, transcript_text, cov):
                    n_t += 1
                if fp._covered(gold, store_text, cov):
                    n_m += 1
            print(f"  probed {conv.sample_id}: cumulative n={n}", flush=True)
    print(f"\nRESULT — coverage-only store, all {len(convs)} convs, cats 1-4:")
    print(f"  n = {n}  (matches funnel's 1540 if method is identical)")
    print(f"  gold_in_transcript = {100*n_t/n:.1f}%")
    print(f"  gold_in_memory     = {100*n_m/n:.1f}%   vs OLD 72.3% | Phase1 65.8%")


asyncio.run(main())
