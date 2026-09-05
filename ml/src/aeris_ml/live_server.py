import asyncio
import os
import shlex
import time
from collections import Counter
from collections import deque
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import paramiko
import numpy as np
import torch
import sys

# Ensure local modules can be imported
sys.path.insert(0, '/Users/pulkitpareek18/Desktop/AERIS/ml/src')
from aeris_ml.models import SpatialPerceptionNet, PRESENCE_CLASSES
from aeris_ml.nexmon_stream import iter_nexmon_pcap_stream
from aeris_ml.pi_adapter import adapt_pi_csi_window, MODEL_LINKS, MODEL_DELAY_BINS

app = FastAPI()

# --- Configuration (env-overridable so you can point at the real Pi) ---
PI_HOST = os.environ.get("AERIS_PI_HOST", "10.230.42.102")
PI_USER = os.environ.get("AERIS_PI_USER", "aeris")
PI_PASSWORD = os.environ.get("AERIS_PI_PASSWORD", "aeris")
PI_RECORDER_URL = os.environ.get("AERIS_PI_RECORDER_URL", "http://127.0.0.1:8765")
PI_CAPTURE_INTERFACE = os.environ.get("AERIS_PI_CAPTURE_INTERFACE", "wlan0")
PI_TRAFFIC_INTERFACE = os.environ.get("AERIS_PI_TRAFFIC_INTERFACE", "wlan1")
PI_PROBE_TARGET = os.environ.get("AERIS_PI_PROBE_TARGET", "1.1.1.1")
LOCAL_PCAP = os.environ.get("AERIS_LOCAL_PCAP", "/tmp/live_capture.pcap")
CHECKPOINT = os.environ.get(
    "AERIS_CHECKPOINT",
    "/Users/pulkitpareek18/Desktop/AERIS/ml/checkpoints/spatial_perception_v1.pth",
)

# Streaming config: Pi probe rate ~40 Hz; use a 2-second window like training.
WINDOW_SECONDS = 2.0
WINDOW_PACKETS = 80  # 40 Hz * 2 s; adapter averages over the window anyway
EMIT_INTERVAL_SECONDS = 0.5  # dashboard updates; CSI itself remains stream-driven

STREAM_COMMAND = (
    f"sudo -n ping -D -O -I {shlex.quote(PI_TRAFFIC_INTERFACE)} "
    f"-i 0.025 -s 1000 {shlex.quote(PI_PROBE_TARGET)} "
    ">/dev/null 2>&1 & ping_pid=$!; "
    "trap 'kill $ping_pid 2>/dev/null' EXIT; "
    f"exec sudo -n tcpdump -U -i {shlex.quote(PI_CAPTURE_INTERFACE)} "
    "-n -s 0 'dst port 5500' -w -"
)

# --- Load trained model ---
device = torch.device("cpu")
model = None
try:
    ckpt = torch.load(CHECKPOINT, map_location=device)
    if isinstance(ckpt, dict) and "state_dict" in ckpt:
        model = SpatialPerceptionNet(
            delay_bins=ckpt.get("delay_bins", MODEL_DELAY_BINS),
            num_links=ckpt.get("num_links", MODEL_LINKS),
        ).to(device)
        model.load_state_dict(ckpt["state_dict"])
    else:  # legacy raw state_dict
        model = SpatialPerceptionNet(delay_bins=MODEL_DELAY_BINS, num_links=MODEL_LINKS).to(device)
        model.load_state_dict(ckpt)
    model.eval()
    print(f"Loaded trained model weights from {CHECKPOINT}")
except Exception as e:
    print(f"Warning: Could not load trained weights ({e}); using untrained model.")
    model = SpatialPerceptionNet(delay_bins=MODEL_DELAY_BINS, num_links=MODEL_LINKS).to(device)
    model.eval()


def _read_next(iterator):
    """Make a blocking iterator safe for asyncio.to_thread."""
    try:
        return next(iterator)
    except StopIteration:
        return None


