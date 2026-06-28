# Synthesis Answerer — Spec (the multi_hop lever toward 70%)

## Why
multi_hop = 50.4% (V2), the category with the most headroom. The failure
classification of the headline showed **70% of multi_hop failures are
gold-present-but-wrong**: the supporting hops ARE in the retrieved context, but the
answer model does not combine them. That is an architecture gap, not a prompt gap.
The +9 net multi_hop from prompting alone (V2) shows the model CAN answer these when
the input is better organized. A dedicated synthesis step that merges the retrieved
passages into a unified, cross-referenced brief BEFORE the answer model sees them
attacks the 49.6% failure rate at its root.

## Design (benchmark-side first, server-side later)
Start in the harness (`benchmark/query.py`), not in IronMem's Rust. Reason: it
isolates the synthesis effect, iterates in minutes, and validates the hypothesis
before any server build. If it wins, port to a server-side `reflection.rs`
synthesis pass (store- or retrieve-time) as the product path.

Pipeline today:  retrieve -> build_context (numbered passages) -> answer
Pipeline with synthesis:  retrieve -> build_context -> **synthesize** -> answer

The synthesis call takes the question + the numbered passages and returns a
consolidated evidence brief: same facts, but merged by entity/event/timeline, with
cross-references resolved and the facts-that-must-be-combined explicitly chained.
The answer model then answers from the brief.

## Prompt (draft)
SYNTHESIS_PROMPT (in query.py):
> You are preparing evidence for another model that will answer a question about
> people from their conversation history. Below are retrieved memory passages, some
> relevant and some not.
> Question: {question}
> Passages: {context}
> Write a single consolidated brief of only the facts relevant to the question.
> - Merge facts about the same entity, event, or timeline into one statement.
> - Make every cross-reference explicit: resolve pronouns and relative dates, and
>   connect facts that must be combined (e.g. "X did Y" + "Y was on Z" -> "X did Y on Z").
> - Preserve every distinct relevant fact; drop nothing, invent nothing.
> - If passages conflict, keep both and note the conflict.
> Output only the brief, as a short list of consolidated facts.

## Code changes (contained)
- `benchmark/query.py`: `SYNTHESIS_PROMPT` + `async def synthesize_context(gemini, cfg, question, context_text)`; in `retrieve_and_answer`, if `cfg.synthesize`, replace `context_text` with the brief before `answer_question`.
- `benchmark/config.py`: `synthesize: bool = False`; `synthesis_model: str | None = None` (None -> use answerer_model).
- `benchmark/run.py`: `--synthesize` flag, `--synthesis-model`; wire to cfg; record in output metadata.

## Key decisions
- **Brief replaces raw passages** (cleanest test of the hypothesis). The prompt is
  hard-instructed to preserve all relevant facts. If results show fact-loss, fall
  back to brief + raw passages appended (belt-and-suspenders, more tokens).
- **Synthesis model = Pro** by default (multi_hop is hard reasoning). A Flash-synthesis
  variant is a cheaper follow-up to test cost/quality.
- **Uniform vs gated**: build uniform (synthesize every question). Measure per-category.
  If single_hop/temporal regress, gate synthesis to multi_hop via the existing
  `classify_question` router (cfg.route machinery already present).

## Cost
One extra LLM call per question (~2x answer-stage cost; ~+45-60 min on a full Pro run).
Acceptable for one validation run. Mitigations if needed: Flash synthesis, or gate to
multi_hop only (~18% of questions).

## Validation (build-all-then-test: ONE combined paid run)
Run `--answer-prompt v3 --synthesize` (v3 carve-out + synthesis together), Pro p100/k25,
us-west1, same store. Canary 1 conv first (catch breakage + fact-loss). Compare to the
V2 65.9% headline:
- multi_hop lift (target: 50.4% -> 55-60%)
- per-category regression check (single_hop/temporal must hold; gate if not)
- the v3 carve-out's temporal recovery rides along in the same run
- Flash 2nd-judge κ to confirm any gain is real

## Expected impact
multi_hop 50.4 -> ~57 (+6.6 on 282 = +1.2 overall) + carve-out (~+0.3-0.6) puts overall
~67.5-68%. Reaching 70 likely also needs a second multi_hop increment (better synthesis,
or higher retrieve-limit feeding more complete hop sets) and/or single_hop gains. This is
the first and biggest step, not the whole distance.
