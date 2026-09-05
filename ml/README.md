# AERIS ML V1

This directory contains the first end-to-end AERIS machine-learning baseline: real open-source CSI data, preprocessing, supervised presence training, live Raspberry Pi inference, and dashboard transport.

V1 is an engineering baseline, not a final scientific claim. It establishes a repeatable path from CSI bytes to a live prediction and gives us a clean place to improve the model every day.

## Pipeline

```text
raw CSI
  -> annotation alignment
  -> resampling and phase sanitization
  -> CIR/PDP extraction
  -> normalized temporal windows
  -> SpatialPerceptionNet
  -> empty / inside / outside
```

For the live Pi path:

```text
Nexmon CSI on Pi
  -> temporary probe traffic
  -> tcpdump bytes over SSH
  -> incremental PCAP decoder
  -> Pi geometry adapter
  -> model inference
  -> FastAPI WebSocket
```

## Important Files

| File | Responsibility |
| --- | --- |
| `src/aeris_ml/models.py` | `SpatialPerceptionNet`, presence classes, calibration method. |
| `src/aeris_ml/train.py` | Dataset loading, weighted training, validation, checkpointing. |
| `src/aeris_ml/build_corpus.py` | Raw Zenodo CSI to versioned PDP corpus. |
| `src/aeris_ml/preprocessing.py` | Phase sanitization, CIR/PDP extraction, resampling, normalization. |
| `src/aeris_ml/pi_adapter.py` | Maps Pi Nexmon geometry to the V1 model input. |
| `src/aeris_ml/nexmon_stream.py` | Incremental PCAP stream decoder. |
| `src/aeris_ml/live_server.py` | Pi SSH stream, inference, and dashboard WebSocket. |
| `src/aeris_ml/eval_demo.py` | Offline inference demo using only OSS data. |
| `src/aeris_ml/calibrate.py` | Empty-room calibration command. |
| `configs/datasets.yaml` | Dataset registry and provenance metadata. |

## Environment

From the repository root, the current development environment is `.venv`:

```bash
uv pip install --python .venv/bin/python \
  numpy torch pyyaml fastapi uvicorn paramiko websockets
```

Run ML commands from `ml/` with `PYTHONPATH=src`:

```bash
cd ml
PYTHONPATH=src ../.venv/bin/python <script>
```

## V1 Corpus

V1 uses the Zenodo `presence_movement` dataset, record 3677366. The selected open-source recordings cover four environments: `128a`, `260`, `G19`, and `G21`.

The corpus builder creates 2-second windows at a 1-second step and resamples to 50 Hz. It only uses packets inside official annotation intervals; unlabeled gaps are not silently treated as empty rooms.

Build it:

```bash
cd ml
PYTHONPATH=src ../.venv/bin/python -m aeris_ml.build_corpus
```

Output:

```text
datasets/processed/presence_movement/corpus_v1.npz
```

The corpus contains:

- `X`: normalized PDP, shaped `[samples, links, delay_bins]`.
- `Y`: `[inside, outside]` labels.
- `empty`: verified empty-room flag.
- `env`: source room identifier.

## Train V1

```bash
cd ml
PYTHONPATH=src ../.venv/bin/python src/aeris_ml/train.py \
  --corpus ../datasets/processed/presence_movement/corpus_v1.npz \
  --epochs 30 --batch-size 64
```

The checkpoint is `ml/checkpoints/spatial_perception_v1.pth`.

It contains the model `state_dict`, input geometry, and class names:

```text
empty, inside, outside
```

V1 uses a weighted three-class classifier instead of independent count MSE. This prevents the rare `outside` class from being learned as always zero.

## Offline Evaluation

```bash
cd ml
PYTHONPATH=src ../.venv/bin/python src/aeris_ml/eval_demo.py \
  --checkpoint checkpoints/spatial_perception_v1.pth \
  --corpus ../datasets/processed/presence_movement/corpus_v1.npz \
  --num 30
```

This prints per-window predictions, probabilities, and dashboard-style aggregates without requiring the Pi.

## Live Pi Inference

Start the FastAPI WebSocket server:

```bash
cd ml
AERIS_PI_HOST=10.230.42.102 \
PYTHONPATH=src ../.venv/bin/python -m uvicorn aeris_ml.live_server:app \
  --host 0.0.0.0 --port 8000
```

The live bridge does not require a recorder trial. It starts temporary ping traffic and `tcpdump` over SSH, streams PCAP bytes directly, and writes no dataset session. The dashboard consumes `ws://localhost:8000/ws`.

The payload includes:

- `room_dims`: current model room estimate.
- `human_counts`: temporally smoothed inside/outside display counts.
- `presence`: raw probabilities, smoothed label, and confidence.
- `cir`: current 30-bin PDP chart data.
- `update_id`, `updated_at`, `signal_delta`: realtime telemetry.

The Pi adapter accepts typical Nexmon link/subcarrier shapes and maps them to the V1 model's `[3, 30]` input. This lets us observe the live path now, but Pi-native fine-tuning is required for reliable cross-hardware accuracy.

## Calibration

The model supports empty-room calibration through `calibrate.py`:

```bash
cd ml
PYTHONPATH=src ../.venv/bin/python -m aeris_ml.calibrate \
  --checkpoint checkpoints/spatial_perception_v1.pth \
  --calibration path/to/empty_room_pdp.npz \
  --room-width 2.74 --room-depth 2.74 \
  --out checkpoints/spatial_perception_calibrated.pth
```

Calibration freezes the presence head and adapts the encoder/background to the deployment room. The current room-size head is not physically validated because the public training data has no room-dimension labels; real labeled room sizes are a required V2 improvement.

## Current Limitations

- Public pretraining uses Intel CSI data; the Pi uses Nexmon Broadcom CSI.
- The geometry adapter is deterministic compatibility logic, not learned domain adaptation.
- Public labels mostly represent one person, so multi-person counting is not validated.
- `outside` is approximated from transition labels such as `Approach`, `Enter`, `Exit`, and `Departure`.
- Room dimensions currently use a weak placeholder training target.
- A random window split can contain correlated windows from the same session; room/session-held-out evaluation is needed for stronger claims.

## V2 Worklist

1. Add Pi-native CSI captures to training and validation.
2. Add true room width/depth labels.
3. Use subject/session/room-held-out splits.
4. Collect balanced inside/outside and multi-person examples.
5. Improve temporal modeling and confidence calibration.
6. Compress and move validated inference onto the Pi.

## Validation Commands

```bash
cd ml
PYTHONPATH=src ../.venv/bin/python -m py_compile \
  src/aeris_ml/models.py \
  src/aeris_ml/train.py \
  src/aeris_ml/live_server.py \
  src/aeris_ml/nexmon_stream.py
```

Dashboard checks run from the repository root:

```bash
npm --prefix dashboard run lint
npm --prefix dashboard run build
```
