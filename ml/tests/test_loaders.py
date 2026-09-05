from __future__ import annotations

import gzip
import json
import struct
from pathlib import Path

import numpy as np

from aeris_ml.datasets.aeris_pcap import AerisPcapDataset
from aeris_ml.datasets.csi_traces import CsiTracesDataset
from aeris_ml.datasets.presence_movement import PresenceMovementDataset
from aeris_ml.labels import CommonLabel


def _write_presence_fixture(root: Path) -> None:
    (root / "annotations.csv").write_text(
        "begin_time,end_time,room,oid,label\n"
        "1.000000,4.000000,G21,O1,Mobile\n"
        "5.000000,6.000000,G21,O1,Stationary\n",
        encoding="utf-8",
    )
    with gzip.open(root / "G21-13.csi.json.gz", "wt", encoding="utf-8") as handle:
        for idx in range(7):
            sample = {
                "t": float(idx),
                "csi": [
                    [{"r": idx + 1, "i": 0}, {"r": idx + 2, "i": 1}],
                    [{"r": idx + 3, "i": 0}, {"r": idx + 4, "i": 1}],
                    [{"r": idx + 5, "i": 0}, {"r": idx + 6, "i": 1}],
                ],
            }
            handle.write(json.dumps(sample) + "\n")


def _ipv4_udp_packet(payload: bytes) -> bytes:
    ethernet = b"\xaa" * 6 + b"\xbb" * 6 + b"\x08\x00"
    total_length = 20 + 8 + len(payload)
    ip = struct.pack(
        "!BBHHHBBH4s4s",
        0x45,
        0,
        total_length,
        0,
        0,
        64,
        17,
        0,
        b"\x0a\x00\x00\x01",
        b"\x0a\x00\x00\x02",
    )
    udp = struct.pack("!HHHH", 12345, 5500, 8 + len(payload), 0)
    return ethernet + ip + udp + payload


def _nexmon_payload(sequence: int, values: np.ndarray) -> bytes:
    source = bytes.fromhex("001122334455")
    core_spatial = 0
    chanspec = 0x1000
    chip = 0x4345
    header = b"\x11\x11" + source + struct.pack("<HHHH", sequence, core_spatial, chanspec, chip)
    pairs = np.column_stack([values.astype("<i2"), np.zeros(values.shape[0], dtype="<i2")])
    return header + pairs.astype("<i2").tobytes()


def _write_aeris_session(root: Path) -> None:
    session = root / "20260822-120000-L001-P001-enter"
    session.mkdir()
    values = np.arange(1, 65, dtype=np.int16)
    pcap_header = b"\xd4\xc3\xb2\xa1" + struct.pack("<HHIIII", 2, 4, 0, 0, 65535, 1)
    records = []
    for idx, ts in enumerate([1.0, 1.025, 1.05]):
        payload = _nexmon_payload(idx + 1, values + idx)
        packet = _ipv4_udp_packet(payload)
        sec = int(ts)
        usec = int(round((ts - sec) * 1_000_000))
        records.append(struct.pack("<IIII", sec, usec, len(packet), len(packet)) + packet)
    (session / "capture.pcap").write_bytes(pcap_header + b"".join(records))
    (session / "metadata.json").write_text(
        json.dumps(
            {
                "session_id": session.name,
                "participant_code": "P001",
                "location_code": "L001",
                "walking_type": "enter",
                "scenario": "LOS",
                "pi_hotspot_distance_m": 3.2,
                "radio": {"bandwidth_mhz": 20},
            }
        ),
        encoding="utf-8",
    )
    (session / "labels.csv").write_text(
        "phase_index,label,start_epoch,end_epoch,start_s,end_s,valid_for_training,instruction\n"
        "0,movement,1.0,2.0,0.0,1.0,True,Walk\n",
        encoding="utf-8",
    )
    (session / "quality.json").write_text(json.dumps({"quality_status": "PASS"}), encoding="utf-8")


def test_presence_movement_loader(tmp_path) -> None:
    _write_presence_fixture(tmp_path)
    loader = PresenceMovementDataset(tmp_path)
    assert len(loader.discover_files()) == 1
    records = list(loader.iter_recordings())
    assert len(records) == 2
    mobile = records[0]
    assert mobile.label == CommonLabel.HUMAN_MOTION
    assert mobile.original_label == "Mobile"
    assert mobile.csi.shape == (4, 2, 3)
    assert mobile.antenna_link_labels == ["link_0", "link_1"]


def test_aeris_loader_reuses_session_format_and_decodes_nexmon(tmp_path) -> None:
    _write_aeris_session(tmp_path)
    loader = AerisPcapDataset(tmp_path)
    records = list(loader.iter_recordings())
    assert len(records) == 1
    record = records[0]
    assert record.dataset_id == "aeris"
    assert record.label == CommonLabel.ENTER
    assert record.csi.shape == (3, 1, 64)
    assert record.mask.shape == (1, 64)
    assert int(record.csi[0, 0, 0].real) == 1
    assert record.metadata["existing_recorder_parse_pcap"]["packet_count"] == 3


def test_stub_loader_raises_informative_error(tmp_path) -> None:
    loader = CsiTracesDataset(tmp_path)
    assert loader.discover_files() == []
    try:
        list(loader.iter_recordings())
    except NotImplementedError as exc:
        assert "esp_col_desp.md" in str(exc)
    else:
        raise AssertionError("Expected CSI Traces stub to raise NotImplementedError")
