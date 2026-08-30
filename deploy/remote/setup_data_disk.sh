#!/usr/bin/env bash
# 在远端以 root 执行：把空白数据盘做成 ext4 并挂到 /data。
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

DISK="${1:-/dev/vdb}"
MNT="${2:-/data}"

if [[ ! -b "$DISK" ]]; then
  echo "找不到磁盘 $DISK" >&2
  exit 1
fi

if ! lsblk -n -o FSTYPE,TYPE "${DISK}" | grep -q 'part\|ext4\|xfs'; then
  if [[ -z "$(lsblk -n -o NAME,TYPE "$DISK" | awk '$2=="part"{print $1}')" ]]; then
    echo "分区 $DISK"
    parted "$DISK" --script mklabel gpt mkpart primary ext4 1MiB 100%
    partprobe "$DISK" || true
    udevadm settle || true
    sleep 2
  fi
fi

PART="${DISK}1"
for i in 1 2 3 4 5 6 7 8 9 10; do
  [[ -b "$PART" ]] && break
  partprobe "$DISK" || true
  udevadm settle || true
  sleep 1
done
if [[ ! -b "$PART" ]]; then
  echo "分区不存在: $PART" >&2
  lsblk "$DISK"
  ls -l /dev/vd* || true
  exit 1
fi

if [[ -z "$(blkid -s TYPE -o value "$PART" || true)" ]]; then
  echo "mkfs.ext4 $PART"
  mkfs.ext4 -F -L data "$PART"
fi

mkdir -p "$MNT"
if ! mountpoint -q "$MNT"; then
  mount "$PART" "$MNT"
fi

uuid="$(blkid -s UUID -o value "$PART")"
if ! grep -q "$uuid" /etc/fstab; then
  echo "UUID=$uuid $MNT ext4 defaults,nofail 0 2" >> /etc/fstab
fi

chown vipuser:vipuser "$MNT"
mkdir -p "$MNT/huggingface"
chown -R vipuser:vipuser "$MNT/huggingface"

echo "=== 完成 ==="
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT "$DISK"
df -h "$MNT"
