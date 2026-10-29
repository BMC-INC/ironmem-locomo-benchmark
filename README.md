# IronMem LoCoMo Benchmark

Evaluation harness and published result artifacts for [IronMem](https://github.com/BMC-INC/Iron-mem) on the [LoCoMo](https://github.com/snap-research/locomo) long-term conversational memory benchmark (Maharana et al., ACL 2024).

This repository is the research and evaluation companion to the IronMem runtime.
It contains the runner, per-question results, comparison baselines, and methodology
notes. It is intended for developers evaluating memory retrieval and for readers
checking the evidence behind IronMem's published claims.

[![Validation](https://github.com/BMC-INC/ironmem-locomo-benchmark/actions/workflows/validate.yml/badge.svg)](https://github.com/BMC-INC/ironmem-locomo-benchmark/actions/workflows/validate.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)

[Verify a result](#verify-the-published-result-offline) · [Run an evaluation](#reproducing) · [Methodology](#methodology) · [Limitations](#what-the-headline-does-not-claim)

## Verify the published result offline

Python 3.11+ is sufficient to check the headline artifact. This does not install
IronMem, access a memory store, use credentials, or call a model:

```sh
git clone --depth 1 https://github.com/BMC-INC/ironmem-locomo-benchmark.git
cd ironmem-locomo-benchmark
python3 scripts/verify_result.py \
  results/full_gpu_p100_k25_v2agg_normhints9_20260701T233607Z.json \
  --expected-questions 1986 --expected-scored 1540
```

Expected: **1,986 recorded questions, 1,540 scored, 1,115 correct, 446 excluded,
and 0.724 overall**. The verifier checks unique question IDs, categories, coverage,
error-free completion, binary scores, and recomputed aggregates. It prints the
artifact's SHA-256 so the input is identifiable. Arithmetic verification does
not independently validate the judge's answers or reproduce retrieval.

## Results

The benchmark evaluates 10 LoCoMo conversations and 1,986 questions, of which 1,540 questions in categories 1-4 are scored. The 446 adversarial questions in category 5 are logged but excluded from the headline accuracy. Pro = Gemini 2.5 Pro answerer + judge. Flash = Gemini 2.5 Flash.

### Headline: 72.4% Pro-judged record run (2026-07-01)

`results/full_gpu_p100_k25_v2agg_normhints9_20260701T233607Z.json`

Verified full-corpus record run using Gemini 2.5 Pro as answerer and judge, hybrid retrieval, pool 100, retrieve-limit 25, synthesis/aggregation, deterministic normalized-hint recall supplements, and the GPU-ready cross-encoder path. The run processed all 1,986 questions and completed with the live IronMem store at 29,867 memories and 61,229 observations.

| Category | Score | n |
|---|---:|---:|
| single_hop | 77.2% | 841 |
| multi_hop | 58.2% | 282 |
| temporal | 77.0% | 321 |
| open_domain | 57.3% | 96 |
| **Overall** | **72.4%** | **1,540 scored** |

Local paths and cloud project identifiers in console logs and run notes are redacted; benchmark measurements are unchanged. The console artifact for the record run is committed at `results/raw_console/full_gpu_p100_k25_v2agg_normhints9_20260701T233607Z_console.log`.

### Earlier locked baselines

The June 27 V2 baseline remains in the repository as an immutable comparison point:

`results/upg8_PRO_p100_k25_v2.json` (Pro answerer + Pro judge, hybrid, p100/k25, v2 answer prompt, skip-ingest, error_count 0).

| Category | Score | n |
|---|---:|---:|
| single_hop | 69.6% | 841 |
| multi_hop | 50.4% | 282 |
| temporal | 76.0% | 321 |
| open_domain | 45.8% | 96 |
| **Overall** | **65.9%** | **1,540 scored** |

Judge independence on the V2 baseline: Flash 2nd-judge Cohen's kappa = 0.88, 94.5% raw agreement on an n=200 sample. On that sample the Pro judge agreed with gold 63.5% of the time and the Flash judge 62.0% (`results/judge_agreement_PRO_v2.json`).

### Run History

| Run | Config | Overall | multi_hop | temporal | Judge | File |
|---|---|---:|---:|---:|---|---|
| **Record run** | Pro/Pro, hybrid, p100/k25, v2 aggregation + synthesis + normalized-hint supplements, cross-encoder ready | **72.4%** | **58.2%** | **77.0%** | Pro | `results/full_gpu_p100_k25_v2agg_normhints9_20260701T233607Z.json` |
| Governance-off | Pro/Pro, hybrid, p100/k25, governance retrieval weights disabled | 68.4% | -- | -- | Pro | `results/upg13_PRO_p100_k25_v2_govoff.json` |
| Governed parity | Pro/Pro, hybrid, p100/k25 | 66.3% | -- | -- | Pro | `results/upg11_PRO_p100_k25_v2_parity.json` |
| V2 baseline | Pro/Pro, hybrid, p100/k25, v2 prompt | 65.9% | 50.4% | 76.0% | Pro, kappa 0.88 | `results/upg8_PRO_p100_k25_v2.json` |
| V1 | Pro/Pro, hybrid, p100/k25, v1 prompt | 65.9% | 47.2% | 78.5% | Pro, kappa 0.80 | `results/upg6_PRO_p100_k25.json` |
| Flash baseline | Flash/Flash, hybrid, p100/k25 | 60.7% | 42.6% | 67.3% | Flash | `results/upg_validation_fullstack_flash_p100_k25.json` |
| Prior Pro | Pro/Pro, hybrid, pool 50 + rerank | 60.9% | -- | -- | Pro | `results/upg3_PRO_C_rerank_pool50.json` |

The progression matters: prompt tuning alone redistributed accuracy without changing the V2 overall score. The later gains came from retrieval and answer-construction work: synthesis/aggregation, broader evidence recovery, multi-hop normalization, deterministic recall supplements, and the cross-encoder-capable retrieval path.

## Methodology

### Retrieval Stack

IronMem hybrid retrieval pipeline includes:
- Full-text search (SQLite FTS5) + vector embeddings (bge-small-en-v1.5)
- Reciprocal Rank Fusion (RRF) to merge FTS + vector candidate lists
- Routed weighted fusion and query decomposition
- Temporal and graph recall with provenance-aware edges
- Entity alias expansion and temporal conflict handling
- Structured-evidence reranking and rerank re-anchoring
- Local cross-encoder reranking when available
- Multi-hop synthesis / answer aggregation
- Deterministic normalized-hint recall supplements used by the 72.4% record run
- Content-addressed deduplication and source-backed recovery through IronMem CCR

### Corpus

The benchmark uses the live IronMem memory store rather than a benchmark-only memory implementation. Earlier locked runs used a store of approximately 28,554 memories, 3,836 graph edges, and 36,735 chunks with CCR compression of 75.1%. The July 1 record run executed against a later live store containing 29,867 memories and 61,229 observations.

### Evaluation Protocol

1. Run against a pre-loaded IronMem corpus; no benchmark-only retrieval implementation replaces the live server.
2. Retrieve context from IronMem using the configured retrieval stack.
3. The answerer generates a response from retrieved evidence.
4. The LLM judge scores the response against the gold answer (CORRECT / WRONG).
5. Categories 1-4 are scored; category 5 adversarial questions are logged but excluded from headline accuracy.
6. Runs preserve model, backend, retrieval configuration, output JSON, and console artifacts so results can be audited and compared.
7. Runs with infrastructure errors are not treated as valid headline evaluations.

### Governed Recall (what standard benchmarks do not measure)

Standard memory benchmarks primarily measure answer or retrieval quality: did the system recall enough information to answer correctly? They do not measure several properties required for durable agent memory:

- **Writer identity:** Who recorded this memory?
- **Trust tier:** How reliable is this memory?
- **Provenance:** Can the answer be traced back to original evidence?
- **Classification:** Is the memory PHI / PII / confidential?
- **Consent and retention:** Is the system allowed to retain or disclose it?
- **Legal hold:** Can the memory be deleted?
- **Contradiction state:** Does competing evidence remain unresolved?
- **Influence policy:** Is this memory permitted to shape this task or action?
- **Ledger integrity:** Is there a tamper-evident record of memory mutation?

IronMem tracks these properties alongside retrieval. Benchmark accuracy therefore measures only one dimension of the system.

## Reproducing

### Prerequisites

- Python 3.11+
- Running IronMem REST server (`ironmem server`, default port 37778). The `ironmem serve` command starts MCP transport instead.
- One supported LLM backend: Vertex AI with Google Cloud ADC (default), Azure OpenAI with Managed Identity, or public OpenAI with an API key
- LoCoMo dataset (`data/locomo10.json` from [snap-research/locomo](https://github.com/snap-research/locomo))

### Setup

```bash
git clone --depth 1 https://github.com/BMC-INC/ironmem-locomo-benchmark.git
cd ironmem-locomo-benchmark
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Configure the backend using [.env.example](.env.example). For Vertex, select your
own authorized `GOOGLE_CLOUD_PROJECT` (or pass `--vertex-project`) and configure
Application Default Credentials. There is no default billing project.

Use a dedicated IronMem database for evaluation. Ingest writes benchmark
conversations to that server; a different project prefix alone is not a separate
database. See IronMem's [isolated trial instructions](https://github.com/BMC-INC/Iron-mem/blob/main/docs/installation-and-recovery.md#isolate-a-trial).
The provider-backed commands below make paid model calls. In this harness,
`--dry-run` means a smaller evaluation and can still call models; use the offline
verifier above when you want no model calls.

### Ingest and evaluate (first run)

```bash
python -m benchmark.run --strategy hybrid --concurrency 8 \
  --vertex-location us-west1 --output results/initial_ingest.json
```

### Run evaluation

The harness exposes the retrieval, answer-prompt, synthesis, reranking, and normalization controls used throughout the committed run history. Establish a baseline on your own isolated corpus, then compare configurations using the same corpus and judge. The saved historical results alone do not recreate the live database state used in July 2026.

```bash
# Evaluate the V2 baseline configuration against your already-ingested corpus
python -m benchmark.run --strategy hybrid --skip-ingest \
  --pool 100 --retrieve-limit 25 --concurrency 8 \
  --answer-prompt v2 --vertex-location us-west1 \
  --output results/your_run_name.json

# Flash second-judge agreement (kappa)
python scripts/judge_agreement.py results/your_run_name.json \
  --judge-model gemini-2.5-flash --sample 200 \
  --output results/judge_agreement_your_run.json
```

For the exact July 1 record-run evidence, see:

- `results/full_gpu_p100_k25_v2agg_normhints9_20260701T233607Z.json`
- `results/raw_console/full_gpu_p100_k25_v2agg_normhints9_20260701T233607Z_console.log`
- `RUN_NOTE_2026-07-01_gpu_episodic_reconstruction.md`

### Azure OpenAI / OpenAI backend

The same ingest, answer, judge, salvage, and judge-agreement paths use the backend selected by `BENCH_LLM`. Azure model values are deployment names.

```bash
# Azure VM with a system-assigned Managed Identity and the
# Cognitive Services OpenAI User role. No API key is needed.
export BENCH_LLM=openai
export AZURE_OPENAI_ENDPOINT=https://YOUR-RESOURCE.openai.azure.com
export AZURE_OPENAI_API_VERSION=2024-10-21
export OPENAI_ANSWERER_MODEL=YOUR-ANSWERER-DEPLOYMENT
export OPENAI_JUDGE_MODEL=YOUR-JUDGE-DEPLOYMENT

python -m benchmark.run --strategy hybrid --skip-ingest \
  --pool 100 --retrieve-limit 25 --concurrency 6 \
  --answer-prompt v2 --output results/azure_run.json

# Public OpenAI alternative
export BENCH_LLM=openai
export OPENAI_API_KEY=...
export OPENAI_ANSWERER_MODEL=gpt-4.1
export OPENAI_JUDGE_MODEL=gpt-4.1
```

Every output records `llm_backend`, `answerer_model`, and `judge_model` so results from different providers cannot be mistaken for an apples-to-apples comparison.

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
    query.py            # Retrieval + answer generation / synthesis controls
    query_agentic.py    # Agentic answerer variant
    ingest.py           # LoCoMo -> IronMem ingestion
    judge.py            # LLM judge scoring
    gemini.py           # Vertex AI Gemini client
    openai_client.py    # Azure MI / OpenAI-compatible client
    llm.py              # Backend selection + model mapping
    ironmem_client.py   # IronMem server HTTP client
    config.py           # Run config + model/region defaults
  scripts/
    judge_agreement.py  # 2nd-judge kappa calculation
    funnel_probe.py     # Retrieval funnel analysis
    analyze_failures.py # Per-category / per-stage failure classification
    pool_curve.py       # Recall curve by pool size
    combine_results.py  # Merge / compare result files
    audit_retrieval_floor.py
    build_canary_dataset.py
    build_flip_set.py
    classify_flips.py
  data/
    *.json              # Canary, regression, and targeted evaluation sets
  results/
    *.json              # Result files (committed)
    raw_console/        # Console logs for reproducibility
    regression/         # Regression and flip-analysis artifacts
  RUN_NOTE_*.md         # Per-run methodology notes
  SYNTHESIS_ANSWERER_SPEC.md
```

## Levers (what we have learned)

| Lever | Type | Status | Observed / intended impact |
|---|---|---|---|
| Answer prompt tuning | Lateral | Maxed at V2 baseline | multi_hop +3.2pp and open_domain +2.0pp with net-zero overall vs V1 |
| Synthesis / answer aggregation | Multi-hop reasoning | Active in record-run path | Helps combine gold-present evidence that retrieval alone cannot answer |
| Cross-encoder reranker | Retrieval recall | Built and record-run ready | GPU record run reports `cross_encoder_ready=True` |
| Pool/context size | Retrieval recall | Tuned | Earlier sweeps found ~84% raw recall around pool 150; record run uses p100/k25 |
| Normalized-hint supplements | Deterministic recall recovery | Active in record run | Part of the path from the 65.9% V2 baseline to the 72.4% record run |
| Regression/canary harness | Evaluation reliability | Built | Tracks rescued vs regressed questions before accepting broader changes |
| open_domain | Extraction/source constrained | Lower priority | Some gold answers are not recoverable from source transcript evidence |

## Comparing systems fairly

Compare systems on the same corpus, question set, answerer, judge, scoring
protocol, retrieval budget, and preprocessing. Scores from different benchmarks
or judge models do not establish a ranking. This repository publishes IronMem's
own evidence; it does not establish that other systems lack governance features.

## What the headline does not claim

The 72.4% result is a LoCoMo result under the configuration described above. It is not claimed to be directly comparable to scores produced with different answerers, judges, benchmarks, retrieval budgets, or benchmark-specific preprocessing. The repository keeps earlier baselines and regression artifacts specifically so improvements can be examined against known configurations rather than only through a single headline number.

The record run includes benchmark-specific normalized-hint supplements and
configuration tuning. It is not a held-out demonstration of general coding
accuracy. Exact historical reruns also depend on the original memory-store state,
model availability, and provider behavior; this repository does not include an
export of that live database. Use the stored per-question artifacts for auditing
and fresh, matched runs for new comparisons.

Likewise, LoCoMo does not test IronMem's full governed-memory surface: source reversibility, provenance lineage, contradiction sets, inference quarantine, valid-time semantics, purpose-bound influence controls, consent/retention policy, and tamper-evident mutation history require separate evaluations.

## Development and validation

After installing the dependencies:

```sh
python -m unittest discover -s tests -v
python -m benchmark.run --help
```

[CI](https://github.com/BMC-INC/ironmem-locomo-benchmark/actions/workflows/validate.yml)
runs mocked client/configuration tests and verifies the committed headline
artifact. It does not launch paid evaluations or certify benchmark quality.
Report reproducible harness problems in [issues](https://github.com/BMC-INC/ironmem-locomo-benchmark/issues),
including the commit, backend, and redacted configuration. Keep credentials,
personal memory stores, and private evaluation data out of commits.

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
