"""Track B transitive synthesis over every LoCoMo project (POST /synthesize).

Derives new multi-hop facts from existing facts and stores them additively, and
positively reinforces the source facts — which populates the #5 temporal-trust
signal (trust_ref_count / trust_last_validated_at). MUTATES the store, so
snapshot mem.db first. Synthesis is synchronous (one LLM call per entity-group),
hence the long per-request timeout and the per-project group cap.

Usage:
  .venv/bin/python scripts/run_synthesis.py            # DRY RUN (counts only)
  .venv/bin/python scripts/run_synthesis.py --apply    # writes derived facts
"""
import asyncio
import sys
from pathlib import Path

import httpx

REPO = Path("/Users/kingjames/Projects/ironmem-locomo-benchmark")
sys.path.insert(0, str(REPO))
from benchmark.config import Config  # noqa: E402
from benchmark.ingest import load_conversations  # noqa: E402

MAX_GROUPS = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--max-groups=")), "150"))


async def main():
    cfg = Config()
    apply = "--apply" in sys.argv
    convs = load_conversations(str(REPO / "data/locomo10.json"))
    tot = {"scanned": 0, "groups": 0, "derived": 0, "sources_reinforced": 0}
    print(f"Synthesis {'APPLY' if apply else 'DRY-RUN'} | {len(convs)} projects | max_groups={MAX_GROUPS}")
    async with httpx.AsyncClient(timeout=1800) as http:
        for conv in convs:
            project = cfg.project_for(conv.sample_id, "hybrid")
            body = {"project": project, "apply": apply, "limit": 2000, "max_groups": MAX_GROUPS}
            try:
                r = await http.post(f"{cfg.ironmem_url}/synthesize", json=body)
                r.raise_for_status()
                rep = r.json()
            except Exception as e:
                print(f"  {project}: ERROR {e}", flush=True)
                continue
            for k in tot:
                tot[k] += int(rep.get(k, 0))
            print(
                f"  {project}: scanned={rep.get('scanned')} groups={rep.get('groups')} "
                f"derived={rep.get('derived')} reinforced={rep.get('sources_reinforced')}",
                flush=True,
            )
    print(f"\nTOTAL ({'APPLIED' if apply else 'DRY-RUN'}): {tot}")


asyncio.run(main())
