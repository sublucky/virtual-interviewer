#!/usr/bin/env bash
# 在远端以 root 执行：卸 550-server，装 580-server。
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

echo "=== 升级前 ==="
nvidia-smi | head -3 || true

apt-get update -y

mapfile -t old < <(dpkg -l | awk '/550-server/ && /nvidia|libnvidia|xserver-xorg-video-nvidia/ {print $2}')
if ((${#old[@]})); then
  echo "卸载: ${old[*]}"
  apt-get purge -y "${old[@]}"
fi
apt-get autoremove -y --purge

apt-get install -y \
  -o Dpkg::Options::="--force-confdef" \
  -o Dpkg::Options::="--force-confold" \
  nvidia-driver-580-server

echo "=== 已安装 ==="
dpkg -l 'nvidia-*-580-server' 2>/dev/null | awk '/^ii/{print $2, $3}'
echo INSTALL_DONE
