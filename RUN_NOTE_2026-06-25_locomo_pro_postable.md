# RUN NOTE — LoCoMo Pro-judged "postable" baseline (rerank A′ vs C′)

**Immutable record. Do not edit after finalization.** Finalized 2026-06-25 ~14:00 PDT. Times are PDT (UTC−7).
Companion to `RUN_NOTE_2026-06-24_locomo_upgraded_rerank.md` (the Flash-judged rerank sweep) and `RUN_NOTE_2026-06-24_locomo_full_run.md` (the pre-upgrade Pro baseline). This note **resolves the model confound** flagged in the Flash note: it re-runs the same upgraded store with **Gemini 2.5 Pro** as both answerer and judge.

## What this run is
The clean, apples-to-apples **Pro answerer + Pro judge** measurement of the **upgraded** IronMem (origin/main `657def4`, built `--features local-onnx`), comparing rerank OFF (control) against rerank ON · pool 50 (headline) on the same hybrid store. Reused intact across both passes (`--skip-ingest`, no re-ingest). Fired off-peak so Pro had Dynamic-Shared-Quota capacity — the run picked `us-west1`, the region that passed the burst gate.

This is the run the `com.execlayer.locomo-pro-postable` launchd one-shot existed to produce. It completed clean and the job self-removed.

## Exact commands
Driven by `scripts/run_pro_postable.sh` (region auto-picked by `scripts/pick_pro_region.py` → `us-west1`). Pro is the harness default for both answerer and judge, so no model flags are passed:
```bash
P=.venv/bin/python; C=8; L=us-west1
# A′ — rerank OFF (control)
$P -m benchmark.run --strategy hybrid --skip-ingest --concurrency $C --vertex-location $L \
   --output upg3_PRO_A_rerankoff.json
# C′ — rerank ON, candidate pool 50 (headline / postable)
$P -m benchmark.run --strategy hybrid --skip-ingest --rerank --pool 50 --concurrency $C --vertex-location $L \
   --output upg3_PRO_C_rerank_pool50.json
```

## Environment
| field | value |
|---|---|
| answerer / judge model | `gemini-2.5-pro` (Vertex AI) — both passes |
| Vertex project / region | `queueflow-sentinel` / `us-west1` (ADC auth) |
| eval concurrency | 8 |
| IronMem | upgraded binary `657def4`, `--features local-onnx`, `http://localhost:37778` |
| embedder | `bge-small-en-v1.5` (dim 384); store ≈ 19,718 memories / 47,387 observations |
| dataset | `data/locomo10.json` — full LoCoMo-10, 10 conversations, 1,986 questions |
| scope | categories 1–4 scored (1,540 Qs); 446 adversarial answered but **not** scored |
| billing | first-party Gemini Pro on the $300 free-trial credit |
| run window | A′ 12:37–13:10 PDT, C′ 13:11–13:59 PDT, 2026-06-25 |

## Output files
| file | what |
|---|---|
| `results/upg3_PRO_A_rerankoff.json` | Pass A′ — Pro, rerank off (control) |
| `results/upg3_PRO_C_rerank_pool50.json` | Pass C′ — Pro, rerank on, pool 50 (headline) |
| `results/raw_console/pro_postable_console.log` | rendered tables exactly as printed |

## Results — accuracy (Pro-judged, cats 1–4, n=1,540; both error_count = 0)
| category | n | A′: rerank off | C′: rerank pool50 | Δ rerank (A′→C′) |
|---|---:|---:|---:|---:|
| single_hop | 841 | 53.27% | **66.59%** | **+13.32** |
| multi_hop | 282 | 26.95% | **41.49%** | **+14.54** |
| open_domain | 96 | 32.29% | **40.62%** | **+8.33** |
| temporal | 321 | 65.73% | **69.16%** | **+3.43** |
| **overall** | **1,540** | **49.74%** | **60.91%** | **+11.17** |

## Cross-run comparison (now valid — same models)
**vs the pre-upgrade Pro baseline** (`RUN_NOTE_2026-06-24_locomo_full_run.md`, hybrid, Pro+Pro):
| category | old Pro baseline | C′ (upgraded + rerank) | Δ |
|---|---:|---:|---:|
| single_hop | 56.4% | 66.59% | **+10.2** |
| multi_hop | 29.8% | 41.49% | **+11.7** |
| open_domain | 42.7% | 40.62% | −2.1 |
| temporal | 73.5% | 69.16% | −4.3 |
| **overall** | **54.2%** | **60.91%** | **+6.7** |

**vs the Flash-judged version of the same config** (`upg2_hybrid_C_rerank_pool50.json`): Flash scored this exact pool-50 store at **54.48%**; Pro scores it at **60.91%** — i.e. the Flash judge **understated true accuracy by ~6.4 pts**. This is the confound the Flash run note warned about, now quantified and removed.

## Findings
1. **Rerank pool50 is the dominant lever on the real (Pro) number: +11.2 pts overall** (49.7% → 60.9%), helping every category, biggest on the hardest (multi_hop +14.5, single_hop +13.3). This corroborates the Flash sweep's +15.2 internal finding — the lever is real, not a judge artifact.
2. **The upgraded store + rerank beats the old Pro baseline by +6.7 pts overall** — the headline win. Driven by the hard categories: **multi_hop +11.7** and **single_hop +10.2** vs the old store.
3. **Honest regressions vs the old baseline:** temporal −4.3 and open_domain −2.1. Likely the `{'fact': …}` wrapper noise and/or one-hop graph expansion pulling distractors into these categories. Flagged for the next pass.
4. **Rerank is load-bearing:** the control (A′, rerank OFF on the upgraded store) is **49.7%, *below* the old 54.2% baseline.** The upgraded store only wins once rerank is on. Validates the "ship `pool=50` with rerank" decision — and the decision to land all upgrades before measuring (rerank-off mid-build would have read as a regression).

## Path to 70%
Headline is **60.9%**; target is 70% (`ROADMAP_TO_70.md`). The retrieval funnel (Flash run, config-identical store, retrieval stages model-independent — see `RUN_NOTE_2026-06-24_locomo_upgraded_rerank.md`) shows the #1 remaining leak unchanged: **compression drops ~23% of gold facts** (`gold_in_memory` 72.3%). **Phase 1** (untruncated transcript + Reflexion coverage pass, drafted + compiling in `~/Projects/Iron-mem-fix/src/provider.rs`) targets that leak directly. Gate: `gold_in_memory` 72.3% → ≥90%.

## Provenance / caveats
- Both passes `error_count = 0`, independently re-verified from the JSON after the run (not just the script's self-check).
- `--skip-ingest` on both passes — the baseline store was never mutated mid-run; the store-write hold was held until completion, then lifted.
- The `{'fact': …}` wrapper still present in this binary (`657def4`); affects A′ and C′ equally, so the rerank comparison holds. Phase 1 addresses it.
- No funnel was run in this job (the postable script runs only A′/C′); the leak analysis carries over from the config-identical Flash run.
