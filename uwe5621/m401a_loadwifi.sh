#!/bin/bash
# Load the UWE5621DS WiFi driver on a mainline (ophub) Amlogic G12A kernel.
# Run with sudo, e.g.:  sudo ./loadwifi.sh
set -e

DRV_DIR="$(cd "$(dirname "$0")/.." && pwd)"

dmesg -C || true
modprobe cfg80211

insmod "${DRV_DIR}/unisocwcn/uwe5621_bsp_sdio.ko"
echo "bsp loaded"
sleep 2

insmod "${DRV_DIR}/unisocwifi/sprdwl_ng.ko"
echo "wifi loaded"

sleep 2
ip -br link show wlan0 || echo "wlan0 not present yet, check dmesg"
