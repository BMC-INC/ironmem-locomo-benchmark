# HANDOFF — IronMem × LoCoMo benchmark + Vertex/full memory upgrades
*Snapshot: 2026-06-24 ~16:25 PDT. Written so the conversation can be compacted and resumed cleanly.*

## TL;DR — where we are right now
1. **LoCoMo benchmark of IronMem on Vertex Gemini 2.5 Pro: DONE.** Session 52.3% / hybrid 54.2% overall (1,986 Q). Finding: **retrieval recall is the bottleneck (~75% of failures)**, multi-hop worst.
2. **IronMem inference swap (Anthropic → Vertex/Google Cloud): DEPLOYED + VERIFIED 2026-06-24 ~15:39 PDT.** Live config: `provider=vertex`, `model=gemini-2.5-flash`, `vertex_project=queueflow-sentinel`, `vertex_location=us-central1`. End-to-end test compression succeeded through the launchd server via Vertex (`memory_id=21757`, +10 facts, **zero Anthropic calls**); test project wiped after. Two bugs found + fixed during deploy — see **DEPLOY LOG** below.
3. **Full memory upgrades: DEPLOYED + VERIFIED 2026-06-24 ~16:15 PDT.** No LoCoMo rerun was performed after upgrades. User explicitly wants the benchmark saved until all planned feature work is complete. Live binary now includes Vertex retry/visible failures, wider rerank pool, source-grounded fact/procedure chunks, one-hop graph evidence chains, and dream/sleep consolidation surfaces.

## Who / context
- User: **James Benton (King James)**, ExecLayer CEO. GCP project **`queueflow-sentinel`** (ADC auth working via `gcloud auth application-default login`). **Gemini 2.5 only** (not 2.0). Has large GCP credit; pays for Anthropic API out of pocket (~$10 left) → wants IronMem inference on Google Cloud.
- **iCloud gotcha (important):** repos under `~/Desktop` are iCloud-evicted to dataless placeholders → `git`/`cargo`/reads hang. Real work lives in **`~/Projects`** / `~/dev` (local). Do NOT use `~/Desktop/Iron-mem` (stale) — the live source is `~/Projects/Iron-mem-fix`.

## Key locations
| What | Path |
|---|---|
| Benchmark harness (Vertex Gemini) | `~/Projects/ironmem-locomo-benchmark` (git, commit `509e84f`; venv `.venv` py3.12 + `google-genai`) |
| **IronMem source (LIVE)** | `~/Projects/Iron-mem-fix` (`name=ironmem`, git `main` @ `657def4`) |
| IronMem runtime | `~/.ironmem/` → `bin/ironmem`, `mem.db`, `settings.json`, `server.log`, `fastembed_cache/` |
| IronMem server | launchd agent **`com.execlayer.ironmem`** on `http://localhost:37778`, embedder `bge-small-en-v1.5` (needs `--features local-onnx`) |
| Live upgraded binary | `~/.ironmem/bin/ironmem` (deployed from `~/Projects/Iron-mem-fix/target/release/ironmem`, built with `--features local-onnx`) |

## Benchmark results (final, all 1,986 Q, cats 1–4; Gemini 2.5 Pro answerer+judge)
| category | session | hybrid | Δ |
|---|---:|---:|---:|
| single_hop | 54.6% | 56.4% | +1.8 |
| multi_hop | 26.6% | 29.8% | +3.2 |
| open_domain | 41.7% | 42.7% | +1.0 |
| temporal | 72.0% | 73.5% | +1.6 |
| **overall** | **52.3%** | **54.2%** | **+1.9** |
Failure split (session, 735 wrong): abstained 343, retrieval_gap 205, answerer_gap 187 → **~75% retrieval-side**. Files: `results/gemini_locomo_{session,hybrid,full_run}.json`.

## Docs already written (in `~/Projects/ironmem-locomo-benchmark`)
- `IRONMEM_UPGRADE_PLAN.md` — phased plan (0 reliability/diagnostics → 1 recall → 2 fact-index/chains → 3 product). Phase-1 branch PENDING the funnel numbers. Retention funnel: `raw has fact → compression kept fact → candidate pool has fact @25/@50 → reranker kept it → answerer used it`.
- `IRONMEM_VERTEX_INFERENCE_SPEC.md` — the swap spec (also handed to Codex format).
- `RUN_NOTE_2026-06-24_locomo_full_run.md` — immutable run note (records the discarded-then-redone hybrid run).
- `scripts/analyze_failures.py`, `scripts/combine_results.py`.

