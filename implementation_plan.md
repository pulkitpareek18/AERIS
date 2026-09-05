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
  Build a minimal UI (using shadcn/ui) to visualize the live room boundaries and crowd counts.

