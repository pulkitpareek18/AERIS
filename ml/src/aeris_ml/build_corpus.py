"""Build a versioned, windowed Power-Delay-Profile corpus from raw Zenodo CSI.

Reads every *.csi.json.gz under datasets/raw/presence_movement, aligns packets
to annotations.csv, converts frequency-domain CSI to time-domain CIR/PDP via
aeris_ml.preprocessing.extract_cir, windows over time, and writes a single
compressed corpus NPZ that the PyTorch training loop consumes.

Unlike the original scratch/prepare_real_data.py (which scored each packet in
isolation), this builds temporal windows so the network learns the *dynamics*
of human reflections, which is what actually generalizes to the Pi.
"""

from __future__ import annotations

import csv
import gzip
import json
import os
from dataclasses import dataclass

import numpy as np


RAW_DIR = "/Users/pulkitpareek18/Desktop/AERIS/datasets/raw/presence_movement"
OUT_DIR = "/Users/pulkitpareek18/Desktop/AERIS/datasets/processed/presence_movement"
OUT_PATH = os.path.join(OUT_DIR, "corpus_v1.npz")

# Labels where a human is physically INSIDE the room and active/still.
INSIDE_LABELS = {"Mobile", "Stationary"}
# Labels where a human is approaching / leaving / crossing the threshold.
# These are the closest thing to "through-wall / outside" motion in this data.
OUTSIDE_LABELS = {"Approach", "Departure", "Enter", "Exit"}
# Room is verified empty.
EMPTY_LABELS = {"Gone"}

# Windowing config: Zenodo sampling is irregular; we re-bin by time.
WINDOW_S = 2.0
STEP_S = 1.0
TARGET_RATE_HZ = 50.0          # resample irregular packet timestamps to a stable grid


@dataclass
class Annotation:
    label: str
    begin: float
    end: float


def load_annotations(path: str) -> dict[str, list[Annotation]]:
    by_room: dict[str, list[Annotation]] = {}
    if not os.path.exists(path):
        return by_room
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            room = row["room"]
            by_room.setdefault(room, []).append(
                Annotation(
                    label=row["label"],
                    begin=float(row["begin_time"]),
                    end=float(row["end_time"]),
                )
            )
    for anns in by_room.values():
        anns.sort(key=lambda a: a.begin)
    return by_room


