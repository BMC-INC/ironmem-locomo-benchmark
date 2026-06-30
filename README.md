# IronMem LoCoMo Benchmark

Reproducible evaluation of [IronMem](https://github.com/BMC-INC/Iron-Mem) on the [LoCoMo](https://github.com/snap-research/locomo) long-term conversational memory benchmark (Maharana et al., ACL 2024).

IronMem is the only memory system where every recalled memory carries writer identity, trust tier, classification, and a tamper-evident ledger entry.

## Results

All runs use the live IronMem memory store (28,554+ memories, 10 LoCoMo conversations, 1,986 questions of which 1,540 are scored). Pro = Gemini 2.5 Pro answerer + judge. Flash = Gemini 2.5 Flash. Config: hybrid retrieval, pool 100, retrieve-limit 25, skip-ingest (pre-loaded corpus), 0 errors.

Question accounting: the LoCoMo corpus is 1,986 questions. Categories 1 to 4 (single_hop, multi_hop, temporal, open_domain) total 1,540 scored questions. The 446 adversarial questions (category 5) are logged but excluded from scoring, so every overall accuracy below is over the 1,540 scored set.

### Headline: governance-off, Pro-judged (2026-06-30)

The strongest configuration sets the two governance retrieval boosts (writer-tier weight and temporal-trust weight) to 0, so candidates are ranked on pure relevance. Governance metadata (writer identity, trust tier, provenance, ledger) is still recorded on every memory and remains fully queryable; it just no longer tilts ranking. `results/upg13_PRO_p100_k25_v2_govoff.json` (Pro answerer + Pro judge, hybrid, p100/k25, v2 answer prompt, governance weights 0, skip-ingest, error_count 0).

| Category | Score | n |
|---|---|---|
| single_hop | 72.1% | 841 |
| multi_hop | 52.5% | 282 |
| temporal | 78.2% | 321 |
| open_domain | 50.0% | 96 |
| **Overall** | **68.4%** | **1,540 scored** |

#### Single-variable A/B: governance weights

Same Pro/Pro, v2, p100/k25 config; the only difference is the governance retrieval weights. Both runs are clean (0 / 1,986 errors).

| Config | Overall | single_hop | multi_hop | temporal | open_domain |
|---|---|---|---|---|---|
| Governed (weights 0.05, default) | 66.3% | 69.0% | 50.0% | 77.9% | 52.1% |
| **Governance-off (weights 0)** | **68.4%** | 72.1% | 52.5% | 78.2% | 50.0% |
| Δ | **+2.1** | +3.1 | +2.5 | +0.3 | -2.1 |

Why it moves: the writer-tier and temporal-trust boosts each add 0.05 to a Reciprocal Rank Fusion base score that spans only 0.0119 to 0.0167 across the top 25 candidates (the gap between adjacent ranks is about 0.0003). At that scale a 0.05 add does not nudge near-ties as intended, it hard-partitions candidates by writer tier and overrides relevance. Zeroing both restores pure relevance ranking and lifts overall accuracy by 2.1 points, with the gains in single_hop (+3.1) and multi_hop (+2.5). The lone regression is open_domain (-2.1, n=96, the smallest and noisiest category). Governed baseline file: `results/upg11_PRO_p100_k25_v2_parity.json` (cloud, 66.3%); the earlier local run `results/upg8_PRO_p100_k25_v2.json` reproduced this at 65.9%.

Judge independence (measured on the governed v2 run): Flash 2nd-judge Cohen's kappa = 0.88, 94.5% raw agreement on an n=200 sample. On that sample the Pro judge agreed with gold 63.5% of the time and the Flash judge 62.0% (`results/judge_agreement_PRO_v2.json`).

### Run History

| Run | Config | Overall | multi_hop | temporal | Judge | File |
|---|---|---|---|---|---|---|
| V2 governance-off (headline) | Pro/Pro, hybrid, p100/k25, v2, gov weights 0 | 68.4% | 52.5% | 78.2% | Pro, kappa 0.88 | `results/upg13_PRO_p100_k25_v2_govoff.json` |
| V2 governed (cloud parity) | Pro/Pro, hybrid, p100/k25, v2 | 66.3% | 50.0% | 77.9% | Pro | `results/upg11_PRO_p100_k25_v2_parity.json` |
| V2 governed (local) | Pro/Pro, hybrid, p100/k25, v2 prompt | 65.9% | 50.4% | 76.0% | Pro, kappa 0.88 | `results/upg8_PRO_p100_k25_v2.json` |
| V1 | Pro/Pro, hybrid, p100/k25, v1 prompt | 65.9% | 47.2% | 78.5% | Pro, kappa 0.80 | `results/upg6_PRO_p100_k25.json` |
| Flash baseline | Flash/Flash, hybrid, p100/k25 | 60.7% | 42.6% | 67.3% | Flash | `results/upg_validation_fullstack_flash_p100_k25.json` |
| Prior Pro | Pro/Pro, hybrid, pool 50 + rerank | 60.9% | -- | -- | Pro | `results/upg3_PRO_C_rerank_pool50.json` |

V2 vs V1: same overall (65.9%), better-shaped on the hard categories. multi_hop +3.2pp and open_domain +2.0pp, paid for by temporal -2.5pp and single_hop -0.3pp. The answer prompt is a maxed lateral lever: it redistributes accuracy across categories at net zero overall (roughly 114 questions rescued, 114 regressed).

## Methodology

### Retrieval Stack

IronMem hybrid retrieval pipeline:
- Full-text search (SQLite FTS5) + vector embeddings (bge-small-en-v1.5)
- Reciprocal Rank Fusion (RRF) to merge FTS + vector candidate lists
- LLM reranker over the fused pool (cross-encoder backend built but GPU-gated, not active in these runs)
- Temporal knowledge graph with provenance edges and supersession tracking
- Content-addressed deduplication (CCR, 75.1% compression)

### Corpus

10 LoCoMo conversations ingested via the hybrid strategy (session compression + graph/entity enrichment). The corpus is the live IronMem production store, not a benchmark-only snapshot. Live store at eval time: ~28,554 memories, 3,836 graph edges, 36,735 chunks, CCR compression 75.1%.

### Evaluation Protocol

1. Pre-loaded corpus (`--skip-ingest`). No re-ingestion between runs.
2. Answerer generates a response from retrieved context.
3. LLM judge scores the response against the gold answer (CORRECT / WRONG).
4. A second judge (different model) scores the same responses for independence verification (Cohen's kappa).
5. Error-count gate: any run with errors > 0 (for example 429 throttles) is discarded.

### Governed Recall (what benchmarks do not measure)

Standard memory benchmarks measure retrieval quality: did the system recall the right answer? They do not measure:

- **Writer identity:** Who recorded this memory?
- **Trust tier:** How reliable is this memory?
- **Provenance:** Can you trace this answer back to the original evidence?
- **Classification:** Is this memory PHI / PII / confidential?
- **Legal hold:** Can this memory be deleted?
- **Ledger integrity:** Is there a tamper-evident record of every memory mutation?

IronMem tracks all of these. Every memory in the benchmark corpus carries governance metadata. No other evaluated memory system does.

## Reproducing

### Prerequisites

- Python 3.11+
- Running IronMem server (`ironmem serve`, default port 37778)
- Google Cloud ADC with Vertex AI access (Gemini 2.5 Pro/Flash)
- LoCoMo dataset (`data/locomo10.json` from [snap-research/locomo](https://github.com/snap-research/locomo))

### Setup

```bash
git clone https://github.com/BMC-INC/ironmem-locomo-benchmark.git
cd ironmem-locomo-benchmark
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### Ingest (first time only)

```bash
python -m benchmark.run --strategy hybrid --concurrency 8 \
  --vertex-location us-west1 --output results/initial_ingest.json
```

### Run evaluation

```bash
# Reproduce the V2 headline: Pro answerer + Pro judge, pool 100, retrieve-limit 25
python -m benchmark.run --strategy hybrid --skip-ingest \
  --pool 100 --retrieve-limit 25 --concurrency 8 \
  --answer-prompt v2 --vertex-location us-west1 \
  --output results/your_run_name.json

# Flash second-judge agreement (kappa)
python scripts/judge_agreement.py results/your_run_name.json \
  --judge-model gemini-2.5-flash --sample 200 \
  --output results/judge_agreement_your_run.json
```

### Analysis

```bash
# Failure classification (per category / per funnel stage)
python scripts/analyze_failures.py results/your_run_name.json

# Retrieval funnel (where gold answers leak between store and answerer)
python scripts/funnel_probe.py --strategy hybrid --pool 100 \
  --scored results/your_run_name.json \
  --output results/funnel_your_run.json

# Recall curve by pool size (LLM-free; sweeps pool 10..200)
python scripts/pool_curve.py
```

## Architecture

```
ironmem-locomo-benchmark/
  benchmark/
    run.py              # Main runner (ingest + answer + judge orchestration)
    query.py            # Retrieval + answer generation
    query_agentic.py    # Agentic answerer variant
    ingest.py           # LoCoMo -> IronMem ingestion
    judge.py            # LLM judge scoring
    gemini.py           # Vertex AI Gemini client
    ironmem_client.py   # IronMem server HTTP client
    config.py           # Run config + model/region defaults
  scripts/
    judge_agreement.py  # 2nd-judge kappa calculation
    funnel_probe.py     # Retrieval funnel analysis
    analyze_failures.py # Per-category / per-stage failure classification
    pool_curve.py       # Recall curve by pool size
    combine_results.py  # Merge / compare result files
  data/
    locomo10.json       # LoCoMo dataset (not committed, see Setup)
  results/
    *.json              # Result files (committed)
    raw_console/        # Console logs for reproducibility
  SYNTHESIS_ANSWERER_SPEC.md
```

## Levers (what we have learned)

| Lever | Type | Status | Impact |
|---|---|---|---|
| Governance retrieval weights off | Retrieval ranking | Confirmed (current headline) | +2.1pp overall (single +3.1, multi +2.5); open_domain -2.1 |
| Answer prompt tuning | Lateral (redistributes, net 0) | Maxed at V2 | multi_hop +3.2pp, open_domain +2.0pp; paid by temporal -2.5pp |
| Synthesis answerer | multi_hop | Built; preliminary runs flat | No overall gain yet; clean re-run pending |
| Cross-encoder reranker | Retrieval recall | Tested on GPU, lost as built | -5.7pp vs the LLM reranker; it reranks truncated text, structured-evidence variant untested |
| Pool size | Retrieval recall | Settled at 100 (LLM rerank) | raw recall 73.2% @100, 83.9% @150, flat @200 |
| open_domain | Extraction-capped | Low priority | much of open_domain gold is not in the source transcript |

## Path past 70%

Governance-off banks 68.4%. The remaining 1.6 points to clear 70% sit almost entirely in the two laggard categories, multi_hop (52.5%) and open_domain (50.0%); single_hop (72.1%) and temporal (78.2%) are already strong.

Candidate levers, in priority order:

1. **Structured-evidence reranking.** Both the LLM reranker and the GPU cross-encoder currently rerank truncated document text (roughly the first few hundred characters of each candidate). Reranking structured evidence instead, the atomic fact, its event date, the speaker, and the source turn ids, hands the reranker the fields that actually decide multi_hop and temporal questions. The off-the-shelf cross-encoder lost as-built (-5.7pp) precisely because it reranked truncated text, so the structured-evidence form is the untested and most promising version of this lever.
2. **Routed weighted fusion.** Today every category shares one broad RRF blend of the FTS and vector candidate lists. Routing the fusion weights by question type (lexical-heavy for single_hop, graph and temporal-heavy for multi_hop and temporal) targets the categories that are leaking instead of a single one-size blend.
3. **Per-fact temporal proximity.** multi_hop questions that chain across dated events benefit from boosting candidates whose event dates are close to the dates referenced in the question. That is a relevance signal, the kind that just helped, not a trust signal, the kind that just hurt.

open_domain (50.0%) is largely extraction-capped: a meaningful share of its gold answers are not present in the source transcript, so it is a ceiling rather than a lever. The realistic route past 70% is lifting multi_hop from 52.5% toward 60% via levers 1 and 2. On the current category weights (282 of 1,540 scored), a multi_hop move of that size is worth roughly 1.4 points of overall on its own, and the single_hop and temporal gains from structured-evidence reranking carry it the rest of the way.

## Comparison with other systems

| System | Benchmark | Overall | multi_hop | temporal | Governed |
|---|---|---|---|---|---|
| **IronMem** | LoCoMo (Pro judge) | **68.4%** | 52.5% | 78.2% | Yes |
| Mem0 | LoCoMo (gpt-4o-mini judge) | 75.78%\* | 46.88%\* | 85.05%\* | No |
| Supermemory | LongMemEval (recall metric) | 95% R@15\* | -- | 91%\* | No |

\* Self-reported by those projects on different judge models and/or a different benchmark. Not independently reproduced here and not directly comparable. A clean comparison requires running each system on the same benchmark with the same judge. Of note, IronMem's multi_hop (52.5%) exceeds Mem0's reported multi_hop (46.88%) despite Mem0's higher reported overall on a more lenient judge model.

## Citation

```bibtex
@misc{ironmem-locomo-2026,
  title={IronMem LoCoMo Benchmark: Governed Memory Evaluation},
  author={Benton, James},
  year={2026},
  url={https://github.com/BMC-INC/ironmem-locomo-benchmark}
}
```

LoCoMo dataset citation:
```bibtex
@inproceedings{maharana2024locomo,
  title={Evaluating Very Long-Term Conversational Memory of {LLM} Agents},
  author={Maharana, Adyasha and Lee, Dong-Ho and Tulyakov, Sergey and Bansal, Mohit and Barbieri, Francesco and Fang, Yuwei},
  booktitle={Proceedings of ACL 2024},
  year={2024},
  doi={10.18653/v1/2024.acl-long.747}
}
```

## License

Apache-2.0
