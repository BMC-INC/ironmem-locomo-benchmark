# IronMem Upgrade Plan — LoCoMo-driven

**Status:** Phase 0 cleared to start (code only). Phase 1 branch **PENDING** experiment + funnel numbers (~3:00 PM PDT, 2026‑06‑24).
**Source of truth for numbers:** `results/gemini_locomo_full_run.json` + the in‑flight retrieval experiment (`results/exp_session_limit25*.json`).
**Authoring split:** benchmark harness + diagnostics = Claude (this repo, `~/Projects/ironmem-locomo-benchmark`); IronMem Rust edits = you / Codex.

---

## ⛔ OPERATIONAL CONSTRAINTS (READ FIRST)

1. **No server restart, no DB writes, no second IronMem instance until the LoCoMo run finishes.**
   The live retrieval experiment is querying the running server on `:37778` and reading `~/.ironmem/mem.db`. Until it completes (~3 PM PDT): do **not** restart the server, swap the running binary, run `ironmem wipe`, or launch a second instance. Editing source and `cargo build` are fine — just **deploy/restart only after the test lands.** (We already lost one run to mid‑run disruption.)
2. **Phase 1 architecture is not chosen yet.** Do not commit to recall‑first/rerank vs fact‑index‑first until the funnel numbers below are filled in. It will likely leak at multiple stages; fund them in order of leak size.

---

## Baseline & core finding

Answerer + judge: **Vertex AI Gemini 2.5 Pro** · project `queueflow-sentinel` / `us-central1` · cats 1–4 scored (446 adversarial excluded, mem0‑comparable).

| category | session | hybrid | Δ |
|---|---:|---:|---:|
| single_hop | 54.6% | 56.4% | +1.8 |
| multi_hop | 26.6% | 29.8% | +3.2 |
| open_domain | 41.7% | 42.7% | +1.0 |
| temporal | 72.0% | 73.5% | +1.6 |
| **overall** | **52.3%** | **54.2%** | **+1.9** |

**The win is not a smarter answer model — Gemini is already capable. ~75% of wrong answers are retrieval‑side** (of 735 session errors: 343 abstentions + 205 retrieval gaps + 187 answerer gaps). The scandal: single_hop — the *easiest* category — has **313 / 382** failures from non‑retrieval. Facts that demonstrably exist in one session aren't reaching the model.

---

## The retention funnel (primary instrument)

Measure, per question, where the gold fact is lost:

```
raw has fact -> compression kept fact -> candidate pool has fact @25/@50 -> reranker kept it -> answerer used it
```

Each arrow is a measurable drop‑off. Biggest drop = where to invest. Stage → metric → fix:

| stage | metric | if this is the big leak → fix |
|---|---|---|
| raw → compression | **lost‑fact rate** | compression fidelity / atomic fact index (2.1) |
| compression → candidates | **recall@25**, **recall@50** | recall‑first retrieval, candidate count (1.A) |
| candidates → reranked context | **rerank retention** | reranker quality / Gemini reranker (1.B) |
| context → answer | **answerer miss rate** | answer prompt, evidence formatting (small) |
| (cross‑cutting) | **429 / error count** | provider reliability (0.1, 0.4) |

### Live results — PENDING (fill after ~3 PM PDT)

| metric | value | source |
|---|---|---|
| recall@25 (gold fact in top‑25 candidates) | `_pending_` | exp_session_limit25.json |
| recall@50 (gold fact in top‑50 candidates) | `_pending_` (needs added limit=50 pass) | exp_session_limit50.json |
| rerank retention (gold kept through rerank) | `_pending_` | exp_session_limit25_rerank.json |
| lost‑fact rate (compression dropped it) | `_pending_` | fidelity probe (post‑test) |
| answerer miss rate (context had it, answer wrong) | `_pending_` | full_run.json failure analysis |
| 429 / error count (this experiment) | `_pending_` | experiment.log |

> Note: the running experiment covers `@25` and `@25+rerank`. `recall@50` needs one more cheap `--skip-ingest --retrieve-limit 50` pass (queue after the current run). `lost‑fact rate` comes from the fidelity probe (richer than entity/date diff — see 0.3).

---

## Phase 0 — reliability + diagnostics (start now; deploy after the test)

