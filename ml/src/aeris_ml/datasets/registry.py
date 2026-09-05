"""Machine-readable dataset registry."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any

import yaml


SOURCE_REGISTRY_PATH = Path(__file__).resolve().parents[3] / "configs" / "datasets.yaml"


def default_registry_path() -> Path:
    candidates = [
        SOURCE_REGISTRY_PATH,
        Path(sys.prefix) / "configs" / "datasets.yaml",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return SOURCE_REGISTRY_PATH


DEFAULT_REGISTRY_PATH = default_registry_path()


@dataclass
class DatasetInfo:
    dataset_id: str
    official_name: str
    official_url: str
    download_url: str | None
    citation: str
    license: str
    source_format: str
    csi_hardware_chipset: str
    frequency_band: str
    channel_bandwidth: str
    number_of_subcarriers: str
    antenna_configuration: str
    sampling_rate: str
    available_labels: list[str]
    intended_aeris_use: str
    loader_implementation_status: str
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DatasetInfo":
        return cls(
            dataset_id=str(data["dataset_id"]),
            official_name=str(data["official_name"]),
            official_url=str(data["official_url"]),
            download_url=data.get("download_url"),
            citation=str(data.get("citation") or "verification_required"),
            license=str(data.get("license") or "verification_required"),
            source_format=str(data.get("source_format") or "verification_required"),
            csi_hardware_chipset=str(data.get("csi_hardware_chipset") or "verification_required"),
            frequency_band=str(data.get("frequency_band") or "verification_required"),
            channel_bandwidth=str(data.get("channel_bandwidth") or "verification_required"),
            number_of_subcarriers=str(data.get("number_of_subcarriers") or "verification_required"),
            antenna_configuration=str(data.get("antenna_configuration") or "verification_required"),
            sampling_rate=str(data.get("sampling_rate") or "verification_required"),
            available_labels=list(data.get("available_labels") or []),
            intended_aeris_use=str(data.get("intended_aeris_use") or ""),
            loader_implementation_status=str(data.get("loader_implementation_status") or "stub"),
            notes=str(data.get("notes") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "official_name": self.official_name,
            "official_url": self.official_url,
            "download_url": self.download_url,
            "citation": self.citation,
            "license": self.license,
            "source_format": self.source_format,
            "csi_hardware_chipset": self.csi_hardware_chipset,
            "frequency_band": self.frequency_band,
            "channel_bandwidth": self.channel_bandwidth,
            "number_of_subcarriers": self.number_of_subcarriers,
            "antenna_configuration": self.antenna_configuration,
            "sampling_rate": self.sampling_rate,
            "available_labels": self.available_labels,
            "intended_aeris_use": self.intended_aeris_use,
            "loader_implementation_status": self.loader_implementation_status,
            "notes": self.notes,
        }


class DatasetRegistry:
    def __init__(self, path: str | Path = DEFAULT_REGISTRY_PATH) -> None:
        self.path = Path(path)
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        self._items = {
            item["dataset_id"]: DatasetInfo.from_dict(item)
            for item in data.get("datasets", [])
        }

    def list(self) -> list[DatasetInfo]:
        return [self._items[key] for key in sorted(self._items)]

    def get(self, dataset_id: str) -> DatasetInfo:
        try:
            return self._items[dataset_id]
        except KeyError as exc:
            known = ", ".join(sorted(self._items))
            raise KeyError(f"Unknown dataset '{dataset_id}'. Known datasets: {known}") from exc

    def ids(self) -> list[str]:
        return sorted(self._items)


def get_default_registry() -> DatasetRegistry:
    return DatasetRegistry(DEFAULT_REGISTRY_PATH)
