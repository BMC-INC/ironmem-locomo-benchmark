#!/usr/bin/env bash
set -euo pipefail

# Package the exact local IronMem source, LoCoMo benchmark harness, and current
# LoCoMo IronMem store for a GPU VM. Nothing is deleted locally.

PROJECT="${PROJECT:-queueflow-sentinel}"
BUCKET="${BUCKET:-queueflow-sentinel-benchmarks}"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
IRONMEM_SRC="${IRONMEM_SRC:-/Users/kingjames/Projects/Iron-mem-fix}"
BENCH_SRC="${BENCH_SRC:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
IRONMEM_DB="${IRONMEM_DB:-$HOME/.ironmem/mem.db}"
IRONMEM_SETTINGS="${IRONMEM_SETTINGS:-$HOME/.ironmem/settings.json}"

if [[ ! -d "$IRONMEM_SRC" ]]; then
  echo "IronMem source not found: $IRONMEM_SRC" >&2
  exit 2
fi
if [[ ! -d "$BENCH_SRC" ]]; then
  echo "Benchmark source not found: $BENCH_SRC" >&2
  exit 2
fi
if [[ ! -f "$IRONMEM_DB" ]]; then
  echo "IronMem DB not found: $IRONMEM_DB" >&2
  exit 2
fi

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT

echo "Packaging IronMem from $IRONMEM_SRC"
tar \
  --exclude target \
  --exclude .git \
  --exclude '.DS_Store' \
  --exclude 'phase1_provider_DRAFT.patch' \
  -czf "$workdir/ironmem-src.tar.gz" \
  -C "$(dirname "$IRONMEM_SRC")" "$(basename "$IRONMEM_SRC")"

echo "Packaging benchmark from $BENCH_SRC"
tar \
  --exclude .git \
  --exclude .venv \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  --exclude '.DS_Store' \
  --exclude 'IRONMEM.md' \
  --exclude 'results/raw_console/*.pid' \
  -czf "$workdir/bench-src.tar.gz" \
  -C "$(dirname "$BENCH_SRC")" "$(basename "$BENCH_SRC")"

gcloud storage buckets describe "gs://$BUCKET" --project "$PROJECT" >/dev/null 2>&1 || \
  gcloud storage buckets create "gs://$BUCKET" --project "$PROJECT" --location us-west1

prefix="gs://$BUCKET/gpu-locomo/$RUN_ID"
echo "Uploading artifacts to $prefix"
gcloud storage cp "$workdir/ironmem-src.tar.gz" "$prefix/ironmem-src.tar.gz"
gcloud storage cp "$workdir/bench-src.tar.gz" "$prefix/bench-src.tar.gz"
gcloud storage cp "$IRONMEM_DB" "$prefix/mem.db"
if [[ -f "$IRONMEM_SETTINGS" ]]; then
  gcloud storage cp "$IRONMEM_SETTINGS" "$prefix/settings.json"
fi

cat <<EOF
PACKED
PROJECT=$PROJECT
BUCKET=$BUCKET
RUN_ID=$RUN_ID
GCS_PREFIX=$prefix

Next:
  PROJECT=$PROJECT BUCKET=$BUCKET RUN_ID=$RUN_ID bash scripts/cloud/gcp_gpu_locomo_create.sh
EOF
