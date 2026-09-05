"""Common dataset loader protocol."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from aeris_ml.preprocessing import generate_windows, normalize_record, resample_record
from aeris_ml.schema import CsiRecord, save_record_npz
from aeris_ml.validation import ConversionSummary, validate_recording


def safe_stem(value: str | None, fallback: str = "recording") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", (value or "").strip()).strip("-._")
    return cleaned[:96] or fallback


class DatasetLoader(ABC):
    dataset_id: str

    def __init__(self, root: str | Path, **options: Any) -> None:
        self.root = Path(root).expanduser()
        self.options = dict(options)

    @abstractmethod
    def metadata(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def discover_files(self) -> list[Path]:
        raise NotImplementedError

    @abstractmethod
    def iter_recordings(self) -> Iterator[CsiRecord]:
        raise NotImplementedError

    def validate_recording(self, record: CsiRecord):
        return validate_recording(record)

    def convert(
        self,
        output_dir: str | Path,
        *,
        target_rate_hz: float = 50.0,
        window_s: float = 2.5,
        step_s: float = 0.5,
        normalize: bool = True,
        max_recordings: int | None = None,
    ) -> ConversionSummary:
        out = Path(output_dir).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        summary = ConversionSummary(recordings_discovered=len(self.discover_files()))

        for record_index, record in enumerate(self.iter_recordings()):
            if max_recordings is not None and record_index >= max_recordings:
                break
            report = self.validate_recording(record)
            if not report.ok:
                summary.recordings_rejected += 1
                summary.rejected.append(
                    {
                        "session_id": record.session_id,
                        "dataset_id": record.dataset_id,
                        "issues": report.to_dict()["issues"],
                    }
                )
                continue

            prepared = resample_record(record, target_rate_hz=target_rate_hz)
            if normalize:
                prepared = normalize_record(prepared)

            window_count = 0
            for window in generate_windows(prepared, window_s=window_s, step_s=step_s):
                stem = safe_stem(window.session_id, f"recording-{record_index:05d}-window-{window_count:05d}")
                save_record_npz(window, out / stem)
                summary.windows_generated += 1
                window_count += 1

            if window_count == 0:
                stem = safe_stem(prepared.session_id, f"recording-{record_index:05d}")
                save_record_npz(prepared, out / stem)

            summary.recordings_converted += 1
            summary.observe_record(record)

        return summary


class StubDatasetLoader(DatasetLoader):
    reason = "This dataset adapter needs a verified source-format sample or official format document."

    def metadata(self) -> dict[str, Any]:
        return {"dataset_id": self.dataset_id, "loader_status": "stub", "reason": self.reason}

    def discover_files(self) -> list[Path]:
        return []

    def iter_recordings(self) -> Iterator[CsiRecord]:
        raise NotImplementedError(f"{self.dataset_id}: {self.reason}")
