"""Adapter scaffold for WISDOM CSI Traces."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from aeris_ml.datasets.base import DatasetLoader
from aeris_ml.schema import CsiRecord


class CsiTracesDataset(DatasetLoader):
    dataset_id = "csi_traces"

    def metadata(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "official_url": "https://github.com/senselab-iitm/wisdom",
            "status": "stub",
            "reason": (
                "The public repository states that the ESP32 CSI trace column description "
                "lives in data/human_activity_recognition/esp_col_desp.md inside the "
                "Google Drive dataset. Provide that file or a small official sample before parsing."
            ),
        }

    def discover_files(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(self.root.rglob("*.txt")) + sorted(self.root.rglob("*.csv"))

    def iter_recordings(self) -> Iterator[CsiRecord]:
        raise NotImplementedError(self.metadata()["reason"])
