# System Design Document

## 1. High-Level Architecture
The system consists of three distinct layers:
1. **Edge Capture Node (Raspberry Pi 4):** Captures raw CSI via Nexmon and logs trial metadata to SQLite.
2. **ML Pipeline (`aeris_ml`):** Python-based engine that processes raw CSI, extracts Channel Impulse Response (CIR), and runs deep learning inference.
3. **Visualization Dashboard:** A modern, minimal web interface to display live surveillance metrics (room dimensions and human counts).

## 2. Machine Learning Architecture
Due to the 1x1 antenna constraint, we rely entirely on Time-Domain analysis.
- **Preprocessing:** 
  - IFFT (Inverse Fast Fourier Transform) converts Frequency to Time (Delay).
  - Phase Sanitization removes hardware-induced phase offsets (CFO/SFO).
- **RoomEstimator Network:** 
  - Input: Time-averaged static CIR.
  - Architecture: 1D-CNN or Multi-Layer Perceptron (MLP).
  - Output: Estimated Distance to boundaries (X, Y in meters).
- **SpatialCrowdCounter Network:**
  - Input: Dynamic CIR variance over time (Doppler profile).
  - Architecture: Spatio-temporal network (e.g., ConvLSTM or 2D CNN over Delay-Time maps).
  - Output: `count_inside` (integer), `count_outside` (integer).

## 3. UI/UX Design (Dashboard)
The dashboard must look like a high-end research tool. We will use **Next.js**, **Tailwind CSS**, and **shadcn/ui**.

### Design Language
- **Theme:** Minimalist, high-contrast dark mode.
- **Components (shadcn/ui):**
  - `Card`: For displaying big metrics (e.g., "Inside: 2", "Outside: 1").
  - `Progress`: To show the model's confidence scores.
  - `Badge`: To indicate connection status (Live/Offline).
  - `Chart` (via Recharts integrated with shadcn): For real-time plotting of the room boundary and CIR delay profile.
- **Layout:** A clean grid layout. Top row for critical counts. Bottom row for technical graphs (CIR profiles, room blueprint).

