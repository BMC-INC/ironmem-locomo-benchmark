#!/bin/bash
# One-shot off-peak Pro-judged "postable" run, fired by launchd at 04:30 local.
# Produces the clean Pro-vs-Pro internal comparison on the UPGRADED store:
#   A' = rerank OFF (control)   C' = rerank ON pool 50 (headline)
# Pro answerer + Pro judge are the harness DEFAULTS, so no model flags are passed.
# Self-disables after one success via a done-marker so it never re-runs.
set -uo pipefail
REPO=/Users/kingjames/Projects/ironmem-locomo-benchmark
PY=$REPO/.venv/bin/python
MARK=$HOME/.ironmem/.pro_postable_done
PLIST=$HOME/Library/LaunchAgents/com.execlayer.locomo-pro-postable.plist
LOG=$REPO/results/raw_console/pro_postable_console.log
cd "$REPO" || exit 9

[ -f "$MARK" ] && { echo "[$(date)] already done ($MARK) — skipping" >>"$LOG"; exit 0; }

{
  echo "=================================================================="
  echo "[$(date)] PRO postable run starting"

  # Make sure IronMem is answering before we spend Pro tokens.
  if ! curl -s -m 8 http://localhost:37778/context -X POST \
        -H 'content-type: application/json' \
        -d '{"project":"/benchmark/locomo/probe","query":"x","limit":1}' >/dev/null 2>&1; then
    echo "[$(date)] IronMem server not responding on :37778 — aborting (will retry next fire)"
    exit 3
  fi

  REGION=$("$PY" "$REPO/scripts/pick_pro_region.py" 2>/dev/null || echo NONE)
  echo "[$(date)] Pro capacity check -> region: $REGION"
  if [ "$REGION" = "NONE" ] || [ -z "$REGION" ]; then
    echo "[$(date)] Pro still capacity-throttled (no region passed the burst gate) — skipping, staying armed for next trigger"
    exit 0
  fi
  C=8

  echo "[$(date)] PASS A' — Pro, rerank OFF (control)"
  "$PY" -m benchmark.run --strategy hybrid --skip-ingest --concurrency $C \
      --vertex-location "$REGION" --output upg3_PRO_A_rerankoff.json
  rcA=$?
  echo "[$(date)] PASS A' exit=$rcA"

  echo "[$(date)] PASS C' — Pro, rerank ON pool 50 (headline / postable)"
  "$PY" -m benchmark.run --strategy hybrid --skip-ingest --rerank --pool 50 --concurrency $C \
      --vertex-location "$REGION" --output upg3_PRO_C_rerank_pool50.json
  rcC=$?
  echo "[$(date)] PASS C' exit=$rcC"

  # Only mark done + self-disable if BOTH passes wrote output with 0 errors.
  ok=$("$PY" - <<PYEOF
import json,sys
def errs(p):
    try: return json.load(open(p)).get("error_count",999)
    except Exception: return 999
a=errs("results/upg3_PRO_A_rerankoff.json")
c=errs("results/upg3_PRO_C_rerank_pool50.json")
print("OK" if (a==0 and c==0) else f"NOTYET a_err={a} c_err={c}")
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
  echo "[$(date)] PRO postable run finished"
} >>"$LOG" 2>&1
