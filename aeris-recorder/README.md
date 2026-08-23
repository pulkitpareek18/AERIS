# AERIS Recorder v3

AERIS Recorder v3 is a dedicated Wi-Fi CSI data collection and labeling system for the Raspberry Pi 4. It combines high-speed Nexmon CSI capture with a local SQLite database (`aeris.db`), atomic participant & location profiles, privacy-preserving session metadata, and simultaneous access from the Pi touchscreen (800×480) and external computers on the local network.

---

## What's New in v3

1. **Local SQLite Database (`aeris.db`)**:
   - Built with Python's built-in `sqlite3` using WAL mode, busy timeout, and foreign keys.
   - Idempotent schema migrations via `PRAGMA user_version`.
   - Atomic, monotonic code generation (`P001`, `P002`, `L001`, `L002`) that is never reused.
   - Soft-deactivation protection (deactivated profiles cannot be accidentally deleted if referenced by recordings).

2. **Strict Privacy Architecture**:
   - Participant names are kept **only** in the local SQLite database for UI convenience.
   - Participant names **never** appear in session directory names, PCAP files, `labels.csv`, or exported `metadata.json`.
   - Recording files use only anonymous codes (e.g., `P003`).

3. **Three-State Touchscreen Interface (800×480)**:
   - **Trial Setup**: Clean dropdowns for Participant, Location, Walking Type, and Clothing, plus `+ Add` modal dialogs and a prominent `START TRIAL` button.
   - **Recording**: Real-time phase instruction, large countdown timer, live packet counters, and `STOP` button.
   - **Results**: Immediate PASS/FAIL quality summary with detailed check criteria, metrics breakdown, and session path.
   - Persists previous selections in `localStorage` for rapid repetitive trials.

4. **Simultaneous Multi-Screen Access**:
   - The backend listens on `0.0.0.0:8765`.
   - Raspberry Pi touchscreen runs Chromium in fullscreen kiosk mode on `http://127.0.0.1:8765`.
   - Remote laptops, tablets, or computers on the same network connect via `http://aeris.local:8765` or `http://<PI_IP>:8765`.
   - Real-time state synchronization across all connected screens.

---

## Installation & Upgrade

### Install / Upgrade on Raspberry Pi

```bash
cd /home/aeris/aeris-recorder
sudo bash install.sh
```

The installer:
- Safely preserves any existing `aeris.db` and `aeris-config.json`.
- Initializes and migrates the database schema (`PRAGMA user_version = 1`).
- Sets proper file ownership without altering existing PCAP capture files.
- Updates desktop and system application launcher icons.

### Launching the Application

Double-click **AERIS Recorder** on the Raspberry Pi desktop, or run from terminal:

```bash
aeris-recorder
```

Optional flags:
- `aeris-recorder --windowed` — Runs in a windowed 800×480 frame instead of kiosk fullscreen.
- `aeris-recorder --self-test` — Runs hardware and configuration verification.
- `aeris-recorder --self-test /path/to/capture.pcap` — Verifies and analyzes an existing PCAP capture.
- `aeris-recorder --init-db` — Initializes or migrates the SQLite schema without launching the server.

---

## Database Schema (`aeris.db`)

### `participants`
| Column | Type | Description |
|---|---|---|
| `id` | `INTEGER PRIMARY KEY` | Auto-incrementing primary key |
| `participant_code` | `TEXT UNIQUE` | Auto-generated code (e.g. `P001`, `P002`) |
| `name` | `TEXT NOT NULL` | Local-only participant name |
| `height_cm` | `REAL` | Optional height in cm |
| `body_type` | `TEXT` | `slim`, `average`, `broad`, `prefer_not_to_say` |
| `gender` | `TEXT` | `male`, `female`, `non_binary`, `other`, `prefer_not_to_say` |
| `active` | `INTEGER` | `1` (active) or `0` (deactivated) |
| `created_at` | `TEXT` | ISO 8601 UTC timestamp |
| `updated_at` | `TEXT` | ISO 8601 UTC timestamp |

