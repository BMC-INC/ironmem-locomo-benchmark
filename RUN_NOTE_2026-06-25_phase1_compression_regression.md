# RUN NOTE — Phase 1 compression fix: INGESTED, MEASURED, REVERTED (regression)

> ## ⚠️ CORRECTION — appended 2026-06-25 ~18:10 PDT (supersedes the conclusion below)
> **The "regression" below was a MEASUREMENT ARTIFACT, not a real regression.** The funnel's `gold_in_memory` gate probes only the top-500 memories per conversation (`--store-limit 500`). The coverage pass ~**doubles** stored facts (now 1,341–2,064 memories/conv), pushing gold *past the 500-memory probe window* — so the gate read 65.8% even though the gold is in the store.
> **Proof (internal inconsistency):** the funnel's own `in_pool_50` (88.3%, per-question retrieval) exceeded `gold_in_memory@500` (70.9%) — impossible unless the store probe undercounts. Re-probing the full store: `gold_in_memory` = **92.4% @ store-limit 2000** (gold_in_transcript ceiling 93.8%) → the coverage pass retains **~98%** of in-transcript gold. **The compression gate is CLEARED.**
> **Real effect of the coverage pass:** extraction is solved; the bottleneck moved to **retrieval** (2× store volume dilutes the pool50→rerank→top10 path — which is why Phase 1's Flash *answer* score dipped −1.25 despite better extraction). Next lever = retrieval (raise pool/limit), not extraction. End-to-end answer accuracy is being confirmed in an off-peak scoring run (`com.execlayer.locomo-coverage-score`). The numbers in the body below are real but the *gate-failure interpretation* is wrong; the metric is unreliable when store volume changes.

**Immutable record. Do not edit after finalization.** Finalized 2026-06-25 ~16:30 PDT. Times are PDT (UTC−7).
Companion to `ROADMAP_TO_70.md` (Wave 2 = compression) and `RUN_NOTE_2026-06-25_locomo_pro_postable.md` (the 60.9% baseline). This note records a **negative result**: the drafted Phase 1 extraction fix regressed the compression gate and was reverted from the live binary.

## What this run is
First ingest + measurement of the **Phase 1** compression fix (IronMem working tree on `657def4` + the Phase-1 `provider.rs` change). Phase 1 bundled **two** changes to the compression/extraction path:
1. **Untruncated primary window** — `build_prompt` per-turn caps raised **500/300 → 8000/4000 chars**.
2. **Reflexion coverage pass** — a second LLM call (`recover_missed_facts`) that re-reads the full transcript against the first-pass facts and appends misses.

Deployed to the live launchd binary, then the LoCoMo store was **re-ingested** (`--wipe`) through the new compression and scored with **Flash** (answerer + judge, us-west1) — a cheap, same-model A/B vs the prior Flash pool50 (54.5%), deliberately **not** spending a Pro headline run.

## Exact commands
```bash
# deploy Phase 1 binary → restart launchd server (see DEPLOY in HANDOFF)
cd ~/Projects/ironmem-locomo-benchmark; P=.venv/bin/python; C=8; L=us-west1
$P -m benchmark.run --strategy hybrid --wipe --rerank --pool 50 --concurrency $C --vertex-location $L \
   --answerer-model gemini-2.5-flash --judge-model gemini-2.5-flash --output upg4_phase1_flash_C_rerank_pool50.json
$P scripts/funnel_probe.py --strategy hybrid --pool 50 --scored results/upg4_phase1_flash_C_rerank_pool50.json \
   --output funnel_phase1.json
```

## Result — REGRESSION (error_count = 0; numbers trustworthy)
| Metric | Old compression (657def4) | **Phase 1** | Δ |
|---|---:|---:|---:|
| **`gold_in_memory` (the gate)** | 72.3% | **65.8%** | **−6.5** ❌ |
| compression-kept \| in-transcript | 76.9% | 70.0% | −6.9 |
| gold facts lost at ingest (of 1,445) | 334 | **434** | +100 lost |
| Flash pool50 overall (cats 1–4, n=1,540) | 54.5% | **53.25%** | −1.25 |

Per-category Flash overall (old → Phase 1): single 61.2→62.3 (+1.1), multi 39.7→35.1 (−4.6), open 38.5→31.3 (−7.2), temporal 54.5→52.0 (−2.5). The score followed the gate **down**. Target was `gold_in_memory` ≥ 90%; Phase 1 moved it the wrong way.

Files: `results/upg4_phase1_flash_C_rerank_pool50.json`, `results/funnel_phase1.json`.

## Diagnosis
The coverage pass can only **add** facts, so it physically cannot lower `gold_in_memory`. The regression therefore comes entirely from change **#1, the untruncated primary window (500/300 → 8000/4000)**: feeding Flash a 16× longer transcript under the same "concise, 9-section, one-call" extraction prompt made it extract a **smaller fraction** of facts. Extraction does not scale with context when the prompt and output budget stay fixed — a known small-model failure. The bigger window hurt more than it helped, and the coverage pass didn't recover the gap.

**The diagnosis that compression is the #1 leak still holds** — the funnel re-confirms it (now 65.8%). It is the specific *fix* that failed, not the target.

## Action taken
- **Reverted** `src/provider.rs` to `657def4` (Phase 1 removed); rebuilt + redeployed the live binary = known-good compression **+ #3 cost instrumentation + #5 temporal-trust trajectory** (both verified, additive, unaffected). Verified live (`/status` `governance_cost` present; server healthy).
- Phase 1 draft saved as `~/Projects/Iron-mem-fix/phase1_provider_DRAFT.patch` (not lost).
- Backups: `~/.ironmem/bin/ironmem.bak-batch-20260625`.

## Next experiment (in flight)
**Coverage-pass-ONLY**: keep the primary window tight at 500/300 (the config that gave 72.3%), re-apply *only* the full-transcript auditor (it reads the untruncated transcript independently). This isolates the one half of Phase 1 that can't regress the gate and may already clear 72.3%. → `results/upg5_coverageonly_*.json` + `funnel_coverageonly.json`. If it still falls short of ~90%: chunked atomic extraction + a focused fact-only extraction call (ROADMAP Wave 2.1).

## Lesson
Phase 1 bundled two levers, violating the roadmap's own "change one wave at a time, attribute the delta" discipline. Bundling hid which half worked. The coverage-only re-test restores that discipline. Re-ingest before any future benchmark — the store currently holds the regressed Phase-1 ingest.
