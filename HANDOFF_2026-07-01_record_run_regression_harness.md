# HANDOFF - 2026-07-01 - IronMem record-run regression harness

## Resume Here

James wants IronMem to become the best memory tool on the market, not a weaker benchmark toy. Do not revert product upgrades just to chase a score. The next work is additive: preserve the new governance/retrieval stack, preserve all benchmark data for later LLM training, and build a fast regression harness from saved run artifacts before spending another full Pro benchmark.

The immediate build target is:

1. Turn saved LoCoMo runs into reusable diagnostic/training data.
2. Recover the `upg8 -> upg11` regressions without deleting the `d86f70f` product work.
3. Add a source-fact retention floor so exact source facts cannot be crowded out by routed fusion.
4. Replace generic flattened synthesis with an E-mem-style, source-backed master aggregator gated to multi-hop.
5. Activate and verify the cross-encoder as a separate recall lever.
6. Run canaries against the flip set first; only run full Pro when gates pass.

## Non-Negotiables

- Keep every run artifact. Do not delete or overwrite `results/*.json`, `results/raw_console/*.log`, or prior handoff/run-note files.
- Treat run data as future training/evaluation corpus. Full per-question rows are valuable even when a run regresses.
- Do not use full Pro runs as the debugging loop. Build canaries from saved artifacts first.
- Do not make IronMem less capable to increase one score. Add retention, routing, and evidence safeguards on top of the product stack.
- Before claiming cross-encoder results, verify `/status` shows `"backend": "cross_encoder"` and `"cross_encoder_ready": true`.

## Current Empirical State

All three important recent files are saved and include `per_question` rows for all 1,986 questions, including retrieved context, generated answer, ground truth, score, and error fields.

| Run | Config | Overall | single_hop | multi_hop | temporal | open_domain | Error count |
|---|---|---:|---:|---:|---:|---:|---:|
| `results/upg8_PRO_p100_k25_v2.json` | old headline, Pro/Pro, `--rerank --pool 100 --retrieve-limit 25`, v2 | 65.9% | 69.6% | 50.4% | 76.0% | 45.8% | 0 |
| `results/upg10_PRO_p100_k25_v2_d86f70f_20260701.json` | bad no-rerank run, Pro/Pro, missing `--rerank` | 53.6% | 56.4% | 34.0% | 65.7% | 45.8% | 0 |
| `results/upg11_PRO_rerank_p100_k25_v2_d86f70f_20260701.json` | corrected rerank run on `d86f70f`, Pro/Pro | 62.8% | 65.9% | 42.2% | 78.5% | 43.8% | 0 |

`upg11` recovered most of the bad no-rerank run, but it is still -3.1pp below `upg8`. The shape matters: temporal improved +2.5pp, but single_hop fell -3.7pp and multi_hop fell -8.1pp.

Math from `upg11`: about 967/1540 scored questions correct. A 70% run needs 1078/1540, so the gap is roughly +111 correct answers. Merely restoring `upg8` does not reach 70; it only gets back to ~65.9%.

## Why The Latest Upgrade Degraded

The degradation is not an auth failure or judge failure. The corrected `upg11` run completed cleanly with `error_count: 0`.

The likely root cause is retrieval/context composition:

- `d86f70f` is `feat(retrieval): route fusion and structured rerank evidence (#22)`.
- It heavily changed `src/retrieval.rs`.
- The route fusion helped temporal.
- It also allowed exact source facts and bridge facts to be demoted or crowded by synthesized/derived/narrative memories.
- The generic synthesis experiment had already shown flattening is not enough; multi-hop needs evidence-preserving reconstruction, not broad summarization.

Do not revert the product work. Add safeguards:

- Threshold override / source-fact retention floor.
- Union activation for strong single-path signals before weighted fusion fills the rest.
- Multi-hop evidence aggregator that extracts quotes first, then reasons.

## Cross-Encoder Was Built But Not Active

Live `/status` during `upg11`:

```json
"rerank": {
  "backend": "llm",
  "cross_encoder_ready": false
}
```

Why: `--rerank` enables the server rerank path, but it does not choose the backend. The server loads cross-encoder only when config/env sets `rerank.backend = "cross_encoder"` at startup.

Current launchd plist has no environment override:

```text
~/Library/LaunchAgents/com.execlayer.ironmem.plist
ProgramArguments = ~/.ironmem/bin/ironmem server
EnvironmentVariables only sets PATH
```

Activation path:

