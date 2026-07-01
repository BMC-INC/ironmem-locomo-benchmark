# GPU Canary: Episodic Reconstruction

Date: 2026-07-01

This run series tested the additive E-mem-style episodic reconstruction path on
the GCP L4 canary VM after deploying the patched IronMem cross-encoder binary.
All JSON and console artifacts are preserved under `results/` and
`results/raw_console/`.

## What changed

- Added `--episodic-reconstruct` for multi-hop questions.
- Added source episode expansion through IronMem `retrieve_original`.
- Added a reconstruction step that extracts question-relevant evidence before
  the master aggregator answers.
- Added cheap supplemental recall controls so cross-encoder cost stays at one
  reranked call per question:
  - `--supplement-multi-query`
  - `--supplement-limit`
  - `--supplement-hints-only`
- Patched IronMem's source-fact floor to rank retained exact/source facts by
  lexical overlap strength instead of taking the first eligible FTS rows.

## GPU sample results

All runs below used the 12-question lost/gained multi-hop canary, pool 100,
retrieve limit 25, Gemini Pro answerer/judge, and cross-encoder rerank.

| Run | Episodic | Overall | Multi-hop | Errors |
| --- | --- | ---: | ---: | ---: |
| `canary_lost_gained_ce_sample12_gpu_p100_k25_v2agg_supphints4_20260701T103200Z.json` | no | 75.00% | 75.00% | 0 |
| `canary_lost_gained_ce_sample12_gpu_p100_k25_v2agg_supphints8_20260701T103200Z.json` | no | 66.67% | 66.67% | 0 |
| `canary_lost_gained_ce_sample12_gpu_p100_k25_v2agg_suppfocus4_20260701T103200Z.json` | no | 58.33% | 58.33% | 0 |
| `canary_lost_gained_ce_sample12_gpu_p100_k25_v2agg_episodic_focus4_20260701T103200Z.json` | yes | 41.67% | 41.67% | 0 |
| `canary_lost_gained_ce_sample12_gpu_p100_k25_v2agg_episodic_focus4b_20260701T103200Z.json` | yes | 66.67% | 66.67% | 0 |
| `canary_lost_gained_ce_sample12_gpu_p100_k25_v2agg_episodic_focus4c_20260701T103200Z.json` | yes | 66.67% | 66.67% | 0 |
| `canary_lost_gained_ce_sample12_gpu_p100_k25_v2agg_focus4d_20260701T103200Z.json` | no | 75.00% | 75.00% | 0 |

## Decision

Do not launch a full Pro benchmark with `--episodic-reconstruct` yet. The best
episodic canary recovered from 41.67% to 66.67%, but it still trails the best
non-episodic GPU canary at 75.00%.

The best current record-attempt candidate from this run family is the
non-episodic cross-encoder path with focused supplemental hints:

```bash
python -m benchmark.run --strategy hybrid --skip-ingest --rerank \
  --require-cross-encoder --route --synthesize \
  --supplement-multi-query 4 --supplement-limit 4 --supplement-hints-only \
  --pool 100 --retrieve-limit 25 --concurrency 8 \
  --answer-prompt v2 --vertex-location us-west1 \
  --output results/<run_name>.json
```

## Next build target

The episodic path is useful infrastructure, but the next accuracy work should be
question-type-specific evidence extraction and answer normalization on the flip
set before another full paid run. The failures showed over-broad list answers,
missed merged-name conflicts, and activity/category normalization errors.

Canary gate for future work:

- Must beat the 75.00% 12-question GPU canary before a full Pro run.
- Must not regress the recovered `supphints4` wins.
- Must preserve every run artifact for later training/eval use.
