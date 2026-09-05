"""AERIS Recorder session loader with Nexmon CSI PCAP extraction."""

from __future__ import annotations

import csv
import json
import struct
import sys
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np

from aeris_ml.datasets.base import DatasetLoader
from aeris_ml.labels import map_label
from aeris_ml.preprocessing import infer_sampling_rate
from aeris_ml.schema import CsiRecord


class PcapDecodeError(RuntimeError):
    pass


def _classic_pcap_layout(magic: bytes) -> tuple[str, float]:
    layouts = {
        b"\xd4\xc3\xb2\xa1": ("<", 1_000_000.0),
        b"\xa1\xb2\xc3\xd4": (">", 1_000_000.0),
        b"\x4d\x3c\xb2\xa1": ("<", 1_000_000_000.0),
        b"\xa1\xb2\x3c\x4d": (">", 1_000_000_000.0),
    }
    try:
        return layouts[magic]
    except KeyError as exc:
        raise PcapDecodeError("Only classic PCAP files are supported in this milestone.") from exc


def _udp_payload(packet: bytes) -> bytes | None:
    ip_offset = 18 if packet[12:14] == b"\x81\x00" else 14
    if len(packet) <= ip_offset or packet[ip_offset] >> 4 != 4:
        return None
    ip_header_len = (packet[ip_offset] & 0x0F) * 4
    if len(packet) < ip_offset + ip_header_len + 8 or packet[ip_offset + 9] != 17:
        return None
    udp_offset = ip_offset + ip_header_len
    destination_port = struct.unpack("!H", packet[udp_offset + 2 : udp_offset + 4])[0]
    if destination_port != 5500:
        return None
    return packet[udp_offset + 8 :]


def _parse_nexmon_payload(payload: bytes) -> dict[str, Any] | None:
    """Parse the official Nexmon CSI UDP payload.

    The Nexmon README documents: magic bytes, source MAC, sequence number,
    core/spatial-stream byte pair, chanspec, chip version, then CSI int16
    real/imag pairs. AERIS current hardware is bcm43455c0, for which the CSI
    values are signed 16-bit integers.
    """

    if len(payload) < 20 or payload[:2] != b"\x11\x11":
        return None

    # Modern Nexmon payload: 2-byte magic. Some older captures used 4-byte
    # magic; support both without changing recorder behavior.
    if payload[:4] == b"\x11\x11\x11\x11":
        header_len = 18
        source_start = 4
    else:
        header_len = 16
        source_start = 2

    if len(payload) <= header_len or (len(payload) - header_len) < 4:
        return None

    source_mac = ":".join(f"{byte:02x}" for byte in payload[source_start : source_start + 6])
    seq = struct.unpack("<H", payload[source_start + 6 : source_start + 8])[0]
    core_spatial = struct.unpack("<H", payload[source_start + 8 : source_start + 10])[0]
    chanspec = struct.unpack("<H", payload[source_start + 10 : source_start + 12])[0]
    chip_version = struct.unpack("<H", payload[source_start + 12 : source_start + 14])[0]
    core = core_spatial & 0x7
    spatial_stream = (core_spatial >> 3) & 0x7

    raw = payload[header_len:]
    raw = raw[: len(raw) - (len(raw) % 4)]
    if not raw:
        return None
    pairs = np.frombuffer(raw, dtype="<i2").reshape(-1, 2)
    csi = pairs[:, 0].astype(np.float32) + 1j * pairs[:, 1].astype(np.float32)

    return {
        "source_mac": source_mac,
        "sequence": int(seq),
        "core": int(core),
        "spatial_stream": int(spatial_stream),
        "link_id": f"core{core}_ss{spatial_stream}",
        "chanspec": int(chanspec),
        "chip_version": int(chip_version),
        "csi": csi,
    }


def _subcarrier_positions(count: int) -> np.ndarray:
    half = count // 2
    return np.arange(-half, count - half, dtype=float)


def _nexmon_mask(link_count: int, subcarrier_count: int) -> np.ndarray:
    positions = _subcarrier_positions(subcarrier_count)
    valid = np.ones(subcarrier_count, dtype=bool)
    if subcarrier_count == 64:
        invalid = {-32, -31, -30, -29, 0, 29, 30, 31}
    elif subcarrier_count == 128:
        invalid = set(range(-64, -58)) | {-1, 0, 1} | set(range(59, 64))
    elif subcarrier_count == 256:
        invalid = set(range(-128, -122)) | {-1, 0, 1} | set(range(123, 128))
    else:
        invalid = {0}
    valid[np.isin(positions, list(invalid))] = False
    return np.tile(valid, (link_count, 1))