1. Set one of:
   - `IRONMEM_RERANK_BACKEND=cross_encoder` in launchd environment, or
   - add `"backend": "cross_encoder"` under `rerank` in `~/.ironmem/settings.json`.
2. Restart launchd.
3. Verify:
   ```bash
   curl -s localhost:37778/status | .venv/bin/python -m json.tool
   ```
4. Only proceed if it reports:
   ```json
   "backend": "cross_encoder",
   "cross_encoder_ready": true
   ```

If it reports `cross_encoder_ready: false`, inspect `~/.ironmem/server.log` for the exact load failure. Do not run a "cross-encoder" benchmark unless readiness is true.

## Research Signals To Borrow

### E-mem

Source: https://arxiv.org/abs/2601.21714 and https://arxiv.org/html/2601.21714

E-mem directly matches the Caroline/Sweden LoCoMo failure: one memory says Caroline moved from her home country 4 years ago; another, topically different memory identifies the home country as Sweden. Plain top-k/vector retrieval misses the second hop.

Borrow these ideas without rebuilding E-mem wholesale:

- Multi-pathway activation: a memory can survive because any pathway strongly activates it.
- Two-stage cascade: threshold override first, weighted fill second.
- Master aggregator: verbatim evidence quotes with timestamps first, then logic trace, then final answer.
- Deep mode only for hard/multi-hop questions; keep fast retrieval for simple facts.

### RGMem

Source: https://arxiv.org/abs/2510.16392

Public summaries report RGMem at 86.17% on LoCoMo with a multi-scale memory evolution design. Treat the exact score as external until independently reproduced, but the architectural lesson is useful: preserve microscopic evidence while building higher-level evolving abstractions. For IronMem this maps to keeping source facts/ledger/provenance while adding scale-aware consolidation and profile/evidence layers.

## Data Retention And Training Corpus Plan

Keep all current data:

- `results/upg8_PRO_p100_k25_v2.json`
- `results/upg10_PRO_p100_k25_v2_d86f70f_20260701.json`
- `results/upg11_PRO_rerank_p100_k25_v2_d86f70f_20260701.json`
- all older `results/*.json`
- all `results/raw_console/*.log`
- run notes and handoffs

Next recommended script after the flip harness:

```text
scripts/index_run_artifacts.py
```

Purpose:

- Produce `results/run_manifest.json`.
- Hash each result file.
- Record timestamp, model, config, error_count, scores, and file size.
- Never mutate or delete source artifacts.

Future training JSONL shape:

```json
{
  "run_id": "upg11_PRO_rerank_p100_k25_v2_d86f70f_20260701",
  "question_id": "...",
  "conversation_id": "...",
  "category": "multi_hop",
  "question": "...",
  "ground_truth": "...",
  "retrieved_context": "...",
  "generated_answer": "...",
  "score": 0,
  "bucket": "lost",
  "failure_type": "retrieval_demotion",
  "source_result_file": "results/upg11_..."
}
```

## Build Sequence

### Step 1 - Build the flip set

Create:

```text
scripts/build_flip_set.py
```

Inputs:

```bash
--baseline results/upg8_PRO_p100_k25_v2.json
--candidate results/upg11_PRO_rerank_p100_k25_v2_d86f70f_20260701.json
--output results/regression/regression_set_upg8_vs_upg11.json
```

Join key:

```text
(conversation_id, question_id)
```

Buckets:

- `lost`: baseline correct, candidate wrong.
- `gained`: baseline wrong, candidate correct.
- `stable_wrong`: both wrong.
- `stable_correct`: both correct.

Output should preserve both run contexts, answers, scores, and result file names. Do not overwrite by default; require `--force` for overwrites.

### Step 2 - Classify why lost questions flipped

Create:

```text
scripts/classify_flips.py
```

Input:

```bash
--regression-set results/regression/regression_set_upg8_vs_upg11.json
--output results/regression/flip_classification_upg8_vs_upg11.json
```

Initial heuristic classes:

- `retrieval_demotion`: gold-bearing phrase/memory appears in baseline context but not candidate context.
- `answerer_failure`: gold-bearing evidence appears in both contexts but candidate answer is wrong.
- `partial_bridge`: one hop appears in candidate, another hop is missing or demoted.
- `attention_dilution`: candidate context contains evidence but buries it under unrelated/synthesized items.
- `unknown`: manual review needed.

This script should also emit summary counts by category and class.

### Step 3 - Patch retrieval with source-fact retention floor

Repo:

