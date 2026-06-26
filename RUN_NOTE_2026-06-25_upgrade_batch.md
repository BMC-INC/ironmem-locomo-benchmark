# RUN NOTE — Upgrade batch: synthesis (B-track), temporal-trust wiring (#5), temporal fusion (B), multi-query (A), router (E), fidelity (F)

**Immutable once finalized.** Drafted 2026-06-25 ~19:45 PDT. Implementation section is final; RESULTS appended when the ablation matrix lands.
Companion to `RUN_NOTE_2026-06-25_retrieval_lever.md` (the diagnosis: extraction solved, retrieval is the lever, the top-k cut + temporal-low-ranking are the targets). This note records building & deploying the full set of retrieval upgrades and the ablation that measures them.

## What was built (all gated/additive; defaults reproduce the prior ranker exactly)

**Rust (`~/Projects/Iron-mem-fix`, deployed live; binary `target/release/ironmem` → `~/.ironmem/bin/ironmem`; backup `ironmem.bak-upg6-pre-20260625`):**
- **D — Track B transitive synthesis.** New `reflection::synthesize()` + `POST /synthesize`. Distinct from the existing consolidation `run()` (which merges near-dupes and records −0.5): synthesis groups facts that share a salient entity term, LLM-derives NEW multi-hop facts (only those requiring ≥2 inputs), stores them ADDITIVELY (`kind=fact`, `tags=synthesized,derived`), and **positively reinforces each source** via `record_memory_feedback(weight>0)`. Sanity-checked: conv-26 scanned 1,511 facts, derived a fact in dry-run.
- **#5 — temporal-trust, now actually wired.** `governance::trust_trajectory_boost` was dead code (gated, but never called and — critically — `ref_count` was always 0 on the benchmark because nothing fed it). Now: (a) wired into the `/context` RRF path via `retrieval::apply_trust_boost` (re-ranks the fused order by trust; no-op when `temporal_trust.weight=0` or no candidate has trust; zero DB cost when off), and (b) **fed a live signal** — D's source reinforcement sets `trust_ref_count`/`trust_last_validated_at`, so corroborated facts that fed a derivation rank higher. This closes the gap noted earlier (#5 was a no-op on LoCoMo for lack of a feedback signal).
- **B — temporal-event fusion weight.** Gated `temporal_event_fusion_weight` (default 1 = unchanged) pushes the date-bearing temporal-event id-list into RRF N times, lifting exact dated facts that semantic/keyword channels rank low (the recall curve showed temporal gold flat 10→30, jumping at 50).
- Plumbing: `db::trust_meta_for` (batch trust fetch), `retrieval::set_retrieval_tuning` installed once at startup from `config.temporal_trust` (no `&Config` threading through 5 hot-path callers), `config.temporal_trust.temporal_event_fusion_weight`. Also fixed a pre-existing `clippy::needless_range_loop` in the #3 `metrics.rs` that had slipped the `-D warnings` gate.
- Validation: `cargo check` ✓, `clippy --all-targets -D warnings` ✓, `cargo test` 10/10 (incl. 3 trust-trajectory tests) ✓.

**Harness (`~/Projects/ironmem-locomo-benchmark`):**
- **A — multi-query / query expansion** (`benchmark/query.py`): `--multi-query N` expands the question into N variants (Gemini), retrieves per-variant (composing rerank/pool), RRF-fuses harness-side, takes top `retrieve_limit`. N=0 = unchanged. Smoke (1 conv): N=3 63.2% vs N=0 55.9%.
- **E — governed retrieval router** (`--route`): heuristic question classifier (temporal / multi-hop / default — on question TEXT, never the gold label) selects per-question params (multi-hop→multi-query 3 + limit≥20; temporal→limit≥25). [built by the warm multi-query agent]
- **F — #2 fidelity suite** (`scripts/fidelity_suite.py`): splits every error into RETRIEVAL-failure (gold never reached the answerer) vs ANSWERER-failure (gold reached it, still wrong), per category.
- Drivers: `scripts/run_synthesis.py` (POST /synthesize per project), `scripts/run_upgrade_matrix.sh` (the ablation).

## The ablation (Flash both sides, us-west1, `--skip-ingest`, current coverage store — NO re-ingest; ingestion unchanged)
Attribution by isolating one lever at a time (the discipline the Phase-1 bundling violated):

| arm | store | levers | isolates |
|---|---|---|---|
| **A** `upg6_A_baseline` | coverage | none | reference |
| **B** `upg6_B_synth` | +synthesis | none | synthesis retrieval effect |
| **C** `upg6_C_synth_mq3` | +synthesis | multi-query 3 | query expansion (multi-hop) |
| **D** `upg6_D_synth_route` | +synthesis | router | per-category routing |
| **E** `upg6_E_synth_l25` | +synthesis | retrieve-limit 25 | the top-k cut |
| **F** `upg6_F_synth_levers` | +synthesis | temporal fusion + trust (server) | B + #5 |
| **G** `upg6_G_synth_levers_route_l25` | +synthesis | all of the above | kitchen sink |

Server levers (F/G) enabled mid-pipeline via `settings.json` `temporal_trust` block (weight 0.05, temporal_event_fusion_weight 2) + restart. Funnel + fidelity on G. **Pro headline on the winning config** (prior Pro baseline 60.9% = coverage store, pool50/l10, no upgrades).

## RESULTS
_TODO — appended when `scripts/run_upgrade_matrix.sh` completes (overnight). Watch `results/raw_console/upgrade_matrix.log` and `results/upg6_*.json`._

## Notes
- New Rust batch is DEPLOYED but UNCOMMITTED (held pending explicit OK). Prior batch `ea08515` (#3/#5-dormant/coverage) is on origin/main.
- mem.db snapshotted to `mem.db.bak-pre-synthesis` before synthesis mutates it; settings backed up to `settings.json.bak-prelevers` before the lever toggle. Reversible.