- **0.1 Durable provider calls** — job‑backed retry + backoff + **visible failure state** on every LLM call (compression, rerank, answer). Root cause of both burns today: the silent compression WARN and the 11×429. *Done = a forced 429/500 retries then surfaces an error count; never a silently‑empty memory.* **(code now, restart after test)**
- **0.2 Configurable compression provider (`anthropic | vertex`)** — NOT a blind default switch. Compression quality is now a benchmark variable; keep both and compare lost‑fact rate before crowning a default. *Done = ingest runs with `ANTHROPIC_API_KEY` unset when provider=vertex.* **(code now, restart after test)**
- **0.3 Compression‑fidelity probe** — per session, audit raw turns vs compressed memory. Must catch **relational facts** ("X because Y", "A introduced B to C") and **source‑span coverage**, plus a **judge‑based raw‑vs‑compressed fact audit** — not just entity/date/number diffing. Emits **lost‑fact rate**. *(Claude builds the benchmark‑side version; run after the test.)*
- **0.4 Concurrency / quota** — eval concurrency configurable (done in harness) + quota‑aware backoff; **request a Vertex QPM bump for `queueflow-sentinel`** (console action, safe now).

## Phase 1 — recall (PENDING; branch chosen by the funnel)

- **1.A Recall‑first mode** — retrieve 25–50 candidates → rerank to final K. Track recall@10/25/50, MRR, gold‑present‑before‑rerank.
- **1.B Gemini reranker on Vertex** — BM25 + vector + graph candidates → Gemini ranks evidence snippets → answerer sees top evidence. *First check whether IronMem's existing `--rerank` is already a cross‑encoder vs an LLM call — may just need pointing at Vertex, not a rewrite.*
- **Branch rule:** if `@25/@50 + rerank` gives a big lift → 1.A/1.B is the immediate move. If it barely moves and **lost‑fact rate** is high → **2.1 jumps to the front** (the facts aren't in the store; ranking can't fix that).

## Phase 2 — structure (the keystone)

- **2.1 Atomic fact index** — every compressed memory emits `{subject, predicate, object, time, source_memory_id, source_span, confidence, namespace}`; retrieval hits the fact table before raw chunks. **Keystone — unlocks 2.2 and 2.3, and is the provenance/governance substrate for IronMem + SovereignClaw + Operator OS.**
- **2.2 Evidence‑chain retrieval (multi‑hop)** — fact A → extract bridge entities → fact B → assemble chain → answerer. Fastest path to multi_hop (80 retrieval‑gap **+ 80 answerer‑gap**). Depends on 2.1.
- **2.3 Temporal as‑of layer** — event intervals, "last known as of," supersession, contradiction history on 2.1's `time` field. **Data note:** temporal retrieval is already good (only **5** retrieval‑gaps); wins are answer‑time date handling, so don't over‑invest in temporal *retrieval*.

## Phase 3 — product / governance track (parallel; does not gate score)

- **3.1 Namespace federation** — `user:* / project:* / operator-os:tenant:* / sovereignclaw:* / repo:* / agent:*` + share / local / consent / summarize rules.
- **3.2 Memory health dashboard** — **metrics first** (retrieval hit rate, lost‑fact rate, compression failures, candidate recall, rerank retention, orphaned graph nodes, namespace‑leak risk, stale facts, top missed entities, eval‑delta by category), UI later.
- **3.3 Eval‑guided autotuner** — **last.** Requires a fast 150–200Q dev split + a held‑out set (the full run is ~50 min — can't autotune on it; held‑out avoids overfitting LoCoMo). Tunes retrieval weights / rerank thresholds / expansion per category and locks winning configs.

---

## Decisions / pushbacks (locked)

- **Query planner (5‑way classifier): defer.** It taxes *every* query with a classify‑LLM call before the per‑class strategies exist. Start with 2 branches (multi_hop→graph, temporal→as‑of) **after** 2.1; single/open‑domain ride the default path.
- **Compression→Vertex: configurable, measured, not blind‑default** (0.2).
- **Dashboard = metrics first, UI later** (3.2).
- **Autotuner & federation: own tracks, don't block recall work.**

## Framing — the "Memory Compiler"

Not a separate big bet — it's the *name* for `0.3 + 2.1 + 2.2 + 2.3 + compression + governance metadata`. IronMem compiles raw interaction history into **governed, source‑linked, temporal, queryable memory artifacts** (archive + summary + atomic facts + temporal events + entity graph + profile deltas + governance metadata). Retrieval queries the compiled representation, not just text chunks. Each artifact is one item above → reachable incrementally, not a rewrite.

## Governance through‑line (ExecLayer)

`source_memory_id / source_span / confidence` (2.1) + `as‑of` (2.3) + consent rules (3.1) **are** the substrate for deterministic AI governance: governable memory = provenance + when‑was‑this‑true + who‑can‑see‑it. **The recall work is the governance work.**

## Sequencing (TL;DR)

```
0.1 provider reliability  ─┐ (code now)
0.4 concurrency + QPM bump ┘ (quota bump safe now)
        │
   [experiment lands ~3 PM] → deploy 0.1/0.2 restart
        │
0.3 fidelity probe (richer) + funnel recall computation  → FILL the table above
        │
   choose Phase 1 branch (1.A/1.B  OR  2.1-first)
        │
2.1 atomic fact index → 2.2 evidence chains → (then product track 3.x)
```
