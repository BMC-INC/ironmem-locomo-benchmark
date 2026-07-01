# IronMem LoCoMo Benchmark

Reproducible evaluation of [IronMem](https://github.com/BMC-INC/Iron-Mem) on the [LoCoMo](https://github.com/snap-research/locomo) long-term conversational memory benchmark (Maharana et al., ACL 2024).

IronMem is the only memory system where every recalled memory carries writer identity, trust tier, classification, and a tamper-evident ledger entry.

## Results

All runs use the live IronMem memory store (28,554+ memories, 10 LoCoMo conversations, 1,986 questions of which 1,540 are scored). Pro = Gemini 2.5 Pro answerer + judge. Flash = Gemini 2.5 Flash. Config: hybrid retrieval, pool 100, retrieve-limit 25, skip-ingest (pre-loaded corpus), 0 errors.

Question accounting: the LoCoMo corpus is 1,986 questions. Categories 1 to 4 (single_hop, multi_hop, temporal, open_domain) total 1,540 scored questions. The 446 adversarial questions (category 5) are logged but excluded from scoring, so every overall accuracy below is over the 1,540 scored set.

### Headline: V2 Pro-judged (2026-06-27)

`results/upg8_PRO_p100_k25_v2.json` (Pro answerer + Pro judge, hybrid, p100/k25, v2 answer prompt, skip-ingest, error_count 0).

| Category | Score | n |
|---|---|---|
| single_hop | 69.6% | 841 |
| multi_hop | 50.4% | 282 |
| temporal | 76.0% | 321 |
| open_domain | 45.8% | 96 |
| **Overall** | **65.9%** | **1,540 scored** |

Judge independence: Flash 2nd-judge Cohen's kappa = 0.88, 94.5% raw agreement on an n=200 sample. On that sample the Pro judge agreed with gold 63.5% of the time and the Flash judge 62.0% (`results/judge_agreement_PRO_v2.json`).

<!-- PLACEHOLDER: upg9 synthesis results go here when validated. Synthesis answerer is built but not yet validated against this baseline. -->

### Run History

| Run | Config | Overall | multi_hop | temporal | Judge | File |
|---|---|---|---|---|---|---|
| V1 | Pro/Pro, hybrid, p100/k25, v1 prompt | 65.9% | 47.2% | 78.5% | Pro, kappa 0.80 | `results/upg6_PRO_p100_k25.json` |
| V2 (headline) | Pro/Pro, hybrid, p100/k25, v2 prompt | 65.9% | 50.4% | 76.0% | Pro, kappa 0.88 | `results/upg8_PRO_p100_k25_v2.json` |
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
  RUN_NOTE_*.md         # Per-run methodology notes
  SYNTHESIS_ANSWERER_SPEC.md
```

## Levers (what we have learned)

| Lever | Type | Status | Impact |
|---|---|---|---|
| Answer prompt tuning | Lateral (redistributes, net 0) | Maxed at V2 | multi_hop +3.2pp, open_domain +2.0pp; paid by temporal -2.5pp |
| Synthesis answerer | multi_hop (the real lever) | Built, not yet validated | Target: multi_hop 50 -> 55-60% |
| Cross-encoder reranker | Retrieval recall | Built, GPU-gated (`cross_encoder_ready: false`) | pool 150 sweet spot (~84% raw recall) |
| Pool size | Retrieval recall | Settled at 100 (LLM rerank) / 150 (cross-encoder) | raw recall 73.2% @100, 83.9% @150, flat @200 |
| open_domain | Extraction-capped | Low priority | much of open_domain gold is not in the source transcript |

## Comparison with other systems

| System | Benchmark | Overall | multi_hop | temporal | Governed |
|---|---|---|---|---|---|
| **IronMem** | LoCoMo (Pro judge) | **65.9%** | 50.4% | 76.0% | Yes |
| Mem0 | LoCoMo (gpt-4o-mini judge) | 75.78%\* | 46.88%\* | 85.05%\* | No |
| Supermemory | LongMemEval (recall metric) | 95% R@15\* | -- | 91%\* | No |

\* Self-reported by those projects on different judge models and/or a different benchmark. Not independently reproduced here and not directly comparable. A clean comparison requires running each system on the same benchmark with the same judge. Of note, IronMem's multi_hop (50.4%) exceeds Mem0's reported multi_hop (46.88%) despite Mem0's higher reported overall on a more lenient judge model.

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
