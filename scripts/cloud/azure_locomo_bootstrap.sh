#!/usr/bin/env bash
# Bootstrap IronMem + LoCoMo judge/benchmark stack on an Azure Ubuntu VM.
# Run as root (or via: curl | sudo bash) after scp of artifacts, OR with
# IRONMEM_TARBALL / BENCH_TARBALL already on the machine.
#
# Layout:
#   /opt/ironmem          IronMem source + release binary
#   /opt/bench            ironmem-locomo-benchmark
#   /root/.ironmem        mem.db + settings.json
#   systemd: ironmem.service  → REST :37778
#
# Env (optional):
#   BENCH_LLM=openai
#   AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_VERSION
#   OPENAI_ANSWERER_MODEL, OPENAI_JUDGE_MODEL (Azure deployment names)
#   AZURE_USE_MI=1 (default; system-assigned Managed Identity)
#   VERTEX_PROJECT, VERTEX_LOCATION (when BENCH_LLM=vertex)
#   IRONMEM_FEATURES   (default: empty; use "gpu" if NVIDIA present)

set -euo pipefail
exec > >(tee -a /var/log/azure-locomo-bootstrap.log) 2>&1

IRONMEM_DIR=/opt/ironmem
BENCH_DIR=/opt/bench
TARGET_DIR=/opt/ironmem-target
FEATURES="${IRONMEM_FEATURES:-}"
export DEBIAN_FRONTEND=noninteractive
export HOME=/root

echo "=== Azure LoCoMo bootstrap $(date -Is) ==="

apt-get update
apt-get install -y \
  build-essential ca-certificates curl git jq \
  libssl-dev pkg-config python3-dev python3-pip python3-venv \
  sqlite3 unzip

if ! command -v cargo >/dev/null 2>&1; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
fi
# shellcheck disable=SC1091
source /root/.cargo/env
rustup default stable

mkdir -p "$IRONMEM_DIR" "$BENCH_DIR" "$TARGET_DIR" /root/.ironmem /var/log/locomo

# Prefer pre-uploaded tarballs
if [[ -f /tmp/ironmem-src.tar.gz ]]; then
  tar -xzf /tmp/ironmem-src.tar.gz -C "$IRONMEM_DIR" --strip-components=1
fi
if [[ -f /tmp/bench-src.tar.gz ]]; then
  tar -xzf /tmp/bench-src.tar.gz -C "$BENCH_DIR" --strip-components=1
fi
if [[ -f /tmp/mem.db ]]; then
  cp /tmp/mem.db /root/.ironmem/mem.db
fi
if [[ -f /tmp/settings.json ]]; then
  cp /tmp/settings.json /root/.ironmem/settings.json
else
  echo '{}' >/root/.ironmem/settings.json
fi

# Build IronMem
if [[ -f "$IRONMEM_DIR/Cargo.toml" ]]; then
  cd "$IRONMEM_DIR"
  if [[ -n "$FEATURES" ]]; then
    CARGO_TARGET_DIR="$TARGET_DIR" cargo build --release --features "$FEATURES"
  else
    CARGO_TARGET_DIR="$TARGET_DIR" cargo build --release
  fi
  install -m 0755 "$TARGET_DIR/release/ironmem" /usr/local/bin/ironmem
  /usr/local/bin/ironmem --version || true
fi

# Settings for local server
tmp_settings="$(mktemp)"
jq --arg proj "${VERTEX_PROJECT:-}" --arg loc "${VERTEX_LOCATION:-us-west1}" '
  .db_path = "/root/.ironmem/mem.db" |
  .database_url = "sqlite:///root/.ironmem/mem.db?mode=rwc" |
  (if $proj != "" then .provider = "vertex" | .vertex_project = $proj | .vertex_location = $loc else . end)
' /root/.ironmem/settings.json >"$tmp_settings"
mv "$tmp_settings" /root/.ironmem/settings.json

