"""Preprocessing utilities for heterogeneous Wi-Fi CSI datasets."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

import numpy as np

from aeris_ml.schema import CsiRecord


def complex_to_amplitude(csi: np.ndarray) -> np.ndarray:
    return np.abs(np.asarray(csi))


def log_amplitude(amplitude: np.ndarray, epsilon: float = 1e-6) -> np.ndarray:
    return np.log(np.maximum(np.asarray(amplitude, dtype=float), epsilon))


def apply_subcarrier_mask(values: np.ndarray, mask: np.ndarray) -> np.ndarray:
    arr = np.asarray(values)
    m = np.asarray(mask, dtype=bool)
    if arr.ndim != 3:
        raise ValueError("values must use [time, antenna_link, subcarrier] axes")
    if m.shape != arr.shape[1:]:
        raise ValueError(f"mask shape {m.shape} does not match CSI link/subcarrier shape {arr.shape[1:]}")
    return arr[:, m].reshape(arr.shape[0], 1, int(m.sum()))


def infer_sampling_rate(timestamps: np.ndarray) -> float | None:
    ts = np.asarray(timestamps, dtype=float)
    if ts.size < 2:
        return None
    gaps = np.diff(ts)
    gaps = gaps[gaps > 0]
    if gaps.size == 0:
        return None
    median_gap = float(np.median(gaps))
    return 1.0 / median_gap if median_gap > 0 else None


def validate_timestamps(timestamps: np.ndarray) -> None:
    ts = np.asarray(timestamps, dtype=float)
    if ts.ndim != 1 or ts.size == 0:
        raise ValueError("timestamps must be a non-empty 1D array")
    if not np.all(np.isfinite(ts)):
        raise ValueError("timestamps contain NaN or infinite values")
    if np.any(np.diff(ts) <= 0):
        raise ValueError("timestamps must be strictly increasing")


def resample_temporal(
    values: np.ndarray,
    timestamps: np.ndarray,
    target_rate_hz: float = 50.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Linearly resample `[time, antenna_link, subcarrier]` CSI to a fixed rate."""

    if target_rate_hz <= 0:
        raise ValueError("target_rate_hz must be positive")
    validate_timestamps(timestamps)
    arr = np.asarray(values)
    if arr.ndim != 3:
        raise ValueError("values must use [time, antenna_link, subcarrier] axes")
    ts = np.asarray(timestamps, dtype=float)
    if arr.shape[0] != ts.size:
        raise ValueError("values and timestamps disagree on time dimension")
    if ts.size == 1:
        return arr.copy(), ts.copy()

    step = 1.0 / float(target_rate_hz)
    new_ts = np.arange(ts[0], ts[-1] + step * 0.5, step, dtype=float)
    flat = arr.reshape(arr.shape[0], -1)
    out = np.empty((new_ts.size, flat.shape[1]), dtype=np.result_type(arr.dtype, float))
    if np.iscomplexobj(flat):
        for idx in range(flat.shape[1]):
            out[:, idx] = np.interp(new_ts, ts, flat[:, idx].real) + 1j * np.interp(
                new_ts, ts, flat[:, idx].imag
            )
    else:
        for idx in range(flat.shape[1]):
            out[:, idx] = np.interp(new_ts, ts, flat[:, idx])
    return out.reshape(new_ts.size, arr.shape[1], arr.shape[2]), new_ts


def resample_record(record: CsiRecord, target_rate_hz: float = 50.0) -> CsiRecord:
    csi, ts = resample_temporal(record.csi, record.timestamps, target_rate_hz)
    rssi = None
    if record.rssi is not None and record.rssi.shape[0] == record.timestamps.size:
        rssi_arr = np.asarray(record.rssi)
        if rssi_arr.ndim == 1:
            rssi, _ = resample_temporal(rssi_arr[:, None, None], record.timestamps, target_rate_hz)
            rssi = rssi[:, 0, 0]
        elif rssi_arr.ndim == 2:
            rssi, _ = resample_temporal(rssi_arr[:, :, None], record.timestamps, target_rate_hz)
            rssi = rssi[:, :, 0]
    return replace(record, csi=csi, timestamps=ts, rssi=rssi, sampling_rate_hz=target_rate_hz)


def generate_windows(
    record: CsiRecord,
    window_s: float = 2.5,
    step_s: float = 0.5,
    min_fraction: float = 0.95,
) -> Iterator[CsiRecord]:
    if window_s <= 0 or step_s <= 0:
        raise ValueError("window_s and step_s must be positive")
    validate_timestamps(record.timestamps)
    ts = record.timestamps
    start = float(ts[0])
    stop = float(ts[-1])
    idx = 0
    cursor = start
    while cursor + window_s <= stop + 1e-9:
        end = cursor + window_s
        right_edge = ts < end if end < stop else ts <= end
        selected = (ts >= cursor) & right_edge
        expected = max(1, int(round((record.sampling_rate_hz or infer_sampling_rate(ts) or 1.0) * window_s)))
        if int(selected.sum()) >= max(1, int(expected * min_fraction)):
            metadata = dict(record.metadata)
            metadata.update({"window_index": idx, "window_start_s": cursor - start, "window_end_s": end - start})
            yield replace(
                record,
                csi=record.csi[selected],
                timestamps=ts[selected],
                rssi=record.rssi[selected] if record.rssi is not None and record.rssi.shape[0] == ts.size else record.rssi,
                session_id=f"{record.session_id or 'recording'}_win{idx:05d}",
                metadata=metadata,
            )
            idx += 1
        cursor += step_s


