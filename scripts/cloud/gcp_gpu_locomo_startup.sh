#!/usr/bin/env bash
set -euo pipefail

exec > >(tee -a /var/log/locomo-gpu-startup.log) 2>&1

metadata() {
  curl -fsS -H "Metadata-Flavor: Google" \
    "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1"
}

BUCKET="$(metadata bench-bucket)"
RUN_ID="$(metadata bench-run-id)"
GCS_PREFIX="gs://${BUCKET}/gpu-locomo/${RUN_ID}"
IRONMEM_DIR="/opt/ironmem"
BENCH_DIR="/opt/bench"
TARGET_DIR="/opt/ironmem-target"

echo "=== LoCoMo GPU startup: $(date -Is) ==="
echo "GCS_PREFIX=${GCS_PREFIX}"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  build-essential \
  ca-certificates \
  curl \
  git \
  jq \
  libssl-dev \
  pkg-config \
  python3-dev \
  python3-pip \
  python3-venv \
  sqlite3

if ! command -v cargo >/dev/null 2>&1; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | \
    sh -s -- -y --profile minimal
fi
export HOME=/root
source /root/.cargo/env
rustup default stable

mkdir -p "$IRONMEM_DIR" "$BENCH_DIR" "$TARGET_DIR" /root/.ironmem /var/log/locomo

echo "Copying packaged artifacts"
gcloud storage cp "${GCS_PREFIX}/ironmem-src.tar.gz" /tmp/ironmem-src.tar.gz
gcloud storage cp "${GCS_PREFIX}/bench-src.tar.gz" /tmp/bench-src.tar.gz
gcloud storage cp "${GCS_PREFIX}/mem.db" /root/.ironmem/mem.db
if gcloud storage cp "${GCS_PREFIX}/settings.json" /root/.ironmem/settings.json; then
  echo "Copied uploaded settings.json"
else
  echo "{}" >/root/.ironmem/settings.json
fi

tar -xzf /tmp/ironmem-src.tar.gz -C "$IRONMEM_DIR" --strip-components=1
tar -xzf /tmp/bench-src.tar.gz -C "$BENCH_DIR" --strip-components=1

tmp_settings="$(mktemp)"
jq '
  .rerank.enabled = false |
  .rerank.model = "" |
  .rerank.pool = 100 |
  .rerank.backend = "cross_encoder" |
  .rerank.cross_encoder_model = "bge-reranker-v2-m3" |
  .rerank.cross_encoder_max_candidates = 100 |
  .provider = "vertex" |
  .vertex_project = "queueflow-sentinel" |
  .vertex_location = "us-west1" |
  .db_path = "/root/.ironmem/mem.db" |
  .database_url = "sqlite:///root/.ironmem/mem.db?mode=rwc"
' /root/.ironmem/settings.json >"$tmp_settings"
mv "$tmp_settings" /root/.ironmem/settings.json

echo "GPU status before build"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi || true
else
  echo "nvidia-smi not found yet; Deep Learning VM images normally install it before startup completes"
fi

echo "Building IronMem with GPU feature"
cd "$IRONMEM_DIR"
CARGO_TARGET_DIR="$TARGET_DIR" cargo build --release --features gpu

ORT_LIB_DIR="$(dirname "$(find /root/.cache/ort.pyke.io -name libonnxruntime.so -print -quit)")"
if [[ -z "$ORT_LIB_DIR" || ! -f "$ORT_LIB_DIR/libonnxruntime.so" ]]; then
  echo "Could not locate libonnxruntime.so under /root/.cache/ort.pyke.io" >&2
  exit 1
fi
echo "$ORT_LIB_DIR" >/etc/ld.so.conf.d/onnxruntime.conf
ldconfig
export LD_LIBRARY_PATH="${ORT_LIB_DIR}:${LD_LIBRARY_PATH:-}"

install -m 0755 "$TARGET_DIR/release/ironmem" /usr/local/bin/ironmem
/usr/local/bin/ironmem --version

echo "Preparing benchmark venv"
cd "$BENCH_DIR"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip wheel
if [[ -f requirements.txt ]]; then
  .venv/bin/pip install -r requirements.txt
else
  .venv/bin/pip install google-genai httpx rich python-dotenv
fi

cat >/etc/systemd/system/ironmem.service <<EOF
[Unit]
Description=IronMem local server for LoCoMo GPU benchmark
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=HOME=/root
Environment=DATABASE_URL=sqlite:///root/.ironmem/mem.db?mode=rwc
Environment=IRONMEM_RERANK_BACKEND=cross_encoder
Environment=IRONMEM_RERANK_CROSS_ENCODER_MODEL=bge-reranker-v2-m3
Environment=LD_LIBRARY_PATH=${ORT_LIB_DIR}
WorkingDirectory=/opt/ironmem
ExecStart=/usr/local/bin/ironmem server
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/ironmem.log
StandardError=append:/var/log/ironmem.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now ironmem

echo "Waiting for IronMem /status"
for _ in $(seq 1 120); do
  if curl -fsS http://127.0.0.1:37778/status >/tmp/ironmem-status.json; then
    cat /tmp/ironmem-status.json | jq .
    echo "READY $(date -Is)" >/var/log/locomo/READY
    exit 0
  fi
  sleep 5
done

echo "IronMem did not become healthy" >&2
journalctl -u ironmem --no-pager -n 200 || true
exit 1
