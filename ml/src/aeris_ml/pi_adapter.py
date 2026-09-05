"""Adapt Raspberry Pi (Nexmon bcm43455c0) CSI to the trained model's input grid.

The open-source baseline model was trained on Intel-5300 CSI shaped
[num_links=3, delay_bins=30]. The Pi's Nexmon capture is a different geometry:
typically 1-2 receive links and 64 OFDM subcarriers (including guard/null/DC
carriers that must be discarded). This module performs a deterministic,
model-agnostic adaptation so the live Pi feed can drive the OSS-trained model
immediately. A later `autocorrect_calibration` pass refines accuracy for the
specific room.

Steps (mirrors the training pipeline exactly):
  raw complex CSI [links, sub] -> sanitize phase -> Hamming -> IFFT -> |PDP|
  -> subcarrier selection/pooling to 30 delay bins -> link tiling to 3 channels
  -> per-window median/IQR normalization (same as build_corpus).
"""

from __future__ import annotations

import numpy as np

from aeris_ml.preprocessing import extract_cir

MODEL_LINKS = 3
MODEL_DELAY_BINS = 30


def _robust_normalize_window(pdp: np.ndarray) -> np.ndarray:
    """Per-window median/IQR normalization, matching build_corpus exactly."""
    med = np.median(pdp, axis=(0, 1), keepdims=True)
    q75 = np.percentile(pdp, 75, axis=(0, 1), keepdims=True)
    q25 = np.percentile(pdp, 25, axis=(0, 1), keepdims=True)
    iqr = np.maximum(q75 - q25, 1e-6)
    return ((pdp - med) / iqr).astype(np.float32)


def _fit_links(pdp: np.ndarray, target_links: int = MODEL_LINKS) -> np.ndarray:
    """Map [links, delay_bins] -> [target_links, delay_bins].

    - 1 link  -> tile x3 (the single physical echo stream drives all channels).
    - 2 links -> [L0, L1, mean(L0,L1)].
    - >=3     -> take the first `target_links`.
    """
    links = pdp.shape[0]
    if links == target_links:
        return pdp
    if links == 1:
        return np.repeat(pdp, target_links, axis=0)
    if links == 2:
        mean_row = ((pdp[0] + pdp[1]) * 0.5)[None, :]
        return np.concatenate([pdp, mean_row], axis=0)
    return pdp[:target_links]


def _fit_delay_bins(pdp: np.ndarray, target_bins: int = MODEL_DELAY_BINS) -> np.ndarray:
    """Map [links, sub] -> [links, target_bins].

    Drops guard/null carriers by cropping symmetric edges (and DC), then linearly
    interpolates the remaining power onto `target_bins` sample positions. This is
    a geometry adaptation (NOT physical resampling of delay) that preserves the
    echo profile's shape so the trained encoder sees a familiar PDP layout.
    """
    links, sub = pdp.shape
    if sub == target_bins:
        return pdp

    # Remove DC center bin and symmetric guard bands before resampling.
    keep = np.arange(sub)
    if sub % 2 == 0:  # drop the two central bins (DC + neighbor) for even counts
        keep = np.delete(keep, [sub // 2 - 1, sub // 2])
    else:
        keep = np.delete(keep, sub // 2)
    edge = max(1, int(0.06 * sub))  # drop ~6% guard carriers each side
    keep = keep[edge:len(keep) - edge] if keep.size > 2 * edge else keep

    src = pdp[:, keep]
    src_pos = np.linspace(0.0, 1.0, src.shape[1])
    dst_pos = np.linspace(0.0, 1.0, target_bins)
    out = np.empty((links, target_bins), dtype=np.float32)
    for l in range(links):
        out[l] = np.interp(dst_pos, src_pos, src[l])
    return out


def adapt_pi_csi_window(csi_window: np.ndarray) -> np.ndarray:
    """Convert a Pi CSI window to model-ready PDP [3, 30].

    Args:
        csi_window: complex CSI, either [links, subcarriers] for a single averaged
            frame or [time, links, subcarriers] for a sliding window. If a time
            axis is present, the PDP is averaged over it (stable estimate), exactly
            like build_corpus.extract_pdp_batch.

    Returns:
        np.float32 array of shape (MODEL_LINKS, MODEL_DELAY_BINS), normalized.
    """
    arr = np.asarray(csi_window, dtype=np.complex64)
    if arr.ndim == 2:
        arr = arr[None, ...]  # -> [1, links, sub]
    if arr.ndim != 3:
        raise ValueError(f"csi_window must be [links,sub] or [time,links,sub]; got {arr.shape}")

    pdp = extract_cir(arr, apply_window=True)     # [time, links, sub]
    pdp = pdp.mean(axis=0)                        # -> [links, sub] stable over window
    pdp = _fit_delay_bins(pdp, MODEL_DELAY_BINS)  # -> [links, 30]
    pdp = _fit_links(pdp, MODEL_LINKS)            # -> [3, 30]
    return _robust_normalize_window(pdp)          # -> [3, 30] float32
