#!/bin/bash
# Upgrade ablation matrix on the CURRENT coverage store (NO re-ingest — ingestion
# is unchanged). Isolates each lever so we can attribute the delta:
#   A baseline (pre-synthesis, levers off)         -> reference
#   --- run Track B synthesis (mutates the store) ---
#   B +synthesis                                   -> synthesis retrieval effect
#   C +synthesis +multi-query 3                    -> query expansion (multi-hop)
#   D +synthesis +router                           -> per-category routing
#   E +synthesis +retrieve-limit 25                -> top-k cut (recall curve lever)
#   --- enable B/#5 server levers (settings.json + restart) ---
#   F +synthesis +server-levers (temporal fusion + trust)
#   G +synthesis +server-levers +router +limit25   -> kitchen sink
# Flash answerer+judge (cheap, comparable to prior 54.5%), us-west1, --skip-ingest.
# Run AFTER deploying the new binary AND after a synthesis dry-run sanity check.
set -uo pipefail
REPO=/Users/kingjames/Projects/ironmem-locomo-benchmark
PY=$REPO/.venv/bin/python
LOG=$REPO/results/raw_console/upgrade_matrix.log
DB=$HOME/.ironmem/mem.db
SETTINGS=$HOME/.ironmem/settings.json
L=us-west1; C=8
M="--answerer-model gemini-2.5-flash --judge-model gemini-2.5-flash"
cd "$REPO" || exit 9

wait_server() {
  curl -s --retry 60 --retry-delay 2 --retry-connrefused -m 8 \
    http://localhost:37778/status >/dev/null 2>&1
}

score() { # $1=out-stem ; $2.. = extra flags
  local out="$1"; shift
  echo "[$(date)] SCORE $out : $*"
  "$PY" -m benchmark.run --strategy hybrid --skip-ingest --rerank --pool 50 \
    --concurrency $C --vertex-location "$L" $M "$@" --output "$out.json"
  echo "[$(date)] $out exit=$?"
}

{
  echo "=================================================================="
  echo "[$(date)] upgrade matrix starting"
  wait_server || { echo "server down — abort"; exit 3; }

  # (A baseline `upg6_A_baseline` is scored SEPARATELY before this pipeline,
  #  pre-synthesis, levers off — it's the reference for the deltas below.)

  # Track B synthesis (snapshot first — this MUTATES the store)
  echo "[$(date)] snapshot mem.db -> mem.db.bak-pre-synthesis"
  cp "$DB" "$DB.bak-pre-synthesis"
  echo "[$(date)] running synthesis --apply"
  "$PY" scripts/run_synthesis.py --apply
  echo "[$(date)] synthesis done"

  # B/C/D/E — synthesized store, harness levers (server levers still OFF)
  score upg6_B_synth                 --retrieve-limit 10
  score upg6_C_synth_mq3             --retrieve-limit 10 --multi-query 3
  score upg6_D_synth_route           --retrieve-limit 10 --route
  score upg6_E_synth_l25             --retrieve-limit 25

  # Enable B/#5 server levers: temporal-event fusion weight + temporal-trust.
  echo "[$(date)] enabling server levers (settings.json + restart)"
  cp "$SETTINGS" "$SETTINGS.bak-prelevers"
  "$PY" - "$SETTINGS" <<'PYEOF'
import json, sys
p = sys.argv[1]
cfg = json.load(open(p))
cfg["temporal_trust"] = {
    "weight": 0.05,
    "recency_halflife_days": 30,
    "ref_saturation": 5,
    "temporal_event_fusion_weight": 2,
}
json.dump(cfg, open(p, "w"), indent=2)
print("temporal_trust enabled:", cfg["temporal_trust"])
PYEOF
  launchctl kickstart -k "gui/$(id -u)/com.execlayer.ironmem"
  wait_server || { echo "server didn't come back after lever enable — abort"; exit 4; }
  echo "[$(date)] server back with levers on"

  score upg6_F_synth_levers          --retrieve-limit 10
  score upg6_G_synth_levers_route_l25 --retrieve-limit 25 --route

  # Funnel + fidelity on the synthesized + levers-on store (store-limit 2000)
  echo "[$(date)] funnel + fidelity on the best arm (G)"
  "$PY" scripts/funnel_probe.py --strategy hybrid --pool 50 --final-limit 25 \
    --store-limit 2000 --scored results/upg6_G_synth_levers_route_l25.json \
    --output funnel_upg6_G.json
  "$PY" scripts/fidelity_suite.py --scored results/upg6_G_synth_levers_route_l25.json \
    --strategy hybrid --pool 50 --final-limit 25 --store-limit 2000 \
    --output fidelity_upg6_G.json

  echo "[$(date)] upgrade matrix finished"
} >>"$LOG" 2>&1
