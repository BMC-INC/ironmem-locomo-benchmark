# IronMem inference: Anthropic → Google Cloud (Vertex AI Gemini) — Codex spec

**Goal:** route **all** of IronMem's server-side LLM inference off the metered Anthropic API onto **Vertex AI Gemini**, so it bills to GCP credit. Keep `anthropic` selectable for comparison. This is plan item **0.2** + reliability **0.1**.

**Why now:** IronMem is wired into every repo via the session-start hook, so every session-end (and graph/relations/correction work) calls Anthropic — continuously draining the last ~$10 of metered Claude credit. Vertex bills to the GCP project (plenty of credit). ADC is already configured (`gcloud auth application-default login` done today).

## Scope — find ALL Anthropic call sites, not just compression
IronMem calls an LLM in several places. Grep the Rust source for: `anthropic`, `api.anthropic.com`, `x-api-key`, `claude-`, `ANTHROPIC_API_KEY`. Expected call sites (from `ironmem` subcommands): **compress** (session compression), **RELATIONS / graph extraction** (`graph-backfill`, `reconcile`), **corrections** mining (`corrections`), any **LLM rerank** path. Route them all through one abstraction.

## Design — one provider behind a trait

> **Good news from `ironmem config`:** IronMem ALREADY exposes `provider` + `model` (currently `"anthropic"` / `"claude-sonnet-4-6"`), and a separate `rerank.model` (also `claude-sonnet-4-6`, pool 20). So a provider abstraction very likely already exists. **Codex's first step:** grep the provider match/enum (where `"anthropic"` is handled) and check whether a `vertex`/`gemini`/`google` arm already exists. If it does, this is a *config flip + a Vertex client impl*, not a refactor. The compression model is currently **Claude Sonnet 4.6** (capable) — so Flash is a real quality change; the fidelity probe (0.3) is how we confirm it's safe.

```
trait InferenceProvider { async fn complete(&self, prompt: &str, max_tokens: u32) -> Result<String>; }
struct AnthropicProvider { ... }   // existing behavior
struct VertexProvider   { project, location, model, token_source }
```
Select from the existing `provider` config field. Default `vertex` once the fidelity probe confirms parity.

**Cover the rerank path too:** `rerank.model` is also `claude-sonnet-4-6` — when `rerank.enabled=true`, reranking calls Anthropic. Route it through the same provider so `--rerank` runs on Vertex as well (this is why the earlier rerank experiment variant would have hit Anthropic).

## Config (env)
| var | value |
|---|---|
| `IRONMEM_LLM_PROVIDER` | `anthropic` \| `vertex` (default → `vertex`) |
| `IRONMEM_VERTEX_PROJECT` | the GCP project holding the credit (e.g. `queueflow-sentinel` — **confirm which billing has the larger pool**) |
| `IRONMEM_VERTEX_LOCATION` | `us-central1` (or `global`; any region with Gemini — region is just latency/credit, no France) |
| `IRONMEM_COMPRESS_MODEL` | `gemini-2.5-flash` |
| `ANTHROPIC_API_KEY` | only read when provider=anthropic |

**Model choice — Flash, deliberately.** Compression/relations run on *every* session across *all* repos, synchronously. Pro is a slow thinking model (~10–30s/call) → it'd lag every session-end everywhere. `gemini-2.5-flash` is ~2–4s, current-gen, and strong for extraction/summarization. Keep Pro for the benchmark answerer/judge (quality-critical, low volume). We verify Flash isn't dropping facts via the fidelity probe (plan 0.3) before locking it.

## Vertex call (REST)
```
POST https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT}/locations/{LOCATION}/publishers/google/models/{MODEL}:generateContent
# location=global → host is aiplatform.googleapis.com
Authorization: Bearer <ADC token>
Content-Type: application/json

{ "contents":[{"role":"user","parts":[{"text":"<prompt>"}]}],
  "generationConfig":{ "temperature":0, "maxOutputTokens":<budget>,
                       "thinkingConfig":{"thinkingBudget":0} } }   # Flash supports 0 = no thinking → fastest/cheapest
```
Parse `candidates[0].content.parts[*].text` (skip `thought` parts, concatenate); guard empty/blocked.

## Auth (ADC → bearer token)
- Quick: shell out to `gcloud auth application-default print-access-token` (cache ~50 min; tokens last ~1 h).
- Robust: a Rust GCP auth crate (`gcp_auth` / `google-cloud-auth`) loads ADC and auto-refreshes. Scope `https://www.googleapis.com/auth/cloud-platform`.

## Reliability (fold in plan 0.1 — fixes the silent-failure bug)
- Retry 429/5xx with backoff; on terminal failure **surface a visible FAILED status / metric** and (ideally) enqueue for retry. **Never** silently return an empty/short memory — that's the bug that corrupted the first hybrid run.

## Rollout
1. Build (`cargo build --release`), set the env vars, **restart the server** (brief bounce for all repos — pick a moment that's OK).
2. Verify: trigger one session-end → server log shows `compressed → memory_id=…` with **no** Anthropic call; confirm it works with `ANTHROPIC_API_KEY` unset.
3. Run the **fidelity probe** (plan 0.3): compare lost-fact rate `vertex-flash` vs `anthropic` before making Vertex the hard default. If Flash regresses, set `IRONMEM_COMPRESS_MODEL=gemini-2.5-pro`.

## After the swap
- Re-ingest the benchmark conversations into the (isolated) instance → that re-ingest now burns **GCP** credit, not Anthropic.
- Then re-run the LoCoMo retrieval experiment (incl. `recall@50`).
