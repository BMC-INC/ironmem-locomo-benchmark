# RUN NOTE — LoCoMo upgraded run (rerank + candidate-pool sweep)

**Immutable record. Do not edit after finalization.** Finalized 2026-06-24 ~21:xx PDT (calibration pending). Times are PDT (UTC−7).
Companion to `RUN_NOTE_2026-06-24_locomo_full_run.md` (the pre-upgrade Pro baseline) and `IRONMEM_UPGRADE_PLAN.md`.

## What this run is
The **first** LoCoMo benchmark of the **upgraded** IronMem (origin/main `657def4`, built `--features local-onnx`) — testing the new server-side **LLM reranker** over two candidate-pool widths against a rerank-off baseline. Hybrid ingest only, reused intact across all three passes (`--skip-ingest`, no re-ingest).

## ⚠️ Read this before comparing to the old baseline
This run's answerer **and** judge are **Gemini 2.5 Flash**, not Pro. This was **forced, not chosen**: Gemini 2.5 Pro had effectively **zero concurrent capacity** on this project's Dynamic Shared Quota all evening — 8-way bursts returned `429 RESOURCE_EXHAUSTED` in *every* region including `global`; even concurrency-2 mostly self-throttled. Flash returned `200` everywhere under the same load. So:

- **Valid comparison = INTERNAL (A vs B vs C), all Flash, apples-to-apples.** This is where "rerank works" is proven.
- **Cross-run vs the old Pro baseline (54.2%) is CONFOUNDED** by the answerer/judge model swap (Pro→Flash). The Flash-no-rerank baseline (Pass A, 39.3%) sits ~15 pts below the Pro-no-rerank baseline purely from the weaker model. Do **not** report "upgrade ties baseline."

## Exact commands
```bash
P=.venv/bin/python; C=8; LOC=global
M="--answerer-model gemini-2.5-flash --judge-model gemini-2.5-flash"
# A — rerank OFF (baseline)
$P -m benchmark.run --strategy hybrid --skip-ingest --concurrency $C --vertex-location $LOC $M \
   --output upg2_hybrid_A_rerankoff.json
# B — rerank ON, candidate pool 25
$P -m benchmark.run --strategy hybrid --skip-ingest --rerank --pool 25 --concurrency $C --vertex-location $LOC $M \
   --output upg2_hybrid_B_rerank_pool25.json
# C — rerank ON, candidate pool 50
$P -m benchmark.run --strategy hybrid --skip-ingest --rerank --pool 50 --concurrency $C --vertex-location $LOC $M \
   --output upg2_hybrid_C_rerank_pool50.json
# Funnel (joined to C)
$P scripts/funnel_probe.py --strategy hybrid --pool 50 --scored results/upg2_hybrid_C_rerank_pool50.json \
   --output funnel_hybrid.json
```

## Environment
| field | value |
|---|---|
| answerer / judge model | `gemini-2.5-flash` (Vertex AI) — see capacity note above |
| Vertex project / region | `queueflow-sentinel` / `global` (ADC auth) |
| eval concurrency | 8 |
| IronMem | upgraded binary `657def4`, `--features local-onnx`, `http://localhost:37778` |
| embedder | `bge-small-en-v1.5` (dim 384); store ≈ 19,692 memories / 47,365 observations |
| dataset | `data/locomo10.json` — full LoCoMo-10, 10 conversations, 1,986 questions |
| scope | categories 1–4 scored (1,540 Qs); 446 adversarial answered but **not** scored by the harness |
| billing | first-party Gemini on the $300 free-trial credit |

## Output files
| file | what |
|---|---|
| `results/upg2_hybrid_A_rerankoff.json` | Pass A — rerank off |
| `results/upg2_hybrid_B_rerank_pool25.json` | Pass B — rerank on, pool 25 |
| `results/upg2_hybrid_C_rerank_pool50.json` | Pass C — rerank on, pool 50 |
| `results/funnel_hybrid.json` | retrieval funnel, joined to C |
| `results/judge_calibration_pro.json` | Pro-vs-Flash judge calibration (see note) |
| `results/raw_console/upg2_sweep_console_2026-06-24.log` | rendered tables/funnel exactly as printed |

