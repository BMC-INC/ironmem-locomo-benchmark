# RUN NOTE — 2026-06-27 — V2 answer prompt (completeness-focused)

Experiment: can a redesigned answerer prompt lift the laggard categories (multi_hop,
open_domain) without loss to single_hop/temporal? Built off the failure
classification of the 65.9% V1 headline (70% of multi_hop failures were
gold-present-but-wrong, i.e. answerer-side, not retrieval).

Wired `--answer-prompt v1|v2` (default v1) in `benchmark/{query,config,run}.py`;
version recorded in output metadata. V2 = completeness-first: answer every part and
qualifier; exhaustive list aggregation; resolve relative dates to anchored absolute;
commit to best inference on "likely/might" questions; no preamble.

Config identical to V1 headline: Pro answerer + Pro judge, hybrid, --skip-ingest,
--rerank, pool 100, retrieve-limit 25, us-west1, conc 8. Same 28,554-memory store.
ONLY the answer prompt differs. 1986 q, 1540 scored, **0 errors**, 1:15:16.

## Result (results/upg8_PRO_p100_k25_v2.json)
| category    | V1     | V2     | Δ     | rescued / regressed |
|-------------|--------|--------|-------|---------------------|
| single_hop  | 69.9%  | 69.6%  | −0.3  | +55 / −58           |
| multi_hop   | 47.2%  | 50.4%  | +3.2  | +30 / −21           |
| open_domain | 43.8%  | 45.8%  | +2.0  | +9 / −7             |
| temporal    | 78.5%  | 76.0%  | −2.5  | +20 / −28           |
| **overall** | 65.9%  | 65.9%  | +0    | 114 / 114           |

**Net zero on overall (1015/1540 both).** The prompt redistributes points across
categories; it does not raise the total. multi_hop +9 (rescued 30 / regressed 21) is
the clearest real signal: the target worked. temporal −8 and single_hop −3 paid for it.

## Judge + honesty checks
- Flash 2nd-judge agreement (results/judge_agreement_PRO_v2.json): **κ 0.8824, 94.5%
  raw** (Pro 63.5 vs Flash 62.0 on n=200). κ rose vs V1's 0.80 — V2 answers are more
  decisive / inter-judge-consistent even at flat score.
- Cat-5 honesty: V2 abstained on adversarial MORE, not less (387/446 vs V1 378/446).
  The commit-to-inference rule did NOT cause adversarial over-answering.

## Diagnosis of the temporal cost (drives the next carve-out)
The 28 temporal regressions are a mix: (a) judge run-to-run noise (both answers near-
identical / both abstaining, scored differently); (b) commit-to-inference rule
guessing a wrong date where V1 abstained and scored (GOLD "16 March 2023" → V2 "After
losing her job in January 2023"); (c) over-elaboration adding confusing date clauses.
A chunk is below the judge noise floor.

## Verdict
The answer prompt is a **lateral lever, now maxed.** A single global prompt cannot lift
the laggards without collateral. 65.9% stands; V2 is the better-shaped 65.9% (higher on
hard categories, higher κ) and is the reported headline.

## Next (the actual path to 70)
1. **Date-abstention carve-out (v3):** "if the source has no explicit date, say so
   rather than infer one." Surgical fix for temporal (b) above, keeps the multi_hop
   gains. Expected ~+0.3 to +0.6 overall.
2. **Synthesis answerer (the real lever):** merge retrieved hops into a unified context
   before the answer model — attacks the multi_hop 49.6% failure rate at its root
   (gold-present-but-uncombined). The +9 from prompting alone shows the headroom.
3. open_domain remains extraction-capped (50% of in-transcript gold stored); low
   priority (n=96).

Files: results/upg8_PRO_p100_k25_v2.json, results/judge_agreement_PRO_v2.json,
canaries results/canary_PRO_v2{,b,c}.json, console results/raw_console/final_v2_console.log
