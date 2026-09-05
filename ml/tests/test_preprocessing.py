from __future__ import annotations

import numpy as np

from aeris_ml.labels import CommonLabel
from aeris_ml.preprocessing import (
    complex_to_amplitude,
    fill_missing_packets,
    generate_windows,
    log_amplitude,
    motion_energy,
    packet_gap_report,
    resample_record,
    resample_temporal,
    robust_normalize,
    temporal_difference_channel,
)
from aeris_ml.schema import CsiRecord


def test_resampling_and_missing_packet_fill() -> None:
    timestamps = np.array([0.0, 0.1, 0.3])
    csi = np.array([[[1.0, 2.0]], [[2.0, 4.0]], [[4.0, 8.0]]])
    out, new_ts = resample_temporal(csi, timestamps, target_rate_hz=10.0)
    assert out.shape == (4, 1, 2)
    np.testing.assert_allclose(new_ts, [0.0, 0.1, 0.2, 0.3])
    np.testing.assert_allclose(out[:, 0, 0], [1.0, 2.0, 3.0, 4.0])

    filled, filled_ts = fill_missing_packets(csi, timestamps, target_rate_hz=10.0)
    np.testing.assert_allclose(filled, out)
    np.testing.assert_allclose(filled_ts, new_ts)


def test_window_generation_and_variable_subcarriers() -> None:
    timestamps = np.arange(0.0, 4.0, 0.5)
    csi = np.ones((timestamps.size, 2, 7), dtype=np.float32)
    record = CsiRecord(
        csi=csi,
        timestamps=timestamps,
        label=CommonLabel.HUMAN_MOTION,
        original_label="walk",
        dataset_id="synthetic",
        sampling_rate_hz=2.0,
        mask=np.ones((2, 7), dtype=bool),
        session_id="var-subcarrier",
    )
    windows = list(generate_windows(record, window_s=1.0, step_s=0.5))
    assert len(windows) >= 5
    assert all(window.csi.shape[1:] == (2, 7) for window in windows)

    resampled = resample_record(record, target_rate_hz=4.0)
    assert resampled.csi.shape[1:] == (2, 7)


def test_amplitude_normalization_difference_and_energy() -> None:
    complex_csi = np.array([[[3 + 4j, 1 + 0j]], [[6 + 8j, 2 + 0j]], [[9 + 12j, 4 + 0j]]])
    amp = complex_to_amplitude(complex_csi)
    np.testing.assert_allclose(amp[:, 0, 0], [5.0, 10.0, 15.0])
    assert np.all(np.isfinite(log_amplitude(amp)))

    normalized, median, iqr = robust_normalize(amp)
    assert normalized.shape == amp.shape
    assert median.shape == (1, 1, 2)
    assert iqr.shape == (1, 1, 2)

    diff = temporal_difference_channel(amp)
    assert diff[0, 0, 0] == 0.0
    assert diff[1, 0, 0] == 5.0
    energy = motion_energy(complex_csi)
    assert energy.shape == (3,)
    assert energy[0] == 0.0


def test_packet_gap_report() -> None:
    report = packet_gap_report(np.array([0.0, 0.1, 0.2, 1.6]), excessive_gap_s=1.0)
    assert report["count"] == 3
    assert report["excessive_gap_count"] == 1
