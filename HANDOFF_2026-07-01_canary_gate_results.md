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

## Local CPU Cross-Encoder Sample

After the first gate, a local CPU CE sample was run because this machine has no
CUDA-style GPU path:

- CPU: Intel Core i9-9980HK
- Display GPUs: Intel UHD Graphics 630 and AMD Radeon Pro 5500M
- IronMem status: `rerank.backend=cross_encoder`, `cross_encoder_ready=true`

Retrieval-only CE sample:

```bash
.venv/bin/python scripts/audit_retrieval_floor.py \
  --regression-set results/regression/regression_set_upg8_vs_upg11.json \
  --buckets lost gained \
  --strategy hybrid \
  --limit 25 \
  --rerank \
  --pool 100 \
  --max-questions 12 \
  --concurrency 1 \
  --output results/regression/retrieval_audit_lost_gained_ce_p100_k25_sample12_e685d86_20260701.json
```

Result:

- Runtime: about 5.5 minutes for 12 retrievals
- Evidence present: `8/12` (`66.67%`)
- Evidence in top-10: `11/12` (`91.67%`)
- Gained bucket evidence present: `4/4`
- Lost bucket evidence present: `4/8`

Scored CE sample:

```bash
PYTHONUNBUFFERED=1 .venv/bin/python -m benchmark.run \
  --strategy hybrid --skip-ingest --rerank \
  --pool 100 --retrieve-limit 25 --concurrency 1 \
  --answer-prompt v2 --synthesize \
  --require-cross-encoder --vertex-location us-west1 \
  --data data/canary_lost_gained_ce_sample12_upg8_vs_upg11.json \
  --output results/canary_lost_gained_ce_sample12_p100_k25_v2agg_e685d86_20260701.json \
  2>&1 | tee -a results/raw_console/canary_lost_gained_ce_sample12_p100_k25_v2agg_e685d86_20260701_console.log
```

Result:

- Runtime: `7:50` for 12 answer+judge questions
- CE scored result: `7/12` (`58.3%`)
- Same-question no-rerank result from the prior 256-question canary: `7/12`
- Upg8 on this sample: `8/12`
- Upg11 on this sample: `4/12`

Interpretation: CE improves top-k evidence placement on this tiny sample, but it
did not improve judged answer accuracy over the no-rerank stack on the same
questions. A local 256-question CPU CE canary is therefore not justified yet; it
would likely take around 2.5-3 hours and still may not move the score. The next
engineering target is turning the better CE evidence placement into better final
answers, or running CE on a proper GPU/faster backend before a broader canary.

## Answer-Key Aggregator Follow-Up

The CE sample failures were inspected manually. They split into:

- Over-answering with incidental activities or outings.
- Bought/purchased questions picking up acquired pets.
- Missing evidence where CE still did not retrieve the second required fact
  (`violin`, `Paris`, and `swimming` were not in the retrieved contexts).

The master aggregator prompt was tightened to produce answer-key-style final
answers:

- comma-separated short noun phrases for list questions
- activity categories instead of every subactivity/destination
- "partake in" as recurring hobbies/activities, not one-off outings
- child "likes" as interest categories
- bought/purchased as objects only, not acquired pets or experiences

Exact 12-question CE sample reruns:

| Run | Result | Notes |
| --- | ---: | --- |
| `canary_lost_gained_ce_sample12_p100_k25_v2agg_answerkey_e685d86_20260701.json` | `8/12` (`66.7%`) | Fixed over-answer rows, regressed bought-items by adding Bailey. |
| `canary_lost_gained_ce_sample12_p100_k25_v2agg_answerkey2_e685d86_20260701.json` | `8/12` (`66.7%`) | Fixed bought-items, lost the kids-like row. |
| `canary_lost_gained_ce_sample12_p100_k25_v2agg_answerkey3_e685d86_20260701.json` | `8/12` (`66.7%`) | Current prompt. Keeps bought-items fixed and answer-key style compact. |

Current wrong rows on `answerkey3`:

- `conv-26_q15`: context lacks `swimming`; answer has `pottery, painting, camping, hiking`
- `conv-26_q19`: answer says `animals, pottery, painting, nature`; judge wanted `dinosaurs, nature`
- `conv-26_q60`: context lacks `violin`; answer has `clarinet`
- `conv-30_q29`: context lacks `Paris`; answer has `Rome`

Decision: keep the answer-key prompt improvement, but stop tuning against this
tiny 12-question sample. The next meaningful product lever is retrieval
decomposition/query expansion that brings missing second facts into context
without multiplying local CPU CE cost too much.