def read_nexmon_pcap(path: str | Path) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    pcap_path = Path(path)
    frames: list[tuple[float, dict[str, Any]]] = []
    with pcap_path.open("rb") as handle:
        header = handle.read(24)
        if len(header) != 24:
            raise PcapDecodeError("PCAP global header is incomplete.")
        endian, scale = _classic_pcap_layout(header[:4])
        while True:
            record_header = handle.read(16)
            if not record_header:
                break
            if len(record_header) != 16:
                raise PcapDecodeError("PCAP record header is truncated.")
            sec, fraction, captured_length, _original_length = struct.unpack(endian + "IIII", record_header)
            packet = handle.read(captured_length)
            if len(packet) != captured_length:
                raise PcapDecodeError("PCAP packet body is truncated.")
            payload = _udp_payload(packet)
            if payload is None:
                continue
            parsed = _parse_nexmon_payload(payload)
            if parsed is None:
                continue
            frames.append((sec + fraction / scale, parsed))

    if not frames:
        raise PcapDecodeError("No Nexmon CSI UDP payloads were decoded from the PCAP.")

    frames.sort(key=lambda item: item[0])
    link_ids = sorted({frame["link_id"] for _ts, frame in frames})
    link_index = {link_id: idx for idx, link_id in enumerate(link_ids)}
    subcarrier_count = max(int(frame["csi"].shape[0]) for _ts, frame in frames)
    grouped: dict[tuple[int, float], dict[str, np.ndarray]] = defaultdict(dict)
    group_meta: dict[tuple[int, float], dict[str, Any]] = {}

    for ts, frame in frames:
        key = (int(frame["sequence"]), round(float(ts), 6))
        grouped[key][frame["link_id"]] = frame["csi"]
        group_meta[key] = frame

    timestamps: list[float] = []
    csi_rows: list[np.ndarray] = []
    for key in sorted(grouped, key=lambda item: item[1]):
        row = np.zeros((len(link_ids), subcarrier_count), dtype=np.complex64)
        for link_id, csi in grouped[key].items():
            row[link_index[link_id], : csi.shape[0]] = csi
        timestamps.append(key[1])
        csi_rows.append(row)

    csi = np.stack(csi_rows, axis=0)
    ts_arr = np.asarray(timestamps, dtype=float)
    sample_meta = next(iter(group_meta.values()))
    metadata = {
        "source": "nexmon_csi_udp_pcap",
        "source_macs": sorted({frame["source_mac"] for _ts, frame in frames}),
        "link_ids": link_ids,
        "subcarrier_count": subcarrier_count,
        "subcarrier_positions": _subcarrier_positions(subcarrier_count).tolist(),
        "chanspec": sample_meta.get("chanspec"),
        "chip_version": sample_meta.get("chip_version"),
        "packet_count": int(len(frames)),
        "sampling_rate_hz": infer_sampling_rate(ts_arr),
    }
    return csi, ts_arr, metadata


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _load_labels(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _import_existing_parser():
    repo_root = Path(__file__).resolve().parents[4]
    candidates = [
        repo_root / "aeris-recorder",
        repo_root,
    ]
    for candidate in candidates:
        if candidate.exists() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))

    try:
        import aeris_recorder_server  # type: ignore
    except Exception:
        return None
    return aeris_recorder_server.parse_pcap


class AerisPcapDataset(DatasetLoader):
    dataset_id = "aeris"

    def metadata(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "format": "AERIS session directories with Nexmon CSI PCAPs",
            "root": str(self.root),
        }

    def discover_files(self) -> list[Path]:
        if (self.root / "capture.pcap").exists():
            return [self.root / "capture.pcap"]
        if not self.root.exists():
            return []
        return sorted(self.root.glob("*/capture.pcap"))

    def iter_recordings(self) -> Iterator[CsiRecord]:
        parse_existing = _import_existing_parser()
        for pcap_path in self.discover_files():
            session_dir = pcap_path.parent
            metadata_json = _load_json(session_dir / "metadata.json")
            quality_json = _load_json(session_dir / "quality.json")
            labels = _load_labels(session_dir / "labels.csv")
            csi, timestamps, pcap_meta = read_nexmon_pcap(pcap_path)

            original_label = str(metadata_json.get("walking_type") or "")
            if not original_label:
                movement = next((row for row in labels if row.get("label") == "movement"), None)
                original_label = str(movement.get("label")) if movement else "unknown"

            existing_summary = None
            existing_error = None
            if parse_existing is not None:
                try:
                    existing_summary = parse_existing(pcap_path)
                except Exception as exc:
                    existing_error = str(exc)

            bandwidth = metadata_json.get("radio", {}).get("bandwidth_mhz") or metadata_json.get("bandwidth_mhz")
            mask = _nexmon_mask(csi.shape[1], csi.shape[2])
            merged_metadata = {
                "aeris_metadata": metadata_json,
                "aeris_quality": quality_json,
                "aeris_labels": labels,
                "pcap": pcap_meta,
                "existing_recorder_parse_pcap": existing_summary,
                "existing_recorder_parse_error": existing_error,
            }

            yield CsiRecord(
                csi=csi,
                timestamps=timestamps,
                rssi=None,
                subcarrier_frequencies=np.asarray(pcap_meta["subcarrier_positions"], dtype=float),
                mask=mask,
                label=map_label(original_label, "aeris"),
                original_label=original_label,
                dataset_id="aeris",
                sampling_rate_hz=pcap_meta.get("sampling_rate_hz"),
                antenna_link_labels=list(pcap_meta["link_ids"]),
                chipset=str(metadata_json.get("radio", {}).get("chipset") or "Broadcom BCM43455/Nexmon"),
                frequency_band=str(metadata_json.get("radio", {}).get("band") or ""),
                bandwidth_mhz=float(bandwidth) if bandwidth not in (None, "") else None,
                subject_id=str(metadata_json.get("participant_code") or ""),
                environment_id=str(metadata_json.get("location_code") or ""),
                session_id=str(metadata_json.get("session_id") or session_dir.name),
                los_nlos=metadata_json.get("scenario"),
                distance_m=metadata_json.get("pi_hotspot_distance_m"),
                metadata=merged_metadata,
            )
