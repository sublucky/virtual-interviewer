#!/usr/bin/env bash
# 将远端 NVIDIA Server 驱动升到 580.x（CUDA 13 / torch cu130 所需，最低 580.65.06）。
# 会 apt 安装 nvidia-driver-580-server 并 reboot，不把密码打到日志。

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

INNER="${SCRIPT_DIR}/upgrade_nvidia_driver_inner.sh"
echo_cfg
echo "将安装 nvidia-driver-580-server 并重启远端（当前应为 550.x）"

remote_ssh "mkdir -p '${REMOTE_DIR}/deploy/remote'"
# 只同步这一份 inner 脚本
if [[ -n "${REMOTE_PASS:-}" ]]; then
  SSHPASS="${REMOTE_PASS}" sshpass -e scp -o StrictHostKeyChecking=accept-new -P "${REMOTE_PORT}" \
    "${INNER}" "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/deploy/remote/upgrade_nvidia_driver_inner.sh"
else
  scp -o StrictHostKeyChecking=accept-new -P "${REMOTE_PORT}" \
    "${INNER}" "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/deploy/remote/upgrade_nvidia_driver_inner.sh"
fi

remote_ssh "chmod +x '${REMOTE_DIR}/deploy/remote/upgrade_nvidia_driver_inner.sh' && printf '%s\n' '${REMOTE_PASS}' | sudo -S -p '' bash '${REMOTE_DIR}/deploy/remote/upgrade_nvidia_driver_inner.sh'"

echo "驱动包已装上，正在 reboot…"
remote_ssh "printf '%s\n' '${REMOTE_PASS}' | sudo -S -p '' reboot" || true
