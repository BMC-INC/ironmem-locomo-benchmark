#!/bin/bash
# One-shot off-peak Pro-judged HEADLINE run at the 2026-06-26 winning config:
#   pool=100, retrieve-limit=25, rerank ON.  (See RUN_NOTE_2026-06-26_coverage_2x2.md —
#   Flash D arm = 63.25%, +2.35 past the old Pro 60.9% headline.)
# Pro answerer + Pro judge are the harness DEFAULTS, so no model flags are passed.
# NEW vs run_pro_postable.sh: an ADC precheck (this config's Flash run died 2026-06-26 with
# 1986/1986 RefreshError because Vertex ADC had silently expired — never again silently).
# Self-disables after one clean success via a done-marker so it never re-runs.
set -uo pipefail
REPO=/Users/kingjames/Projects/ironmem-locomo-benchmark
PY=$REPO/.venv/bin/python
MARK=$HOME/.ironmem/.pro_headline_p100l25_done
PLIST=$HOME/Library/LaunchAgents/com.execlayer.locomo-pro-headline.plist
LOG=$REPO/results/raw_console/pro_headline_p100l25_console.log
OUT=upg7_PRO_p100l25.json
C=8
cd "$REPO" || exit 9

[ -f "$MARK" ] && { echo "[$(date)] already done ($MARK) — skipping" >>"$LOG"; exit 0; }

{
  echo "=================================================================="
  echo "[$(date)] PRO headline run starting (pool100 / retrieve-limit25)"

  # (1) ADC precheck — fail LOUD, do not spend a Pro run on expired creds.
  if ! gcloud auth application-default print-access-token >/dev/null 2>&1; then
    echo "[$(date)] Vertex ADC EXPIRED — run 'gcloud auth application-default login' then re-kick."
    echo "[$(date)] aborting, staying armed (exit 4)"
    exit 4
  fi
  echo "[$(date)] ADC ok"

  # (2) IronMem must be answering before we spend Pro tokens.
  if ! curl -s -m 8 http://localhost:37778/context -X POST \
        -H 'content-type: application/json' \
        -d '{"project":"/benchmark/locomo/probe","query":"x","limit":1}' >/dev/null 2>&1; then
    echo "[$(date)] IronMem server not responding on :37778 — aborting (retry next fire)"
    exit 3
  fi

  # (3) Pro capacity gate (DSQ throttling) — pick a region that passes a burst probe, else skip.
  REGION=$("$PY" "$REPO/scripts/pick_pro_region.py" 2>/dev/null || echo NONE)
  echo "[$(date)] Pro capacity check -> region: $REGION"
  if [ "$REGION" = "NONE" ] || [ -z "$REGION" ]; then
    echo "[$(date)] Pro still capacity-throttled — skipping, staying armed for next trigger"
    exit 0
  fi

  echo "[$(date)] HEADLINE — Pro answerer+judge, rerank ON, pool 100, retrieve-limit 25"
  "$PY" -m benchmark.run --strategy hybrid --skip-ingest --rerank \
      --pool 100 --retrieve-limit 25 --concurrency $C \
      --vertex-location "$REGION" --output "$OUT"
  rc=$?
  echo "[$(date)] HEADLINE exit=$rc"

  ok=$("$PY" - "$OUT" <<'PYEOF'
import json,sys
try:
    d=json.load(open("results/"+sys.argv[1]))
    e=d.get("error_count",999); ov=(d.get("results") or {}).get("overall")
    print(f"OK overall={ov}" if e==0 else f"NOTYET err={e}")
except Exception as ex:
    print("NOTYET unreadable:", ex)
PYEOF
)
  echo "[$(date)] result check: $ok"
  if [[ "$ok" == OK* ]]; then
    touch "$MARK"
    rm -f "$PLIST" 2>/dev/null
    echo "[$(date)] SUCCESS ($ok) — marker set, launchd plist removed (one-shot complete)"
  else
    echo "[$(date)] not clean — leaving job armed to retry on next fire"
  fi
  echo "[$(date)] PRO headline run finished"
} >>"$LOG" 2>&1