```text
/Users/kingjames/Projects/Iron-mem-fix
```

Patch target:

```text
src/retrieval.rs
```

Goal: add a threshold-override stage before route-weighted fusion output is truncated. Strong exact FTS/BM25/source-fact hits get guaranteed slots in the final retrieve-limit window. Route fusion still runs and keeps helping temporal, but it cannot bury exact source facts.

Implementation shape:

1. Compute strong lexical/source candidates from FTS signal.
2. Filter out broad/noisy matches.
3. Reserve a small budget, e.g. 3-5 slots or dynamic by route.
4. Prefer `kind=fact` and source-linked memories over synthesized/derived memories when exact lexical evidence is strong.
5. Deduplicate with fused candidates.
6. Fill remaining slots from routed fusion.

Acceptance:

- Lost single-hop examples regain exact fact in top context.
- Temporal gained examples do not regress.
- No deletion or disabling of route fusion.

### Step 4 - Replace flattened synthesis with E-mem-style master aggregator

Repo:

```text
/Users/kingjames/Projects/ironmem-locomo-benchmark
```

Patch target:

```text
benchmark/query.py
```

Do not use generic synthesis for every question. Gate to multi-hop / complex questions only.

Aggregator contract:

1. Extract verbatim evidence quotes with timestamps/source refs first.
2. Resolve pronouns and speaker names.
3. Write a short `logic_trace` connecting hops and resolving conflicts.
4. Produce a final direct answer.

For benchmark scoring, the final answer should remain concise. For future training, preserve the evidence/logic trace in per-question output if feasible.

### Step 5 - Canary against flip set

Do not run full Pro until canaries pass.

Recommended canary set:

- all `lost`
- all `gained`
- a stratified sample of `stable_wrong`

Gates:

- Retrieval demotion losses: exact facts back in top context.
- Multi-hop canary beats `upg8` on the multi-hop subset.
- Single-hop lost questions recover without sacrificing temporal gained questions.
- No new regression on `gained`.
- If testing cross-encoder, `/status` must show `cross_encoder_ready: true`.

### Step 6 - Cross-encoder as separate recall lever

After Steps 1-5, activate cross-encoder and test on the flip set first.

Potential configs:

```bash
--rerank --pool 150 --retrieve-limit 25
--rerank --pool 150 --retrieve-limit 35
```

Only then run full Pro. Cross-encoder is orthogonal to the retention floor and aggregator:

- retention floor rescues exact facts from demotion
- aggregator rescues uncombined hops
- cross-encoder improves recall/ranking in the candidate pool

## Exact Commands To Start Next Session

```bash
cd /Users/kingjames/Projects/ironmem-locomo-benchmark
git status --short --branch
ls -lh results/upg8_PRO_p100_k25_v2.json \
  results/upg11_PRO_rerank_p100_k25_v2_d86f70f_20260701.json
```

Then build scripts:

```bash
mkdir -p results/regression
$EDITOR scripts/build_flip_set.py
$EDITOR scripts/classify_flips.py
```

Run scripts:

```bash
.venv/bin/python scripts/build_flip_set.py \
  --baseline results/upg8_PRO_p100_k25_v2.json \
  --candidate results/upg11_PRO_rerank_p100_k25_v2_d86f70f_20260701.json \
  --output results/regression/regression_set_upg8_vs_upg11.json

.venv/bin/python scripts/classify_flips.py \
  --regression-set results/regression/regression_set_upg8_vs_upg11.json \
  --output results/regression/flip_classification_upg8_vs_upg11.json
```

Then patch IronMem:

```bash
cd /Users/kingjames/Projects/Iron-mem-fix
git status --short --branch
```

Be careful: current branch is `feat/wave4-gpu-cross-encoder`, ahead of `main`, with local dirty files (`Cargo.lock`, `phase1_provider_DRAFT.patch`). Do not lose or revert unrelated work.

## Completion Definition For The Next Build

The next build is not "done" when code compiles. It is done when:

1. Flip set exists and is committed or safely saved.
2. Flip classification exists and summarizes lost/gained/stable_wrong.
3. Retention-floor patch is implemented and tested.
4. Multi-hop aggregator is implemented and gated.
5. Canary run shows recovered lost questions without sacrificing gained ones.
6. Cross-encoder activation is verified separately if included.
7. All new run data is preserved under `results/` with console logs.

## Caution

The record run should be treated like a launch, not like a debug command. Do not spend a full Pro run until the canary says the levers are working.
