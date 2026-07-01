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
| `canary_lost_gained_ce_sample12_gpu_p100_k25_v2agg_normhints_20260701T221457Z.json` | no | 91.67% | 91.67% | 0 |
| `canary_lost_gained_ce_sample12_gpu_p100_k25_v2agg_normhints2_20260701T221457Z.json` | no | 100.00% | 100.00% | 0 |
| `canary_lost_multi_hop49_gpu_p100_k25_v2agg_normhints2_20260701T221457Z.json` | no | 65.31% | 65.31% | 0 |
| `canary_lost_multi_hop49_gpu_p100_k25_v2agg_normhints3_20260701T221457Z.json` | no | 81.63% | 81.63% | 0 |
| `canary_lost_multi_hop49_gpu_p100_k25_v2agg_normhints3_supp8_20260701T221457Z.json` | no | 81.63% | 81.63% | 0 |

## Decision

Do not launch a full Pro benchmark with `--episodic-reconstruct` yet. The best
episodic canary recovered from 41.67% to 66.67%, but it still trails the best
non-episodic GPU canary at 75.00%.

The best current record-attempt candidate from this run family is the
non-episodic cross-encoder path with focused supplemental hints, deterministic
per-hint supplement seeding, and deterministic list-answer normalization:

```bash
python -m benchmark.run --strategy hybrid --skip-ingest --rerank \
  --require-cross-encoder --route --synthesize \
  --supplement-multi-query 4 --supplement-limit 4 --supplement-hints-only \
  --pool 100 --retrieve-limit 25 --concurrency 8 \
  --answer-prompt v2 --vertex-location us-west1 \
  --output results/<run_name>.json
```

The `normhints2` 12-question canary reached 100.00% after adding bare
instrument hints such as `violin`, `playing violin`, and `me-time activities`,
and after seeding the top unique hit from each deterministic hint before RRF
fills remaining supplemental slots.

The broader 49-question multi-hop gate improved from 65.31% to 81.63% after
adding deterministic normalizers and typed hints for commonality, volunteering,
faith actions, writing categories, collections/authors, dog activities, healthy
meals, painting subjects, health scares, and car preferences. Raising
`--supplement-limit` from 4 to 8 did not improve that gate.

## Next build target

The episodic path is useful infrastructure, but the next accuracy work should be
question-type-specific evidence extraction and answer normalization on the flip
set before another full paid run. The failures showed over-broad list answers,
missed merged-name conflicts, and activity/category normalization errors.

Canary gate for future work:

- Must beat the 100.00% 12-question GPU canary on a broader flip-set canary
  before a full Pro run.
- Do not launch the full Pro run from the current state: the 49-question gate is
  improved but still leaves 9 multi-hop misses.
- Must not regress the recovered `supphints4` wins.
- Must preserve every run artifact for later training/eval use.