## Results — accuracy (judge-scored, cats 1–4, n=1,540; all error_count = 0)
| category | n | A: rerank off | B: rerank pool25 | C: rerank pool50 | Δ rerank (A→C) |
|---|---:|---:|---:|---:|---:|
| single_hop | 841 | 45.18% | 56.96% | **61.24%** | **+16.06** |
| multi_hop | 282 | 22.34% | 34.75% | **39.72%** | **+17.38** |
| open_domain | 96 | 26.04% | 37.50% | **38.54%** | **+12.50** |
| temporal | 321 | 42.68% | 48.60% | **54.52%** | **+11.84** |
| **overall** | **1,540** | **39.29%** | **49.94%** | **54.48%** | **+15.19** |

**Findings (internal, valid):**
1. **The reranker is a large, uniform win — +15.2 pts overall**, and it helps *every* category (biggest on the hardest, multi_hop +17.4).
2. **Wider candidate pool helps: pool 50 beats pool 25 by +4.5 pts** overall. Feed the reranker more candidates.
3. **pool 50 is the configuration to ship.**

## Retrieval funnel (joined to Pass C; cats 1–4, n=1,540)
| stage | recall | conditional retention | absolute lost |
|---|---:|---|---:|
| gold in raw transcript | 93.8% (1,445) | — | 95 Qs gold not in transcript |
| gold survives into memory | 72.3% (1,114) | compression\|in-transcript **76.9%** | **−334 (compression)** |
| gold in candidate pool 50 | 84.5% (1,302) | pool50\|in-memory **95.7%** | −48 (retrieval) |
| reranker keeps gold in top-10 | 72.6% (1,118) | rerank\|in-pool50 **85.4%** | −155 (rerank truncation) |

**Leak ranking (where to fix next):**
1. **Compression drops ~23% of gold facts** (−334 Qs) during ingest — *by far* the biggest leak, tied to the `{'fact': …}` wrapper smell in stored memory. Fix this and the ceiling rises most.
2. **Reranker truncates to top-10**, dropping 155 Qs whose gold *was* in the pool. Raising the post-rerank limit (or pool→limit ratio) recovers some recall.
3. **Embedding retrieval is essentially solved** — 95.7% of in-memory gold reaches the pool.

Note the reranker keeps *fewer* gold facts (72.6%) than raw pool recall (84.5%) yet yields the *highest* accuracy — it trades a little recall for much better **precision** (right fact ranked up, noise removed), and the answerer nets out ahead.

## Judge calibration (Pro vs Flash) — capacity-limited
The plan was to have **Gemini 2.5 Pro re-judge a 200-question sample** of Pass C to bound the "Flash graded Flash" self-preference concern (raw agreement + Cohen's kappa).

- **Attempt 1** (us-west1, concurrency 2, 12-min cap): only **5/200** Pro judgments completed before the deadline — DSQ throttling. n=5 is meaningless; recorded at `results/judge_calibration_pro_attempt1_n5.json` for the audit trail only. **Not usable.**
- **Attempt 2** (us-west1, **concurrency 1 / sequential**, 35-min cap): running at finalization. Sequential avoids the retry-storm that defeats concurrent Pro calls tonight. Result → `results/judge_calibration_pro.json`. _[KAPPA / n TO BE FOLDED IN ON COMPLETION]_

If Attempt 2 also comes back with too small an n, the calibration is **deferred** until Pro DSQ capacity returns (off-peak). It does not affect the internal A/B/C comparison above, which is the headline result.

## Provenance / caveats
- **Tainted file to ignore:** an earlier `results/upg_hybrid_A_rerankoff.json` (34.2%, 626 errors) was produced during the us-central1 Pro-capacity incident and is **invalid** — superseded by the `upg2_*` files here.
- **Model confound** (see top): headline is Flash, old baseline is Pro — internal comparison only.
- **`{'fact': …}` wrapper:** the upgraded compiler stores facts as a wrapper string; it adds noise to retrieved context and likely depresses absolute scores. It affects A/B/C equally, so the rerank comparison holds.
