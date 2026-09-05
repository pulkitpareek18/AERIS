"""Adapter scaffold for ARIL."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from aeris_ml.datasets.base import DatasetLoader
from aeris_ml.schema import CsiRecord


class ArilDataset(DatasetLoader):
    dataset_id = "aril"

    def metadata(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "official_url": "https://github.com/geekfeiw/ARIL",
            "status": "stub",
            "reason": (
                "The official ARIL repository links Google Drive data but does not expose "
                "a small machine-readable sample in the code repository. Provide a sample "
                "or the original data description before parsing."
            ),
        }

    def discover_files(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(self.root.rglob("*"))

    def iter_recordings(self) -> Iterator[CsiRecord]:
        raise NotImplementedError(self.metadata()["reason"])
