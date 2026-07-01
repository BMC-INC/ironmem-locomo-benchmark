#!/usr/bin/env bash
set -euo pipefail

# Create a GPU VM that builds/runs IronMem + LoCoMo from artifacts packaged by
# gcp_gpu_locomo_pack.sh. Defaults to a single NVIDIA L4 in us-west1-a.

PROJECT="${PROJECT:-queueflow-sentinel}"
BUCKET="${BUCKET:-queueflow-sentinel-benchmarks}"
RUN_ID="${RUN_ID:?Set RUN_ID from gcp_gpu_locomo_pack.sh output}"
safe_run_id="$(echo "$RUN_ID" | tr '[:upper:]_' '[:lower:]-' | tr -cd 'a-z0-9-')"
INSTANCE="${INSTANCE:-locomo-gpu-${safe_run_id}}"
ZONE="${ZONE:-us-west1-a}"
GPU_KIND="${GPU_KIND:-l4}" # l4 | t4
BOOT_DISK_SIZE="${BOOT_DISK_SIZE:-250GB}"
STARTUP_SCRIPT="${STARTUP_SCRIPT:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/gcp_gpu_locomo_startup.sh}"
IMAGE_FAMILY="${IMAGE_FAMILY:-common-cu129-ubuntu-2204-nvidia-580}"
IMAGE_PROJECT="${IMAGE_PROJECT:-deeplearning-platform-release}"
MAX_RUN_DURATION="${MAX_RUN_DURATION:-12h}"

if [[ ! -f "$STARTUP_SCRIPT" ]]; then
  echo "Startup script not found: $STARTUP_SCRIPT" >&2
  exit 2
fi

case "$GPU_KIND" in
  l4)
    MACHINE_TYPE="${MACHINE_TYPE:-g2-standard-8}"
    ACCELERATOR_ARGS=()
    ;;
  t4)
    MACHINE_TYPE="${MACHINE_TYPE:-n1-standard-8}"
    ACCELERATOR_ARGS=(--accelerator="type=nvidia-tesla-t4,count=1")
    ;;
  *)
    echo "Unsupported GPU_KIND=$GPU_KIND (use l4 or t4)" >&2
    exit 2
    ;;
esac

echo "Creating $INSTANCE in $ZONE with GPU_KIND=$GPU_KIND MACHINE_TYPE=$MACHINE_TYPE"

cmd=(
gcloud compute instances create "$INSTANCE" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --machine-type="$MACHINE_TYPE" \
  --maintenance-policy=TERMINATE \
  --provisioning-model=STANDARD \
  --image-family="$IMAGE_FAMILY" \
  --image-project="$IMAGE_PROJECT" \
  --boot-disk-size="$BOOT_DISK_SIZE" \
  --boot-disk-type=pd-ssd \
  --scopes=https://www.googleapis.com/auth/cloud-platform \
  --metadata=install-nvidia-driver=True,bench-bucket="$BUCKET",bench-run-id="$RUN_ID" \
  --metadata-from-file=startup-script="$STARTUP_SCRIPT" \
  --max-run-duration="$MAX_RUN_DURATION" \
  --instance-termination-action=DELETE \
  --quiet
)
if ((${#ACCELERATOR_ARGS[@]})); then
  cmd+=("${ACCELERATOR_ARGS[@]}")
fi
"${cmd[@]}"

cat <<EOF
CREATED
INSTANCE=$INSTANCE
ZONE=$ZONE
PROJECT=$PROJECT
GPU_KIND=$GPU_KIND
RUN_ID=$RUN_ID

Watch startup:
  gcloud compute ssh $INSTANCE --zone=$ZONE --project=$PROJECT --command='sudo tail -f /var/log/locomo-gpu-startup.log'

Check status:
  gcloud compute ssh $INSTANCE --zone=$ZONE --project=$PROJECT --command='sudo test -f /var/log/locomo/READY && sudo cat /var/log/locomo/READY || sudo tail -n 80 /var/log/locomo-gpu-startup.log'
EOF
