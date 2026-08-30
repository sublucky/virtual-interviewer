#!/usr/bin/env bash
# 用法：./deploy/remote/ssh.sh [远程命令...]
# 无参数则打开交互式 SSH。

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

if [[ $# -eq 0 ]]; then
  remote_ssh
else
  remote_ssh "$@"
fi