### `locations`
| Column | Type | Description |
|---|---|---|
| `id` | `INTEGER PRIMARY KEY` | Auto-incrementing primary key |
| `location_code` | `TEXT UNIQUE` | Auto-generated code (e.g. `L001`, `L002`) |
| `name` | `TEXT NOT NULL` | Location description / name |
| `pi_hotspot_distance_m` | `REAL` | Distance between Pi and AP hotspot |
| `scenario` | `TEXT` | `LOS`, `NLOS_ONE_WALL`, `NLOS_MULTIPLE_WALLS` |
| `notes` | `TEXT` | Optional environmental notes |
| `active` | `INTEGER` | `1` (active) or `0` (deactivated) |
| `created_at` | `TEXT` | ISO 8601 UTC timestamp |
| `updated_at` | `TEXT` | ISO 8601 UTC timestamp |

### `trials`
| Column | Type | Description |
|---|---|---|
| `id` | `INTEGER PRIMARY KEY` | Auto-incrementing primary key |
| `session_id` | `TEXT UNIQUE` | Format: `YYYYMMDD-HHMMSS-{loc}-{part}-{type}` |
| `participant_id` | `INTEGER` | Foreign key referencing `participants(id)` |
| `location_id` | `INTEGER` | Foreign key referencing `locations(id)` |
| `walking_type` | `TEXT` | `enter`, `exit`, `across_left_to_right`, `across_right_to_left`, `toward_pi`, `away_from_pi` |
| `clothing` | `TEXT` | `light`, `normal`, `heavy`, `woollen_winter` |
| `quality_status` | `TEXT` | `PASS`, `FAIL`, `INCOMPLETE`, `ERROR` |
| `packet_count` | `INTEGER` | Total CSI packets captured |
| `packet_rate_hz` | `REAL` | Average packet arrival rate |
| `maximum_gap_s` | `REAL` | Maximum packet gap observed in seconds |
| `session_directory` | `TEXT` | Absolute path to session directory |
| `started_at` | `TEXT` | ISO 8601 UTC start time |
| `completed_at` | `TEXT` | ISO 8601 UTC completion time |

---

## REST API Endpoints

- `GET /api/status` — Lightweight status snapshot of current state and active trial metrics (zero PCAP parsing during capture).
- `GET /api/health` — Backend health check.
- `GET /api/participants[?active=1]` — List registered participants.
- `POST /api/participants` — Register a participant: `{"name": "...", "height_cm": 175, "body_type": "average", "gender": "male"}`.
- `PATCH /api/participants/{id}` — Update participant fields or deactivate (`{"active": false}`).
- `GET /api/locations[?active=1]` — List registered locations.
- `POST /api/locations` — Register a location: `{"name": "...", "pi_hotspot_distance_m": 4.5, "scenario": "LOS", "notes": "..."}`.
- `PATCH /api/locations/{id}` — Update location fields or deactivate (`{"active": false}`).
- `GET /api/trials[?limit=50]` — List recorded trials with participant and location codes.
- `GET /api/trials/{session_id}` — Get specific trial record.
- `POST /api/start` — Start a new trial: `{"participant_id": 1, "location_id": 1, "walking_type": "enter", "clothing": "normal"}`.
- `POST /api/stop` — Gracefully stop the active trial and finalize recording.
- `POST /api/shutdown` — Cleanly stop capture and shut down server.

---

## Rollback Instructions

If needed, roll back to the previous backup created during installation:

```bash
# Locate timestamped backup directory (e.g. /home/aeris/aeris-recorder-backup-YYYYMMDD-HHMMSS)
cd /home/aeris
cp -r aeris-recorder-backup-YYYYMMDD-HHMMSS/* aeris-recorder/
cd aeris-recorder
sudo bash install.sh
```