def infer_window(csi_window: np.ndarray):
    """Run the model on a sliding CSI window. Returns (room_dims, probs, counts, pdp_for_chart)."""
    # Adapt Pi geometry -> model input [3, 30] PDP (handles window averaging + norm).
    pdp_model = adapt_pi_csi_window(csi_window)              # (3, 30)
    x = torch.tensor(pdp_model, dtype=torch.float32).unsqueeze(0).to(device)
    with torch.no_grad():
        room_dims, logits = model(x)
        probs = torch.softmax(logits, dim=-1)[0]
        counts = model.counts_from_logits(logits)[0]
    rd = room_dims[0].tolist()
    prob = probs.tolist()
    cnt = counts.tolist()
    # Use the first link's PDP for the dashboard echo chart (physical, un-normalized).
    return rd, prob, cnt, pdp_model[0].tolist()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"Connecting to Raspberry Pi ({PI_HOST})...")
    try:
        ssh.connect(PI_HOST, username=PI_USER, password=PI_PASSWORD, timeout=5)
        print("Connected to Pi successfully!")
    except Exception as e:
        print(f"Failed to connect to Pi: {e}")
        await websocket.send_json({"status": "PI_CONNECTION_FAILED", "error": str(e)})
        return

    # Stream the growing PCAP once. No SFTP snapshots or repeated file decoding.
    _stdin, stream_stdout, _stderr = ssh.exec_command(STREAM_COMMAND, get_pty=False)
    stream_iterator = iter_nexmon_pcap_stream(stream_stdout)
    window_frames: deque = deque(maxlen=WINDOW_PACKETS)
    stream_shape: tuple[int, int] | None = None
    prediction_history: deque = deque(maxlen=5)
    update_id = 0
    previous_pdp: np.ndarray | None = None
    last_emit = 0.0

    try:
        await websocket.send_json({"status": "LIVE_CSI_STREAM_CONNECTED"})
        while True:
            item = await asyncio.to_thread(
                _read_next, stream_iterator
            )
            if item is None:
                break
            _timestamp, csi_frame, _meta = item
            # A Nexmon stream can change link grouping while packets arrive.
            # Never stack mixed geometries; restart the rolling window cleanly.
            if stream_shape != csi_frame.shape:
                window_frames.clear()
                stream_shape = csi_frame.shape
            window_frames.append(csi_frame)

            now = time.monotonic()
            if len(window_frames) < 4 or now - last_emit < EMIT_INTERVAL_SECONDS:
                continue

            csi_window = np.stack(list(window_frames), axis=0)
            rd, prob, cnt, chart_pdp = infer_window(csi_window)
            current_pdp = np.asarray(chart_pdp, dtype=np.float32)
            signal_delta = 0.0 if previous_pdp is None else float(
                np.mean(np.abs(current_pdp - previous_pdp))
            )
            previous_pdp = current_pdp
            update_id += 1
            raw_label = PRESENCE_CLASSES[int(np.argmax(prob))]
            prediction_history.append((raw_label, prob))
            stable_label = Counter(label for label, _ in prediction_history).most_common(1)[0][0]
            stable_index = PRESENCE_CLASSES.index(stable_label)
            stable_confidence = sum(
                probabilities[stable_index]
                for _, probabilities in prediction_history
            ) / len(prediction_history)
            stable_counts = [
                1 if stable_label == "inside" else 0,
                1 if stable_label == "outside" else 0,
            ]
            chart_data = [
                {"delayBin": i, "power": float(v)} for i, v in enumerate(chart_pdp)
            ]
            payload = {
                "status": "LIVE_PI_DATA",
                "update_id": update_id,
                "updated_at": time.time(),
                "signal_delta": round(signal_delta, 5),
                "room_dims": [round(rd[0], 1), round(rd[1], 1)],
                "human_counts": stable_counts,
                "presence": {
                    "empty": round(prob[PRESENCE_CLASSES.index("empty")], 3),
                    "inside": round(prob[PRESENCE_CLASSES.index("inside")], 3),
                    "outside": round(prob[PRESENCE_CLASSES.index("outside")], 3),
                    "label": stable_label,
                    "raw_label": raw_label,
                    "confidence": round(stable_confidence, 3),
                },
                "cir": chart_data,
            }
            await websocket.send_json(payload)
            last_emit = now

    except WebSocketDisconnect:
        print("Client disconnected")
    finally:
        stream_stdout.close()
        ssh.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
