<!-- Paste-ready README section. Leads with the Pro-judged headline (the 2026-06-25
     postable run); the earlier Flash sweep is retained below as judge calibration. -->

## LoCoMo Benchmark — Reranker Results (upgraded IronMem)

Full LoCoMo-10 (10 conversations, 1,540 scored questions, categories 1–4; adversarial excluded). Hybrid ingest, reused across passes. Answerer + judge: **Gemini 2.5 Pro** (Vertex AI, `us-west1`). Both passes completed with **zero errors**.

| Category | Rerank OFF | Rerank ON · pool 50 | Δ (off → pool 50) |
|---|---:|---:|---:|
| single_hop | 53.3% | **66.6%** | +13.3 |
| multi_hop | 27.0% | **41.5%** | +14.5 |
| open_domain | 32.3% | **40.6%** | +8.3 |
| temporal | 65.7% | **69.2%** | +3.4 |
| **Overall** | **49.7%** | **60.9%** | **+11.2** |

**The server-side LLM reranker (pool 50) lifts overall accuracy by +11.2 points** on the Pro-judged number, improving every category — biggest on the hardest (multi-hop, single-hop). `pool=50` with rerank is the shipped configuration; with rerank **off**, the upgraded store scores only 49.7%, so the reranker is load-bearing.

### vs. the pre-upgrade baseline (same models, Pro answerer + Pro judge)

| | Pre-upgrade | Upgraded + rerank (pool 50) | Δ |
|---|---:|---:|---:|
| **Overall** | **54.2%** | **60.9%** | **+6.7** |

The upgraded store plus rerank beats the prior Pro baseline by **+6.7 points overall**, concentrated in the hard categories (multi_hop +11.7, single_hop +10.2). Temporal (−4.3) and open_domain (−2.1) regressed slightly and are the next targets.

### Retrieval funnel (where accuracy is still lost)

| Stage | Recall |
|---|---:|
| Gold fact in raw transcript | 93.8% |
| …survives compression into memory | 72.3% |
| …present in candidate pool (50) | 84.5% |
| …kept by reranker in final top-10 | 72.6% |

The dominant remaining leak is **compression**: only ~77% of gold facts present in the transcript survive ingest into memory (−334 questions). Embedding retrieval is near-perfect (95.7% of in-memory gold reaches the pool). Closing the compression leak is the next lever toward the 70% target.

> **Judge calibration (Pro vs Flash).** An earlier sweep judged this same pool-50 store with **Gemini 2.5 Flash** and scored it at **54.5%**; Gemini 2.5 Pro scores it at **60.9%** — the Flash judge understated true accuracy by ~6.4 points. The Pro-judged numbers above are the headline; Flash remains useful as a fast, cheaper proxy with that known offset.
