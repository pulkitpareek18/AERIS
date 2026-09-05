"""Offline OSS-data inference demo (no Raspberry Pi required).

Runs the trained SpatialPerceptionNet over windows from the held-out portion of
the open-source corpus and prints the same kind of payload the live dashboard
would show (room dims, inside/outside presence, per-window decision), purely
from public data.

Usage:
    cd ml && PYTHONPATH=src ../.venv/bin/python src/aeris_ml/eval_demo.py \
        --corpus ../datasets/processed/presence_movement/corpus_v1.npz \
        --num 30
"""

from __future__ import annotations

import argparse

import numpy as np
import torch

from aeris_ml.models import SpatialPerceptionNet, PRESENCE_CLASSES

DEFAULT_CKPT = "checkpoints/spatial_perception_v1.pth"
DEFAULT_CORPUS = "../datasets/processed/presence_movement/corpus_v1.npz"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint", default=DEFAULT_CKPT)
    ap.add_argument("--corpus", default=DEFAULT_CORPUS)
    ap.add_argument("--num", type=int, default=30, help="number of windows to show")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    ckpt = torch.load(args.checkpoint, map_location="cpu")
    model = SpatialPerceptionNet(delay_bins=ckpt["delay_bins"], num_links=ckpt["num_links"])
    model.load_state_dict(ckpt["state_dict"])
    model.eval()

    data = np.load(args.corpus, allow_pickle=False)
    X = data["X"]
    Y = data["Y"]
    env = data["env"]
    empty = data["empty"]
    n = X.shape[0]

    rng = np.random.default_rng(args.seed)
    idxs = rng.choice(n, size=min(args.num, n), replace=False)

    true_names = []
    for i in idxs:
        inside, outside = Y[i][0], Y[i][1]
        if inside > 0.5:
            true_names.append("inside")
        elif outside > 0.5:
            true_names.append("outside")
        else:
            true_names.append("empty")

    x = torch.tensor(X[idxs], dtype=torch.float32)
    with torch.no_grad():
        room_dims, logits = model(x)
        probs = torch.softmax(logits, dim=-1).numpy()
    pred_idx = probs.argmax(axis=-1)

    correct = 0
    print(f"\n{'room':>6} {'true':>8} {'pred':>8} {'conf':>5}  "
          f"{'P(empty)':>8} {'P(inside)':>9} {'P(outside)':>10}")
    print("-" * 64)
    for k, i in enumerate(idxs):
        pred_name = PRESENCE_CLASSES[pred_idx[k]]
        ok = pred_name == true_names[k]
        correct += ok
        conf = probs[k][pred_idx[k]]
        print(f"{env[i]:>6} {true_names[k]:>8} {pred_name:>8} {conf:5.2f}  "
              f"{probs[k][0]:8.2f} {probs[k][1]:9.2f} {probs[k][2]:10.2f}  "
              f"{'OK' if ok else 'x'}")

    acc = correct / len(idxs)
    print("-" * 64)
    print(f"Sampled-window accuracy: {correct}/{len(idxs)} = {acc:.3f}")

    # Aggregate view like the dashboard's metric cards.
    counts = torch.tensor(probs[:, 1:3])  # (inside, outside) soft counts
    print("\nDashboard-style aggregate over sampled windows:")
    print(f"  Humans inside (mean prob):   {probs[:,1].mean():.2f}")
    print(f"  Humans outside/through-wall: {probs[:,2].mean():.2f}")
    print(f"  Room dims (model, m):        {room_dims[:,0].mean():.1f} x {room_dims[:,1].mean():.1f}")
    print(f"  Empty-room confidence (mean):{probs[:,0].mean():.2f}")


if __name__ == "__main__":
    main()
