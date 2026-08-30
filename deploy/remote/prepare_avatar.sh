#!/usr/bin/env bash
# 用 assets/avatars/interviewer_female_01_silence.mp4 生成 LiveTalking wav2lip 形象。
# 前置：远端已 setup_livetalking.sh，且 models/wav2lip.pth 存在。

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
VIDEO_LOCAL="${AVATAR_VIDEO:-${ROOT}/assets/avatars/interviewer_female_01_silence.mp4}"
AVATAR_ID="${AVATAR_ID:-interviewer_female_01}"
IMG_SIZE="${AVATAR_IMG_SIZE:-256}"
LIVETALKING_DIR="${LIVETALKING_DIR:-$(dirname "${REMOTE_DIR}")/LiveTalking}"
MODELS_LOCAL="${ROOT}/data/livetalking-models"

if [[ ! -f "${VIDEO_LOCAL}" ]]; then
  echo "缺少静音素材：${VIDEO_LOCAL}" >&2
  exit 1
fi

echo_cfg
echo "avatar_id=${AVATAR_ID} video=${VIDEO_LOCAL} livetalking=${LIVETALKING_DIR}"

# 同步权重（若本机已下好）
if [[ -f "${MODELS_LOCAL}/wav2lip.pth" ]]; then
  echo "同步 wav2lip.pth …"
  remote_ssh "mkdir -p ${LIVETALKING_DIR}/models"
  if [[ -n "${REMOTE_PASS:-}" ]]; then
    SSHPASS="${REMOTE_PASS}" sshpass -e rsync -az -e "ssh ${SSH_OPTS[*]}" \
      "${MODELS_LOCAL}/wav2lip.pth" \
      "${REMOTE_USER}@${REMOTE_HOST}:${LIVETALKING_DIR}/models/wav2lip.pth"
  else
    rsync -az -e "ssh ${SSH_OPTS[*]}" \
      "${MODELS_LOCAL}/wav2lip.pth" \
      "${REMOTE_USER}@${REMOTE_HOST}:${LIVETALKING_DIR}/models/wav2lip.pth"
  fi
elif [[ -f "${MODELS_LOCAL}/wav2lip256.pth" ]]; then
  echo "同步 wav2lip256.pth → wav2lip.pth …"
  remote_ssh "mkdir -p ${LIVETALKING_DIR}/models"
  if [[ -n "${REMOTE_PASS:-}" ]]; then
    SSHPASS="${REMOTE_PASS}" sshpass -e rsync -az -e "ssh ${SSH_OPTS[*]}" \
      "${MODELS_LOCAL}/wav2lip256.pth" \
      "${REMOTE_USER}@${REMOTE_HOST}:${LIVETALKING_DIR}/models/wav2lip.pth"
  else
    rsync -az -e "ssh ${SSH_OPTS[*]}" \
      "${MODELS_LOCAL}/wav2lip256.pth" \
      "${REMOTE_USER}@${REMOTE_HOST}:${LIVETALKING_DIR}/models/wav2lip.pth"
  fi
fi

# 同步静音视频
remote_ssh "mkdir -p ${LIVETALKING_DIR}/data/tmp ${LIVETALKING_DIR}/data/avatars"
REMOTE_VIDEO="${LIVETALKING_DIR}/data/tmp/${AVATAR_ID}_silence.mp4"
if [[ -n "${REMOTE_PASS:-}" ]]; then
  SSHPASS="${REMOTE_PASS}" sshpass -e rsync -az -e "ssh ${SSH_OPTS[*]}" \
    "${VIDEO_LOCAL}" "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_VIDEO}"
else
  rsync -az -e "ssh ${SSH_OPTS[*]}" \
    "${VIDEO_LOCAL}" "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_VIDEO}"
fi

remote_ssh bash -s <<EOF
set -euo pipefail
export PATH="\$HOME/miniconda3/bin:\$PATH"
# shellcheck disable=SC1091
source "\$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate livetalking
cd "${LIVETALKING_DIR}"
if [[ ! -f models/wav2lip.pth ]]; then
  echo "缺少 models/wav2lip.pth。请从夸克/Google Drive 下载 wav2lip256.pth 并改名为 wav2lip.pth" >&2
  exit 1
fi
# 生成形象（256 对应 wav2lip256 权重）
python -m avatars.wav2lip.genavatar \\
  --video_path "${REMOTE_VIDEO}" \\
  --avatar_id "${AVATAR_ID}" \\
  --img_size ${IMG_SIZE} \\
  --save_path ./data/avatars \\
  --pads 0 10 0 0
ls -la "data/avatars/${AVATAR_ID}" | head
echo "frames=\$(ls data/avatars/${AVATAR_ID}/full_imgs 2>/dev/null | wc -l)"
echo "Avatar 就绪：${AVATAR_ID}"
EOF
