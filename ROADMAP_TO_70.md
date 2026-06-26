# ROADMAP — IronMem LoCoMo 54.5 → ~70

> Planning doc. Companion to `RUN_NOTE_2026-06-24_locomo_upgraded_rerank.md` (the run that produced the funnel below) and `IRONMEM_UPGRADE_PLAN.md`.
> Code touchpoints are in the IronMem server repo `~/Projects/Iron-mem-fix/src/`. Benchmark/funnel tooling is in this repo.

## The framing
This is **not** a rebuild. IronMem already has the right architecture — hybrid search, RRF fusion, a graph-edge layer, temporal/bi-temporal fields, an LLM reranker, and a reflection sleep-cycle all exist in code today. The gap to 70 is **quality hardening of existing components**: the funnel leaks are bugs/over-pruning in code that's already there, not missing features.

Three honest constraints up front:
1. **Some of the gap is model tier, not memory.** Same memory systems score ~45 with an 8B answerer vs ~67 with GPT-4o-mini (Memory-R1 paper). The 54.5 is Flash-judged; the scheduled Pro run reclaims some "for free." Pin a fixed answerer/judge and report it, or the number isn't comparable to anyone.
2. **The ceiling is ~93%, and the judge is lenient** (6.4% of LoCoMo keys are wrong; the standard judge accepts ~63% of wrong-but-topical answers). 70 is the honest target; ignore the 90s vendor numbers (Zep 94.7 / ZeroMemory 96 are non-comparable or unverified).
3. **Don't build a new graph.** mem0's own graph variant *lost* on multi-hop (47.2 vs 51.2 flat). IronMem already has graph edges — harden their use, don't expand the engine.

## The funnel we're fixing (best config, pool 50, 1,540 scored Qs)
```
gold in transcript      93.8%  (1,445)        95 Qs unfixable (gold not in convo / label noise)
gold survives compress  72.3%  (1,114)   ← −334 Qs  COMPRESSION  (biggest leak)
gold in pool@50         84.5%  (1,302)        −48 Qs  retrieval (small)
reranker keeps in top10 72.6%  (1,118)   ← −155 Qs  RERANK over-pruning
answerer correct        54.5%  ( 839)    ← ~280 Qs  gold reached answerer but answer wrong
```
Two buckets: **~537 Qs where gold never reaches the answerer** (compression + rerank + retrieval) and **~280 Qs where gold is present but the answer is still wrong** (context noise + reasoning + model tier).

---

## WAVE 1 — Same-day hardening (LOW effort) · attacks RERANK leak (−155) + context bucket (~280)

**1.1 Recall-preserving rerank.** Today `retrieval.rs::fuse_rerank(base, order, limit)` rebuilds the final list from the LLM's returned order — if the LLM omits an item (its scores are unstable run-to-run even at temp 0), that gold candidate is dropped. `reanchor(narrow, wide)` is already a partial guard; finish the job.
- **Change:** final set = `union(reranker top-N, retriever top-k)` capped at the limit — the reranker may **reorder** but never **delete** a candidate the retriever already surfaced.
- **Touchpoint:** `retrieval.rs::fuse_rerank` / `reanchor` / `rerank_search`.
- **Recovers:** most of the 155.  **Effort:** S.

**1.2 Raise the default candidate pool 3×→5×.** The benchmark already proved pool 50 beats pool 25 by +4.5, but the default is `let pool = (limit * 3).max(30)` (= 30 at limit 10).
- **Change:** `(limit * 5).max(50)`.  **Touchpoint:** `retrieval.rs::hybrid_search_in_namespace` (~line 468).  **Effort:** S. **Impact:** proven +~4.

**1.3 Clean the answerer context.** The benchmark sees facts wrapped as `{'fact': '...'}` — structural noise the answerer must parse around.
- **Change:** emit bare proposition strings in the `/context` response; **dedup** near-identical facts; order **U-shaped** (most-relevant first *and* last — lost-in-the-middle is real); cap at ~12 facts.
- **Touchpoint:** the `/context` serialization in `server.rs`/`mcp.rs` (confirm where the `{'fact':…}` wrapper is emitted) + `fuse_rerank` ordering.  **Effort:** S–M.