## The Vertex swap — what changed (implemented in `Iron-mem-fix`)
- `src/provider.rs`: added `Provider::Vertex` reusing the existing Gemini request/response structs; new `vertex_text()` / `compress_vertex()` — Vertex `aiplatform.googleapis.com` endpoint, **ADC bearer token via `gcloud auth application-default print-access-token`**, `generationConfig` with `thinkingBudget=0` (Flash, fast). Dispatch in `compress()` + `complete_with()` bypasses api-key resolution for Vertex (covers compression AND rerank).
- `src/config.rs`: added `vertex_project: Option<String>` + `vertex_location: String` (default `us-central1`; env overrides `IRONMEM_VERTEX_PROJECT` / `IRONMEM_VERTEX_LOCATION`).
- Built with `cargo build --release --features local-onnx` (the `local-onnx`/fastembed feature is REQUIRED — ollama is down, so a plain build would degrade retrieval to keyword-only). 0 errors/warnings.
- Compression model decision: **`gemini-2.5-flash`** (it runs synchronously on every session in every repo → latency matters; Pro would lag everything). Verify Flash parity later via the fidelity probe.

## DEPLOY LOG — COMPLETED 2026-06-24 ~15:39 PDT
Deployed the Vertex binary + config and restarted the launchd agent. **Two bugs surfaced and were fixed before leaving it live** (both would have caused *silent* compression failure across all repos — the plan-0.1 bug):

1. **launchd PATH (config fix, survives rebuilds).** The agent ran with `PATH=/usr/bin:/bin:/usr/sbin:/sbin` → the server's `gcloud` shell-out for the ADC token failed (gcloud is at `/usr/local/bin`). Fixed in `~/Library/LaunchAgents/com.execlayer.ironmem.plist` by adding:
   ```xml
   <key>EnvironmentVariables</key>
   <dict><key>PATH</key><string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
   ```
   Reload requires `launchctl bootout` + `bootstrap` (a plain `kickstart -k` does NOT pick up env changes). Backup: `…plist.bak`.
