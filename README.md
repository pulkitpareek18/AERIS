# AERIS

AERIS is a camera-free Wi-Fi sensing system for research into human presence, movement, and spatial perception from Channel State Information (CSI).

The project is being built incrementally. **V1 is a real open-source-data baseline connected to a live Raspberry Pi CSI stream.** It validates the end-to-end signal path and dashboard, but it is not yet a validated room-measurement or multi-person counting system.

## What Works In V1

- Real CSI ingestion from the Zenodo `presence_movement` dataset.
- Four-room public corpus with windowed Power Delay Profiles (PDPs).
- Three-state presence model: `empty`, `inside`, and `outside`.
- Raspberry Pi Nexmon CSI decoding and direct live streaming over SSH.
- Live dashboard with presence output, PDP telemetry, room estimate, and update/frame telemetry.
- Geometry adapter from Pi Nexmon CSI to the current OSS-trained model input.

## Repository Layout

| Path | Purpose |
| --- | --- |
| `aeris-recorder/` | Raspberry Pi recorder, Nexmon capture, labeling UI, SQLite metadata, and session exports. |
| `ml/` | Dataset loaders, preprocessing, corpus building, model, training, live bridge, and calibration. |
| `dashboard/` | Next.js dashboard for live CSI-derived output. |
| `datasets/raw/` | Downloaded public/raw data. Ignored by Git. |
| `datasets/processed/` | Generated corpora and prepared arrays. Ignored by Git. |
| `ml/checkpoints/` | Generated model checkpoints. Ignored by Git. |
| `docs/` | Dataset and engineering documentation. |

## Quick Start

### 1. Install the ML environment

From the repository root on macOS/Linux:

```bash
uv pip install --python .venv/bin/python numpy torch pyyaml fastapi uvicorn paramiko websockets
```

The project currently uses the root `.venv`. On Apple Silicon, PyTorch can use the `mps` device automatically.

### 2. Start the live inference server

The server connects to the Pi at `10.230.42.102` by default. Override it with `AERIS_PI_HOST` when needed:

```bash
cd ml
AERIS_PI_HOST=10.230.42.102 \
PYTHONPATH=src ../.venv/bin/python -m uvicorn aeris_ml.live_server:app \
  --host 0.0.0.0 --port 8000
```

The live bridge starts temporary probe traffic and streams CSI directly from the Pi with SSH/tcpdump. It does **not** require starting a labeled recorder trial or creating a dataset session.

### 3. Start the dashboard

In another terminal:

```bash
npm --prefix dashboard run dev
```

Open <http://localhost:3000>.

## V1 OSS Training

V1 uses Zenodo record 3677366, the `presence_movement` CSI dataset. The selected corpus contains real recordings from rooms `128a`, `260`, `G19`, and `G21`.

The corpus builder:

1. Reads line-delimited compressed CSI JSON.
2. Aligns packets only to verified annotation intervals.
3. Resamples irregular packets to a stable 50 Hz grid.
4. Converts complex CSI to a phase-sanitized PDP using windowed IFFT.
5. Builds 2-second windows with 1-second steps.
6. Normalizes each window with median/IQR scaling.

Build the corpus:

```bash
cd ml
PYTHONPATH=src ../.venv/bin/python -m aeris_ml.build_corpus
```

Output:

```text
datasets/processed/presence_movement/corpus_v1.npz
```

Train the V1 model:

```bash
cd ml
PYTHONPATH=src ../.venv/bin/python src/aeris_ml/train.py \
  --corpus ../datasets/processed/presence_movement/corpus_v1.npz \
  --epochs 30 --batch-size 64
```

The checkpoint is written to `ml/checkpoints/spatial_perception_v1.pth`.

Run an offline OSS-only inference demo:

```bash
cd ml
PYTHONPATH=src ../.venv/bin/python src/aeris_ml/eval_demo.py \
  --checkpoint checkpoints/spatial_perception_v1.pth \
  --corpus ../datasets/processed/presence_movement/corpus_v1.npz \
  --num 30
```

## Model V1

The model is `SpatialPerceptionNet` in [ml/src/aeris_ml/models.py](ml/src/aeris_ml/models.py). It receives a normalized `[3, 30]` PDP tensor and produces:

- Room dimensions: `[width, depth]` regression output.
- Presence logits: `empty`, `inside`, `outside` classification output.

The presence task uses class-weighted cross-entropy. This replaced the earlier independent MSE count heads, which collapsed the rare `outside` class to zero.

The current room-dimension head is **not physically validated**: the public dataset does not provide room dimensions, so V1 uses a weak placeholder target. Room dimensions must be calibrated or retrained with real room-size labels before they are treated as measurements.

## Live Pi Data Path

```text
Pi Nexmon CSI
  -> temporary ping/probe traffic
  -> tcpdump PCAP bytes over persistent SSH
  -> incremental Nexmon decoder
  -> rolling CSI window
  -> Pi geometry adapter
  -> SpatialPerceptionNet
  -> WebSocket payload
  -> Next.js dashboard
```

The Pi adapter in [ml/src/aeris_ml/pi_adapter.py](ml/src/aeris_ml/pi_adapter.py) maps typical Pi Nexmon geometry into the current OSS model's 3-link/30-bin input. This is a compatibility bridge, not a substitute for cross-hardware training.

## Calibration Roadmap

The next improvements are deliberately incremental:

1. Collect Pi empty-room and labeled movement data.
2. Replace geometry adaptation with Pi-native training/fine-tuning.
3. Add real room width/depth labels and remove the placeholder room target.
4. Improve multi-person and inside/outside supervision.
5. Add temporal evaluation, room-held-out splits, and confidence calibration.
6. Move inference onto the Pi after the live model is accurate enough.

> The authoritative next-phase plan is the **V2 Digital-Twin Recorder &
> Auto-Labeled Data** section in [implementation_plan.md](implementation_plan.md).

The calibration helper is available for an empty-room capture:

```bash
cd ml
PYTHONPATH=src ../.venv/bin/python -m aeris_ml.calibrate \
  --checkpoint checkpoints/spatial_perception_v1.pth \
  --calibration path/to/empty_room_pdp.npz \
  --out checkpoints/spatial_perception_calibrated.pth
```

## Data, Privacy, and Checkpoints

Do not commit raw CSI, PCAPs, participant databases, participant names, private metadata, API keys, or generated checkpoints. Large and sensitive artifacts belong in the ignored data/checkpoint paths listed above.

The recorder keeps participant names in its local SQLite database. Canonical ML targets should preserve original labels while excluding identity and participant attributes from prediction targets.

## Testing

ML syntax/import checks:

```bash
cd ml
PYTHONPATH=src ../.venv/bin/python -m py_compile \
  src/aeris_ml/models.py \
  src/aeris_ml/train.py \
  src/aeris_ml/live_server.py \
  src/aeris_ml/nexmon_stream.py
```

Dashboard checks:

```bash
npm --prefix dashboard run lint
npm --prefix dashboard run build
```

## License and Citations

Check each public dataset's license before redistribution or publication. The V1 presence/movement source is Zenodo record 3677366 and should be cited using its official metadata. Also cite Nexmon CSI and any additional datasets used in future training runs.