**Gate:** re-run the scored sweep; expect the rerank-kept funnel stage to climb from 72.6% toward ~85%+ and a few points of overall lift. Hold here only if it regresses.

---

## WAVE 2 — Extraction fidelity (MED effort) · attacks the #1 leak: COMPRESSION (−334)

Root cause: `compress.rs` persists a **concise** set of facts — concision is recall-killing by design, so salient facts are silently dropped at ingest.

**2.1 Atomic, exhaustive, decontextualized extraction.** Rewrite the extraction prompt/path to emit **all** facts as self-contained atomic propositions: resolve coreference ("my sister" → name) and relative dates ("last summer" → ISO) *at extraction time*, and explicitly instruct over-extraction (dedup happens later). Feed a rolling window of recent turns so cross-turn references resolve.
- **Touchpoint:** `compress.rs::persist` / `persist_fact` / `remember_with_governance` (the fact-emission prompt + the rolling-context it sees).

**2.2 Reflexion coverage pass.** After first extraction, a second LLM call re-reads the source turn and lists "salient facts present in the conversation but missing from the candidates," then appends them. This is the directly-on-target fix for "in transcript but didn't survive," and it reuses the `reflection.rs` pattern (today it consolidates fragments; add a *coverage* mode at ingest).
- **Touchpoint:** new ingest-time coverage check, modeled on `reflection.rs::run`.

**2.3 Don't let reconciliation delete fresh facts.** Confirm whether `remember_with_governance` / reflection runs any inline UPDATE/DELETE that can drop a just-extracted fact. Make writes **near-add-only**; keep consolidation in the async sleep-cycle (`reflection.rs`), not on the write path.

**Gate:** the existing **93.8 → 72.3 transcript-survival metric is the regression test.** Target **≥ 90%** survival, with a per-category breakdown (temporal will move first as decontextualization lands). This is the highest-impact wave.

---

## WAVE 3 — Multi-hop + temporal reasoning (MED effort) · attacks the weak categories (multi-hop ~40%)

**3.1 Iterative retrieval for multi-hop.** A single top-k pass can't answer "what city did X move to after the job mentioned in session 3?" Add a capped 2–3-hop loop: retrieve → reason → rewrite query → retrieve again, reusing the existing `hybrid_search` + reranker. This is *the* proven multi-hop lever (+~15 QA pts in the literature) and needs no new storage.
- **Touchpoint:** new orchestration around `retrieval.rs::rerank_search`; optionally a "note per chunk before answering" (Chain-of-Note) step in the answerer prompt.

**3.2 Harden the existing graph edges for multi-hop.** Edges already exist (`graph_ids_for_query`, `graph_edge_score`, `MemoryEdge`). Verify they're actually being traversed and weighted at query time for multi-hop questions, and that extraction is populating them with real relations (not just co-occurrence). Tune `graph_edge_score`, don't expand the engine.

**3.3 First-class temporal grounding.** The schema already has `valid_from`; extraction already date-stamps facts (`compress.rs` ~line 421) and retrieval has `temporal_event_ids_for_query`. Finish it: ensure **every** fact carries event-date + session-date as fields (not in text), inject those dates into the answerer prompt, and add **invalidate-don't-delete** supersession (set `valid_to`/`t_invalid` instead of overwriting) so date questions resolve against the right version. This is where Zep's temporal edge comes from — a data-model finish, no graph engine.
- **Touchpoint:** `db.rs` schema (`valid_from` → add `valid_to`), `compress.rs` date stamping, `retrieval.rs::temporal_event_ids_for_query`, answerer context.

**Gate:** multi-hop and temporal category accuracy on the scored sweep.

---

