#!/usr/bin/env bash
# AERIS V2.0 radio feasibility — STEP 2: bring up the volunteer AP and read
# per-client RSSI. The AP is pinned to the SAME channel the CSI link uses, so it
# does not disturb Nexmon capture on wlan0.
#
# Usage (on the Pi): sudo bash v2_radio_feasibility_2_ap.sh
# Then connect one volunteer phone to SSID below and watch the RSSI table.
set -u

AP_IFACE="${AERIS_AP_IFACE:-wlan0_ap}"   # virtual AP iface (or a USB dongle name)
AP_SSID="${AERIS_AP_SSID:-AERIS_V2_TEST}"
AP_PASS="${AERIS_AP_PASS:-aerisv2test}"

echo "== installing hostapd/dnsmasq if missing =="
command -v hostapd >/dev/null || sudo apt-get update -y && sudo apt-get install -y hostapd 2>&1 | tail -2

echo "== creating virtual AP iface ${AP_IFACE} on the SAME radio as the CSI client =="
# onboard combo needs AP on the CSI channel (iw: #channels <= 1); 2.4GHz g-mode only.
sudo iw phy phy0 interface add ${AP_IFACE} type __ap 2>&1 || \
  echo "NOTE: if this fails, set AERIS_AP_IFACE to your Realtek dongle name (e.g. wlan2)"

echo "== detecting CSI channel/bandwidth from wlan1 (the CSI client link) =="
CHAN=$(iw dev wlan1 info 2>/dev/null | sed -n 's/.*channel \([0-9]\+\).*/\1/p' | head -1)
CHAN=${CHAN:-11}
echo "CSI channel = ${CHAN} (onboard combo forces AP onto same channel)"

AP_CONF=$(mktemp)
cat > "$AP_CONF" <<CONF
interface=${AP_IFACE}
driver=nl80211
ssid=${AP_SSID}
hw_mode=g
channel=${CHAN}
ieee80211n=1
wmm_enabled=1
auth_algs=1
wpa=2
wpa_passphrase=${AP_PASS}
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
ctrl_interface=/var/run/hostapd
CONF

echo "== bringing up hostapd on ${AP_IFACE} (channel ${CHAN}) =="
# hostapd will pick/create the interface if the driver supports a virtual AP,
# otherwise it must point at a real extra adapter. See STEP 1 output.
sudo hostapd -B "$AP_CONF"
sleep 2

echo
echo "== per-client RSSI (volunteer phones). Watch 'signal avg'. Ctrl-C to stop. =="
i=0
while [ $i -lt 300 ]; do
  clear
  echo "AERIS volunteer clients on ${AP_SSID} @ channel ${CHAN}  ($(date +%T))"
  echo "-------------------------------------------------------------"
  sudo iw dev "${AP_IFACE}" station dump 2>/dev/null \
    | awk '/^Station/{mac=$2} /signal avg/{sig=$3} mac&&sig{printf "  MAC %s  signal %s dBm\n", mac, sig; mac=""; sig=""}'
  i=$((i+1)); sleep 2
done
sudo killall hostapd 2>/dev/null || true
