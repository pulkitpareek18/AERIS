"""Partial loaders for verified SenseFi processed-data layouts."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np

from aeris_ml.datasets.base import DatasetLoader
from aeris_ml.labels import map_label
from aeris_ml.preprocessing import infer_sampling_rate
from aeris_ml.schema import CsiRecord


UT_HAR_CLASSES = ["lie_down", "fall", "walk", "pickup", "run", "sit_down", "stand_up"]


class SenseFiProcessedDataset(DatasetLoader):
    """Load the parts of SenseFi whose processed format is verified by code.

    UT-HAR data/label arrays and Widar CSV BVP samples are supported. NTU-Fi
    .mat amplitude files intentionally raise until scipy and a local sample are
    available.
    """

    def __init__(self, root: str | Path, dataset_id: str, **options: Any) -> None:
        self.dataset_id = dataset_id
        super().__init__(root, **options)

    def metadata(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "official_url": "https://github.com/xyanchen/WiFi-CSI-Sensing-Benchmark",
            "format": "SenseFi processed arrays",
            "root": str(self.root),
        }

    def _find_ut_root(self) -> Path:
        for candidate in [self.root, self.root / "UT_HAR", self.root / "Data" / "UT_HAR"]:
            if (candidate / "data").exists() and (candidate / "label").exists():
                return candidate
        return self.root / "UT_HAR"

    def _find_widar_root(self) -> Path:
        for candidate in [self.root, self.root / "Widardata", self.root / "Data" / "Widardata"]:
            if candidate.exists():
                return candidate
        return self.root / "Widardata"

    def discover_files(self) -> list[Path]:
        if self.dataset_id == "sensefi_ut_har":
            root = self._find_ut_root()
            return sorted((root / "data").glob("*.csv")) + sorted((root / "data").glob("*.npy"))
        if self.dataset_id == "sensefi_widar":
            root = self._find_widar_root()
            return sorted(root.rglob("*.csv"))
        if self.dataset_id in {"sensefi_ntu_fi_har", "sensefi_ntu_fi_humanid"}:
            return sorted(self.root.rglob("*.mat")) if self.root.exists() else []
        return []

    def _iter_ut_har(self) -> Iterator[CsiRecord]:
        root = self._find_ut_root()
        data_files = sorted((root / "data").glob("*"))
        label_files = sorted((root / "label").glob("*"))
        label_arrays: list[np.ndarray] = []
        for path in label_files:
            with path.open("rb") as handle:
                label_arrays.append(np.load(handle, allow_pickle=False).reshape(-1))
        labels = np.concatenate(label_arrays) if label_arrays else np.asarray([], dtype=int)

        global_index = 0
        for data_path in data_files:
            with data_path.open("rb") as handle:
                data = np.load(handle, allow_pickle=False)
            data = data.reshape(len(data), 1, 250, 90)
            for sample_idx, sample in enumerate(data):
                label_idx = int(labels[global_index]) if global_index < labels.size else -1
                original = UT_HAR_CLASSES[label_idx] if 0 <= label_idx < len(UT_HAR_CLASSES) else str(label_idx)
                # SenseFi shape is [link, time, subcarrier]; canonical is [time, link, subcarrier].
                csi = np.transpose(sample, (1, 0, 2)).astype(np.float32)
                timestamps = np.arange(csi.shape[0], dtype=float)
                yield CsiRecord(
                    csi=csi,
                    timestamps=timestamps,
                    label=map_label(original, self.dataset_id),
                    original_label=original,
                    dataset_id=self.dataset_id,
                    sampling_rate_hz=infer_sampling_rate(timestamps),
                    subcarrier_frequencies=np.arange(csi.shape[2], dtype=float),
                    mask=np.ones(csi.shape[1:], dtype=bool),
                    antenna_link_labels=["processed_link_0"],
                    chipset="Intel 5300 NIC",
                    session_id=f"{data_path.stem}-{sample_idx:05d}",
                    metadata={"source_file": str(data_path.name), "representation": "processed_ut_har_amplitude"},
                )
                global_index += 1

    def _iter_widar(self) -> Iterator[CsiRecord]:
        for path in self.discover_files():
            label = path.parent.name
            data = np.genfromtxt(path, delimiter=",")
            try:
                bvp = data.reshape(22, 20, 20)
            except ValueError as exc:
                raise ValueError(f"{path} does not match SenseFi Widar BVP shape 22x20x20") from exc
            # Treat the second BVP axis as time for a canonical placeholder. It is
            # explicitly marked as processed BVP, not raw CSI.
            csi = np.transpose(bvp, (1, 0, 2)).astype(np.float32)
            timestamps = np.arange(csi.shape[0], dtype=float)
            yield CsiRecord(
                csi=csi,
                timestamps=timestamps,
                label=map_label(label, self.dataset_id),
                original_label=label,
                dataset_id=self.dataset_id,
                sampling_rate_hz=infer_sampling_rate(timestamps),
                subcarrier_frequencies=np.arange(csi.shape[2], dtype=float),
                mask=np.ones(csi.shape[1:], dtype=bool),
                antenna_link_labels=[f"bvp_component_{idx}" for idx in range(csi.shape[1])],
                chipset="Intel 5300/Widar processed BVP",
                session_id=path.stem,
                metadata={"source_file": str(path), "representation": "processed_widar_bvp"},
            )

    def iter_recordings(self) -> Iterator[CsiRecord]:
        if self.dataset_id == "sensefi_ut_har":
            yield from self._iter_ut_har()
            return
        if self.dataset_id == "sensefi_widar":
            yield from self._iter_widar()
            return
        raise NotImplementedError(
            f"{self.dataset_id}: NTU-Fi processed .mat loading requires scipy and a local sample for verification."
        )
