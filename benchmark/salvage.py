"""Salvage the errored questions from a prior result file.

A full LoCoMo Pro run is ~3h and writes its result only at the very end, with
no resume. So a transient infra failure mid-run (Pro 429 throttle, ADC token
expiring partway through) leaves a chunk of questions with `error` set, and the
only previous remedy was re-running all ~1986 from scratch.

This module re-answers ONLY the errored question_ids from an existing result,
reusing the exact tested answer+judge path (`run.eval_one`), and merges the
fresh rows back into a NEW output file. The original is never modified, so the
~1700 good answers already paid for are preserved no matter what.

    # verify the plan first (no API calls):
    python -m benchmark.salvage --input results/upg9_PRO_p100_k25_v3syn.json --plan

    # after `gcloud auth application-default login`, run the salvage:
    python -m benchmark.salvage --input results/upg9_PRO_p100_k25_v3syn.json \
        --output results/upg9_PRO_p100_k25_v3syn_SALVAGED.json --concurrency 4
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import httpx
from rich.console import Console

from . import __version__
from .config import CATEGORY_MAP
from .gemini import GeminiClient
from .ingest import load_conversations
from .ironmem_client import IronMemClient
from .run import Clients, REPO_ROOT, _progress, aggregate, build_config, eval_one

console = Console()
DEFAULT_DATA = REPO_ROOT / "data" / "locomo10.json"


def _errored_ids(per_question: list[dict]) -> set[str]:
    return {r["question_id"] for r in per_question if r.get("error")}


def _build_items(conversations: list, want: set[str]) -> list[tuple]:
    """Filtered [(conv, q_index, qa), ...] for exactly the wanted question_ids.

    Indices come from enumerate(conv.qa), matching how run_eval forms
    question_id, so the re-answered rows keep their original ids.
    """
    items = []
    for conv in conversations:
        for i, qa in enumerate(conv.qa):
            if f"{conv.sample_id}_q{i}" in want:
                items.append((conv, i, qa))
    return items


def _args_for(meta: dict, a) -> SimpleNamespace:
    """Reconstruct the config the original run used (models/prompt/synthesize
    from the saved metadata) plus the retrieval flags that the result file does
    not store (passed on the CLI, defaulting to the headline config)."""
    return SimpleNamespace(
        answerer_model=a.answerer_model or meta.get("answerer_model"),
        judge_model=a.judge_model or meta.get("judge_model"),
        answer_prompt=a.answer_prompt or meta.get("answer_prompt_version") or "v1",
        synthesize=meta.get("synthesize", False) if a.synthesize is None else a.synthesize,
        synthesis_model=meta.get("synthesis_model"),
        concurrency=a.concurrency,
        retrieve_limit=a.retrieve_limit,
        pool=a.pool,
        multi_query=a.multi_query,
        route=a.route,
        vertex_project=a.vertex_project,
        vertex_location=a.vertex_location,
        rerank=a.rerank,
    )


async def _reanswer(cfg, conversations, items, strategy: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=cfg.request_timeout) as http:
        clients = Clients(IronMemClient(cfg, http), GeminiClient(cfg))
        try:
            status = await clients.ironmem.status()
            console.print(f"[dim]server ok: {status.get('memories')} memories, "
                          f"{status.get('observations')} observations[/dim]")
        except Exception as exc:
            console.print(f"[red]Cannot reach IronMem: {exc}[/red]")
            raise
        sem = asyncio.Semaphore(cfg.max_concurrency)
        with _progress() as progress:
            task = progress.add_task(f"salvage judge={cfg.judge_model}", total=len(items))
            rows = await asyncio.gather(*(
                eval_one(clients, cfg, sem, conv, i, qa, strategy, False, progress, task)
                for conv, i, qa in items
            ))
    return list(rows)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Re-answer only the errored questions from a prior result.")
    p.add_argument("--input", required=True, help="prior result JSON with errored questions")
    p.add_argument("--output", default=None, help="merged output (default: <input>_SALVAGED.json)")
    p.add_argument("--data", default=str(DEFAULT_DATA))
    p.add_argument("--plan", action="store_true", help="report what would be re-run, make NO API calls")
    p.add_argument("--concurrency", type=int, default=4, help="keep low to avoid re-tripping the Pro throttle")
    # retrieval flags not stored in the result file — default to the headline config
    p.add_argument("--retrieve-limit", type=int, default=25)
    p.add_argument("--pool", type=int, default=100)
    p.add_argument("--rerank", action="store_true", default=True)
    p.add_argument("--vertex-location", default="us-west1")
    p.add_argument("--vertex-project", default=None)
    # rarely overridden; default to the saved metadata
    p.add_argument("--answerer-model", default=None)
    p.add_argument("--judge-model", default=None)
    p.add_argument("--answer-prompt", default=None, choices=["v1", "v2", "v3", None])
    p.add_argument("--synthesize", default=None, action=argparse.BooleanOptionalAction)
    p.add_argument("--synthesis-model", default=None)
    p.add_argument("--multi-query", type=int, default=None)
    p.add_argument("--route", action="store_true")
    a = p.parse_args(argv)

    src = Path(a.input)
    doc = json.load(open(src))
    per_q = doc["per_question"]
    want = _errored_ids(per_q)
    strategy = doc.get("ingest_strategy", "hybrid")

    conversations = load_conversations(a.data)
    items = _build_items(conversations, want)

    by_cat = Counter(CATEGORY_MAP.get(int(qa.get("category", 0)), "?") for _, _, qa in items)
    console.print(f"[bold]{len(want)} errored question(s)[/bold] in {src.name}; "
                  f"matched {len(items)} to the dataset.")
    console.print(f"[dim]by category: {dict(by_cat)}[/dim]")
    missing = len(want) - len(items)
    if missing:
        console.print(f"[yellow]⚠ {missing} errored id(s) did not match any dataset question[/yellow]")

    if a.plan:
        console.print("[green]--plan: no API calls made.[/green]")
        return 0

    cfg = build_config(_args_for(doc, a))
    new_rows = asyncio.run(_reanswer(cfg, conversations, items, strategy))

    rescued = {r["question_id"]: r for r in new_rows}
    still_err = [r for r in new_rows if r.get("error")]
    merged = [rescued.get(r["question_id"], r) for r in per_q]

    results, counts = aggregate(merged, include_adversarial=False)
    errors = sum(1 for r in merged if r.get("error"))

    doc.update({
        "results": results,
        "category_counts": counts,
        "question_count": len(merged),
        "error_count": errors,
        "per_question": merged,
        "salvaged_from": src.name,
        "salvage_reanswered": len(new_rows),
        "salvage_still_errored": len(still_err),
        "harness_version": __version__,
    })

    out = Path(a.output) if a.output else src.with_name(src.stem + "_SALVAGED.json")
    json.dump(doc, open(out, "w"), indent=2)
    console.print(f"\n[bold]re-answered {len(new_rows)}[/bold] | "
                  f"still errored {len(still_err)} | total error_count now {errors}")
    console.print(f"results: {results}")
    console.print(f"[green]→ written {out}[/green]")
    if still_err:
        console.print("[yellow]Some questions errored again (auth/throttle). Re-run salvage on the "
                      "new file to top up the remainder.[/yellow]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
