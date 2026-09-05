import torch
import torch.nn as nn


# Mutually-exclusive presence states for the classification head.
PRESENCE_CLASSES = ("empty", "inside", "outside")


class SpatialPerceptionNet(nn.Module):
    """
    Simultaneous Room Dimension Estimation and Through-Wall presence sensing.
    Uses the Time-Domain Power Delay Profile (PDP) as input.
    Input tensor shape: (batch, num_links, delay_bins).

    Outputs:
      room_dims        : (batch, 2) -> [width, depth] regression
      presence_logits  : (batch, 3) -> {empty, inside, outside} classification
    """

    def __init__(self, delay_bins: int, num_links: int = 1):
        super().__init__()
        self.delay_bins = delay_bins
        self.num_links = num_links

        self.spatial_encoder = nn.Sequential(
            nn.Conv1d(num_links, 16, kernel_size=5, padding=2),
            nn.ReLU(),
            nn.MaxPool1d(2),
            nn.Conv1d(16, 32, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.MaxPool1d(2),
            nn.Conv1d(32, 64, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.AdaptiveAvgPool1d(1),
        )

        self.room_head = nn.Sequential(
            nn.Linear(64, 32), nn.ReLU(), nn.Linear(32, 2)  # [width, depth]
        )
        # Mutually-exclusive 3-way presence classifier: {empty, inside, outside}.
        # This is the primary supervised task and avoids the rare-class collapse
        # that independent MSE count regression suffered from.
        self.presence_head = nn.Linear(64, len(PRESENCE_CLASSES))

    def forward(self, pdp_input):
        features = self.spatial_encoder(pdp_input)
        features = features.view(features.size(0), -1)
        room_dims = self.room_head(features)
        presence_logits = self.presence_head(features)
        return room_dims, presence_logits

    def counts_from_logits(self, presence_logits: torch.Tensor) -> torch.Tensor:
        """Convert presence logits to (inside, outside) soft counts for the dashboard."""
        probs = torch.softmax(presence_logits, dim=-1)
        inside = probs[:, PRESENCE_CLASSES.index("inside")]
        outside = probs[:, PRESENCE_CLASSES.index("outside")]
        return torch.stack([inside, outside], dim=-1)

    @torch.no_grad()
    def predict(self, pdp_input):
        """Inference helper -> (room_dims, presence_probs, (inside, outside))."""
        self.eval()
        room_dims, logits = self.forward(pdp_input)
        probs = torch.softmax(logits, dim=-1)
        return room_dims, probs, self.counts_from_logits(logits)

    def autocorrect_calibration(self,
                                calibration_pdp: torch.Tensor,
                                target_room_dims: tuple[float, float] | None = None,
                                epochs: int = 50,
                                lr: float = 1e-4,
                                device: torch.device | str | None = None):
        """
        Few-shot domain adaptation for a new Pi deployment room (the 'autocorrect').

        Feed ~10 seconds of *verified empty room* PDP windows from the Pi.
        The presence_head is FROZEN (so the empty/inside/outside physics learned
        from the open-source corpus is preserved), while the spatial_encoder and
        room_head are fine-tuned so the model re-learns this room's static
        background reflections and pushes empty-room predictions toward 'empty'.

        Args:
            calibration_pdp: Tensor (N, num_links, delay_bins) of empty-room PDP windows.
            target_room_dims: Optional known (width, depth) in meters; if omitted the
                room head is regularized to stay at its calibrated prediction.
            epochs: Fine-tuning epochs.
            lr: Small learning rate to avoid catastrophic forgetting.
            device: torch device; inferred from parameters if None.

        Returns:
            dict with final calibration losses.
        """
        if device is None:
            device = next(self.parameters()).device
        device = torch.device(device)
        x = calibration_pdp.to(device, dtype=torch.float32)
        if x.ndim == 2:  # single window
            x = x.unsqueeze(0)

        # Freeze everything, then selectively unfreeze what should adapt.
        for p in self.parameters():
            p.requires_grad = False
        for p in self.spatial_encoder.parameters():
            p.requires_grad = True
        for p in self.room_head.parameters():
            p.requires_grad = True

        trainable = [p for p in self.parameters() if p.requires_grad]
        optimizer = torch.optim.Adam(trainable, lr=lr)
        mse = nn.MSELoss()
        ce = nn.CrossEntropyLoss()

        # Empty-room targets: presence must be the 'empty' class (index 0).
        empty_class = PRESENCE_CLASSES.index("empty")
        empty_targets = torch.full((x.size(0),), empty_class, dtype=torch.long, device=device)

        if target_room_dims is not None:
            room_target = torch.tensor(
                [list(target_room_dims)] * x.size(0), dtype=torch.float32, device=device
            )
        else:
            self.eval()
            with torch.no_grad():
                room_target = self.room_head(
                    self.spatial_encoder(x).view(x.size(0), -1)
                ).detach()

        self.train()
        last = {}
        for _ in range(epochs):
            optimizer.zero_grad()
            pred_room, pred_logits = self.forward(x)
            loss_room = mse(pred_room, room_target)
            loss_empty = ce(pred_logits, empty_targets)
            loss = loss_room + loss_empty
            loss.backward()
            optimizer.step()
            last = {"room_loss": float(loss_room.item()),
                    "empty_loss": float(loss_empty.item()),
                    "total_loss": float(loss.item())}

        # Re-freeze encoder after calibration; leave heads in a consistent state.
        for p in self.spatial_encoder.parameters():
            p.requires_grad = False
        self.eval()
        return last