## WAVE 4 — Lower priority / only if 1–3 stall
- **Cross-encoder reranker** (on-device ONNX `bge-reranker-v2-m3` or `mxbai-rerank-v2`) replacing the LLM reranker for stable, calibrated scores — do this only if Wave 1.1 shows the instability (not the reasoning) is the leak. A/B it.
- **Entity-match retrieval boost** beyond current `query_entities` — part of mem0's top retrieval.
- **Do NOT**: swap the 384-dim embedder (95.7% pool recall proves it's not the bottleneck), or build a bigger graph engine.

---

## Honest expected trajectory
- Wave 1 (rerank union + pool 5× + clean context): proven +~4 from pool alone, plus recovery of much of the 155 rerank-dropped Qs and some of the ~280 context-bucket Qs.
- Wave 2 (extraction 72→≥90 survival): recovers a large share of the 334 — the biggest single move.
- Wave 3 (iterative + temporal finish): lifts the two weak categories.
- Plus Pro/standard-answerer re-baseline: a few points of model-tier.

Net: a credible path from **~54.5 to ~68–72 on a fixed config**. Validate on **LongMemEval / BEAM** before claiming a general win — LoCoMo is small and saturating, and don't trust score deltas smaller than the judge's noise floor.

## Measurement discipline (per wave)
1. Always re-run the **transcript-survival funnel** (`scripts/funnel_probe.py`) — it localizes which stage moved.
2. Score on a **fixed answerer+judge** and state it in every result.
3. Change **one wave at a time**; attribute the delta before stacking the next.

## Paper-aligned additions — arXiv 2606.24775 ("Are We Ready For An Agent-Native Memory System?")
> This roadmap optimizes the **benchmark score**; the additions below optimize **eval-framework credibility** (the paper's modules M1–M4 + RQ5 cost). They overlap with the waves above in two places — marked **⟂** — and should be built as one batch, not a separate track. Status as of 2026-06-25.

| # | Addition | Paper hook | Roadmap convergence | Status |
|---|---|---|---|---|
| **3** | Governance cost instrumentation | RQ5 (utility–latency) | — | **BUILT + tests green.** `metrics.rs`; per-op `count/avg_us/max_us` for consent_check / trust_eval / namespace_resolve / governed_write / tombstone_write on `/status`. |
| **5** | Temporal trust trajectory | Finding 4 (consolidation destroys chronological cues) | **⟂ Wave 3.3** (first-class temporal grounding) — same defect as the Pro run's temporal −4.3 | **BUILT (trajectory) + tests green.** Schema `trust_first_seen_at/last_validated_at/ref_count`; advanced on receipt-confirmed feedback. Retrieval boost = unit-tested pure fn behind `temporal_trust.weight` (default **0.0**, A/B via funnel — do NOT enable un-measured). |
| **1** | Governed retrieval router | M3 (query planning + hybrid search) | **⟂ Wave 1.1/1.2** (rerank union + pool) — extend the existing reranker with trust-tier priority + namespace authority at query time | **NEXT.** Not greenfield — folds into `retrieval.rs::fuse_rerank`/`hybrid_search`. |
| **2** | Representation-fidelity suite | Eval dims (EM/F1, evidence preservation) | Extends the existing funnel (`gold_in_memory` is a partial fidelity metric) | **NEXT.** Measure degradation *attributable to* governance ops, not just write success. |
| **4** | Multi-engine storage adapters | M1 (heterogeneous backends) | — (redefines IronMem's storage identity) | **SPEC'D, gated on decision.** See `IRONMEM_STORAGE_ADAPTER_SPEC.md` §1 fork (recommend: governance shim). Build only after that call + §4 enforcement answers. |

**Batch boundary before the next headline re-run:** Phase 1 (Wave 2 compression) + #5 trajectory + #3 cost are in one binary now. #1 and #2 are the next buildable wave. #4 is spec-first. Per the measurement discipline above, validate each score-affecting lever (Phase 1, #1, #5-retrieval-weight) one at a time on the funnel before stacking.

## Sources
mem0 (arXiv 2504.19413) · Zep/Graphiti (2501.13956) · Dense-X propositions (2312.06648) · IRCoT (2212.10509) · Chain-of-Note (2311.09210) · Memory-R1 answerer-tier table (2508.19828) · **Agent-native memory survey (arXiv 2606.24775) — M1–M4 taxonomy + RQ5 cost** · LoCoMo audit (dev.to/penfieldlabs) · mxbai-rerank-v2 · ZeroEntropy reranker guides.
