#!/usr/bin/env bash
# 启动 LiveTalking（默认用 interviewer_female_01 形象 + wav2lip 口型）。
# 尽量避开 Omni 已占满的卡；失败时可改 ultralight。

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

LIVETALKING_PORT="${LIVETALKING_PORT:-8010}"
LIVETALKING_GPU="${LIVETALKING_GPU:-0}"
LIVETALKING_DIR="${LIVETALKING_DIR:-$(dirname "${REMOTE_DIR}")/LiveTalking}"
AVATAR_ID="${AVATAR_ID:-interviewer_female_01}"
AVATAR_MODEL="${AVATAR_MODEL:-wav2lip}"

echo_cfg
echo "livetalking_dir=${LIVETALKING_DIR} port=${LIVETALKING_PORT} gpu=${LIVETALKING_GPU}"
echo "avatar_id=${AVATAR_ID} model=${AVATAR_MODEL}"

remote_ssh bash -s <<EOF
set -euo pipefail
export PATH="\$HOME/miniconda3/bin:\$PATH"
if [[ ! -d "${LIVETALKING_DIR}" ]]; then
  echo "未找到 LiveTalking 目录 ${LIVETALKING_DIR}" >&2
  echo "请先运行 ./deploy/remote/setup_livetalking.sh" >&2
  exit 1
fi
cd "${LIVETALKING_DIR}"
if [[ ! -d "data/avatars/${AVATAR_ID}" ]]; then
  echo "缺少形象 data/avatars/${AVATAR_ID}，请先运行 ./deploy/remote/prepare_avatar.sh" >&2
  exit 1
fi
if [[ ! -f models/wav2lip.pth && "${AVATAR_MODEL}" == "wav2lip" ]]; then
  echo "缺少 models/wav2lip.pth" >&2
  exit 1
fi
mkdir -p logs
pkill -f "app.py --listenport ${LIVETALKING_PORT}" 2>/dev/null || pkill -f "app.py" 2>/dev/null || true
sleep 1
# shellcheck disable=SC1091
source "\$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate livetalking
export CUDA_VISIBLE_DEVICES=${LIVETALKING_GPU}
nohup python app.py \\
  --listenport ${LIVETALKING_PORT} \\
  --transport webrtc \\
  --model ${AVATAR_MODEL} \\
  --avatar_id ${AVATAR_ID} \\
  > logs/livetalking.log 2>&1 &
echo \$! > logs/livetalking.pid
sleep 2
echo "LiveTalking 已启动 :${LIVETALKING_PORT} pid=\$(cat logs/livetalking.pid) avatar=${AVATAR_ID}"
tail -n 20 logs/livetalking.log || true
EOF
