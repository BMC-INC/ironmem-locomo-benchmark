# RUN NOTE — Representation-fidelity suite (paper addition #2)

Date: 2026-06-26. Tool: `scripts/fidelity_probe.py` (new). Retrieval-only, NO Vertex.

## What it is
Paper #2 (arXiv 2606.24775, module M1). Grades what the funnel's binary
`gold_in_memory` only flags, and isolates the fidelity loss attributable to the
storage/representation pipeline (compression + governed write + consolidation),
independent of retrieval ranking. One empty-query big-limit store pull per
conversation, then local token math — no per-question retrieval, no LLM call.

Metrics (against STORED memories only):
- answer EM / F1 (SQuAD-style best-match)
- answer recall@store (graded gold_in_memory; cross-checks the funnel)
- evidence preservation (recall of gold evidence turns that survived into storage)
- representation loss = 1 − (evidence recall in store / in transcript); the
  transcript term is the extractable ceiling, so the ratio isolates storage loss.

## Result (full 10 convs, 1,540 scored Qs, cats 1-4, store-limit 2000)
- answer EM            45.1%
- answer F1            0.304
- answer recall@store  0.882   (consistent with funnel gold_in_memory ~88-94%)
- evidence preservation 0.873
- representation loss  0.127

By category (EM / F1 / evidence / loss):
- single_hop   n=841  57.7% / 0.336 / 0.880 / 0.120
- temporal     n=321  37.1% / 0.305 / 0.863 / 0.137
- multi_hop    n=282  26.6% / 0.265 / 0.872 / 0.128
- open_domain  n=96   15.6% / 0.136 / 0.850 / 0.150  (descriptive answers; EM strict)

## Read
~87% of gold evidence survives ingest; ~13% representation loss is the storage
pipeline's share (compression is concision-biased by design). EM/F1 track answer
recoverability; open_domain EM is low because answers are descriptive, not verbatim
spans (F1 is the fairer lens there). Use this for "how faithfully stored," the
funnel for "where lost on the way to the answerer."

Output: results/fidelity_hybrid.json
