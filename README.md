# IronMem · LoCoMo Benchmark

Evaluates [IronMem](https://github.com/BMC-INC/Iron-mem) (v0.4.0) against the
**LoCoMo** long-conversation memory benchmark and produces per-category accuracy
scores, judged by an LLM, fully reproducible.

IronMem's memory model is **session compression** — it distills each conversation
session into a compact memory rather than extracting discrete facts. That is
architecturally different from fact-extraction systems (Mem0, Zep). This harness
measures what that buys and what it costs, per question category.

---

## Results

> Populated by `python -m benchmark.run`. Numbers below are filled from the
> committed JSON in [`results/`](results/). Overall = categories 1–4
> (adversarial **excluded**, matching the mem0 harness); adversarial reported
> separately.

| Category | IronMem (session) | IronMem (session + facts) |
|---|---|---|
| single-hop | _tbd_ | _tbd_ |
| multi-hop | _tbd_ | _tbd_ |
| open-domain | _tbd_ | _tbd_ |
| temporal | _tbd_ | _tbd_ |
| **overall (1–4)** | **_tbd_** | **_tbd_** |
| adversarial (excl.) | _tbd_ | _tbd_ |

- **session** — conversations ingested as-is; retrieval relies purely on IronMem's compression.
- **session + facts** — the same, plus LLM-extracted atomic facts stored via IronMem's `remember`. This is the *ablation*: it isolates how much an explicit fact-extraction layer adds on top of compression. The **delta** between the two columns is the finding, not either column alone.

### Comparison with the field

Published LoCoMo numbers from other systems, for orientation. **These were
produced by their own harnesses (different answerer/judge/dataset cuts) — treat
as directional, not head-to-head.** Re-running them under this exact harness is
the only truly apples-to-apples comparison; until then the honest framing is "in
the same arena," not "we beat X."

| System | Approach | Overall | Source |
|---|---|---|---|
| IronMem | session compression | _tbd_ | this repo |
| Mem0 | fact extraction | see ref | [mem0ai/memory-benchmarks](https://github.com/mem0ai/memory-benchmarks) |
| Zep | temporal knowledge graph | see ref | published paper |
| Letta (MemGPT) | paged memory | see ref | published paper |
| Supermemory | fact extraction | see ref | published numbers |

> Cells left as "see ref" are intentionally **not** filled with numbers we did not
> reproduce. Fill them only with a citation, or by re-running that system here.

---

## Methodology

Three stages: **ingest → query → judge**.

**Dataset.** [`locomo10.json`](https://github.com/snap-research/locomo) — 10
multi-session conversations, 1,986 QA annotations. Categories are integer-coded:

| code | category | n | scored |
|---|---|---|---|
| 1 | multi-hop | 282 | ✅ |
| 2 | temporal | 321 | ✅ |
| 3 | open-domain | 96 | ✅ |
| 4 | single-hop | 841 | ✅ |
| 5 | adversarial | 446 | ❌ excluded from overall (reported separately) |

Category 5 (adversarial / unanswerable) is excluded from the headline to match the
mem0 harness; the answerer is still allowed to say "I don't have enough information,"
and we report adversarial accuracy on its own.

**Ingest.** Each conversation → an IronMem project
(`/benchmark/locomo/<sample_id>__<strategy>`). Each timestamped session →
`POST /session/start` → one `POST /event` per turn → `POST /session/end` (which
triggers compression). The session's `date_time` is recorded as a header event so
temporal context survives compression (the REST API otherwise stamps "now").

**Query.** For each question: `GET /context?project&query&limit=10` — IronMem's
hybrid (BM25 + vector) retrieval — then Claude answers using only the retrieved
memories.

**Judge.** GPT-4o (default) scores each answer 0/1 against the gold answer.
Open-domain (cat 3) gold answers are truncated at the first `;` per LoCoMo
convention. Claude is supported as an alternative judge; using a different judge
than the answerer avoids same-family bias.

**Models.** answerer `claude-sonnet-4-20250514`, judge `gpt-4o` (both configurable).

---

## Reproduce

```bash
# 1. Deploy an IronMem build WITH the local embedder (semantic retrieval).
#    A default build has NO embedder -> /context is keyword-only and scores are invalid.
#    Build IronMem with: cargo build --release --features local-onnx, install, restart.
ironmem config | grep -A3 embedding     # confirm the onnx embedder is active

# 2. Set up this harness.
cp .env.example .env                     # then paste your OPENAI_API_KEY (judge)
uv venv && uv pip install -r requirements.txt
./scripts/download_data.sh

# 3. Dry run (1 conversation), then the full run.
uv run python -m benchmark.run --dry-run
uv run python -m benchmark.run --strategy both
```

Useful flags: `--skip-ingest` (re-score only), `--wipe` (clean re-ingest),
`--judge-model gpt-4o|claude-sonnet-4-20250514`, `--strategy session|hybrid|both`,
`--include-adversarial`, `--limit-convs N`.

### Prerequisites

- Python 3.11+ (`uv` recommended)
- IronMem running locally with the **local-onnx** embedder (REST on `:37778`)
- `ANTHROPIC_API_KEY` (answerer) and, for the official run, `OPENAI_API_KEY` (judge)

---

## Why these results look the way they do

IronMem compresses sessions into narrative memories instead of extracting atomic
facts. The hypothesis going in: **stronger on multi-hop and temporal** (compression
preserves cross-turn and time structure), **potentially weaker on single-hop**
(atomic lookups can get smoothed into a summary). The `session` vs `session + facts`
delta is designed to measure exactly that — if the hybrid column closes a single-hop
gap, that quantifies what compression trades away. Whatever the numbers say, they
are published as-is with the raw per-question logs in [`results/`](results/).

## Layout

```
benchmark/   config · ironmem_client · ingest · query · judge · run
data/        locomo10.json (gitignored; download_data.sh fetches it)
results/     committed run outputs (per-question logs included)
scripts/     download_data.sh
```

## License

Apache-2.0 (matches IronMem).