2. **Vertex role (source fix, must be in rebuilds).** With PATH fixed, Vertex returned `400 "Please use a valid role: user, model."` — our `GeminiContent` struct had no `role` field (AI Studio tolerates it, Vertex doesn't). Fixed in `src/provider.rs`: added optional `role: Option<String>` (skip-if-none) to `GeminiContent`, set `Some("user")` on the Vertex request, `None` on the AI Studio request. **⚠️ Out-of-chat rebuilds MUST include this hunk or Vertex breaks again.** Rebuilt `--features local-onnx` (3m14s, clean), redeployed.

**Verification:** test session through the launchd server → `session/end` returned `memory_id=21757, skipped:false`; log `compressed → memory_id=21757 (+10 facts)`; embedder still `bge-small-en-v1.5`; no Anthropic call. Test project `/tmp/ironmem-vertex-deploy-test` wiped (11 memories). Server healthy, 16,089 memories intact.

## FULL-UPGRADE LOG — COMPLETED 2026-06-24 ~16:15 PDT
User corrected the sequencing: **do not rerun LoCoMo between phases.** Implement the full feature set first, then run one meaningful upgraded benchmark. Following that direction, Codex completed the remaining planned upgrades before any benchmark rerun.

### Implemented in `~/Projects/Iron-mem-fix`
- **Provider reliability / visible failure:** shared LLM completion path now retries transient provider failures (`429`, quota/resource exhausted, timeout, 5xx) with configurable backoff. `/session/end` and MCP session end now return `ok:false` on compression failure instead of silently reporting success.
- **Vertex provider retained:** `Provider::Vertex`, `vertex_project`, `vertex_location`, ADC via `gcloud`, Gemini request `role:"user"` for Vertex, AI Studio path leaves role unset.
- **Recall/rerank control:** default rerank candidate pool is now `50`; REST `/context` accepts `pool=` override while final output remains capped by `limit`.
- **Source-grounded Memory Compiler:** fact/procedure chunks created by compression now attach CCR `source_hash` plus `source_start`/`source_end` byte spans when IronMem can locate supporting evidence in the original transcript. Derived fact/procedure memories also record `source_ref` handles like `mem:<parent>:fact:1` and `mem:<parent>:procedure:1` in governance metadata.
- **Graph evidence chains:** graph retrieval now performs a constrained one-hop bridge expansion through connected entities, allowing multi-hop questions to retrieve both the initial relation memory and the bridge/dependency memory without re-enabling broad recency-based entity search.
- **Dream/sleep surfaces:** safe consolidation is now explicit as `ironmem dream`, REST `POST /dream`, and MCP tool `dream_memory`. Defaults remain safe: `dry_run=true`, `apply=false`; applying writes consolidated memories through the existing reflection proposal workflow and never deletes originals.

### Verification run after full upgrades
No LoCoMo benchmark was run.

Code-level checks:
```bash
cargo test --bin ironmem config::tests
cargo test --bin ironmem provider::tests
cargo test --bin ironmem compress::tests
cargo test --bin ironmem retrieval::tests
cargo test --bin ironmem mcp::tests
cargo test --test mcp_stdio_clean
cargo clippy --bin ironmem --features local-onnx -- -D warnings
cargo build --release --features local-onnx
```

Runtime smoke against live launchd server:
- `POST /session/start` + `POST /event` + `POST /session/end` on `/tmp/ironmem-full-upgrade-smoke` returned `ok:true`, `memory_id=21793`, `skipped:false`.
- `/context` returned source-linked chunks including `mem:21793:fact:1` and `mem:21793:procedure:1`.
- `POST /retrieve_original` with `chunk_id=mem:21793:fact:1` returned exact source span metadata (`bytes=54`, `source_start=29`, `source_end=83`).
- `POST /dream` with `dry_run:true`, `apply:false` returned cleanly (`scanned=7`, `proposals=0`, `applied=0`).
- Smoke project was wiped afterwards. Final live status: `ok=true`, `sessions=2094`, `memories=16125`, `memory_edges=3484`, `memory_chunks=74`.

Backups created:
```bash
~/.ironmem/bin/ironmem.bak-phase1-20260624-155743
~/.ironmem/bin/ironmem.bak-full-upgrades-20260624-161359
```

### Rollback (if ever needed)
```bash
cp ~/.ironmem/bin/ironmem.bak-anthropic ~/.ironmem/bin/ironmem
cp ~/.ironmem/settings.json.bak         ~/.ironmem/settings.json   # provider=anthropic
# (optional) restore ~/Library/LaunchAgents/com.execlayer.ironmem.plist.bak — harmless to keep the PATH fix
launchctl kickstart -k gui/$(id -u)/com.execlayer.ironmem
```

### State after full upgrades
- Live source = `~/Projects/Iron-mem-fix`; upgrade commit `657def4` (`feat: upgrade vertex memory compiler and dream flows`).
- Touched source files: `src/compress.rs`, `src/config.rs`, `src/main.rs`, `src/mcp.rs`, `src/provider.rs`, `src/retrieval.rs`, `src/server.rs`.
- When rebuilding: always use `cargo build --release --features local-onnx`, then `cp target/release/ironmem ~/.ironmem/bin/ironmem` + `launchctl kickstart -k gui/$(id -u)/com.execlayer.ironmem`. The plist PATH fix and `settings.json` provider=vertex persist on their own.

## Open items / gotchas
- **Do not run LoCoMo yet unless James explicitly says to.** The current strategy is: finish feature work → one upgraded benchmark run → patch any remaining leaks.
- **DB contention**: while benchmark testing, NOTHING else should write to `mem.db` (other repos' sessions, `ironmem inject`, `ironmem wipe`). Consider an isolated benchmark instance (2nd `port` + separate `db_path`) for clean re-runs.
- **Still useful after feature freeze:** fidelity probe and retention funnel (`raw has fact → compression kept fact → candidate pool has fact @25/@50 → reranker kept it → answerer used it`) should run alongside the next benchmark so the result explains *where* any misses remain.
- Benchmark repo run cmd, when approved: `cd ~/Projects/ironmem-locomo-benchmark && .venv/bin/python -m benchmark.run --strategy session|hybrid|both --wipe --concurrency 10`. IronMem must be running on `:37778`.
