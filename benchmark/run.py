"""IronMem LoCoMo benchmark — main entrypoint (ingest -> query -> judge -> report).

Examples:
    python -m benchmark.run --dry-run                  # 1 conversation, fast sanity run
    python -m benchmark.run --dry-run --judge-model gpt-4o
    python -m benchmark.run --strategy both            # full run, both ingest strategies
    python -m benchmark.run --skip-ingest              # re-score without re-ingesting
    python -m benchmark.run --strategy hybrid --wipe   # clean re-ingest then score
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx
from rich.console import Console
from rich.progress import (BarColumn, MofNCompleteColumn, Progress, TextColumn,
                           TimeElapsedColumn, TimeRemainingColumn)
from rich.table import Table

from . import __version__
from .config import (CATEGORY_MAP, DEFAULT_ANSWERER_MODEL,
                     DEFAULT_JUDGE_MODEL, REPO_ROOT, Config)
from .gemini import GeminiClient
from .ingest import ingest_conversation, load_conversations, total_sessions
from .ironmem_client import IronMemClient
from .judge import judge_answer
from .query import retrieve_and_answer

console = Console()
DEFAULT_DATA = REPO_ROOT / "data" / "locomo10.json"


class Clients:
    def __init__(self, ironmem: IronMemClient, gemini: GeminiClient) -> None:
        self.ironmem = ironmem
        self.gemini = gemini


def ground_truth_for(qa: dict, category: int) -> str:
    if category == 5:
        return str(qa.get("adversarial_answer") or qa.get("answer") or "")
    return str(qa.get("answer") or "")


def _progress() -> Progress:
    return Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        TextColumn("eta"),
        TimeRemainingColumn(),
        console=console,
    )


# --- subprocess helpers (CLI-only operations) ------------------------------

async def wipe_project(project: str) -> None:
    binary = shutil.which("ironmem")
    if not binary:
        return
    proc = await asyncio.create_subprocess_exec(
        binary, "wipe", "-p", project, "-f",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()


def embedder_info() -> str | None:
    """Runtime truth: the server logs the embedder it resolved at startup
    (e.g. "bge-small-en-v1.5 (dim 384)" or "none (keyword/FTS-only retrieval)").
    Static `ironmem config` can read "model: null" even when the onnx embedder is
    active, so we trust the log line instead."""
    log = Path.home() / ".ironmem" / "server.log"
    try:
        if log.exists():
            for line in reversed(log.read_text(errors="ignore").splitlines()):
                if "Embedder:" in line:
                    return line.split("Embedder:", 1)[1].strip()
    except Exception:
        pass
    return None


# --- phases ----------------------------------------------------------------

async def run_ingest(clients: Clients, cfg: Config, conversations: list, strategy: str, *, wipe: bool) -> None:
    sessions_total = total_sessions(conversations)

    # `wipe_project` shells out to `ironmem wipe`, which opens the SQLite DB
    # directly. Running several wipes concurrently (or alongside active ingest
    # writes) deadlocks on the shared DB — fine for a 1-conversation dry-run, but
    # multi-conversation runs would hang. So wipe ALL projects sequentially up
    # front, then ingest concurrently against the already-cleaned projects.
    if wipe:
        for conv in conversations:
            await wipe_project(cfg.project_for(conv.sample_id, strategy))

    sem = asyncio.Semaphore(cfg.ingest_concurrency)
    with _progress() as progress:
        task = progress.add_task(f"ingest:{strategy}", total=sessions_total)

        async def _one(conv) -> None:
            async with sem:
                await ingest_conversation(
                    clients.ironmem, cfg, conv, strategy,
                    gemini=clients.gemini,
                    on_session_done=lambda: progress.advance(task),
                )

        await asyncio.gather(*(_one(c) for c in conversations))


async def eval_one(clients, cfg, sem, conv, q_index, qa, strategy, judge_adversarial, progress, task) -> dict:
    category = int(qa.get("category", 0))
    question = str(qa.get("question", ""))
    gt = ground_truth_for(qa, category)
    project = cfg.project_for(conv.sample_id, strategy)
    error = None
    answer, context_text, memories, score = "", "", [], None
    # LoCoMo adversarial (cat 5) are false-premise questions; mem0/locomo do not
    # score them by gold-answer matching. We still answer + log them, but leave
    # score=None (not scored) unless --include-adversarial is set.
    do_judge = category != 5 or judge_adversarial
    async with sem:
        try:
            answer, context_text, memories = await retrieve_and_answer(
                clients.ironmem, clients.gemini, cfg, project, question
            )
            if do_judge:
                score = await judge_answer(cfg, clients, question, gt, answer, category)
        except Exception as exc:  # keep the run going; record the failure
            error = f"{type(exc).__name__}: {exc}"
            score = 0 if do_judge else None
    progress.advance(task)
    return {
        "conversation_id": conv.sample_id,
        "question_id": f"{conv.sample_id}_q{q_index}",
        "category": CATEGORY_MAP.get(category, str(category)),
        "category_int": category,
        "question": question,
        "ground_truth": gt,
        "retrieved_context": context_text,
        "num_retrieved": len(memories),
        "generated_answer": answer,
        "score": score,
        "error": error,
    }


async def run_eval(clients: Clients, cfg: Config, conversations: list, strategy: str, judge_adversarial: bool) -> list[dict]:
    items = [(conv, i, qa) for conv in conversations for i, qa in enumerate(conv.qa)]
    sem = asyncio.Semaphore(cfg.max_concurrency)
    with _progress() as progress:
        task = progress.add_task(f"eval:{strategy} judge={cfg.judge_model}", total=len(items))
        rows = await asyncio.gather(
            *(eval_one(clients, cfg, sem, conv, i, qa, strategy, judge_adversarial, progress, task)
              for conv, i, qa in items)
        )
    return list(rows)


# --- reporting -------------------------------------------------------------

def aggregate(rows: list[dict], include_adversarial: bool) -> tuple[dict, dict]:
    # Scored tallies count only rows with a non-None score (cat-5 is None unless
    # --include-adversarial). Counts below tally every logged question per category.
    correct: dict[int, int] = defaultdict(int)
    scored: dict[int, int] = defaultdict(int)
    for r in rows:
        if r["score"] is None:
            continue
        c = r["category_int"]
        scored[c] += 1
        correct[c] += r["score"]

    results: dict[str, float] = {}
    for c in (1, 2, 3, 4):
        if scored[c]:
            results[CATEGORY_MAP[c]] = round(correct[c] / scored[c], 4)
    if include_adversarial and scored[5]:
        results["adversarial"] = round(correct[5] / scored[5], 4)

    overall_cats = (1, 2, 3, 4, 5) if include_adversarial else (1, 2, 3, 4)
    ot = sum(scored[c] for c in overall_cats)
    oc = sum(correct[c] for c in overall_cats)
    results["overall"] = round(oc / ot, 4) if ot else 0.0

    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        counts[r["category"]] += 1
    return results, dict(counts)


def write_output(strategy, results, counts, rows, cfg, args) -> Path:
    errors = sum(1 for r in rows if r["error"])
    out = {
        "system": f"IronMem v0.4.0 ({strategy})",
        "benchmark": "LoCoMo",
        "dataset_version": args.dataset_version,
        "ingest_strategy": strategy,
        "answerer_model": cfg.answerer_model,
        "judge_model": cfg.judge_model,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "harness_version": __version__,
        "overall_scope": "categories 1-5" if args.include_adversarial else "categories 1-4 (adversarial excluded)",
        "question_count": len(rows),
        "error_count": errors,
        "category_counts": counts,
        "results": results,
        "per_question": rows,
    }
    results_dir = REPO_ROOT / "results"
    results_dir.mkdir(exist_ok=True)
    if args.output:
        path = Path(args.output)
        if not path.is_absolute():
            path = results_dir / path
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        tag = "dryrun_" if args.dry_run else ""
        judge = cfg.judge_model.replace("/", "-")
        path = results_dir / f"locomo_{tag}{strategy}_{judge}_{stamp}.json"
    path.write_text(json.dumps(out, indent=2))
    return path


def print_summary(strategy, results, counts, rows, path, include_adversarial) -> None:
    table = Table(title=f"LoCoMo — IronMem [{strategy}]  judge-scored", show_lines=False)
    table.add_column("category", style="cyan")
    table.add_column("accuracy", justify="right", style="green")
    table.add_column("n", justify="right", style="dim")
    for key in ("single_hop", "multi_hop", "open_domain", "temporal"):
        if key in results:
            table.add_row(key, f"{results[key] * 100:.1f}%", str(counts.get(key, 0)))
    adv_n = counts.get("adversarial", 0)
    if adv_n:
        if "adversarial" in results:
            table.add_row("adversarial", f"{results['adversarial'] * 100:.1f}%", f"{adv_n}  (in overall)")
        else:
            table.add_row("adversarial", "—", f"{adv_n}  logged, not scored")
    if "overall" in results:
        table.add_row("overall", f"{results['overall'] * 100:.1f}%",
                      f"cats {'1-5' if include_adversarial else '1-4'}")
    console.print(table)
    errors = sum(1 for r in rows if r["error"])
    if errors:
        console.print(f"[yellow]⚠ {errors} question(s) errored. See per_question[].error[/yellow]")
    console.print(f"[dim]→ results written to {path}[/dim]")


# --- main ------------------------------------------------------------------

def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="IronMem LoCoMo benchmark harness")
    p.add_argument("--dry-run", action="store_true", help="run 1 conversation only (sanity)")
    p.add_argument("--conv-index", type=int, default=0, help="which conversation for --dry-run")
    p.add_argument("--limit-convs", type=int, default=None, help="cap number of conversations")
    p.add_argument("--skip-ingest", action="store_true", help="re-score without re-ingesting")
    p.add_argument("--wipe", action="store_true", help="wipe each project before ingest (clean re-run)")
    p.add_argument("--strategy", choices=["session", "hybrid", "both"], default="session")
    p.add_argument("--answerer-model", default=DEFAULT_ANSWERER_MODEL)
    p.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    p.add_argument("--vertex-project", default=None, help="override GCP project for Vertex AI")
    p.add_argument("--vertex-location", default=None, help="override Vertex AI region")
    p.add_argument("--include-adversarial", action="store_true", help="count category 5 in overall")
    p.add_argument("--data", default=str(DEFAULT_DATA))
    p.add_argument("--output", default=None, help="output filename (under results/ unless absolute)")
    p.add_argument("--concurrency", type=int, default=None)
    p.add_argument("--retrieve-limit", type=int, default=None)
    p.add_argument(
        "--rerank",
        action="store_true",
        help="enable IronMem server-side LLM reranking of retrieved candidates",
    )
    p.add_argument(
        "--pool",
        type=int,
        default=None,
        help="candidate-pool size before rerank (server-side ?pool=); pair with --rerank for recall@25/@50",
    )
    p.add_argument("--dataset-version", default="original", help="label only (original | refined)")
    return p.parse_args(argv)


def build_config(args) -> Config:
    cfg = Config()
    cfg.answerer_model = args.answerer_model
    cfg.judge_model = args.judge_model
    if args.concurrency:
        cfg.max_concurrency = args.concurrency
    if args.retrieve_limit:
        cfg.retrieve_limit = args.retrieve_limit
    if args.pool:
        cfg.pool = args.pool
    if args.vertex_project:
        cfg.vertex_project = args.vertex_project
    if args.vertex_location:
        cfg.vertex_location = args.vertex_location
    cfg.rerank = args.rerank
    return cfg


async def amain(args) -> int:
    cfg = build_config(args)

    data_path = Path(args.data)
    if not data_path.exists():
        console.print(f"[red]Dataset not found at {data_path}. "
                      f"Run scripts/download_data.sh first.[/red]")
        return 2

    conversations = load_conversations(str(data_path))
    if args.dry_run:
        if not (0 <= args.conv_index < len(conversations)):
            console.print(f"[red]--conv-index {args.conv_index} out of range (0..{len(conversations) - 1}).[/red]")
            return 2
        conversations = [conversations[args.conv_index]]
    elif args.limit_convs:
        conversations = conversations[: args.limit_convs]

    strategies = ["session", "hybrid"] if args.strategy == "both" else [args.strategy]

    # Vertex AI Gemini client (ADC auth). Construct early so a bad project /
    # missing credentials fail fast with a clear message before any ingest.
    try:
        gemini_client = GeminiClient(cfg)
    except Exception as exc:
        console.print(
            f"[red]Could not initialize Vertex AI client ({type(exc).__name__}: {exc}).\n"
            f"Run: gcloud auth application-default login --project={cfg.vertex_project}[/red]"
        )
        return 2

    nq = sum(len(c.qa) for c in conversations)
    console.print(
        f"[bold]IronMem LoCoMo[/bold] — {len(conversations)} conversation(s), {nq} question(s) | "
        f"answerer={cfg.answerer_model} judge={cfg.judge_model} | "
        f"vertex={cfg.vertex_project}/{cfg.vertex_location} | strategies={strategies}"
    )

    async with httpx.AsyncClient(timeout=cfg.request_timeout) as http:
        clients = Clients(IronMemClient(cfg, http), gemini_client)

        try:
            status = await clients.ironmem.status()
        except Exception as exc:
            console.print(f"[red]Cannot reach IronMem at {cfg.ironmem_url}: {exc}[/red]")
            return 2
        emb = embedder_info()
        console.print(
            f"[dim]server ok: {status.get('memories')} memories, "
            f"{status.get('observations')} observations | embedder: {emb or 'unknown'}[/dim]"
        )
        if emb and emb.lower().startswith("none"):
            console.print("[yellow]⚠ embedder is 'none' — /context retrieval is keyword-only. "
                          "Deploy the local-onnx IronMem build for semantic retrieval.[/yellow]")

        for strategy in strategies:
            if not args.skip_ingest:
                await run_ingest(clients, cfg, conversations, strategy, wipe=args.wipe)
            rows = await run_eval(clients, cfg, conversations, strategy, args.include_adversarial)
            results, counts = aggregate(rows, args.include_adversarial)
            path = write_output(strategy, results, counts, rows, cfg, args)
            print_summary(strategy, results, counts, rows, path, args.include_adversarial)

    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    return asyncio.run(amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
