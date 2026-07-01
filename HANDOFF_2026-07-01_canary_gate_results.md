# IronMem LoCoMo Canary Gate Results - 2026-07-01

Purpose: preserve the diagnostic evidence from the first post-`e685d86` canaries
before spending another full Gemini Pro LoCoMo run.

## Current Stack

- IronMem source commit: `e685d86`
- Benchmark harness branch: `main`
- Local IronMem status: `ok=true`, `rerank.backend=cross_encoder`,
  `cross_encoder_ready=true`
- Canary runs here intentionally used **no rerank** unless stated otherwise,
  because local CPU `bge-reranker-v2-m3` is too slow for a broad canary.

## Retrieval Audit

Command:

```bash
.venv/bin/python scripts/audit_retrieval_floor.py \
  --regression-set results/regression/regression_set_upg8_vs_upg11.json \
  --buckets lost gained \
  --strategy hybrid \
  --limit 25 \
  --output results/regression/retrieval_audit_lost_gained_no_rerank_e685d86_20260701.json
```

Result:

- Overall evidence present: `139/256` (`54.30%`)
- Lost bucket evidence present: `86/152` (`56.58%`)
- Gained bucket evidence present: `53/104` (`50.96%`)
- Saved-context demotions where upg8 had evidence and upg11 lost it: `46`
- Current no-rerank stack recovered saved demotions: `21/46`

Interpretation: the source-fact retention floor helps, but it is not enough by
itself. The next recall lever has to be reranking/cross-encoder or wider recall,
not another prompt-only change.

## Multi-Hop Lost Canary

Dataset:

```bash
data/canary_lost_multi_hop_upg8_vs_upg11.json
```

This contains all `49` multi-hop questions that were correct in `upg8` and wrong
in `upg11`.

Runs:

| Run | Result | Notes |
| --- | ---: | --- |
| `canary_lost_multi_hop_v2agg_no_rerank_e685d86_20260701.json` | `22/49` (`44.9%`) | Aggregator only fired on `11/49`; router was too narrow. |
| `canary_lost_multi_hop_v2agg_routerfix_no_rerank_e685d86_20260701.json` | `21/49` (`42.9%`) | Router fired on most aggregation questions, but prompt was too broad/strict in bad places. |
| `canary_lost_multi_hop_v2agg_promptfix_no_rerank_e685d86_20260701.json` | `25/49` (`51.0%`) | Best multi-hop diagnostic result so far. |

Interpretation: the evidence-first aggregator is useful when it is routed
correctly and constrained to exact final answers. The prompt fix recovered the
diagnostic multi-hop subset above the old `upg8` multi-hop rate on this subset.

## Lost + Gained Safety Canary

Dataset:

```bash
data/canary_lost_gained_upg8_vs_upg11.json
```

This contains all lost and gained questions from the `upg8` vs `upg11` flip set:
`256` questions total.

Runs:

| Run | Overall | Lost Recovered | Gained Retained | Delta vs upg11 | Delta vs upg8 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `canary_lost_gained_v2_no_route_no_synth_no_rerank_e685d86_20260701.json` | `43.0%` | `68/152` | `42/104` | `+6` | `-42` |
| `canary_lost_gained_v2agg_promptfix_no_rerank_e685d86_20260701.json` | `46.1%` | `68/152` | `50/104` | `+14` | `-34` |

Interpretation: router + aggregator is a net positive over the plain answerer on
this diagnostic set (`+8` correct), but the no-rerank stack is still not safe for
a full Pro run because it fails to retain too many of the upg11 gained questions.

## Decision

Do **not** spend a full LoCoMo Pro run on the no-rerank stack.

The next record-attempt gate should be a GPU/cross-encoder canary using:

```bash
PYTHONUNBUFFERED=1 .venv/bin/python -m benchmark.run \
  --strategy hybrid --skip-ingest --rerank \
  --pool 100 --retrieve-limit 25 --concurrency 1 \
  --answer-prompt v2 --synthesize --route \
  --require-cross-encoder --vertex-location us-west1 \
  --data data/canary_lost_gained_upg8_vs_upg11.json \
  --output results/canary_lost_gained_ce_p100_k25_v2agg_promptfix_e685d86_20260701.json \
  2>&1 | tee -a results/raw_console/canary_lost_gained_ce_p100_k25_v2agg_promptfix_e685d86_20260701_console.log
```

Local CPU cross-encoder is expected to be too slow for this command. Run it only
when GPU acceleration is available, or first run a much smaller CE sample.
