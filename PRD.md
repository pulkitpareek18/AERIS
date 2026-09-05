# Product Requirements Document (PRD)

## 1. Vision
Transform the existing AERIS Raspberry Pi single-link Wi-Fi setup into a spatial perception engine capable of simultaneous room dimension estimation and through-wall crowd counting. This serves as a cutting-edge surveillance system strictly for research purposes, targeting top-tier academic conferences.

## 2. Core Objectives
1. **Auto-Sense Room Boundaries:** Mathematically deduce the physical dimensions of a room using static Wi-Fi multipath reflections (Wi-Fi Tomography).
2. **Inside Crowd Counting:** Accurately sense the number of humans present inside the room boundaries.
3. **Through-Wall Crowd Counting:** Accurately sense the number of humans present outside the room (behind walls).

## 3. Scope & Limitations
- **Hardware:** Single-link Wi-Fi (Raspberry Pi 4 with Nexmon). No Angle-of-Arrival (AoA) arrays. Analysis must rely on Time-of-Flight (ToF) via Channel Impulse Response (CIR).
- **Environment:** The system should operate gracefully in both Line-of-Sight (LOS) and Non-Line-of-Sight (NLOS) configurations.

## 4. Execution Phases (Human-in-the-Loop)
We will execute this project iteratively. AI will not run ahead; every phase requires human testing and approval.

* **Phase 1: Signal Processing (Current Phase)**
  - Convert raw frequency CSI to time-domain CIR.
  - Implement phase sanitization and background subtraction.
* **Phase 2: Data Engineering**
  - Update `aeris-recorder` SQLite schema to log exact human counts and locations (inside/outside).
  - Collect controlled datasets.
* **Phase 3: Machine Learning Development**
  - Train `RoomEstimator` (Static CIR -> Room Dimensions).
  - Train `SpatialCrowdCounter` (Dynamic CIR -> In/Out Counts).
* **Phase 4: UI & Dashboard Development**
  - Build a minimal, modern dashboard using Shadcn UI.

