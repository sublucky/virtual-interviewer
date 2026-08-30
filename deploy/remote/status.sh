#!/usr/bin/env bash
# nvidia-smi + Omni / LiveTalking 端口探活。

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

OMNI_PORT="${OMNI_PORT:-8091}"
LIVETALKING_PORT="${LIVETALKING_PORT:-8010}"

remote_ssh bash -s <<EOF
set -euo pipefail
echo "=== nvidia-smi ==="
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=index,name,memory.total,memory.used,utilization.gpu --format=csv
else
  echo "nvidia-smi 不可用"
fi
echo
echo "=== 端口 ==="
for port in ${OMNI_PORT} ${LIVETALKING_PORT} ${APP_PORT:-8090} ${VLLM_PORT:-8000}; do
  if curl -sf -m 2 "http://127.0.0.1:\${port}/v1/models" >/dev/null 2>&1; then
    echo ":\${port}  /v1/models OK"
  elif curl -sf -m 2 -X POST "http://127.0.0.1:\${port}/is_speaking" -H 'Content-Type: application/json' -d '{"sessionid":"health"}' >/dev/null 2>&1; then
    echo ":\${port}  livetalking OK"
  elif ss -lnt 2>/dev/null | grep -q ":\${port} "; then
    echo ":\${port}  listening"
  else
    echo ":\${port}  down"
  fi
done
echo
echo "=== omni 进程 ==="
pgrep -af "vllm" || echo "(无 vllm 进程)"
EOF
