#!/usr/bin/env bash
# 在远端安装/准备 LiveTalking 环境（不含网盘权重下载）。
# 权重：把 wav2lip.pth 放到 REMOTE LiveTalking/models/，
# 或先在本机 data/livetalking-models/ 下载后由 prepare_avatar.sh 同步。

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

LIVETALKING_DIR="${LIVETALKING_DIR:-${REMOTE_DIR}/../LiveTalking}"
# 若 REMOTE_DIR=/home/vipuser/virtual-interviewer → 默认 /home/vipuser/LiveTalking
if [[ "${LIVETALKING_DIR}" == *"virtual-interviewer/../LiveTalking" ]]; then
  LIVETALKING_DIR="$(dirname "${REMOTE_DIR}")/LiveTalking"
fi

echo_cfg
echo "livetalking_dir=${LIVETALKING_DIR}"

remote_ssh bash -s <<EOF
set -euo pipefail
export PATH="\$HOME/miniconda3/bin:\$PATH"
LDIR="${LIVETALKING_DIR}"
if [[ ! -d "\$LDIR/.git" ]]; then
  git clone --depth 1 https://github.com/lipku/LiveTalking.git "\$LDIR" \\
    || git clone --depth 1 https://gitee.com/lipku/LiveTalking.git "\$LDIR"
fi
cd "\$LDIR"
mkdir -p models data/avatars data/tmp logs

if ! conda env list | grep -q '^livetalking '; then
  conda create -y -n livetalking python=3.12
fi
# shellcheck disable=SC1091
source "\$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate livetalking

TORCH_OK=1
python -c "import torch" 2>/dev/null && TORCH_OK=0 || true
if [[ "\$TORCH_OK" -ne 0 ]]; then
  # A100 + 常见驱动：cu121/cu124 均可；失败再手动改
  pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
fi
pip install -r requirements.txt
python -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"
ls -la models/ || true
echo "LiveTalking 环境就绪：\$LDIR"
EOF
