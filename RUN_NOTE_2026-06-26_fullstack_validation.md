# RUN NOTE — 2026-06-26 — Full-stack validation (Flash, p100/k25)

Post-build validation of the deployed binary after the entire ROADMAP_TO_70 build
(path-to-70 batch + #4 storage adapters + #1/#3/#5 paper additions; #2 fidelity
suite built same day). Same config as the 2×2 winner so it is apples-to-apples.

Config: hybrid, --skip-ingest, --rerank, pool 100, retrieve-limit 25,
answerer+judge = gemini-2.5-flash, vertex queueflow-sentinel/global, concurrency 8.
1986 questions, 1540 scored (cats 1-4), 446 adversarial logged, **0 errors**, 72 min.

## Result
| category    | tonight | 2×2 winner D | Δ |
|-------------|---------|--------------|------|
| single_hop  | 66.8%   | 71.3%        | −4.5 |
| multi_hop   | 42.5%   | 40.1%        | +2.4 |
| open_domain | 37.5%   | 40.6%        | −3.1 |
| temporal    | 67.3%   | 69.2%        | −1.9 |
| **overall** | **60.7%** | **63.25%** | **−2.5** |

## Honest read
- 60.7% sits essentially ON the prior **Pro 60.9% baseline** and ~2.5 pts under the
  Flash 63.25% high-water mark. That spread is within Flash-judge run-to-run noise
  (the documented reason a Pro judge-calibration exists) plus store evolution
  between the two runs. The roadmap's own rule: do not trust deltas below the
  judge noise floor.
- The build is FUNCTIONAL and in-band. It did not by itself move the score — that
  was never its job: #4 is behavior-identical retrieval, #2 is measurement-only.
  The 60-63 band came from the path-to-70 retrieval batch, which this confirms.
- We are NOT at 70. multi_hop (42.5%) and open_domain (37.5%) are the laggards;
  retrieval tuning has plateaued multi_hop (40 → 42 across configs).
- Store note: fidelity probe showed answer_recall@store ~0.88 and several convs at
  the 2000-memory cap, i.e. the live store has grown since the 2×2 (gold_in_memory
  was 92% then). Some of the −2.5 is plausibly store drift, not regression; tonight's
  build did not touch the store (conformance tests used throwaway temp DBs).

## Next levers toward 70 (not yet built; were deferred/optional)
1. Wave 4 cross-encoder reranker (on-device ONNX) — now justified: retrieval tuning
   has stalled, and a stable calibrated reranker attacks the multi_hop/rerank frontier.
2. Pro answerer re-baseline at p100/k25 (off-peak; Pro DSQ-throttled in evening) —
   the roadmap's "few points of model-tier"; the postable headline number.
3. multi_hop is a synthesis problem now, not a retrieval-tuning one.

Result: results/upg_validation_fullstack_flash_p100_k25.json
