"""Incremental Nexmon PCAP decoder for a live byte stream."""

from __future__ import annotations

import struct
from collections.abc import Iterator
from typing import BinaryIO, Any

import numpy as np

from aeris_ml.datasets.aeris_pcap import (
    _classic_pcap_layout,
    _parse_nexmon_payload,
    _udp_payload,
)


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise EOFError("CSI stream ended")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def iter_nexmon_pcap_stream(stream: BinaryIO) -> Iterator[tuple[float, np.ndarray, dict[str, Any]]]:
    """Yield decoded CSI rows from a PCAP stream as records arrive.

    The recorder's tcpdump file is tailed over SSH. This parser consumes the
    global header once, then parses each complete packet without rereading old
    bytes. CSI packets sharing sequence/timestamp are grouped into one row.
    """
    header = _read_exact(stream, 24)
    endian, scale = _classic_pcap_layout(header[:4])

    current_key: tuple[int, float] | None = None
    grouped: dict[str, np.ndarray] = {}
    current_meta: dict[str, Any] = {}

    while True:
        record_header = _read_exact(stream, 16)
        sec, fraction, captured_length, _original_length = struct.unpack(
            endian + "IIII", record_header
        )
        packet = _read_exact(stream, captured_length)
        payload = _udp_payload(packet)
        if payload is None:
            continue
        parsed = _parse_nexmon_payload(payload)
        if parsed is None:
            continue

        timestamp = sec + fraction / scale
        key = (int(parsed["sequence"]), round(float(timestamp), 6))
        if current_key is not None and key != current_key and grouped:
            yield _assemble_row(current_key, grouped, current_meta)
            grouped = {}
        current_key = key
        grouped[parsed["link_id"]] = parsed["csi"]
        current_meta = parsed


def _assemble_row(
    key: tuple[int, float], grouped: dict[str, np.ndarray], meta: dict[str, Any]
) -> tuple[float, np.ndarray, dict[str, Any]]:
    link_ids = sorted(grouped)
    subcarrier_count = max(value.shape[0] for value in grouped.values())
    row = np.zeros((len(link_ids), subcarrier_count), dtype=np.complex64)
    for link_index, link_id in enumerate(link_ids):
        values = grouped[link_id]
        row[link_index, : values.shape[0]] = values
    return key[1], row, {
        "link_ids": link_ids,
        "subcarrier_count": subcarrier_count,
        "sequence": key[0],
        "chanspec": meta.get("chanspec"),
        "chip_version": meta.get("chip_version"),
    }
