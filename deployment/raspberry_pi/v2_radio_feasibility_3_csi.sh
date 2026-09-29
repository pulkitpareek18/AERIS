#!/usr/bin/env bash
# AERIS V2.0 radio feasibility — STEP 3: confirm Nexmon CSI on wlan0 still
# streams while the volunteer AP is up and a phone is connected.
# Run AFTER STEP 2 is active in another terminal. Read the CSI packet count.
set -u

CAP_IFACE="${AERIS_CAP_IFACE:-wlan0}"
DUR="${AERIS_TEST_SECS:-10}"

echo "== CSI monitor status (expect 'monitor: 1' and the CSI channel) =="
nexutil -I"${CAP_IFACE}" -m 2>&1 || true
nexutil -I"${CAP_IFACE}" -k 2>&1 || true

# If monitor is off, nothing can be measured. The recorder must have configured
# Nexmon (trial running), so just report and skip instead of touching it.
MON=$(nexutil -I"${CAP_IFACE}" -m 2>/dev/null | grep -o 'monitor: 1' || true)
if [ -z "$MON" ]; then
  echo "NOTE: Nexmon monitor is OFF on ${CAP_IFACE}. Start a trial or run your"
  echo "      normal recorder configure step first, then re-run this script."
fi
echo
echo "== capturing ${DUR}s of CSI (dst port 5500) while AP is running =="
# sudo is used for -w so tcpdump can write the pcap as root; -c avoids root-only reads.
TMP=$(mktemp /tmp/aeris_csi_check.XXXXXX.pcap)
sudo chmod 666 /tmp 2>/dev/null || true
sudo -n timeout "${DUR}" tcpdump -i "${CAP_IFACE}" -n -s 0 'dst port 5500' -w "$TMP" -c 100000 2>&1 | tail -3
echo
COUNT=$(sudo tcpdump -nnr "$TMP" 2>/dev/null | wc -l | tr -d ' ')

CSI_RATE=$(( COUNT / DUR ))
echo "CSI packets captured in ${DUR}s: ${COUNT}"
echo "approx rate: ${CSI_RATE} pkt/s"
rm -f "$TMP"
if [ "${CSI_RATE:-0}" -ge 30 ]; then
  echo "PASS: CSI streaming while volunteer AP is up."
else
  echo "WARN/FAIL: low CSI rate while AP is up — AP/monitor coexistence may need work."
fi
