#!/usr/bin/env bash
# 启动 Qwen3-Omni（Realtime 必须 --no-async-chunk）。
# 单卡：统一进程；多卡：stage0 Thinker+API / stage1 Talker / stage2 Code2Wav。

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

OMNI_PORT="${OMNI_PORT:-8091}"
OMNI_MODEL="${OMNI_MODEL:-Qwen/Qwen3-Omni-30B-A3B-Instruct}"
OMNI_GPU_THINKER="${OMNI_GPU_THINKER:-0}"
OMNI_GPU_TALKER="${OMNI_GPU_TALKER:-1}"
OMNI_MASTER_PORT="${OMNI_MASTER_PORT:-26000}"

echo_cfg

remote_ssh bash -s <<EOF
set -euo pipefail
cd "${REMOTE_DIR}"
mkdir -p logs
if [[ ! -f .venv-omni/bin/activate ]]; then
  echo "先运行 deploy/remote/setup_omni.sh" >&2
  exit 1
fi
# shellcheck disable=SC1091
source .venv-omni/bin/activate

pkill -f ".venv-omni/bin/vllm" 2>/dev/null || true
sleep 2

export VLLM_WORKER_MULTIPROC_METHOD=spawn
export HF_HOME="\${HOME}/.cache/huggingface"
export HF_HUB_DISABLE_XET=1
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
GPU_COUNT=\$(nvidia-smi -L 2>/dev/null | wc -l | tr -d ' ')
echo "检测到 GPU 数量: \${GPU_COUNT}"

if [[ "\${GPU_COUNT}" -eq 2 ]]; then
  echo "双卡分进程：先 Thinker TP=2，加载后再起 Talker/Code2Wav"
  CFG="${REMOTE_DIR}/deploy/remote/qwen3_omni_2x40gb.yaml"
  CUDA_VISIBLE_DEVICES=0,1 nohup vllm serve "${OMNI_MODEL}" \\
    --omni --no-async-chunk --stage-id 0 \\
    --port ${OMNI_PORT} --host 0.0.0.0 \\
    --omni-master-address 127.0.0.1 --omni-master-port ${OMNI_MASTER_PORT} \\
    --deploy-config "\${CFG}" \\
    > logs/omni-stage0.log 2>&1 &
  echo \$! > logs/omni-stage0.pid
  echo "等待 Thinker 权重加载完成..."
  loaded=0
  for i in \$(seq 1 90); do
    if grep -E "Available KV cache memory: [1-9]" logs/omni-stage0.log >/dev/null 2>&1; then
      used0=\$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i 0 | tr -d ' ')
      used1=\$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i 1 | tr -d ' ')
      echo "Thinker 权重 100% used0=\${used0}MiB used1=\${used1}MiB"
      loaded=1
      break
    fi
    if ! kill -0 "\$(cat logs/omni-stage0.pid)" 2>/dev/null; then
      echo "stage0 进程退出" >&2
      tail -n 40 logs/omni-stage0.log >&2 || true
      exit 1
    fi
    sleep 10
  done
  if [[ "\${loaded}" != "1" ]]; then
    echo "Thinker 加载超时，见 logs/omni-stage0.log" >&2
    tail -n 40 logs/omni-stage0.log >&2 || true
    exit 1
  fi
  CUDA_VISIBLE_DEVICES=1 nohup vllm serve "${OMNI_MODEL}" \\
    --omni --no-async-chunk --stage-id 1 --headless \\
    --omni-master-address 127.0.0.1 --omni-master-port ${OMNI_MASTER_PORT} \\
    --deploy-config "\${CFG}" \\
    > logs/omni-stage1.log 2>&1 &
  echo \$! > logs/omni-stage1.pid
  CUDA_VISIBLE_DEVICES=0 nohup vllm serve "${OMNI_MODEL}" \\
    --omni --no-async-chunk --stage-id 2 --headless \\
    --omni-master-address 127.0.0.1 --omni-master-port ${OMNI_MASTER_PORT} \\
    --deploy-config "\${CFG}" \\
    > logs/omni-stage2.log 2>&1 &
  echo \$! > logs/omni-stage2.pid
elif [[ "\${GPU_COUNT}" -lt 2 ]]; then
  echo "单卡统一启动（Thinker+Talker+Code2Wav 同卡，见 qwen3_omni_1gpu.yaml）:${OMNI_PORT}"
  CUDA_VISIBLE_DEVICES=${OMNI_GPU_THINKER} nohup vllm serve "${OMNI_MODEL}" \\
    --omni --no-async-chunk \\
    --port ${OMNI_PORT} --host 0.0.0.0 \\
    --deploy-config "${REMOTE_DIR}/deploy/remote/qwen3_omni_1gpu.yaml" \\
    > logs/omni-unified.log 2>&1 &
  echo \$! > logs/omni-unified.pid
else
  echo "分 stage 启动 thinker=${OMNI_GPU_THINKER} talker=${OMNI_GPU_TALKER}"
  CUDA_VISIBLE_DEVICES=${OMNI_GPU_THINKER} nohup vllm serve "${OMNI_MODEL}" \\
    --omni --no-async-chunk --stage-id 0 \\
    --port ${OMNI_PORT} --host 0.0.0.0 \\
    --omni-master-address 127.0.0.1 --omni-master-port ${OMNI_MASTER_PORT} \\
    > logs/omni-stage0.log 2>&1 &
  echo \$! > logs/omni-stage0.pid

  CUDA_VISIBLE_DEVICES=${OMNI_GPU_TALKER} nohup vllm serve "${OMNI_MODEL}" \\
    --omni --no-async-chunk --stage-id 1 --headless \\
    --omni-master-address 127.0.0.1 --omni-master-port ${OMNI_MASTER_PORT} \\
    > logs/omni-stage1.log 2>&1 &
  echo \$! > logs/omni-stage1.pid

  CUDA_VISIBLE_DEVICES=${OMNI_GPU_TALKER} nohup vllm serve "${OMNI_MODEL}" \\
    --omni --no-async-chunk --stage-id 2 --headless \\
    --omni-master-address 127.0.0.1 --omni-master-port ${OMNI_MASTER_PORT} \\
    > logs/omni-stage2.log 2>&1 &
  echo \$! > logs/omni-stage2.pid
fi

echo "已启动，等待 http://127.0.0.1:${OMNI_PORT}/v1/models ..."
ok=0
for i in \$(seq 1 90); do
  if python - <<'PY'
import urllib.request, sys
try:
    urllib.request.urlopen("http://127.0.0.1:${OMNI_PORT}/v1/models", timeout=3)
    sys.exit(0)
except Exception:
    sys.exit(1)
PY
  then
    echo "Omni 健康检查通过 :${OMNI_PORT}"
    ok=1
    break
  fi
  sleep 10
done
if [[ "\$ok" != "1" ]]; then
  echo "健康检查超时，见 logs/omni-*.log" >&2
  tail -n 50 logs/omni-unified.log logs/omni-stage0.log 2>/dev/null || true
  exit 1
fi
EOF
