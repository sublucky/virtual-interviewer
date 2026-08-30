#!/usr/bin/env bash
# 远端安装 Python venv、vllm-omni，并预拉 Qwen3-Omni 权重。
# 首次很慢；支持 HF_ENDPOINT 镜像与 huggingface_hub 断点续传。

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

OMNI_MODEL="${OMNI_MODEL:-Qwen/Qwen3-Omni-30B-A3B-Instruct}"
HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"

echo_cfg
echo "安装 vllm-omni 并拉取 ${OMNI_MODEL}（HF_ENDPOINT=${HF_ENDPOINT}）"

remote_ssh bash -s <<EOF
set -euo pipefail
cd "${REMOTE_DIR}"
mkdir -p logs models
export HF_ENDPOINT="${HF_ENDPOINT}"
export HF_HOME="\${HOME}/.cache/huggingface"
export OMNI_MODEL="${OMNI_MODEL}"

avail_gb=\$(df -P . | awk 'NR==2 {printf "%d", \$4/1024/1024}')
if [[ "\${avail_gb}" -lt 70 ]]; then
  echo "警告：可用磁盘约 \${avail_gb}GB，30B 权重可能不够，请先清理缓存" >&2
fi

# Ubuntu 系统 python3 常缺 python3-venv/ensurepip（且无免密 sudo）。
# 优先用已有的 Miniconda 创建可用的 .venv-omni。
PY=""
for cand in "\${HOME}/miniconda3/bin/python" "\${HOME}/anaconda3/bin/python" python3; do
  if command -v "\$cand" >/dev/null 2>&1 || [[ -x "\$cand" ]]; then
    if "\$cand" -c "import venv, ensurepip" >/dev/null 2>&1; then
      PY="\$cand"
      break
    fi
  fi
done
if [[ -z "\${PY}" ]]; then
  echo "找不到能创建 venv 的 Python（系统缺 python3-venv，请用 conda 或 apt install python3.10-venv）" >&2
  exit 1
fi
echo "使用 Python: \${PY} \$(\${PY} --version 2>&1)"

if [[ -d .venv-omni && ! -f .venv-omni/bin/activate ]]; then
  echo "检测到残缺 .venv-omni，重建"
  rm -rf .venv-omni
fi
if [[ ! -f .venv-omni/bin/activate ]]; then
  "\${PY}" -m venv .venv-omni
fi
# shellcheck disable=SC1091
source .venv-omni/bin/activate
python -c "import sys; print('venv', sys.prefix, sys.version)"
pip install -U pip wheel
pip install huggingface_hub
# vllm-omni 0.26 必须配对同版本 vllm；官方轮子默认 CUDA 13
pip install "vllm==0.26.0"
pip install "vllm-omni==0.26.0"

python - <<'PY'
import os
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
model = os.environ["OMNI_MODEL"]
print("预拉模型:", model)
try:
    from huggingface_hub import snapshot_download
    snapshot_download(repo_id=model)
    print("snapshot_download 完成")
except Exception as exc:
    print("snapshot_download 跳过/失败（启动时会再拉）:", exc)
PY
echo "setup_omni 完成"
EOF
