#!/usr/bin/env bash
# 启动 LiveTalking。尽量避开 Omni 已占满的卡；失败时可改 ultralight。

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

LIVETALKING_PORT="${LIVETALKING_PORT:-8010}"
LIVETALKING_GPU="${LIVETALKING_GPU:-0}"
LIVETALKING_DIR="${LIVETALKING_DIR:-${REMOTE_DIR}/../LiveTalking}"

echo_cfg
echo "livetalking_dir=${LIVETALKING_DIR} port=${LIVETALKING_PORT} gpu=${LIVETALKING_GPU}"

remote_ssh bash -s <<EOF
set -euo pipefail
if [[ ! -d "${LIVETALKING_DIR}" ]]; then
  echo "未找到 LiveTalking 目录 ${LIVETALKING_DIR}" >&2
  echo "请先 clone https://github.com/lipku/LiveTalking 或设置 LIVETALKING_DIR" >&2
  exit 1
fi
cd "${LIVETALKING_DIR}"
mkdir -p logs
pkill -f "app.py" 2>/dev/null || true
sleep 1
export CUDA_VISIBLE_DEVICES=${LIVETALKING_GPU}
nohup python app.py --listenport ${LIVETALKING_PORT} --transport webrtc \\
  > logs/livetalking.log 2>&1 &
echo \$! > logs/livetalking.pid
echo "LiveTalking 已启动 :${LIVETALKING_PORT} pid=\$(cat logs/livetalking.pid)"
EOF
