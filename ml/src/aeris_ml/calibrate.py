"""One-shot Pi deployment calibration ('autocorrect').

Usage (after collecting ~10s of verified-empty room data from the Pi and
converting it to the same windowed PDP format as the corpus):

    PYTHONPATH=src python -m aeris_ml.calibrate \
        --checkpoint checkpoints/spatial_perception_v1.pth \
        --calibration path/to/empty_room_pdp.npz \
        --room-width 6.0 --room-depth 4.0 \
        --out checkpoints/spatial_perception_calibrated.pth

The calibration NPZ must contain an `X` array of shape (N, num_links, delay_bins)
with the SAME per-window normalization used to build the training corpus
(median/IQR per window). See build_corpus.build_corpus for reference.
"""

from __future__ import annotations

import argparse

import numpy as np
import torch

from aeris_ml.models import SpatialPerceptionNet


def normalize_pdp(X: np.ndarray) -> np.ndarray:
    """Match the per-window median/IQR normalization used in build_corpus."""
    med = np.median(X, axis=(1, 2), keepdims=True)
    q75 = np.percentile(X, 75, axis=(1, 2), keepdims=True)
    q25 = np.percentile(X, 25, axis=(1, 2), keepdims=True)
    iqr = np.maximum(q75 - q25, 1e-6)
    return ((X - med) / iqr).astype(np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--calibration", required=True, help="NPZ with `X` empty-room PDP windows")
    ap.add_argument("--room-width", type=float, default=None)
    ap.add_argument("--room-depth", type=float, default=None)
    ap.add_argument("--epochs", type=int, default=50)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--normalize", action="store_true",
                    help="Apply median/IQR normalization to raw PDP windows")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    ckpt = torch.load(args.checkpoint, map_location="cpu")
    model = SpatialPerceptionNet(delay_bins=ckpt["delay_bins"], num_links=ckpt["num_links"])
    model.load_state_dict(ckpt["state_dict"])

    data = np.load(args.calibration)
    X = data["X"].astype(np.float32)
    if args.normalize:
        X = normalize_pdp(X)
    cal = torch.tensor(X, dtype=torch.float32)

    dims = None
    if args.room_width is not None and args.room_depth is not None:
        dims = (args.room_width, args.room_depth)

    losses = model.autocorrect_calibration(
        cal, target_room_dims=dims, epochs=args.epochs, lr=args.lr)

    torch.save({**ckpt, "state_dict": model.state_dict(),
                "calibrated": True, "calibration_losses": losses}, args.out)
    print("Calibration complete:", losses)
    print(f"Saved calibrated model to {args.out}")


if __name__ == "__main__":
    main()
