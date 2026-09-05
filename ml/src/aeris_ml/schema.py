"""Canonical AERIS CSI record schema and safe serialization."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np

from aeris_ml.labels import CommonLabel


AXIS_ORDER = {
    "csi": "[time, antenna_link, subcarrier]",
    "mask": "[antenna_link, subcarrier]",
    "rssi": "[time] or [time, antenna_link]",
}


def _json_safe(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


@dataclass
class CsiRecord:
    """One CSI recording or window in the canonical AERIS format.

    `csi` uses axis order `[time, antenna_link, subcarrier]`. Values may be
    complex CSI or real-valued amplitude, but all windows in one prepared corpus
    should use the same representation.
    """

    csi: np.ndarray
    timestamps: np.ndarray
    label: CommonLabel
    original_label: str
    dataset_id: str
    sampling_rate_hz: float | None = None
    rssi: np.ndarray | None = None
    subcarrier_frequencies: np.ndarray | None = None
    antenna_link_labels: list[str] = field(default_factory=list)
    mask: np.ndarray | None = None
    chipset: str | None = None
    frequency_band: str | None = None
    bandwidth_mhz: float | None = None
    subject_id: str | None = None
    environment_id: str | None = None
    session_id: str | None = None
    los_nlos: str | None = None
    distance_m: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.csi = np.asarray(self.csi)
        self.timestamps = np.asarray(self.timestamps, dtype=float)
        if not isinstance(self.label, CommonLabel):
            self.label = CommonLabel(str(self.label))
        if self.rssi is not None:
            self.rssi = np.asarray(self.rssi, dtype=float)
        if self.subcarrier_frequencies is not None:
            self.subcarrier_frequencies = np.asarray(self.subcarrier_frequencies, dtype=float)
        if self.mask is not None:
            self.mask = np.asarray(self.mask, dtype=bool)
        elif self.csi.ndim == 3:
            self.mask = np.ones(self.csi.shape[1:], dtype=bool)
        if not self.antenna_link_labels and self.csi.ndim == 3:
            self.antenna_link_labels = [f"link_{idx}" for idx in range(self.csi.shape[1])]

    def copy_with(self, **changes: Any) -> "CsiRecord":
        return replace(self, **changes)

    def metadata_json(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "axis_order": AXIS_ORDER,
            "shape": {
                "csi": list(self.csi.shape),
                "timestamps": list(self.timestamps.shape),
                "rssi": list(self.rssi.shape) if self.rssi is not None else None,
                "mask": list(self.mask.shape) if self.mask is not None else None,
            },
            "dtype": {
                "csi": str(self.csi.dtype),
                "timestamps": str(self.timestamps.dtype),
                "rssi": str(self.rssi.dtype) if self.rssi is not None else None,
            },
            "label": self.label.value,
            "original_label": self.original_label,
            "dataset_id": self.dataset_id,
            "sampling_rate_hz": self.sampling_rate_hz,
            "antenna_link_labels": list(self.antenna_link_labels),
            "chipset": self.chipset,
            "frequency_band": self.frequency_band,
            "bandwidth_mhz": self.bandwidth_mhz,
            "subject_id": self.subject_id,
            "environment_id": self.environment_id,
            "session_id": self.session_id,
            "los_nlos": self.los_nlos,
            "distance_m": self.distance_m,
            "metadata": _json_safe(self.metadata),
        }


def save_record_npz(record: CsiRecord, path: str | Path) -> tuple[Path, Path]:
    """Write compressed NPZ arrays plus JSON sidecar metadata.

    The output never uses pickle. `path` may be either `stem` or `stem.npz`.
    """

    npz_path = Path(path)
    if npz_path.suffix != ".npz":
        npz_path = npz_path.with_suffix(".npz")
    json_path = npz_path.with_suffix(".json")
    npz_path.parent.mkdir(parents=True, exist_ok=True)

    arrays: dict[str, np.ndarray] = {
        "csi": record.csi,
        "timestamps": record.timestamps,
        "mask": record.mask if record.mask is not None else np.ones(record.csi.shape[1:], dtype=bool),
    }
    if record.rssi is not None:
        arrays["rssi"] = record.rssi
    if record.subcarrier_frequencies is not None:
        arrays["subcarrier_frequencies"] = record.subcarrier_frequencies

    np.savez_compressed(npz_path, **arrays)
    json_path.write_text(json.dumps(record.metadata_json(), indent=2, sort_keys=True), encoding="utf-8")
    return npz_path, json_path


def load_record_npz(path: str | Path) -> CsiRecord:
    npz_path = Path(path)
    json_path = npz_path.with_suffix(".json")
    if not json_path.exists():
        raise FileNotFoundError(f"Missing metadata sidecar: {json_path}")

    with np.load(npz_path, allow_pickle=False) as data:
        arrays = {name: data[name] for name in data.files}
    meta = json.loads(json_path.read_text(encoding="utf-8"))

    return CsiRecord(
        csi=arrays["csi"],
        timestamps=arrays["timestamps"],
        rssi=arrays.get("rssi"),
        subcarrier_frequencies=arrays.get("subcarrier_frequencies"),
        mask=arrays.get("mask"),
        label=CommonLabel(meta["label"]),
        original_label=meta.get("original_label", ""),
        dataset_id=meta.get("dataset_id", ""),
        sampling_rate_hz=meta.get("sampling_rate_hz"),
        antenna_link_labels=list(meta.get("antenna_link_labels") or []),
        chipset=meta.get("chipset"),
        frequency_band=meta.get("frequency_band"),
        bandwidth_mhz=meta.get("bandwidth_mhz"),
        subject_id=meta.get("subject_id"),
        environment_id=meta.get("environment_id"),
        session_id=meta.get("session_id"),
        los_nlos=meta.get("los_nlos"),
        distance_m=meta.get("distance_m"),
        metadata=dict(meta.get("metadata") or {}),
    )
