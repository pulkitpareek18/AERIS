"""Validation checks and conversion summaries for AERIS CSI records."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from aeris_ml.labels import CommonLabel
from aeris_ml.preprocessing import infer_sampling_rate, packet_gap_report
from aeris_ml.schema import CsiRecord


@dataclass
class ValidationIssue:
    severity: str
    code: str
    message: str


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def add(self, severity: str, code: str, message: str) -> None:
        self.issues.append(ValidationIssue(severity, code, message))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issues": [issue.__dict__ for issue in self.issues],
        }


def validate_recording(record: CsiRecord, excessive_gap_s: float = 1.0) -> ValidationReport:
    report = ValidationReport()
    csi = np.asarray(record.csi)
    ts = np.asarray(record.timestamps, dtype=float)

    if csi.ndim != 3:
        report.add("error", "invalid_dimensions", "CSI must use [time, antenna_link, subcarrier] axes.")
    elif 0 in csi.shape:
        report.add("error", "empty_recording", f"CSI contains an empty dimension: {csi.shape}.")

    if ts.ndim != 1 or ts.size == 0:
        report.add("error", "empty_timestamps", "timestamps must be a non-empty 1D array.")
    elif csi.ndim == 3 and ts.size != csi.shape[0]:
        report.add("error", "time_dimension_mismatch", "timestamps length does not match CSI time dimension.")

    if ts.size and not np.all(np.isfinite(ts)):
        report.add("error", "invalid_timestamps", "timestamps contain NaN or infinite values.")
    if ts.size > 1 and np.any(np.diff(ts) <= 0):
        report.add("error", "non_monotonic_timestamps", "timestamps must be strictly increasing.")

    if csi.size and not np.all(np.isfinite(csi)):
        report.add("error", "invalid_csi_values", "CSI contains NaN or infinite values.")

    if record.rssi is not None and not np.all(np.isfinite(record.rssi)):
        report.add("error", "invalid_rssi_values", "RSSI contains NaN or infinite values.")

    if record.label is None or record.label == CommonLabel.UNKNOWN:
        report.add("warning", "missing_or_unknown_label", "record label is UNKNOWN; keep original label for later review.")
    if not record.original_label:
        report.add("warning", "missing_original_label", "original dataset label is missing.")
    if not record.dataset_id:
        report.add("error", "missing_dataset_id", "dataset_id is required.")

    if csi.ndim == 3:
        mask = np.asarray(record.mask) if record.mask is not None else np.ones(csi.shape[1:], dtype=bool)
        if mask.shape != csi.shape[1:]:
            report.add("error", "invalid_mask_shape", f"mask shape {mask.shape} does not match {csi.shape[1:]}.")
        elif not np.any(mask):
            report.add("error", "empty_subcarrier_mask", "mask has no valid antenna/subcarrier entries.")

    if ts.size > 1 and np.all(np.diff(ts) > 0):
        gaps = packet_gap_report(ts, excessive_gap_s=excessive_gap_s)
        if gaps["excessive_gap_count"]:
            report.add("warning", "excessive_packet_gaps", f"{gaps['excessive_gap_count']} gaps exceed {excessive_gap_s}s.")

    return report


@dataclass
class ConversionSummary:
    recordings_discovered: int = 0
    recordings_converted: int = 0
    recordings_rejected: int = 0
    windows_generated: int = 0
    class_distribution: Counter[str] = field(default_factory=Counter)
    dataset_distribution: Counter[str] = field(default_factory=Counter)
    subjects: set[str] = field(default_factory=set)
    environments: set[str] = field(default_factory=set)
    packet_rates_hz: list[float] = field(default_factory=list)
    maximum_gaps_s: list[float] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)

    def observe_record(self, record: CsiRecord) -> None:
        self.class_distribution[record.label.value] += 1
        self.dataset_distribution[record.dataset_id] += 1
        if record.subject_id:
            self.subjects.add(str(record.subject_id))
        if record.environment_id:
            self.environments.add(str(record.environment_id))
        rate = record.sampling_rate_hz or infer_sampling_rate(record.timestamps)
        if rate:
            self.packet_rates_hz.append(float(rate))
        if record.timestamps.size > 1 and np.all(np.diff(record.timestamps) > 0):
            self.maximum_gaps_s.append(float(np.max(np.diff(record.timestamps))))

    def _stats(self, values: list[float]) -> dict[str, float | None]:
        if not values:
            return {"min": None, "median": None, "max": None}
        arr = np.asarray(values, dtype=float)
        return {"min": float(np.min(arr)), "median": float(np.median(arr)), "max": float(np.max(arr))}

    def to_dict(self) -> dict[str, Any]:
        return {
            "recordings_discovered": self.recordings_discovered,
            "recordings_converted": self.recordings_converted,
            "recordings_rejected": self.recordings_rejected,
            "windows_generated": self.windows_generated,
            "class_distribution": dict(self.class_distribution),
            "dataset_distribution": dict(self.dataset_distribution),
            "subjects": sorted(self.subjects),
            "environments": sorted(self.environments),
            "packet_rate_statistics_hz": self._stats(self.packet_rates_hz),
            "gap_statistics_s": self._stats(self.maximum_gaps_s),
            "rejected": self.rejected,
        }
