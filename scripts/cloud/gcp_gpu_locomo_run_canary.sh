#!/usr/bin/env bash
set -euo pipefail

# Launch the CE+hints LoCoMo canary on the GPU VM. The run is detached on the VM,
# writes logs locally there, and uploads result artifacts to GCS when complete.

PROJECT="${PROJECT:-queueflow-sentinel}"
BUCKET="${BUCKET:-queueflow-sentinel-benchmarks}"
RUN_ID="${RUN_ID:?Set RUN_ID from gcp_gpu_locomo_pack.sh output}"
INSTANCE="${INSTANCE:-locomo-gpu-${RUN_ID//[^a-zA-Z0-9-]/-}}"
ZONE="${ZONE:-us-west1-a}"
DATASET="${DATASET:-data/canary_lost_gained_ce_sample12_upg8_vs_upg11.json}"
OUTPUT="${OUTPUT:-canary_lost_gained_ce_sample12_gpu_p100_k25_v2agg_supphints4_${RUN_ID}.json}"
CONCURRENCY="${CONCURRENCY:-4}"
SUPPLEMENT_MULTI_QUERY="${SUPPLEMENT_MULTI_QUERY:-1}"
SUPPLEMENT_LIMIT="${SUPPLEMENT_LIMIT:-4}"
SUPPLEMENT_HINTS_ONLY="${SUPPLEMENT_HINTS_ONLY:-1}"
EPISODIC_RECONSTRUCT="${EPISODIC_RECONSTRUCT:-0}"
EPISODIC_EPISODE_LIMIT="${EPISODIC_EPISODE_LIMIT:-10}"
EPISODIC_MAX_ORIGINAL_CHARS="${EPISODIC_MAX_ORIGINAL_CHARS:-2500}"

remote_cmd=$(cat <<'EOF'
set -euo pipefail
if [[ ! -f /var/log/locomo/READY ]]; then
  echo "IronMem is not marked READY yet." >&2
  sudo tail -n 120 /var/log/locomo-gpu-startup.log || true
  exit 2
fi
cd /opt/bench
mkdir -p results/raw_console
status="$(curl -fsS http://127.0.0.1:37778/status)"
echo "$status" | jq .
if [[ "$(echo "$status" | jq -r '.rerank.backend')" != "cross_encoder" ]] || \
   [[ "$(echo "$status" | jq -r '.rerank.cross_encoder_ready')" != "true" ]]; then
  echo "cross_encoder not ready" >&2
  exit 2
fi
nohup bash -lc '
  set -euo pipefail
  cd /opt/bench
  source .venv/bin/activate
  supplement_flags=(
    --supplement-multi-query "$SUPPLEMENT_MULTI_QUERY"
    --supplement-limit "$SUPPLEMENT_LIMIT"
  )
  if [[ "$SUPPLEMENT_HINTS_ONLY" == "1" || "$SUPPLEMENT_HINTS_ONLY" == "true" ]]; then
    supplement_flags+=(--supplement-hints-only)
  fi
  episodic_flags=()
  if [[ "$EPISODIC_RECONSTRUCT" == "1" || "$EPISODIC_RECONSTRUCT" == "true" ]]; then
    episodic_flags+=(
      --episodic-reconstruct
      --episodic-episode-limit "$EPISODIC_EPISODE_LIMIT"
      --episodic-max-original-chars "$EPISODIC_MAX_ORIGINAL_CHARS"
    )
  fi
  PYTHONUNBUFFERED=1 python -m benchmark.run \
    --strategy hybrid --skip-ingest --rerank \
    --pool 100 --retrieve-limit 25 --concurrency "$CONCURRENCY" \
    --answer-prompt v2 --synthesize --route \
    "${supplement_flags[@]}" \
    "${episodic_flags[@]}" \
    --require-cross-encoder --vertex-location us-west1 \
    --data "$DATASET" \
    --output "results/$OUTPUT" \
    2>&1 | tee -a "results/raw_console/${OUTPUT%.json}_console.log"
  gcloud storage cp "results/$OUTPUT" "gs://$BUCKET/gpu-locomo/$RUN_ID/results/$OUTPUT"
  gcloud storage cp "results/raw_console/${OUTPUT%.json}_console.log" "gs://$BUCKET/gpu-locomo/$RUN_ID/results/${OUTPUT%.json}_console.log"
' >"results/raw_console/${OUTPUT%.json}_launcher.log" 2>&1 &
echo $! | sudo tee "/var/log/locomo/${OUTPUT%.json}.pid"
EOF
)

gcloud compute ssh "$INSTANCE" \
  --zone="$ZONE" \
  --project="$PROJECT" \
  --command="BUCKET='$BUCKET' RUN_ID='$RUN_ID' DATASET='$DATASET' OUTPUT='$OUTPUT' CONCURRENCY='$CONCURRENCY' SUPPLEMENT_MULTI_QUERY='$SUPPLEMENT_MULTI_QUERY' SUPPLEMENT_LIMIT='$SUPPLEMENT_LIMIT' SUPPLEMENT_HINTS_ONLY='$SUPPLEMENT_HINTS_ONLY' EPISODIC_RECONSTRUCT='$EPISODIC_RECONSTRUCT' EPISODIC_EPISODE_LIMIT='$EPISODIC_EPISODE_LIMIT' EPISODIC_MAX_ORIGINAL_CHARS='$EPISODIC_MAX_ORIGINAL_CHARS' bash -lc $(printf '%q' "$remote_cmd")"

cat <<EOF
STARTED
INSTANCE=$INSTANCE
ZONE=$ZONE
OUTPUT=$OUTPUT

Watch:
  gcloud compute ssh $INSTANCE --zone=$ZONE --project=$PROJECT --command='sudo tail -f /opt/bench/results/raw_console/${OUTPUT%.json}_console.log'

Download after completion:
  gcloud storage cp gs://$BUCKET/gpu-locomo/$RUN_ID/results/$OUTPUT results/
EOF