def robust_normalize(values: np.ndarray, epsilon: float = 1e-6) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    arr = np.asarray(values, dtype=float)
    median = np.median(arr, axis=0, keepdims=True)
    q75 = np.percentile(arr, 75, axis=0, keepdims=True)
    q25 = np.percentile(arr, 25, axis=0, keepdims=True)
    iqr = np.maximum(q75 - q25, epsilon)
    return (arr - median) / iqr, median, iqr


def normalize_record(record: CsiRecord) -> CsiRecord:
    values = complex_to_amplitude(record.csi) if np.iscomplexobj(record.csi) else np.asarray(record.csi, dtype=float)
    normalized, median, iqr = robust_normalize(values)
    metadata = dict(record.metadata)
    metadata["normalization"] = {
        "method": "median_iqr",
        "median_shape": list(median.shape),
        "iqr_shape": list(iqr.shape),
    }
    return replace(record, csi=normalized, metadata=metadata)


def temporal_difference_channel(values: np.ndarray) -> np.ndarray:
    arr = np.asarray(values)
    diff = np.zeros_like(arr)
    if arr.shape[0] > 1:
        diff[1:] = arr[1:] - arr[:-1]
    return diff


def motion_energy(values: np.ndarray) -> np.ndarray:
    diff = temporal_difference_channel(complex_to_amplitude(values) if np.iscomplexobj(values) else values)
    return np.mean(np.square(diff), axis=tuple(range(1, diff.ndim)))


def packet_gap_report(timestamps: np.ndarray, excessive_gap_s: float = 1.0) -> dict[str, float | int]:
    validate_timestamps(timestamps)
    gaps = np.diff(np.asarray(timestamps, dtype=float))
    if gaps.size == 0:
        return {
            "count": 0,
            "median_s": 0.0,
            "p95_s": 0.0,
            "max_s": 0.0,
            "excessive_gap_count": 0,
        }
    return {
        "count": int(gaps.size),
        "median_s": float(np.median(gaps)),
        "p95_s": float(np.percentile(gaps, 95)),
        "max_s": float(np.max(gaps)),
        "excessive_gap_count": int(np.sum(gaps > excessive_gap_s)),
    }


def fill_missing_packets(
    values: np.ndarray,
    timestamps: np.ndarray,
    target_rate_hz: float = 50.0,
    method: str = "linear",
) -> tuple[np.ndarray, np.ndarray]:
    if method != "linear":
        raise ValueError("Only linear missing-packet interpolation is implemented")
    return resample_temporal(values, timestamps, target_rate_hz=target_rate_hz)


def sanitize_phase(csi: np.ndarray) -> np.ndarray:
    """
    Remove Phase Offsets (CFO/SFO) using a linear fit across subcarriers.
    Requires raw complex CSI `[time, antenna_link, subcarrier]`.
    """
    arr = np.asarray(csi)
    if not np.iscomplexobj(arr):
        return arr  # Cannot sanitize amplitude-only data

    phase = np.angle(arr)
    unwrapped = np.unwrap(phase, axis=-1)
    
    # Fit a line to the unwrapped phase across subcarriers and subtract it
    # We do this per timestamp, per link
    subcarriers = np.arange(arr.shape[-1], dtype=float)
    # Mean across subcarriers
    x_mean = np.mean(subcarriers)
    y_mean = np.mean(unwrapped, axis=-1, keepdims=True)
    
    # Slope (m) = sum((x - x_mean) * (y - y_mean)) / sum((x - x_mean)^2)
    x_diff = subcarriers - x_mean
    m = np.sum(x_diff * (unwrapped - y_mean), axis=-1, keepdims=True) / np.sum(x_diff**2)
    
    # Intercept (c) = y_mean - m * x_mean
    c = y_mean - m * x_mean
    
    # Linear trend
    trend = m * subcarriers + c
    
    sanitized_phase = unwrapped - trend
    
    # Reconstruct complex CSI with sanitized phase and original amplitude
    return np.abs(arr) * np.exp(1j * sanitized_phase)


def extract_cir(csi: np.ndarray, apply_window: bool = True) -> np.ndarray:
    """
    Convert Frequency-Domain CSI to Time-Domain Channel Impulse Response (CIR).
    Optionally applies a Hamming window to suppress sidelobes.
    Returns the Power Delay Profile (absolute value of CIR).
    """
    arr = np.asarray(csi)
    if not np.iscomplexobj(arr):
        raise ValueError("extract_cir requires complex CSI. Has amplitude already been taken?")
        
    sanitized = sanitize_phase(arr)
    
    if apply_window:
        # Apply a Hamming window across subcarriers
        window = np.hamming(sanitized.shape[-1])
        sanitized = sanitized * window
        
    # Perform Inverse FFT over the subcarrier axis (axis=-1)
    cir = np.fft.ifft(sanitized, axis=-1)
    
    # Return the Power Delay Profile (PDP)
    return np.abs(cir)
