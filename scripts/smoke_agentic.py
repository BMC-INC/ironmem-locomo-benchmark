"""Smoke-test Track A: confirm the agentic answerer actually calls
retrieve_original and produces an answer, end-to-end against the live store."""
from __future__ import annotations
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from benchmark.config import Config  # noqa: E402
from benchmark.ironmem_client import IronMemClient  # noqa: E402
from benchmark.gemini import GeminiClient  # noqa: E402
from benchmark.query_agentic import retrieve_and_answer_agentic  # noqa: E402

PROJ = "/benchmark/locomo/conv-50__hybrid"
QUESTIONS = [
    "What event did Calvin attend, and on exactly what calendar date did it take place?",
    "What new hobby has Dave taken up?",
]


async def main() -> None:
    cfg = Config()
    cfg.answerer_model = "gemini-2.5-flash"
    cfg.vertex_location = "global"
    cfg.rerank = True
    cfg.pool = 50
    cfg.retrieve_limit = 10
    async with httpx.AsyncClient(timeout=cfg.request_timeout) as http:
        client = IronMemClient(cfg, http)
        gemini = GeminiClient(cfg)
        for q in QUESTIONS:
            ans, ctx, mems, n = await retrieve_and_answer_agentic(client, gemini, cfg, PROJ, q)
            print("=" * 64)
            print("Q:", q)
            print("retrieve_original calls:", n)
            print("A:", ans)


if __name__ == "__main__":
    asyncio.run(main())
