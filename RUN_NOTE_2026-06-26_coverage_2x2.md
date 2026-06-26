# RUN NOTE — 2026-06-26 — Coverage-store 2×2 retrieval sweep (Flash)

## TL;DR
**Winner: `pool=100, retrieve-limit=25` → 63.25%** overall (Flash answerer+judge), **+8.57 pts**
over the prior control and **+2.35 past the Pro 60.9% headline** — on cheap Flash. Both retrieval
levers (wider pool, bigger reranked top-k) are real and stack sub-additively; bigger top-k is the
stronger single lever. Funnels confirm the mechanism end-to-end.

## Setup
- Fixed coverage-only store (28,311 memories, 59,738 observations), `--skip-ingest` (never re-ingests).
- Factorial: pool {50,100} × reranker top-k {10,25}, rerank ON, Flash both sides, concurrency 8,
  `--vertex-location us-west1`. 1,986 questions, judge-scored. Driver: `scripts/run_coverage_score.sh`.
- Apples-to-apples vs the prior Flash 54.5% (which was pool50/limit10 on this store).

## Operational note (why this is a *re-run*)
The 04:30 launchd job **never fired** — the Mac was asleep through the window (planned wake 08:31) and
launchd did not replay the missed `StartCalendarInterval`. The first manual kick then died with
**every** question erroring `RefreshError: Reauthentication is needed` — Vertex **ADC had expired**
(~24h org session limit). Re-authed (`gcloud auth application-default login`), verified with a live
1-call Vertex smoke, re-kicked clean. (No SA-key fallback: org enforces
`iam.disableServiceAccountKeyCreation`.)

## Results (1,986 Q, judge-scored)

| Arm | pool | top-k | overall | single_hop | multi_hop | open_domain | temporal | err |
|-----|------|-------|---------|-----------|-----------|-------------|----------|-----|
| A control      |  50 | 10 | 54.68% | 63.50 | 32.62 | 29.17 | 58.57 | 0 |
| B wider-pool   | 100 | 10 | 59.09% | 67.18 | 39.01 | 36.46 | 62.31 | 0 |
| C bigger-top-k |  50 | 25 | 61.17% | 68.49 | 40.07 | 38.54 | 67.29 | 1 |
| **D both** ⭐  | **100** | **25** | **63.25%** | 71.34 | 40.07 | 40.62 | 69.16 | 0 |

## Lever analysis
- **Top-k is the stronger lever:** A→C **+6.49** vs pool's A→B **+4.41**.
- **Sub-additive:** combined A→D **+8.57** < 10.90 (sum of individual) → ~2.3 pts overlapping gold.
  Each effect shrinks in the other's presence (pool: +4.41@k10 → +2.08@k25; top-k: +6.49@p50 → +4.16@p100).
- **multi_hop caps at k25** (40.07 in both C and D — wider pool adds nothing). It is now the laggard
  and the next bottleneck.
- Biggest category wins (D vs A): open_domain **+11.45**, temporal **+10.59** — confirms temporal/open
  gold ranks low in the pool and the top-10 cut was starving it.

## Funnel confirmation (store-limit 2000)
| stage | A-corner (p50·k10) | D-corner (p100·k25) |
|-------|--------------------|---------------------|
| gold_in_memory | 92.0% | 92.0% |
| reranker_kept | 74.9% | **83.4%** |
| rerank_kept_given_in_pool50 | 87.1% | **95.5%** |
| **gold lost at reranked cut** | **168 Q** | **59 Q** |
| answerer accuracy (joined) | 54.7% | **63.2%** |

The reranked-cut leak collapses **168 → 59 questions (−65%)**; joined answerer accuracy 54.7% → 63.2%
matches the arm scores exactly. Diagnosis confirmed: the top-k cut was the dominant leak; widening it
(plus a bigger pool to feed the reranker) is the fix.

## Verdict / next
1. **Adopt `pool=100, retrieve-limit=25` as the default** (benchmark config + live binary).
2. **Pro headline at p100/k25** (off-peak, DSQ-throttled) → the postable number. Script:
   `scripts/run_pro_headline_p100l25.sh`. Flash result strongly implies mid-60s on Pro.
3. **Headroom above k25:** raw recall@N was @25 66% → @30 69% → @50 84%. A k=50 arm likely adds more
   (cost: answerer context/latency).
4. **multi_hop (40%) is the new frontier** — retrieval tuning has plateaued it; this is the synthesis/
   reflection (Track B) target.

Artifacts: `results/upg5_cov_p{50,100}_l{10,25}.json`, `results/funnel_cov_p50_l10.json`,
`results/funnel_cov_p100_l25.json`.
