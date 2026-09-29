# AERIS Research Project: Holistic Spatial Perception Implementation Plan

Based on the goal to create a star-level research conference paper (MobiCom, SIGCOMM, SenSys), this plan outlines the technical approach to achieve **Simultaneous Environment Mapping and Through-Wall Crowd Counting using Single-Link Wi-Fi.**

## Technical Approach

### 1. CSI to Power Delay Profile (PDP) Transformation
Raw CSI is in the frequency domain. By applying an Inverse Fast Fourier Transform (IFFT), we convert the CSI into a time-domain Channel Impulse Response (CIR). The peaks in the CIR represent different signal paths (direct path, wall reflections, human reflections).

### 2. Auto-Sensing Room Dimensions (Wi-Fi Tomography)
- **Static Reflection Analysis:** Filter out dynamic movements to isolate static peaks in the CIR. The time delay of these peaks corresponds to distances traveled by signals bouncing off walls.
- **Innovation:** A deep learning model maps these static multi-path delays into a 2D room dimension estimate (width x depth).

### 3. Spatially-Aware Crowd Counting (In vs. Out)
- **Dynamic Reflection Tracking:** Human movement creates dynamic variations (Doppler shifts) at specific time delays in the CIR.
- **Inside vs. Outside:** Correlate dynamic reflections with auto-sensed room dimensions. Delays shorter than wall reflections are **Inside**; longer, attenuated delays are **Outside**.
- **Counting:** A density-estimation neural network outputs a scalar count for both regions.

## Phased Implementation (Human-in-the-Loop)

To ensure you maintain full understanding and control, we will proceed phase-by-phase:

- **Phase 1: Signal Processing Foundation**
  Implement IFFT and phase sanitization in Python to extract clean CIR/PDP from Nexmon data.
- **Phase 2: Data Collection Protocol**
  Define and execute the data collection strategy (0 people, 1 person inside, 2 people inside, 1 inside + 1 outside).
- **Phase 3: Room Sensing Model**
  Train a model on static CIR data to estimate room dimensions.
- **Phase 4: Crowd Counting Model**
  Train the dynamic density estimator to count humans inside vs. outside.
- **Phase 5: Real-time Dashboard**
  Build a minimal UI to visualize the live room boundaries and crowd counts.

---

# V2 Execution Plan: Digital-Twin Recorder & Auto-Labeled Data

V1 proved the CSI → PDP → model → dashboard path on OSS data. V2 records hours of
**Pi-native, geometry-labeled, volunteer-labeled** CSI so we can train a real
room-size and in/out model. This section is the authority for the recorder redesign.

## Radio Architecture (two independent roles, same physical radios)

- **wlan0** — Nexmon CSI **monitor** (unchanged). Tuned to the *existing* hotspot's
  channel/bandwidth via `configure_nexmon()`.
- **wlan1** — Pi is a **client** of the **existing** hotspot (unchanged). This is
  the CSI source link: we read its BSSID/channel to configure the monitor.
- **Pi-owned hotspot** (separate logical AP, typically a USB adapter or a
  coexistence profile): volunteers' phones connect to *this*. It exists ONLY for
  auto-labeling. **Hard constraint:** the Pi hotspot's channel must match the CSI
  channel; we read the CSI channel from `detect_radio()` and pin the AP to it.

## Digital Twin Data Model

- **room**: width_m, depth_m, height_m (user-entered, numeric).
- **anchors**: 9 named points auto-derived — 4 corners, 4 edge-centers, center.
  Each stores its computed (x,y) for reference.
- **devices**: `pi` and `csi_hotspot` — each `anchor_id` (one of the 9) OR free
  (x_m, y_m). Both are drawn on the twin map.
- **door**: `edge` (N/E/S/W) + `position_m` along that edge.
- **volunteers**: MAC, display tag, current `region` (inside/outside).

## Auto-Labeling (inside vs outside per volunteer)

- **Ground truth:** Pi-hotspot association/disassociation events per MAC mark
  a volunteer as *present in twin*; `region` defaults by association.
- **Corroborating signal:** per-client RSSI from `iw station dump` on the AP;
  stronger-than-threshold ⇒ "inside" reinforcement. A short guided calibration
  (empty, one inside) sets the threshold automatically.
- Output: timestamped `(mac, region, rssi, t)` rows written to `labels.csv`,
  aligned with CSI windows so each PDP window gets an auto label without
  manual tapping.

## Recorder V2 UI (digital-twin setup wizard)

1. Enter room width/depth/height (m). Anchor grid auto-renders.
2. Place `pi` and `csi_hotspot` by tapping an anchor (or entering x,y).
3. Mark the door edge + position along it.
4. Volunteer join screen: phones connect to the Pi hotspot; each shows live
   region + RSSI; operator confirms/overrides once.
5. Start recording → continuous CSI + continuous auto-labels for hours.

## Execution Phases

- **V2.0 Radio feasibility (GATING):** prove Pi-host hotspot + simultaneous Nexmon monitor on a fixed channel + per-client RSSI via `iw station dump`. Deliver go/no-go + config diff.
- **V2.1 Schema & endpoints:** extend SQLite (rooms/anchors/devices/door/volunteers), add `/api/room`, `/api/devices`, `/api/volunteers`, `/api/labels`.
- **V2.2 Auto-label service:** subscribe to association events + `iw station dump`; write `labels.csv` keyed by MAC + timestamp; expose live region per volunteer.
- **V2.3 Twin setup UI:** room form, anchor/free placement, door, volunteer join, live region preview (reuses the generic schematic already in the dashboard, driven by real dims).
- **V2.4 Long recording:** hours-long captures with continuous labels; keep quality gates.
- **V2.5 Re-train:** corpus builder reads real room dims + auto labels; train room head on real labels (not the 5.0 placeholder); session/room-held-out eval; run `autocorrect_calibration` per room.

