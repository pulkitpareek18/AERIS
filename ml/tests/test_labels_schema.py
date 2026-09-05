from __future__ import annotations

import numpy as np

from aeris_ml.labels import CommonLabel, map_label
from aeris_ml.schema import CsiRecord, load_record_npz, save_record_npz
from aeris_ml.validation import validate_recording


def _record() -> CsiRecord:
    return CsiRecord(
        csi=np.arange(30, dtype=np.float32).reshape(5, 2, 3),
        timestamps=np.arange(5, dtype=float) * 0.02,
        rssi=np.linspace(-50, -46, 5),
        subcarrier_frequencies=np.array([-1.0, 0.0, 1.0]),
        mask=np.ones((2, 3), dtype=bool),
        label=CommonLabel.HUMAN_MOTION,
        original_label="walking",
        dataset_id="synthetic",
        sampling_rate_hz=50.0,
        antenna_link_labels=["rx0_tx0", "rx1_tx0"],
        chipset="synthetic",
        subject_id="P001",
        environment_id="L001",
        session_id="synthetic-session",
        metadata={"nested": {"ok": True}},
    )


def test_label_mapping_is_conservative() -> None:
    assert map_label("empty") == CommonLabel.EMPTY
    assert map_label("standing") == CommonLabel.HUMAN_STATIC
    assert map_label("Mobile", "presence_movement") == CommonLabel.HUMAN_MOTION
    assert map_label("enter", "aeris") == CommonLabel.ENTER
    assert map_label("exit", "aeris") == CommonLabel.EXIT
    assert map_label("across_left_to_right", "aeris") == CommonLabel.ACROSS
    assert map_label("humanid_subject_01") == CommonLabel.UNKNOWN
    assert map_label("breathing") == CommonLabel.UNKNOWN
    assert map_label("fan") == CommonLabel.NONHUMAN_MOTION


def test_npz_json_round_trip(tmp_path) -> None:
    record = _record()
    npz_path, json_path = save_record_npz(record, tmp_path / "sample")
    assert npz_path.exists()
    assert json_path.exists()

    loaded = load_record_npz(npz_path)
    assert loaded.label == record.label
    assert loaded.original_label == "walking"
    assert loaded.dataset_id == "synthetic"
    np.testing.assert_allclose(loaded.csi, record.csi)
    np.testing.assert_allclose(loaded.timestamps, record.timestamps)
    assert loaded.metadata["nested"]["ok"] is True


def test_schema_validation_rejects_bad_records() -> None:
    good = _record()
    assert validate_recording(good).ok

    bad_ts = good.copy_with(timestamps=np.array([0.0, 0.1, 0.05, 0.2, 0.3]))
    report = validate_recording(bad_ts)
    assert not report.ok
    assert any(issue.code == "non_monotonic_timestamps" for issue in report.issues)

    bad_csi = good.copy_with(csi=good.csi.copy())
    bad_csi.csi[0, 0, 0] = np.nan
    report = validate_recording(bad_csi)
    assert not report.ok
    assert any(issue.code == "invalid_csi_values" for issue in report.issues)

    bad_mask = good.copy_with(mask=np.zeros((2, 3), dtype=bool))
    report = validate_recording(bad_mask)
    assert not report.ok
    assert any(issue.code == "empty_subcarrier_mask" for issue in report.issues)
