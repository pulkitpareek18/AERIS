"""Adapter scaffold for CSI-Bench."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from aeris_ml.datasets.base import DatasetLoader
from aeris_ml.schema import CsiRecord


class CsiBenchDataset(DatasetLoader):
    dataset_id = "csi_bench"

    def metadata(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "official_url": "https://ai-iot-sensing.github.io/projects/project.html",
            "status": "stub",
            "reason": (
                "CSI-Bench is a large Kaggle release with H5/MAT recordings. The repository "
                "documents the directory layout, but this adapter needs a small local H5/MAT "
                "sample and optional h5py/scipy before conversion can be verified."
            ),
        }

    def discover_files(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(self.root.rglob("*.h5")) + sorted(self.root.rglob("*.mat"))

    def iter_recordings(self) -> Iterator[CsiRecord]:
        raise NotImplementedError(self.metadata()["reason"])
