#!/usr/bin/env bash
# 将仓库同步到远端 REMOTE_DIR（不含 .venv / data / node_modules / server.conf）。

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

echo_cfg
remote_ssh "mkdir -p '${REMOTE_DIR}'"
remote_rsync "${ROOT}/" "${REMOTE_DIR}/"
echo "synced -> ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}"
