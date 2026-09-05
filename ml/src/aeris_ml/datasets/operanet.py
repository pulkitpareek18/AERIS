"""Adapter scaffold for OPERAnet."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from aeris_ml.datasets.base import DatasetLoader
from aeris_ml.schema import CsiRecord


class OperanetDataset(DatasetLoader):
    dataset_id = "operanet"

    def metadata(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "official_url": "https://www.nature.com/articles/s41597-022-01573-2",
            "status": "stub",
            "reason": (
                "OPERAnet is distributed as a Figshare multimodal archive. This adapter "
                "needs a local file listing or sample from the WiFi CSI modality before "
                "a parser can be safely implemented."
            ),
        }

    def discover_files(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(self.root.rglob("*csi*"))

    def iter_recordings(self) -> Iterator[CsiRecord]:
        raise NotImplementedError(self.metadata()["reason"])
