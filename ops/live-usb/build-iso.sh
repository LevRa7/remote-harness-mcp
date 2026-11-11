#!/usr/bin/env bash
# ops/live-usb/build-iso.sh
# Reproducible Debian Live USB builder with preinstalled Wi-Fi/NIC firmware
# and the Remote-Harness MCP agent + TUI onboarding screen.

set -euo pipefail

WORK_DIR="${WORK_DIR:-/tmp/rhm-live-build}"
DISTRO="${DISTRO:-trixie}"
ARCH="${ARCH:-amd64}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "[1/5] Preparing live-build workspace at ${WORK_DIR}..."
mkdir -p "${WORK_DIR}"
cd "${WORK_DIR}"

lb config \
  --distribution "${DISTRO}" \
  --architectures "${ARCH}" \
  --archive-areas "main contrib non-free non-free-firmware" \
  --bootappend-live "boot=live components quiet splash toram MESH_SCENARIO=live_usb" \
  --debian-installer false \
  --iso-application "Remote-Harness Live Rescue" \
  --iso-volume "RHM_RESCUE"

echo "[2/5] Writing package lists (firmware + rescue toolkit)..."
mkdir -p config/package-lists
cat << 'EOF' > config/package-lists/rhm-rescue.list.chroot
# Kernel & Non-Free Network/Storage Firmware
firmware-linux
firmware-linux-nonfree
firmware-misc-nonfree
firmware-iwlwifi
firmware-realtek
firmware-atheros
firmware-brcm80211
firmware-mediatek
firmware-intel-sound

# Network & Overlay Tunnels
network-manager
wpasupplicant
iw
wireless-tools
usb-modeswitch
curl
ca-certificates
openssh-client
wireguard-tools

# Storage, Filesystem & Boot Recovery
smartmontools
nvme-cli
parted
gdisk
gddrescue
testdisk
lvm2
mdadm
cryptsetup
btrfs-progs
xfsprogs
ntfs-3g
dosfstools
exfatprogs
e2fsprogs
chntpw
efibootmgr
grub-efi-amd64-bin
shim-signed

# Runtime for Remote-Harness MCP
python3
python3-venv
tmux
jq
qrencode
EOF

echo "[3/5] Injecting Remote-Harness MCP node into chroot overlay..."
CHROOT_OPT="config/includes.chroot/opt/remote-harness-mcp"
mkdir -p "${CHROOT_OPT}"
cp -r "${REPO_ROOT}/core" "${CHROOT_OPT}/"
cp "${REPO_ROOT}/rhm_mcp_server.py" "${CHROOT_OPT}/"

echo "[4/5] Configuring systemd service & udev anti-automount rules..."
mkdir -p config/includes.chroot/etc/systemd/system
cat << 'EOF' > config/includes.chroot/etc/systemd/system/rhm-live-agent.service
[Unit]
Description=Remote-Harness MCP Live USB Rescue Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment="MESH_SCENARIO=live_usb"
WorkingDirectory=/opt/remote-harness-mcp
ExecStart=/usr/bin/python3 /opt/remote-harness-mcp/rhm_mcp_server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

mkdir -p config/includes.chroot/etc/systemd/journald.conf.d
cat << 'EOF' > config/includes.chroot/etc/systemd/journald.conf.d/volatile-limit.conf
[Journal]
Storage=volatile
SystemMaxUse=50M
EOF

echo "[5/5] Ready to build ISO. Run 'sudo lb build' inside ${WORK_DIR} on a Debian host."