# systemd unit for REST server (benchmark needs `server`, not MCP-only `serve`)
cat >/etc/systemd/system/ironmem.service <<'EOF'
[Unit]
Description=IronMem REST server for LoCoMo / judge benchmarks
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Environment=HOME=/root
Environment=DATABASE_URL=sqlite:///root/.ironmem/mem.db?mode=rwc
WorkingDirectory=/root
ExecStart=/usr/local/bin/ironmem server
Restart=always
RestartSec=3
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable ironmem.service
systemctl restart ironmem.service || true

# Benchmark venv
cd "$BENCH_DIR"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip wheel
if [[ -f requirements.txt ]]; then
  .venv/bin/pip install -r requirements.txt
else
  .venv/bin/pip install google-genai httpx rich python-dotenv
fi

# Helper scripts
cat >/usr/local/bin/locomo-health <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "ironmem.service: $(systemctl is-active ironmem.service || true)"
curl -sS -m 5 http://127.0.0.1:37778/status || curl -sS -m 5 -X POST http://127.0.0.1:37778/context \
  -H 'content-type: application/json' \
  -d '{"project":"/benchmark/locomo/probe","query":"x","limit":1}' || true
echo
EOF
chmod +x /usr/local/bin/locomo-health

cat >/usr/local/bin/locomo-canary <<'EOF'
#!/usr/bin/env bash
# Quick canary: hybrid skip-ingest, low concurrency — smoke before full run
set -euo pipefail
cd /opt/bench
OUT="${1:-canary_azure_$(date -u +%Y%m%dT%H%M%SZ).json}"
if ! systemctl is-active --quiet ironmem.service; then
  echo "ironmem.service not active" >&2
  exit 3
fi
.venv/bin/python -m benchmark.run --strategy hybrid --skip-ingest \
  --pool 100 --retrieve-limit 25 --concurrency 4 \
  --answer-prompt v2 \
  --output "results/$OUT"
echo "Wrote results/$OUT"
EOF
chmod +x /usr/local/bin/locomo-canary

cat >/usr/local/bin/locomo-headline <<'EOF'
#!/usr/bin/env bash
# Full fixed-model headline. Backend and deployment names come from the env.
set -euo pipefail
cd /opt/bench
OUT="${1:-headline_azure_$(date -u +%Y%m%dT%H%M%SZ).json}"
C="${CONCURRENCY:-8}"
ARGS=(.venv/bin/python -m benchmark.run --strategy hybrid --skip-ingest \
  --pool 100 --retrieve-limit 25 --concurrency "$C" \
  --answer-prompt v2 --output "results/$OUT")
if [[ "${BENCH_LLM:-vertex}" == "vertex" ]]; then
  ARGS+=(--vertex-location "${VERTEX_LOCATION:-us-west1}")
fi
"${ARGS[@]}"
echo "Wrote results/$OUT"
EOF
chmod +x /usr/local/bin/locomo-headline

cat >/usr/local/bin/locomo-judge-agreement <<'EOF'
#!/usr/bin/env bash
# Second-judge Cohen's kappa on a prior results file
set -euo pipefail
cd /opt/bench
SRC="${1:?usage: locomo-judge-agreement results/foo.json [sample=200]}"
SAMPLE="${2:-200}"
OUT="results/judge_agreement_$(basename "$SRC" .json).json"
.venv/bin/python scripts/judge_agreement.py "$SRC" \
  --judge-model gemini-2.5-flash --sample "$SAMPLE" --output "$OUT"
echo "Wrote $OUT"
EOF
chmod +x /usr/local/bin/locomo-judge-agreement

# Wait for ironmem
for i in $(seq 1 60); do
  if curl -sf -m 2 http://127.0.0.1:37778/status >/dev/null 2>&1 \
    || curl -sf -m 2 -X POST http://127.0.0.1:37778/context \
         -H 'content-type: application/json' \
         -d '{"project":"/benchmark/locomo/probe","query":"x","limit":1}' >/dev/null 2>&1; then
    echo "IronMem REST is up"
    break
  fi
  sleep 2
done

systemctl status ironmem.service --no-pager || true
locomo-health || true
echo "=== bootstrap complete $(date -Is) ==="
echo "Next:"
echo "  locomo-canary"
echo "  locomo-headline"
echo "  locomo-judge-agreement results/<run>.json"
