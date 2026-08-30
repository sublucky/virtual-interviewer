#!/usr/bin/env bash
# 加载 deploy/server.conf，提供 remote_ssh / remote_rsync 封装。
# 不把密码打印到日志。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONF="${ROOT}/deploy/server.conf"

if [[ ! -f "${CONF}" ]]; then
  echo "缺少 ${CONF}，请先 cp deploy/server.conf.example deploy/server.conf" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "${CONF}"

: "${REMOTE_HOST:?}"
: "${REMOTE_PORT:=22}"
: "${REMOTE_USER:?}"
: "${REMOTE_DIR:?}"

SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -p "${REMOTE_PORT}")

remote_ssh() {
  if [[ -n "${REMOTE_PASS:-}" ]]; then
    if ! command -v sshpass >/dev/null 2>&1; then
      echo "需要 sshpass：brew install sshpass / apt install sshpass" >&2
      exit 1
    fi
    SSHPASS="${REMOTE_PASS}" sshpass -e ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" "$@"
  else
    ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" "$@"
  fi
}

remote_rsync() {
  local src="$1" dst="$2"
  local rsync_rsh
  if [[ -n "${REMOTE_PASS:-}" ]]; then
    rsync_rsh="sshpass -e ssh ${SSH_OPTS[*]}"
    SSHPASS="${REMOTE_PASS}" rsync -az --delete \
      -e "${rsync_rsh}" \
      --exclude '.git' --exclude '.venv' --exclude 'web/node_modules' \
      --exclude 'data' --exclude 'web/dist' --exclude '.env' \
      --exclude 'deploy/server.conf' \
      "${src}" "${REMOTE_USER}@${REMOTE_HOST}:${dst}"
  else
    rsync -az --delete \
      -e "ssh ${SSH_OPTS[*]}" \
      --exclude '.git' --exclude '.venv' --exclude 'web/node_modules' \
      --exclude 'data' --exclude 'web/dist' --exclude '.env' \
      --exclude 'deploy/server.conf' \
      "${src}" "${REMOTE_USER}@${REMOTE_HOST}:${dst}"
  fi
}

echo_cfg() {
  echo "host=${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PORT} dir=${REMOTE_DIR}"
  echo "omni_port=${OMNI_PORT:-8091} model=${OMNI_MODEL:-Qwen/Qwen3-Omni-30B-A3B-Instruct}"
  echo "gpu thinker=${OMNI_GPU_THINKER:-0} talker=${OMNI_GPU_TALKER:-1}"
}
