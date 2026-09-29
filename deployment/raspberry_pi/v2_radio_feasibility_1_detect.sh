#!/usr/bin/env bash
# AERIS V2.0 radio feasibility — STEP 1: discover interfaces & capabilities.
# Run ON the Raspberry Pi (over SSH). Purely read-only; changes nothing.
set -u

echo "== /sys/class/net (interfaces) =="
ls /sys/class/net
echo
echo "== iw list: interface combinations (does the chipset support STA+AP + monitor?) =="
iw list 2>/dev/null | sed -n '/valid interface combinations/,/Supported extended features/p' | head -40
echo
echo "== current link state (wlan1 = CSI client of existing hotspot) =="
iw dev wlan1 link 2>&1 || true
echo
echo "== channel/bandwidth (from iw info) =="
iw dev wlan1 info 2>&1 | grep -Ei 'channel|width' || true
echo
echo "== hostapd / dnsmasq present? =="
command -v hostapd dnsmasq 2>&1 || echo "(hostapd/dnsmasq may not be installed)"
echo
echo "== usb wifi dongle? (optional extra AP adapter) =="
lsusb 2>/dev/null | grep -Ei 'wireless|802\.11|ralink|atheros|realtek' || echo "(no obvious USB Wi-Fi dongle detected)"
