#!/bin/bash
# Off-peak Flash-judged scoring of the COVERAGE-ONLY store (already ingested).
#
# REDESIGNED 2026-06-25: the raw recall@N curve on THIS coverage store
# (scripts/pool_curve.py) showed the #1 RETRIEVAL leak is the reranker's top-k
# CUT, not the reranker itself. The LLM reranker HELPS hard (raw recall@10 is
# only 47.7%, but reranked top-10 lands ~73-78% — it surfaces gold from deep in
# the pool), so rerank stays ON. The leak is downstream: raw recall@50 is 83.6%
# vs @10 47.7% — ~36 pts of gold sit in pool positions 11-50 that the top-10 cut
# can't pass to the answerer. The lever is --retrieve-limit (the reranked top-k
# the answerer sees, default 10); --pool raises the ceiling the reranker can
# reach (gold beyond raw top-50). Both have headroom; this factorial separates
# them. raw recall@N: @10 47.7 @15 56.4 @20 61.8 @25 66.0 @30 69.4 @50 83.6.
#
# This runs a 2x2 FACTORIAL over pool {50,100} x retrieve-limit {10,HI} with
# rerank ON, Flash both sides (cheap, off-peak, directly comparable to the prior
# Flash 54.5% which was pool50/limit10). Clean attribution:
#   A pool50/lim10  = control (reproduces prior config on the richer store)
#   B pool100/lim10 = does WIDER POOL alone help?  (hypothesis: barely)
#   C pool50/limHI  = does BIGGER TOP-K alone help? (hypothesis: yes, the lever)
#   D pool100/limHI = both                          (expected best)
# Two bracket funnels (A & D corners, store-limit 2000) confirm the retrieval
# stage recovers gold at the wider top-k. --skip-ingest: never re-ingests (the
# store is fixed; re-ingest would clobber it). Self-disables after one clean run.
set -uo pipefail
REPO=/Users/kingjames/Projects/ironmem-locomo-benchmark
PY=$REPO/.venv/bin/python
MARK=$HOME/.ironmem/.coverage_score_done
PLIST=$HOME/Library/LaunchAgents/com.execlayer.locomo-coverage-score.plist
LOG=$REPO/results/raw_console/coverage_score_console.log
L=us-west1; C=8
HI=25   # high retrieve-limit arm (chosen from the raw recall@N curve)
cd "$REPO" || exit 9

[ -f "$MARK" ] && { echo "[$(date)] already done ($MARK) — skipping" >>"$LOG"; exit 0; }

{
  echo "=================================================================="
  echo "[$(date)] coverage-score 2x2 (pool x retrieve-limit) run starting (HI=$HI)"

  # Wait for the IronMem server (robust to wake-from-sleep: a prior job aborted
  # because the server wasn't up yet at wake — wait up to ~2 min here).
  if ! curl -s --retry 60 --retry-delay 2 --retry-connrefused -m 8 \
        "http://localhost:37778/status" >/dev/null 2>&1; then
    echo "[$(date)] IronMem server not up after wait — aborting (retry next fire)"
    exit 3
  fi
  M="--answerer-model gemini-2.5-flash --judge-model gemini-2.5-flash"

  run() { # $1=pool $2=limit $3=label
    local out="upg5_cov_p$1_l$2.json"
    echo "[$(date)] PASS $3: pool=$1 retrieve-limit=$2 -> $out"
    "$PY" -m benchmark.run --strategy hybrid --skip-ingest --rerank \
        --pool "$1" --retrieve-limit "$2" --concurrency $C \
        --vertex-location "$L" $M --output "$out"
    echo "[$(date)] $3 exit=$?"
  }

  run 50  10  "A control (= prior 54.5%)"
  run 100 10  "B wider-pool-only"
  run 50  $HI "C bigger-topk-only (the lever)"
  run 100 $HI "D both"

  echo "[$(date)] funnel A-corner: pool50 final-limit10 store-limit2000"
  "$PY" scripts/funnel_probe.py --strategy hybrid --pool 50 --final-limit 10 \
      --store-limit 2000 --scored results/upg5_cov_p50_l10.json \
      --output funnel_cov_p50_l10.json
  echo "[$(date)] funnel A exit=$?"
  echo "[$(date)] funnel D-corner: pool100 final-limit$HI store-limit2000"
  "$PY" scripts/funnel_probe.py --strategy hybrid --pool 100 --final-limit "$HI" \
      --store-limit 2000 --scored results/upg5_cov_p100_l$HI.json \
      --output funnel_cov_p100_l$HI.json
  echo "[$(date)] funnel D exit=$?"

  ok=$("$PY" - "$HI" <<'PYEOF'
import json, sys
HI = sys.argv[1]
def errs(p):
    try: return json.load(open(p)).get("error_count", 999)
    except Exception: return 999
files = [f"results/upg5_cov_p50_l10.json", f"results/upg5_cov_p100_l10.json",
         f"results/upg5_cov_p50_l{HI}.json", f"results/upg5_cov_p100_l{HI}.json"]
e = {f: errs(f) for f in files}
print("OK" if all(v == 0 for v in e.values()) else "NOTYET " + " ".join(f"{k}={v}" for k,v in e.items()))
PYEOF
)
  echo "[$(date)] result check: $ok"
  if [[ "$ok" == OK ]]; then
    touch "$MARK"
    rm -f "$PLIST" 2>/dev/null
    echo "[$(date)] SUCCESS — marker set, launchd plist removed (one-shot complete)"
  else
    echo "[$(date)] not clean — leaving job armed to retry on next fire"
  fi
  echo "[$(date)] coverage-score run finished"
} >>"$LOG" 2>&1
