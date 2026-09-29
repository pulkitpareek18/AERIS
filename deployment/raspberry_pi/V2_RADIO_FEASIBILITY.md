# AERIS V2.0 — Radio / RSSI Feasibility Kit

This is the gating step for the Digital-Twin recorder. It proves, on your actual
Raspberry Pi, whether the following coexist at the same time:

- The Pi's existing hotspot link (`wlan1`) that supplies CSI, monitored on `wlan0`.
- A Pi-owned volunteer hotspot (separate AP interface, pinned to the CSI channel).
- Per-client RSSI for each connected volunteer (via `iw station dump`).

## Why this gates V2

Auto-labeling volunteers as inside/outside depends on readable per-client RSSI.
CSI capture depends on Nexmon monitor mode staying up while the AP runs. If these
conflict, we must change the radio strategy before building the twin UI or the
labeling service. Do this first.

## Run order (on the Pi)

```bash
ssh aeris@<PI_IP>
cd aeris-recorder/deployment/raspberry_pi   # or copy this folder there
bash v2_radio_feasibility_1_detect.sh       # read-only discovery, no changes
# Then, in one terminal:
sudo bash v2_radio_feasibility_2_ap.sh      # starts volunteer AP + RSSI table
# Then, in a second terminal (same Pi):
sudo bash v2_radio_feasibility_3_csi.sh     # confirms CSI still streams
```

Connect one volunteer phone to SSID `AERIS_V2_TEST` (password `aerisv2test`)
while STEP 2 runs to see live signal readings.

## Expected PASS signals

- STEP 1: `iw list` shows a valid STA+AP combination, or a USB adapter is present.
- STEP 2: a connected phone appears under the AP interface with a `signal avg`.
- STEP 3: CSI packet rate stays >= ~30 pkt/s while the AP is active.

## Environment overrides

- `AERIS_AP_IFACE` — AP interface name (default `wlan0_ap`; set to a USB dongle like
  `wlan2` when the onboard radio can't hold a virtual AP while monitoring).
- `AERIS_AP_SSID`, `AERIS_AP_PASS` — test AP credentials.
- `AERIS_CAP_IFACE` — Nexmon monitor interface (default `wlan0`).
- `AERIS_TEST_SECS` — CSI capture duration for STEP 3 (default 10).

## Output handoff

Save the three outputs. They decide:
1. Which interface becomes the volunteer AP.
2. The exact `makecsiparams`/hostapd channel pinning.
3. Whether per-client RSSI is reliable enough for the inside/outside auto-labeler.
