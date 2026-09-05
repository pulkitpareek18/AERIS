import os
import argparse

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader, random_split

from aeris_ml.models import SpatialPerceptionNet, PRESENCE_CLASSES

DEFAULT_CORPUS = os.path.abspath(os.path.join(
    os.path.dirname(__file__),
    os.pardir, os.pardir, os.pardir,
    "datasets", "processed", "presence_movement", "corpus_v1.npz",
))


def presence_class_index(inside, outside):
    """Map [inside, outside] counts -> mutually-exclusive presence class index."""
    if inside > 0.5:
        return PRESENCE_CLASSES.index("inside")
    if outside > 0.5:
        return PRESENCE_CLASSES.index("outside")
    return PRESENCE_CLASSES.index("empty")


class CsiDataset(Dataset):
    """PyTorch Dataset over the windowed, normalized multi-room corpus."""

    def __init__(self, npz_path):
        data = np.load(npz_path, allow_pickle=False)
        self.X = data["X"]            # (N, links, delay_bins)
        self.Y = data["Y"]            # (N, 2) -> [inside, outside]
        self.empty = data["empty"]    # (N,)   -> 1.0 if verified-empty room
        self.num_samples = self.X.shape[0]
        self.num_links = self.X.shape[1]
        self.delay_bins = self.X.shape[2]

    def __len__(self):
        return self.num_samples

    def __getitem__(self, idx):
        pdp = torch.tensor(self.X[idx], dtype=torch.float32)
        # No room-dimension ground truth in this public dataset; the room head is
        # supervised later via autocorrect_calibration() on the Pi.
        room_dims = torch.tensor([5.0, 5.0], dtype=torch.float32)
        inside, outside = float(self.Y[idx][0]), float(self.Y[idx][1])
        presence_cls = torch.tensor(presence_class_index(inside, outside), dtype=torch.long)
        return pdp, room_dims, presence_cls

    def class_weights(self) -> torch.Tensor:
        """Inverse-frequency weights so the rarer outside/empty classes are learned."""
        counts = np.zeros(len(PRESENCE_CLASSES), dtype=np.float64)
        for i in range(self.num_samples):
            counts[presence_class_index(float(self.Y[i][0]), float(self.Y[i][1]))] += 1
        counts = np.maximum(counts, 1.0)
        weights = counts.sum() / counts
        weights = weights / weights.mean()
        return torch.tensor(weights, dtype=torch.float32)


def evaluate(model, loader, device):
    model.eval()
    correct = total = 0
    tp = np.zeros(len(PRESENCE_CLASSES))
    fp = np.zeros(len(PRESENCE_CLASSES))
    fn = np.zeros(len(PRESENCE_CLASSES))
    with torch.no_grad():
        for pdp, _, cls in loader:
            pdp = pdp.to(device)
            cls = cls.to(device)
            _, logits = model(pdp)
            pred = logits.argmax(dim=-1)
            correct += (pred == cls).sum().item()
            total += cls.shape[0]
            for c in range(len(PRESENCE_CLASSES)):
                tp[c] += ((pred == c) & (cls == c)).sum().item()
                fp[c] += ((pred == c) & (cls != c)).sum().item()
                fn[c] += ((pred != c) & (cls == c)).sum().item()
    model.train()
    acc = correct / max(1, total)
    per_class = {}
    for i, name in enumerate(PRESENCE_CLASSES):
        prec = tp[i] / (tp[i] + fp[i]) if tp[i] + fp[i] else 0.0
        rec = tp[i] / (tp[i] + fn[i]) if tp[i] + fn[i] else 0.0
        per_class[name] = (float(prec), float(rec))
    return acc, per_class


def train_model(corpus_path=DEFAULT_CORPUS, epochs=20, batch_size=64,
                learning_rate=1e-3, val_fraction=0.2, patience=6):
    print("Initializing Spatial Perception Training Pipeline (multi-room corpus)...")
    dataset = CsiDataset(npz_path=corpus_path)
    print(f"Loaded {len(dataset)} windows | links={dataset.num_links} | "
          f"delay_bins={dataset.delay_bins}")

    n_val = max(1, int(len(dataset) * val_fraction))
    n_train = len(dataset) - n_val
    train_set, val_set = random_split(
        dataset, [n_train, n_val], generator=torch.Generator().manual_seed(42))
    train_loader = DataLoader(train_set, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_set, batch_size=batch_size, shuffle=False)

    device = torch.device("cuda" if torch.cuda.is_available() else
                          ("mps" if torch.backends.mps.is_available() else "cpu"))
    model = SpatialPerceptionNet(
        delay_bins=dataset.delay_bins, num_links=dataset.num_links).to(device)

    optimizer = optim.Adam(model.parameters(), lr=learning_rate, weight_decay=1e-4)
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=2, factor=0.5)
    criterion_room = nn.MSELoss()
    criterion_presence = nn.CrossEntropyLoss(weight=dataset.class_weights().to(device))

    print(f"Training on {device} | {n_train} train / {n_val} val")

    best_val = float("inf")
    best_state = None
    epochs_no_improve = 0

    for epoch in range(epochs):
        model.train()
        epoch_room = epoch_presence = 0.0
        for pdp, true_room, cls in train_loader:
            pdp = pdp.to(device)
            true_room = true_room.to(device)
            cls = cls.to(device)

            optimizer.zero_grad()
            pred_room, logits = model(pdp)
            loss_room = criterion_room(pred_room, true_room)
            loss_presence = criterion_presence(logits, cls)
            total_loss = 0.1 * loss_room + loss_presence  # weak room supervision
            total_loss.backward()
            optimizer.step()

            epoch_room += loss_room.item()
            epoch_presence += loss_presence.item()

        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for pdp, _, cls in val_loader:
                pdp = pdp.to(device)
                cls = cls.to(device)
                _, logits = model(pdp)
                val_loss += criterion_presence(logits, cls).item()
        val_loss /= max(1, len(val_loader))
        scheduler.step(val_loss)

        acc, per_class = evaluate(model, val_loader, device)
        pi, ri = per_class["inside"]
        po, ro = per_class["outside"]
        pe, re = per_class["empty"]
        print(f"Epoch [{epoch+1:02d}/{epochs}] "
              f"train_room={epoch_room/len(train_loader):.4f} "
              f"train_presence={epoch_presence/len(train_loader):.4f} "
              f"val_presence={val_loss:.4f} acc={acc:.3f}")
        print(f"    inside P={pi:.3f} R={ri:.3f} | outside P={po:.3f} R={ro:.3f} "
              f"| empty P={pe:.3f} R={re:.3f}")

        if val_loss < best_val - 1e-4:
            best_val = val_loss
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            epochs_no_improve = 0
        else:
            epochs_no_improve += 1
            if epochs_no_improve >= patience:
                print(f"Early stopping at epoch {epoch+1}.")
                break

    os.makedirs("checkpoints", exist_ok=True)
    save_dict = best_state if best_state is not None else model.state_dict()
    torch.save({"state_dict": save_dict,
                "delay_bins": dataset.delay_bins,
                "num_links": dataset.num_links,
                "presence_classes": list(PRESENCE_CLASSES)},
               "checkpoints/spatial_perception_v1.pth")
    print(f"Best val presence loss: {best_val:.4f}")
    print("Saved best weights to checkpoints/spatial_perception_v1.pth")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", default=DEFAULT_CORPUS)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--lr", type=float, default=1e-3)
    args = parser.parse_args()
    train_model(corpus_path=args.corpus, epochs=args.epochs,
                batch_size=args.batch_size, learning_rate=args.lr)
