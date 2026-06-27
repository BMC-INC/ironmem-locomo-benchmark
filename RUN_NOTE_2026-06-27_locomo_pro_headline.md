# RUN NOTE — 2026-06-27 — Pro headline (FINAL, judged)

The postable headline run on the fully-built stack (entire ROADMAP_TO_70 shipped:
path-to-70 batch + #4 storage adapters + #1/#2/#3/#5 paper additions + Wave 4
cross-encoder committed). Locked methodology, exactly as originally set up:
Gemini 2.5 **Pro** answerer + **Pro** judge, with **Flash** as the 2nd-judge
agreement check.

Config: hybrid, --skip-ingest, --rerank, pool 100, retrieve-limit 25,
answerer+judge = gemini-2.5-pro, vertex queueflow-sentinel/**us-west1** (dodges the
us-central1 Pro DSQ throttle), concurrency 8. Live store = 28,554 memories /
59,859 observations. Rerank backend = LLM (cross-encoder built but not loaded —
CPU; needs GPU at benchmark scale). 1986 questions, 1540 scored (cats 1-4), 446
adversarial logged, **0 errors**, 1:09:49.

Pre-flight: re-auth ADC, then a 1-conv canary (conv-0, error_count 0, 65.8%)
confirmed Pro+us-west1 green before the full launch.

## Result (results/upg6_PRO_p100_k25.json)
| category    | this run | prior Pro (p50/l10) | Flash 2×2 winner D (p100/k25) |
|-------------|----------|---------------------|-------------------------------|
| single_hop  | 69.9%    | —                   | 71.3%                         |
| multi_hop   | 47.2%    | —                   | 40.1%                         |
| open_domain | 43.8%    | —                   | 40.6%                         |
| temporal    | 78.5%    | —                   | 69.2%                         |
| **overall** | **65.9%**| **60.9%**           | **63.25%**                    |

- **+5.0 over the prior Pro baseline (60.9%)** — new postable headline.
- **+2.65 over the Flash high-water (63.25%)** on the identical p100/k25 config.
- Movers: **multi_hop +7.1**, **temporal +9.3** (the prior Pro temporal −4.3
  regression is reversed). open_domain (43.8%) is the lone laggard, extraction-capped.

## Judge agreement (results/judge_agreement_PRO.json)
Flash 2nd judge, n=200:
- raw agreement **91.0%**, **Cohen's κ 0.8024** (Landis-Koch "almost perfect").
- sample accuracy — Pro 66.5% vs Flash 63.5% (Flash ~3 pts lower, the documented
  pattern; bounds self-preference). confusion: both✓=121, pro✓/2nd✗=12,
  pro✗/2nd✓=6, both✗=61.
- → the 65.9% is independently corroborated, not a Pro self-preference artifact.

## Funnel (results/funnel_PRO_p100_k25.json, 1540 scored)
- gold_in_transcript 93.8% → gold_in_memory **92.0%** (extraction SOLVED; the
  corrected figure, not the old store-limit-500 artifact).
- in_pool_25 78.3% · in_pool_50 **85.3%** · reranker_kept **82.9%**.
- conditional retention: compression 97.0%, pool50 91.9%, rerank 94.4%.
- absolute leaks: transcript→memory 44 · **memory→pool50 113 (biggest)** ·
  pool50→reranked 72.
- joined answerer accuracy 65.9% (1015/1540).

## Read / next levers toward 70
- Extraction is done (92%). The bottleneck is now unambiguously **retrieval recall**
  (113 Q of gold sit in the store but miss pool50) plus the **rerank cut** (72 Q).
- That is exactly the **Wave 4 cross-encoder** target — validate on GPU and re-run
  this same config; a calibrated reranker attacks both the memory→pool50 recall and
  the pool50→reranked cut.
- multi_hop (47.2%) is now a synthesis problem more than a retrieval one.
- open_domain (43.8%) is capped upstream by extraction (gold_in_transcript headroom).

Files: results/upg6_PRO_p100_k25.json · results/funnel_PRO_p100_k25.json ·
results/judge_agreement_PRO.json · console results/raw_console/final_pro_console.log
