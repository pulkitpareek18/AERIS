"""Loader for the Zenodo Wi-Fi CSI/RSS human presence and movement dataset."""

from __future__ import annotations

import csv
import gzip
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np

from aeris_ml.datasets.base import DatasetLoader
from aeris_ml.labels import map_label
from aeris_ml.preprocessing import infer_sampling_rate
from aeris_ml.schema import CsiRecord


class PresenceMovementDataset(DatasetLoader):
    dataset_id = "presence_movement"

    def metadata(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "official_url": "https://zenodo.org/records/3677366",
            "format": "line-delimited CSI JSON gzip files plus annotations.csv",
            "root": str(self.root),
        }

    def discover_files(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(self.root.glob("*.csi.json.gz")) + sorted(self.root.glob("*.csi.json"))

    def _load_annotations(self) -> list[dict[str, Any]]:
        path = self.root / "annotations.csv"
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def _iter_lines(self, path: Path):
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                yield from handle
        else:
            with path.open(encoding="utf-8") as handle:
                yield from handle

    def _load_csi_file(self, path: Path) -> tuple[np.ndarray, np.ndarray]:
        timestamps: list[float] = []
        samples: list[np.ndarray] = []
        max_packets = self.options.get("max_packets")
        for line_no, line in enumerate(self._iter_lines(path), start=1):
            if max_packets is not None and len(samples) >= int(max_packets):
                break
            if not line.strip():
                continue
            item = json.loads(line)
            try:
                timestamp = float(item["t"])
                csi_raw = item["csi"]
            except KeyError as exc:
                raise ValueError(f"{path}:{line_no} missing required key {exc}") from exc

            subcarriers: list[list[complex]] = []
            for subcarrier in csi_raw:
                links = []
                for value in subcarrier:
                    links.append(complex(float(value["r"]), float(value["i"])))
                subcarriers.append(links)
            arr = np.asarray(subcarriers, dtype=np.complex64)
            if arr.ndim != 2:
                raise ValueError(f"{path}:{line_no} CSI sample must be [subcarrier, antenna_link]")
            timestamps.append(timestamp)
            samples.append(arr.T)

        if not samples:
            raise ValueError(f"No CSI samples found in {path}")

        link_count = max(sample.shape[0] for sample in samples)
        subcarrier_count = max(sample.shape[1] for sample in samples)
        csi = np.zeros((len(samples), link_count, subcarrier_count), dtype=np.complex64)
        for idx, sample in enumerate(samples):
            csi[idx, : sample.shape[0], : sample.shape[1]] = sample
        return csi, np.asarray(timestamps, dtype=float)

    def _segments_for_file(self, path: Path, annotations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        stem = path.name.split(".csi.json")[0]
        room = stem.split("-")[0]
        segments = [row for row in annotations if row.get("room") == room]
        if segments:
            return segments
        return [
            {
                "begin_time": "",
                "end_time": "",
                "room": room,
                "oid": "",
                "label": "UNKNOWN",
            }
        ]

    def iter_recordings(self) -> Iterator[CsiRecord]:
        annotations = self._load_annotations()
        for path in self.discover_files():
            csi, timestamps = self._load_csi_file(path)
            stem = path.name.split(".csi.json")[0]
            for idx, segment in enumerate(self._segments_for_file(path, annotations)):
                label = str(segment.get("label") or "UNKNOWN")
                begin = segment.get("begin_time")
                end = segment.get("end_time")
                if begin and end:
                    selected = (timestamps >= float(begin)) & (timestamps <= float(end))
                else:
                    selected = np.ones_like(timestamps, dtype=bool)
                if not np.any(selected):
                    continue
                seg_csi = csi[selected]
                seg_ts = timestamps[selected]
                metadata: dict[str, Any] = {
                    "source_file": path.name,
                    "annotation": segment,
                    "format": "presence_movement_csi_json_lines",
                }
                yield CsiRecord(
                    csi=seg_csi,
                    timestamps=seg_ts,
                    rssi=None,
                    subcarrier_frequencies=np.arange(seg_csi.shape[2], dtype=float),
                    mask=np.ones(seg_csi.shape[1:], dtype=bool),
                    label=map_label(label, self.dataset_id),
                    original_label=label,
                    dataset_id=self.dataset_id,
                    sampling_rate_hz=infer_sampling_rate(seg_ts),
                    antenna_link_labels=[f"link_{i}" for i in range(seg_csi.shape[1])],
                    chipset="Intel CSI Tool",
                    frequency_band=None,
                    bandwidth_mhz=None,
                    subject_id=str(segment.get("oid") or ""),
                    environment_id=str(segment.get("room") or stem.split("-")[0]),
                    session_id=f"{stem}-{idx:05d}-{label.lower()}",
                    los_nlos=None,
                    distance_m=None,
                    metadata=metadata,
                )
