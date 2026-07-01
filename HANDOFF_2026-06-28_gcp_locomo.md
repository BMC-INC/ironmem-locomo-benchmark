# HANDOFF — 2026-06-28 (afternoon, PDT)

## TL;DR — RESUME HERE
We moved the LoCoMo Pro-judged retest **off the laptop and onto a GCP VM** because three nights of local runs kept dying on the same two things: ADC token expiring mid-run, and Pro 429 throttling. The VM fix solves both. Right now a VM (`locomo-1`) is building the IronMem server; once it's built you finish the run there.

**Two VMs are live and billing in project `queueflow-sentinel`:**
- `locomo-1` (us-west1-a, n2-standard-8, cloud-platform scope, **attached SA = auth can't expire**, 24h auto-delete) — the LoCoMo retest box. Still compiling the IronMem server.
- `burn-6` (us-east1-d, n2-standard-32, 24h auto-delete) — the $300 credit-burn box running `cargo test` on your Rust repos. Tests appear to have finished (now idle but still billing).

Both auto-delete in 24h. CPU quota was raised `CPUS_ALL_REGIONS 32→256` (granted), so there's headroom.

---

## THE BENCHMARK CONCLUSION (settled — don't re-litigate)
**Headline stays V2 = 65.9%** (Pro answerer + Pro judge, p100/k25, us-west1, `results/upg9_PRO_p100_k25_v3syn.json` is the synthesis run; V2 baseline is the prior `upg8`).

The **synthesis experiment (`--answer-prompt v3 --synthesize`) is NOT a win:**
- The full run errored on 288/1986 questions (218 × Pro 429 throttle + 70 × ADC token expired mid-run). Reported 57.4% is corrupt.
- Error-excluded (1,698 completed): single 69.4 / **multi 47.6** / temporal 77.1 / open 55.1 / **overall 66.0%**. That's flat vs V2 65.9%, and multi_hop went DOWN (was 50.4). So synthesis is not the multi_hop lever.
- The real next lever is the **Wave 4 cross-encoder reranker** (retrieval-side), not synthesis.

The GCP retest's value is now mostly: (a) a clean 0-error number for the record, and (b) **a reliable benchmark pipeline on GCP** so future runs (cross-encoder, etc.) stop failing.

---

## FINISH THE LoCoMo RETEST ON `locomo-1` (next actions)
Setup script ran via `sudo bash /tmp/locomo_setup.sh` (log: `/var/log/locomo_setup.log`, pid was 1129). It: installs Rust+deps, builds IronMem from `gs://queueflow-sentinel-benchmarks/src/im.tar.gz` with `--features local-onnx`, unpacks the benchmark to `/opt/bench` + makes its venv.

When the build is done (`/opt/im/target/release/ironmem` exists), do:
```bash
gcloud compute ssh locomo-1 --zone=us-west1-a --project=queueflow-sentinel
sudo -i
# 1. place the store
mkdir -p /root/.ironmem && gsutil cp gs://queueflow-sentinel-benchmarks/locomo/mem.db /root/.ironmem/mem.db
# 2. start the IronMem server (default port 37778) — VERIFY it logs ~29,251 memories (schema match)
nohup /opt/im/target/release/ironmem server >/var/log/ironmem.log 2>&1 &
sleep 5 && curl -s localhost:37778/status   # expect 29251 memories / 60200 observations
# 3. run the benchmark — Vertex auth is the attached SA (no token dance), in-region us-west1
cd /opt/bench && source .venv/bin/activate
python -m benchmark.run --strategy hybrid --skip-ingest --rerank --pool 100 \
  --retrieve-limit 25 --answer-prompt v3 --synthesize --vertex-location us-west1 \
  --concurrency 8 --output upg9_PRO_GCP_v3syn.json 2>&1 | tee /var/log/locomo_run.log
# 4. ship the result back
gsutil cp /opt/bench/results/upg9_PRO_GCP_v3syn.json gs://queueflow-sentinel-benchmarks/locomo/
```
**Watch for:** if the server doesn't report ~29,251 memories, the `im.tar.gz` (BMC-INC/Iron-mem main) build is schema-incompatible with the laptop-built `mem.db`; rebuild from the matching source (`~/Projects/Iron-mem-fix`) instead. Expect **error_count == 0** this time (attached SA + in-region). Target: confirms ~66% (synthesis flat vs V2), error-free.

---

## THE $300 CREDIT (mostly a dead end — low priority)
- The **$300 Free Trial credit expires 2026-06-29**. It EXCLUDES gen-AI (Vertex/Gemini → always bills GFS) and, per testing, BigQuery too. **Confirmed in-scope: networking/egress; likely Compute Engine.**
- Credit routing is **deterministic by SKU scope + soonest-expiry — you cannot pick the pool.**
- Other credits (same billing account `012EF5-2DDDCC-D57D3F`): **GenAI App Builder $1,000 unused** (expires 2027-05-22, the right pool for Agent/Gemini work), **GFS $2,000** (used ~$385 on Vertex → $1,614 left, expires 2028).
- **Billing export is now ON** (Detailed usage cost + Pricing → `queueflow-sentinel:billing_export`). Populates with ~24h lag. **TOMORROW: query it to see which credit `burn-6`'s Compute Engine cost actually hit.** If Compute → $300, scaling the burn was worth it; if → GFS, stop burning.
- `burn-6` is the only burn node up (didn't scale the fleet pending that confirmation). Its `cargo test` results are in `gs://queueflow-sentinel-benchmarks/burn-6/` — **verify they're real, not the 82-byte `cargo: command not found` stubs from the first broken startup script.**

---

## KEY ARTIFACTS
- **`benchmark/salvage.py`** (NEW, this session) — resume tool: re-answers only the errored `question_id`s from a prior result file and merges into a new file, non-destructive. `--plan` verified 288/288 map. Caveat: stalled on hung Vertex connections when run locally (the very problem the VM solves) — should work fine on `locomo-1`.
- **GCS bucket** `gs://queueflow-sentinel-benchmarks/`: `src/` (im.tar.gz, sc.tar.gz), `locomo/` (mem.db 275MB, bench.tar.gz), `burn-6/` (test results).
- **Original data intact**: `results/upg9_PRO_p100_k25_v3syn.json` (1986 Q, 288 errors, 20.5MB). The 2.5h salvage run stalled and was killed — wrote nothing, lost nothing.
- Local IronMem server still running (pid 777, localhost:37778, store `~/.ironmem/mem.db`).
- Setup scripts in scratchpad: `locomo_setup.sh`, `burn_startup.sh`, `run.sh`.

## STILL PENDING (from earlier, unrelated to today)
- `~/Projects/Iron-mem-fix` branch `feat/temporal-dual-naming` (commits 90b335b + c64acbc: dual-naming + derives/quarantine + auto-dream) — built, tests green, **NOT pushed, needs your OK.**

## LESSONS BANKED TO MEMORY
- ADC expires ~24h AND **can expire mid-run on a 3h+ sweep even if valid at launch** → use a VM-attached SA (metadata-server auto-refresh, never expires). [`project_locomo_vertex_auth_ops`]
- Concurrency-8 Pro re-trips the 429 throttle even off-peak → in-region VM + lower concurrency. [same]
- Synthesis ≈ V2, not the lever; cross-encoder is next. [`project_ironmem_locomo_next`]
