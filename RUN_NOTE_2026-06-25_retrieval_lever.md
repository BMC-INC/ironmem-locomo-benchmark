# RUN NOTE — Retrieval lever identified: the reranked top-k CUT (not pool, not extraction)

**Immutable record. Do not edit after finalization** (except the one VALIDATION line below, marked TODO). Drafted 2026-06-25 ~18:36 PDT (UTC−7).
Companion to `RUN_NOTE_2026-06-25_phase1_compression_regression.md` (extraction is solved — coverage pass retains ~98% of in-transcript gold) and `RUN_NOTE_2026-06-25_locomo_pro_postable.md` (the 60.9% Pro baseline). This note records the diagnosis that moved the bottleneck from extraction → **retrieval**, and the redesigned off-peak experiment that targets it.

## The question
Extraction is solved (coverage pass: `gold_in_memory` 92.4% @ store-limit 2000). So where does gold die between "in the store" and "in the answer"? The retrieval path is: question → IronMem hybrid retrieval (RRF over BM25 + vector + temporal) → candidate pool (`--pool`) → server-side LLM rerank → **top-k cut (`--retrieve-limit`, default 10)** → Gemini answerer.

## The evidence — raw recall@N curve on the LIVE coverage store (LLM-free)
`scripts/pool_curve.py` (new): one `/context` call per question, `rerank=False`, pull top-50 once, slice at each N, check gold coverage (reuses `funnel_probe`'s exact `_covered`/`_tokens`/`_ground_truth` helpers). All 10 convs, cats 1–4, n=1540.

```
RAW recall@N (rerank=False):   N=10   15    20    25    30    40    50
  overall                     47.7  56.4  61.8  66.0  69.4  74.0  83.6
  single_hop  (n=841)         50.8  62.2  68.3  73.5  76.9  81.3  89.9
  multi_hop   (n=282)         38.7  47.5  56.0  60.6  66.0  72.7  82.6
  temporal    (n=321)         57.6  60.4  62.0  64.2  65.1  69.2  82.6
  open_domain (n=96)          13.5  18.8  21.9  22.9  28.1  30.2  34.4
```

## Diagnosis (two findings, one correcting an earlier read)
1. **The LLM reranker HELPS hard — it is not the leak.** Raw recall@10 is only **47.7%**, but reranked top-10 lands ~73–78% (funnel `reranker_kept`). The reranker surfaces gold from deep in the pool into the top 10 (≈ +25 pts at the same k). Rerank stays ON. *(This corrects an earlier note that read `in_pool_25` 77.0% > `reranker_kept` 72.6% as "rerank hurts" — those numbers were the OLD compression store; on THIS coverage store the reranker is a large net positive.)*
2. **The leak is the top-k CUT, and the headroom is in pool positions 11–50.** raw@50 (83.6%) − raw@10 (47.7%) = **~36 pts of gold sit in the pool but below the top-10 cut** that `--retrieve-limit 10` passes to the answerer. The reranker can only emit `retrieve_limit` items; raise the cut and more pooled gold reaches the answerer.

Per category: **single-hop and multi-hop have the most top-k headroom** (single 50.8→73.5→89.9 across 10/25/50; multi 38.7→60.6→82.6). **Temporal is flat 10→30 then jumps at 50** — dates embed poorly, so temporal gold ranks LOW and clusters in positions 40–50; this is exactly the target of the dormant #5 temporal-trust retrieval weight (`temporal_trust.weight`, gated at 0.0). **Open-domain is capped earlier** (gold_in_transcript only 62.5%, raw@50 only 34.4%) — an extraction/world-knowledge ceiling, not a retrieve-limit problem.

## Why the prior off-peak job would have missed this
The previous `run_coverage_score.sh` only varied `--pool` (50 vs 100). `--pool` raises the *ceiling the reranker can reach* (gold beyond raw top-50), but it does **not** change the top-10 cut — so widening the pool alone cannot pass the pooled-but-below-10 gold to the answerer. The lever the prior job never tested is `--retrieve-limit`.

## The redesigned experiment — 2×2 factorial (pool × retrieve-limit), off-peak, Flash both sides
`scripts/run_coverage_score.sh` (rewritten), armed launchd job `com.execlayer.locomo-coverage-score` @ 04:30, `--skip-ingest` (store is fixed), us-west1, rerank ON:

| arm | pool | retrieve-limit | output | tests |
|---|---:|---:|---|---|
| **A** control | 50 | 10 | `upg5_cov_p50_l10.json` | reproduces prior Flash 54.5% on the richer store |
| **B** | 100 | 10 | `upg5_cov_p100_l10.json` | does WIDER POOL alone help? (hypothesis: barely) |
| **C** | 50 | 25 | `upg5_cov_p50_l25.json` | does BIGGER TOP-K alone help? (hypothesis: yes — the lever) |
| **D** | 100 | 25 | `upg5_cov_p100_l25.json` | both (expected best) |

Plus two bracket funnels at store-limit 2000: `funnel_cov_p50_l10.json` (A corner) and `funnel_cov_p100_l25.json` (D corner) to confirm the retrieval stage recovers gold at the wider top-k. Clean attribution: **C−A = retrieve-limit effect; B−A = pool effect; D = compound.** HI=25 chosen from the curve (raw@25 = 66% overall, 73.5% single-hop) — an aggressive-but-bounded 2.5× of the prior top-10.

**Tradeoff to watch:** raising retrieve-limit also feeds the answerer more (10→25) lower-confidence memories; the gold gain must beat the added noise. The factorial's control arms isolate that cleanly — if lim25 regresses vs lim10, we learn it without ambiguity (the Phase-1 bundling lesson: one variable at a time).

## Status
- `scripts/pool_curve.py` (LLM-free recall curve) — added, run, finalized above.
- `scripts/run_coverage_score.sh` — rewritten to the 2×2 factorial; `bash -n` clean; `--retrieve-limit` confirmed a real `benchmark.run` flag; plist armed (`state = not running`, fires 04:30).
- **VALIDATION (done):** 1-conv smoke of the new `--pool 100 --retrieve-limit 25` arm (Flash, us-west1) ran clean — **error_count = 0**, 199/199 scored, and per-question **`num_retrieved` = 25** confirms the wider top-k actually reaches the answerer. ADC is valid; the 04:30 job is safe unattended. (`results/smoke_lever_p100_l25.json`; the 1-conv 65.8% is NOT comparable to the 10-conv 54.5% baseline.)
- Next session: read the 04:30 factorial; if C/D beat A, the retrieval lever is real → run the winning config under **Pro** for a new headline (prior Pro baseline 60.9% was pool50/limit10) and consider sweeping limit higher (curve shows headroom to 50, esp. single/multi-hop). Temporal needs the #5 weight, not just bigger k.