def parse_csi_file(path: str):
    """Yield (timestamp, complex CSI [links, subcarriers]) per packet."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            t = float(item["t"])
            subcarriers = [
                [complex(float(v["r"]), float(v["i"])) for v in sub]
                for sub in item["csi"]
            ]
            arr = np.asarray(subcarriers, dtype=np.complex64).T  # [links, subcarriers]
            yield t, arr


def resample_to_grid(times: np.ndarray, values: np.ndarray, rate_hz: float):
    """Resample irregularly-sampled complex CSI [T, links, sub] onto a fixed time grid."""
    if times.size < 2:
        return times, values
    step = 1.0 / rate_hz
    grid = np.arange(times[0], times[-1], step)
    if grid.size < 2:
        return times, values
    flat = values.reshape(values.shape[0], -1)
    out = np.empty((grid.size, flat.shape[1]), dtype=np.complex64)
    for idx in range(flat.shape[1]):
        col = flat[:, idx]
        out[:, idx] = np.interp(grid, times, col.real) + 1j * np.interp(
            grid, times, col.imag
        )
    return grid, out.reshape(grid.size, values.shape[1], values.shape[2])


def extract_pdp_batch(csi_window: np.ndarray) -> np.ndarray:
    """Frequency-domain CSI [T, links, sub] -> mean Power Delay Profile [links, sub].

    We import extract_cir lazily so this script stays runnable standalone even if
    aeris_ml is not on the path (falls back to a local IFFT implementation).
    """
    try:
        from aeris_ml.preprocessing import extract_cir
        pdp = extract_cir(csi_window, apply_window=True)  # [T, links, sub]
    except Exception:
        # Fallback: sanitize phase + Hamming + IFFT, matching extract_cir defaults.
        arr = csi_window
        phase = np.unwrap(np.angle(arr), axis=-1)
        sub = np.arange(arr.shape[-1], dtype=float)
        xm = sub.mean()
        ym = phase.mean(axis=-1, keepdims=True)
        m = ((sub - xm) * (phase - ym)).sum(axis=-1, keepdims=True) / ((sub - xm) ** 2).sum()
        c = ym - m * xm
        sanitized = np.abs(arr) * np.exp(1j * (phase - (m * sub + c)))
        sanitized *= np.hamming(arr.shape[-1])
        pdp = np.abs(np.fft.ifft(sanitized, axis=-1))
    # Average over the time axis to get a stable per-link PDP for the window.
    return pdp.mean(axis=0).astype(np.float32)


def _flush_segment(tlist, clist, ann, room, win_len, step,
                   X_all, Y_all, room_all, env_all, max_per_segment):
    """Window one buffered labeled segment and append corpus rows."""
    if len(tlist) < win_len:
        return 0
    times_arr = np.asarray(tlist, dtype=float)
    csi = np.stack(clist, axis=0)
    grid_ts, grid_csi = resample_to_grid(times_arr, csi, TARGET_RATE_HZ)

    if ann.label in EMPTY_LABELS:
        counts, empty = (0.0, 0.0), 1.0
    elif ann.label in INSIDE_LABELS:
        counts, empty = (1.0, 0.0), 0.0
    elif ann.label in OUTSIDE_LABELS:
        counts, empty = (0.0, 1.0), 0.0
    else:
        return 0

    made = 0
    for start in range(0, grid_csi.shape[0] - win_len + 1, step):
        if made >= max_per_segment:
            break
        window = grid_csi[start:start + win_len]
        pdp = extract_pdp_batch(window)
        X_all.append(pdp)
        Y_all.append(list(counts))
        room_all.append(empty)
        env_all.append(room)
        made += 1
    return made


def build_corpus():
    annotations_by_room = load_annotations(os.path.join(RAW_DIR, "annotations.csv"))

    files = sorted(
        f for f in os.listdir(RAW_DIR)
        if f.endswith(".csi.json.gz") or f.endswith(".csi.json")
    )
    if not files:
        raise SystemExit(f"No CSI files found under {RAW_DIR}")

    X_all, Y_all, room_all, env_all = [], [], [], []
    win_len = int(WINDOW_S * TARGET_RATE_HZ)
    step = int(STEP_S * TARGET_RATE_HZ)
    # Cap windows per labeled segment so long 'Stationary'/'Mobile' segments do
    # not drown out the rarer transition labels (keeps classes balanced).
    max_per_segment = 120

    for fname in files:
        stem = fname.split(".csi.json")[0]
        room = stem.split("-")[0]
        anns = annotations_by_room.get(room, [])
        if not anns:
            print(f"[skip] {fname}: no annotations for room '{room}'")
            continue

        print(f"[load] {fname} ({len(anns)} labeled segments for room {room})...")
        fpath = os.path.join(RAW_DIR, fname)

        # Stream the file and bucket packets into their labeled segment.
        seg_i = 0
        buf_t: list[float] = []
        buf_c: list[np.ndarray] = []
        windows_made = 0
        packets = labeled = 0
        truncated = False
        try:
            for t, arr in parse_csi_file(fpath):
                packets += 1
                # Advance segment pointer past expired segments.
                while seg_i < len(anns) and t > anns[seg_i].end:
                    windows_made += _flush_segment(
                        buf_t, buf_c, anns[seg_i], room, win_len, step,
                        X_all, Y_all, room_all, env_all, max_per_segment)
                    buf_t, buf_c = [], []
                    seg_i += 1
                if seg_i >= len(anns):
                    break
                ann = anns[seg_i]
                if ann.begin <= t <= ann.end:
                    buf_t.append(t)
                    buf_c.append(arr)
                    labeled += 1
        except EOFError:
            truncated = True
            print(f"  [warn] {fname} is truncated; using readable packets only.")

        # Flush the final open segment.
        if seg_i < len(anns) and buf_t:
            windows_made += _flush_segment(
                buf_t, buf_c, anns[seg_i], room, win_len, step,
                X_all, Y_all, room_all, env_all, max_per_segment)

        print(f"  -> {windows_made} windows from {labeled}/{packets} labeled packets"
              f"{' (truncated)' if truncated else ''}")

    if not X_all:
        raise SystemExit("No windows were produced; check raw files and annotations.")

    X = np.stack(X_all, axis=0).astype(np.float32)            # [N, links, sub]
    Y = np.stack(Y_all, axis=0).astype(np.float32)            # [N, 2]  (inside, outside)
    empty = np.asarray(room_all, dtype=np.float32)            # [N]
    env = np.asarray(env_all)

    # Per-window robust normalization (median/IQR over the sample itself) so the
    # network is not biased by absolute per-room energy scale.
    med = np.median(X, axis=(1, 2), keepdims=True)
    q75 = np.percentile(X, 75, axis=(1, 2), keepdims=True)
    q25 = np.percentile(X, 25, axis=(1, 2), keepdims=True)
    iqr = np.maximum(q75 - q25, 1e-6)
    Xn = ((X - med) / iqr).astype(np.float32)

    os.makedirs(OUT_DIR, exist_ok=True)
    np.savez_compressed(
        OUT_PATH,
        X=Xn,
        Y=Y,
        empty=empty,
        env=env,
        window_s=WINDOW_S,
        target_rate_hz=TARGET_RATE_HZ,
        links=X.shape[1],
        delay_bins=X.shape[2],
    )
    print(f"\n[done] Saved corpus to {OUT_PATH}")
    print(f"  X: {X.shape}  Y: {Y.shape}  links={X.shape[1]} delay_bins={X.shape[2]}")
    print(f"  windows per room: {dict(zip(*np.unique(env, return_counts=True)))}")


if __name__ == "__main__":
    build_corpus()
