# RUN NOTE — LoCoMo full run (session + hybrid)

**Immutable record. Do not edit after finalization.** Finalized 2026-06-24 ~13:35 PDT.
Companion to `IRONMEM_UPGRADE_PLAN.md`. Times are PDT (UTC−7).

## What this run is
The headline IronMem × LoCoMo benchmark on Vertex AI Gemini 2.5 Pro: session strategy + a **clean** hybrid strategy. (The first hybrid attempt was discarded — see "Provenance caveat" below.)

## Exact commands
```
# Session strategy (via scratchpad/run_full.sh)
.venv/bin/python -m benchmark.run --strategy session --wipe --concurrency 10 \
    --output gemini_locomo_session.json

# Hybrid strategy — CLEAN re-run after Anthropic compression credit restored (via scratchpad/run_hybrid.sh)
.venv/bin/python -m benchmark.run --strategy hybrid --wipe --concurrency 10 \
    --output gemini_locomo_hybrid.json

# Combine
.venv/bin/python scripts/combine_results.py \
    results/gemini_locomo_session.json results/gemini_locomo_hybrid.json \
    results/gemini_locomo_full_run.json
```

## Environment
| field | value |
|---|---|
| answerer model | `gemini-2.5-pro` (Vertex AI) |
| judge model | `gemini-2.5-pro` (Vertex AI) |
| Vertex project / region | `queueflow-sentinel` / `us-central1` (ADC auth) |
| eval concurrency | 10 |
| IronMem server | `http://localhost:37778` · version `0.4.0` |
| IronMem DB | `/Users/kingjames/.ironmem/mem.db` |
| embedder | `bge-small-en-v1.5` (dim 384) |
| harness source | `/Users/kingjames/Projects/ironmem-locomo-benchmark` (relocated off iCloud Desktop) |
| git commit | **n/a** — working copy not under version control (recommend `git init` for future runs) |
| dataset | `data/locomo10.json` — full LoCoMo-10, 10 conversations, 1986 questions |
| scope | categories 1–4 scored; 446 adversarial answered but not scored (mem0-comparable) |

## Output files
| file | bytes | mtime (PDT) |
|---|---:|---|
| `results/gemini_locomo_session.json` | 9,778,858 | 2026-06-24 10:56:59 |
| `results/gemini_locomo_hybrid.json` | 9,491,258 | 2026-06-24 13:28:19 |
| `results/gemini_locomo_full_run.json` | 19,373,505 | 2026-06-24 13:28:19 |

## Results
| category | session | hybrid | Δ |
|---|---:|---:|---:|
| single_hop | 54.6% | 56.4% | +1.8 |
| multi_hop | 26.6% | 29.8% | +3.2 |
| open_domain | 41.7% | 42.7% | +1.0 |
| temporal | 72.0% | 73.5% | +1.6 |
| **overall** | **52.3%** | **54.2%** | **+1.9** |

## Error counts
| run | scored | errored | note |
|---|---:|---:|---|
| session | 1540 | **0** | clean |
| hybrid | 1540 | **11** | all `429 RESOURCE_EXHAUSTED` (Vertex QPM at concurrency 10) — slightly understates hybrid |

## Funnel metrics (this run)
Full coverage of the six funnel metrics is in the **rerank-experiment run note** (`RUN_NOTE_2026-06-24_locomo_rerank.md`, pending ~3 PM). What the full run alone establishes (session strategy, of 735 wrong / 1540 scored):

| funnel stage | metric | value |
|---|---|---|
| context → answer | answerer miss (gold in context, answer wrong) | 187 (25.4% of errors) |
| compression → candidates | retrieval gap (gold absent from top-10) | 205 (27.9%) |
| compression / retrieval | abstentions ("not enough info") | 343 (46.7%) |
| recall@25, recall@50 | candidate recall | see rerank run note (pending) |
| candidates → context | rerank retention | see rerank run note (pending) |
| raw → compression | lost-fact rate | see fidelity probe (pending) |

**Read:** ~75% of failures are retrieval-side. single_hop (the easiest category) is the worst offender — 313/382 of its failures are non-retrieval.

## Provenance caveat (the "which run was this?" guard)
The **first** hybrid attempt (run_full.sh, 10:57–~12:10 PDT) was **invalid** and discarded: IronMem's server-side compression calls the Anthropic API, that balance ran dry mid-run, and **104 session compressions failed silently** (server logged a `WARN`; `/session/end` still returned OK, so the harness saw 0 errors). After the Anthropic credit was topped up, hybrid was re-run clean (`run_hybrid.sh`, 12:16–13:28 PDT) with **0 compression failures** — that is the hybrid result recorded above. This is the motivation for plan items **0.1** (loud failures) and **0.2** (compression → Vertex).
