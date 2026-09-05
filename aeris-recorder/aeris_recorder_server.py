#!/usr/bin/env python3
"""AERIS Recorder v3 — local browser UI, SQLite metadata, and Nexmon CSI capture service."""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import shutil
import signal
import socket
import statistics
import struct
import subprocess
import threading
import time
import zipfile
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from aeris_db import (
    AerisDatabase,
    BODY_TYPES,
    CLOTHING_TYPES,
    DatabaseError,
    GENDERS,
    NotFoundError,
    SCENARIOS,
    ValidationError,
    WALKING_TYPES,
    iso_utc_now,
)


VERSION = "3.0.0"
APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "aeris-config.json"
UI_PATH = APP_DIR / "aeris-recorder.html"
if not UI_PATH.exists():
    UI_PATH = APP_DIR.parent / "public" / "aeris-recorder.html"

DEFAULT_SCHEDULE = [
    {
        "label": "warmup",
        "duration_s": 2,
        "valid_for_training": False,
        "display": "WARM-UP",
        "instruction": "Keep the monitored path empty.",
    },
    {
        "label": "empty_calibration",
        "duration_s": 10,
        "valid_for_training": True,
        "display": "EMPTY CALIBRATION",
        "instruction": "Stay outside the monitored path.",
    },
    {
        "label": "empty",
        "duration_s": 15,
        "valid_for_training": True,
        "display": "EMPTY",
        "instruction": "Keep the monitored path completely empty.",
    },
    {
        "label": "transition_enter",
        "duration_s": 3,
        "valid_for_training": False,
        "display": "ENTER NOW",
        "instruction": "Enter the monitored path and begin moving.",
    },
    {
        "label": "movement",
        "duration_s": 15,
        "valid_for_training": True,
        "display": "MOVEMENT",
        "instruction": "Walk naturally through the monitored path.",
    },
    {
        "label": "transition_exit",
        "duration_s": 3,
        "valid_for_training": False,
        "display": "EXIT NOW",
        "instruction": "Leave the monitored path completely.",
    },
    {
        "label": "empty",
        "duration_s": 15,
        "valid_for_training": True,
        "display": "EMPTY",
        "instruction": "Remain outside until the trial finishes.",
    },
]

DEFAULT_CONFIG = {
    "capture_interface": "wlan0",
    "traffic_interface": "wlan1",
    "data_directory": str(Path.home() / "aeris-data" / "sessions"),
    "database_path": str(Path.home() / "aeris-data" / "aeris.db"),
    "makecsiparams_directory": str(
        Path.home() / "nexmon/patches/bcm43455c0/7_45_189/nexmon_csi/utils/makecsiparams"
    ),
    "frame_control": "0x88",
    "ping_interval_s": 0.025,
    "ping_payload_bytes": 1000,
    "minimum_packet_rate": 40.0,
    "maximum_gap_s": 1.0,
    "probe_target": "1.1.1.1",
    "schedule": DEFAULT_SCHEDULE,
}


class RecorderError(RuntimeError):
    pass


def iso_utc(epoch: float | None = None) -> str:
    return datetime.fromtimestamp(epoch or time.time(), tz=timezone.utc).isoformat(
        timespec="milliseconds"
    )


def safe_id(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return cleaned[:48] or fallback


def run_command(
    args: list[str],
    timeout: float = 10,
    check: bool = True,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RecorderError(f"Required command not found: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RecorderError(f"Command timed out: {' '.join(args)}") from exc
    if check and result.returncode:
        raise RecorderError((result.stderr or result.stdout or "Command failed").strip())
    return result


def load_config() -> dict[str, Any]:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open(encoding="utf-8") as handle:
            config.update(json.load(handle))
    if not isinstance(config.get("schedule"), list) or not config["schedule"]:
        raise RecorderError("The experiment schedule is empty or invalid.")
    return config


def require_runtime(config: dict[str, Any]) -> None:
    required = ["iw", "ip", "nexutil", "tcpdump", "ping", "sudo", "make"]
    missing = [name for name in required if shutil.which(name) is None]
    if missing:
        raise RecorderError("Missing required commands: " + ", ".join(missing))
    tool_dir = Path(config["makecsiparams_directory"])
    if not (tool_dir / "makecsiparams").exists() and not (tool_dir / "makecsiparams.c").exists():
        raise RecorderError(f"makecsiparams was not found in {tool_dir}")
    if run_command(["sudo", "-n", "true"], timeout=3, check=False).returncode:
        raise RecorderError("Administrator authorization expired. Close and reopen AERIS Recorder.")


def detect_radio(config: dict[str, Any]) -> dict[str, Any]:
    traffic_if, capture_if = config["traffic_interface"], config["capture_interface"]
    link = run_command(["iw", "dev", traffic_if, "link"]).stdout
    info = run_command(["iw", "dev", traffic_if, "info"]).stdout
    route = run_command(["ip", "route", "show", "default", "dev", traffic_if]).stdout
    bssid = re.search(r"Connected to\s+([0-9a-fA-F:]{17})", link)
    ssid = re.search(r"^\s*SSID:\s*(.+)$", link, re.MULTILINE)
    channel = re.search(r"channel\s+(\d+).*?width:\s*(\d+)\s*MHz", info)
    gateway = re.search(r"\bvia\s+(\S+)", route)
    if not all((bssid, channel, gateway)):
        raise RecorderError(f"Could not read hotspot details from {traffic_if}.")
    address = lambda iface: (Path("/sys/class/net") / iface / "address").read_text().strip()
    return {
        "capture_interface": capture_if,
        "traffic_interface": traffic_if,
        "bssid": bssid.group(1).lower(),
        "ssid": ssid.group(1).strip() if ssid else "unknown",
        "channel": int(channel.group(1)),
        "bandwidth_mhz": int(channel.group(2)),
        "gateway": gateway.group(1),
        "capture_mac": address(capture_if),
        "traffic_mac": address(traffic_if),
    }


def configure_nexmon(config: dict[str, Any], radio: dict[str, Any]) -> dict[str, str]:
    tool_dir = Path(config["makecsiparams_directory"])
    run_command(["make", "-s"], timeout=60, cwd=tool_dir)
    encoded = run_command(
        [
            str(tool_dir / "makecsiparams"),
            "-c",
            f"{radio['channel']}/{radio['bandwidth_mhz']}",
            "-C",
            "1",
            "-N",
            "1",
            "-m",
            radio["bssid"],
            "-b",
            str(config["frame_control"]),
        ],
        cwd=tool_dir,
    ).stdout.strip()
    if not encoded:
        raise RecorderError("makecsiparams returned an empty CSI configuration.")
    capture_if = config["capture_interface"]
    run_command(["sudo", "-n", "ip", "link", "set", capture_if, "up"])
    run_command(["sudo", "-n", "iw", "dev", capture_if, "set", "power_save", "off"])
    run_command(
        ["sudo", "-n", "iw", "dev", config["traffic_interface"], "set", "power_save", "off"],
        check=False,
    )
    run_command(["nexutil", f"-I{capture_if}", "-s500", "-b", "-l34", f"-v{encoded}"], timeout=12)
    run_command(["nexutil", f"-I{capture_if}", "-m1"])
    monitor = run_command(["nexutil", f"-I{capture_if}", "-m"]).stdout.strip()
    chanspec = run_command(["nexutil", f"-I{capture_if}", "-k"]).stdout.strip()
    if "monitor: 1" not in monitor:
        raise RecorderError("Nexmon monitor mode did not enable.")
    if not re.search(rf",\s*{radio['channel']}\b", chanspec):
        raise RecorderError(f"wlan0 did not switch to hotspot channel {radio['channel']}.")
    return {"monitor_query": monitor, "channel_query": chanspec}


def stop_process(process: subprocess.Popen[Any] | None) -> None:
    if process is None or process.poll() is not None:
        return
    for sig, wait in ((signal.SIGINT, 3), (signal.SIGTERM, 2), (signal.SIGKILL, 1)):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            return
        except PermissionError:
            try:
                subprocess.run(
                    ["sudo", "-n", "kill", f"-{int(sig)}", f"-{process.pid}"],
                    check=False,
                    timeout=2,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                try:
                    process.send_signal(sig)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            process.wait(timeout=wait)
            return
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
        except Exception:
            pass


def percentile(values: list[float], percentage: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentage / 100
    lower, upper = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[lower] * (1 - position + lower) + ordered[upper] * (position - lower)


def parse_pcap(path: Path) -> dict[str, Any]:
    layouts = {
        b"\xd4\xc3\xb2\xa1": ("<", 1_000_000),
        b"\xa1\xb2\xc3\xd4": (">", 1_000_000),
        b"\x4d\x3c\xb2\xa1": ("<", 1_000_000_000),
        b"\xa1\xb2\x3c\x4d": (">", 1_000_000_000),
    }
    timestamps: list[float] = []
    rssis: list[int] = []
    frame_controls: Counter[str] = Counter()
    source_macs: Counter[str] = Counter()
    with path.open("rb") as handle:
        header = handle.read(24)
        if len(header) != 24 or header[:4] not in layouts:
            raise RecorderError("Capture is not a supported classic PCAP file.")
        endian, scale = layouts[header[:4]]
        while True:
            record = handle.read(16)
            if not record:
                break
            if len(record) != 16:
                break
            sec, fraction, captured_length, _ = struct.unpack(endian + "IIII", record)
            packet = handle.read(captured_length)
            if len(packet) < 48:
                continue
            ip_offset = 18 if packet[12:14] == b"\x81\x00" else 14
            if len(packet) <= ip_offset or packet[ip_offset] >> 4 != 4:
                continue
            udp_offset = ip_offset + (packet[ip_offset] & 0x0F) * 4
            if len(packet) < udp_offset + 8 or packet[ip_offset + 9] != 17:
                continue
            destination_port = struct.unpack("!H", packet[udp_offset + 2 : udp_offset + 4])[0]
            payload = packet[udp_offset + 8 :]
            if destination_port != 5500 or len(payload) < 18 or payload[:2] != b"\x11\x11":
                continue
            timestamps.append(sec + fraction / scale)
            rssis.append(struct.unpack("b", payload[2:3])[0])
            frame_controls[f"0x{payload[3]:02x}"] += 1
            source_macs[":".join(f"{byte:02x}" for byte in payload[4:10])] += 1
    if not timestamps:
        raise RecorderError("No Nexmon CSI packets were decoded from the capture.")
    gaps = [right - left for left, right in zip(timestamps, timestamps[1:])]
    duration = max(timestamps[-1] - timestamps[0], 0.0)
    return {
        "packet_count": len(timestamps),
        "first_packet_epoch": timestamps[0],
        "last_packet_epoch": timestamps[-1],
        "duration_s": duration,
        "packet_rate_hz": (len(timestamps) - 1) / duration if duration else 0.0,
        "median_rssi_dbm": statistics.median(rssis),
        "frame_controls": dict(frame_controls),
        "source_macs": dict(source_macs),
        "gap_p50_s": statistics.median(gaps) if gaps else 0.0,
        "gap_p95_s": percentile(gaps, 95),
        "gap_p99_s": percentile(gaps, 99),
        "maximum_gap_s": max(gaps, default=0.0),
        "outages": [
            {
                "start_epoch": left,
                "end_epoch": right,
                "start_s": left - timestamps[0],
                "end_s": right - timestamps[0],
                "duration_s": right - left,
            }
            for left, right in zip(timestamps, timestamps[1:])
            if right - left > 0.2
        ],
    }


def parse_tcpdump_summary(path: Path) -> dict[str, int | None]:
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    keys = {
        "packets_captured": r"(\d+) packets captured",
        "packets_received_by_filter": r"(\d+) packets received by filter",
        "packets_dropped_by_kernel": r"(\d+) packets dropped by kernel",
    }
    return {
        key: int(match.group(1)) if (match := re.search(pattern, text)) else None
        for key, pattern in keys.items()
    }


def write_labels(path: Path, events: list[dict[str, Any]], first_packet: float) -> None:
    fields = [
        "phase_index",
        "label",
        "start_epoch",
        "end_epoch",
        "start_s",
        "end_s",
        "valid_for_training",
        "instruction",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for event in events:
            row = dict(
                event,
                start_s=event["start_epoch"] - first_packet,
                end_s=event["end_epoch"] - first_packet,
            )
            writer.writerow({name: row[name] for name in fields})


def build_quality(
    parsed: dict[str, Any],
    config: dict[str, Any],
    source: str,
    tcpdump: dict[str, int | None],
) -> dict[str, Any]:
    expected_fc = str(config["frame_control"]).lower()
    fc_fraction = parsed["frame_controls"].get(expected_fc, 0) / parsed["packet_count"]
    required_duration = max(1.0, sum(float(p["duration_s"]) for p in config["schedule"]) - 2.0)
    checks = {
        "capture_duration": {
            "pass": parsed["duration_s"] >= required_duration,
            "value_s": parsed["duration_s"],
            "minimum_s": required_duration,
        },
        "packet_rate": {
            "pass": parsed["packet_rate_hz"] >= float(config["minimum_packet_rate"]),
            "value_hz": parsed["packet_rate_hz"],
            "minimum_hz": config["minimum_packet_rate"],
        },
        "maximum_gap": {
            "pass": parsed["maximum_gap_s"] <= float(config["maximum_gap_s"]),
            "value_s": parsed["maximum_gap_s"],
            "maximum_s": config["maximum_gap_s"],
        },
        "frame_control": {
            "pass": fc_fraction >= 0.99,
            "expected": expected_fc,
            "matching_fraction": fc_fraction,
        },
        "source_mac": {
            "pass": source.lower() in parsed["source_macs"],
            "expected": source.lower(),
            "observed": parsed["source_macs"],
        },
        "kernel_drops": {
            "pass": tcpdump.get("packets_dropped_by_kernel") == 0,
            "value": tcpdump.get("packets_dropped_by_kernel"),
        },
    }
    passed = all(item["pass"] for item in checks.values())
    return {
        "schema_version": 1,
        "generated_at_utc": iso_utc(),
        "quality_pass": passed,
        "quality_status": "PASS" if passed else "FAIL",
        "checks": checks,
        "tcpdump_summary": tcpdump,
        **parsed,
    }


class TrialRunner:
    def __init__(
        self,
        config: dict[str, Any],
        db: AerisDatabase,
        emit: Callable[..., None],
    ):
        self.config = config
        self.db = db
        self.emit = emit
        self.stop_requested = threading.Event()
        self.capture_process: subprocess.Popen[Any] | None = None
        self.traffic_process: subprocess.Popen[Any] | None = None

    def stop(self) -> None:
        self.stop_requested.set()

    def run(
        self,
        participant: dict[str, Any],
        location: dict[str, Any],
        walking_type: str,
        clothing: str,
        session_id: str,
    ) -> None:
        session_dir: Path | None = None
        capture_log = traffic_log = None
        events: list[dict[str, Any]] = []
        try:
            self.emit("status", text="Checking hardware and hotspot…")
            require_runtime(self.config)
            radio = detect_radio(self.config)
            nexmon = configure_nexmon(self.config, radio)
            now = datetime.now().astimezone()

            session_dir = Path(self.config["data_directory"]).expanduser() / session_id
            session_dir.mkdir(parents=True, exist_ok=False)
            self.emit("session", session_id=session_id, session_dir=str(session_dir))

            pcap_path = session_dir / "capture.pcap"
            labels_path = session_dir / "labels.csv"
            capture_log_path = session_dir / "capture.log"
            traffic_log_path = session_dir / "traffic.log"

            # Strict privacy: participant name is NEVER saved in metadata.json or session paths
            metadata = {
                "schema_version": 1,
                "aeris_recorder_version": VERSION,
                "project": "AERIS",
                "session_id": session_id,
                "created_at_local": now.isoformat(timespec="seconds"),
                "created_at_utc": iso_utc(),
                "participant_code": participant["participant_code"],
                "location_code": location["location_code"],
                "walking_type": walking_type,
                "clothing": clothing,
                "height_cm": participant.get("height_cm"),
                "body_type": participant.get("body_type"),
                "gender": participant.get("gender"),
                "pi_hotspot_distance_m": location.get("pi_hotspot_distance_m"),
                "scenario": location.get("scenario"),
                "location_notes": location.get("notes"),
                "hostname": socket.gethostname(),
                "platform": run_command(["uname", "-a"]).stdout.strip(),
                "radio": radio,
                "nexmon": nexmon,
                "capture_filter": "dst port 5500",
                "frame_control_filter": str(self.config["frame_control"]),
                "probe_target": str(self.config.get("probe_target") or radio["gateway"]),
                "schedule": self.config["schedule"],
            }
            (session_dir / "metadata.json").write_text(
                json.dumps(metadata, indent=2), encoding="utf-8"
            )

            capture_log = capture_log_path.open("w", encoding="utf-8")
            traffic_log = traffic_log_path.open("w", encoding="utf-8")

            self.capture_process = subprocess.Popen(
                [
                    "sudo",
                    "-n",
                    "tcpdump",
                    "-U",
                    "-i",
                    self.config["capture_interface"],
                    "-n",
                    "-s",
                    "0",
                    "dst port 5500",
                    "-w",
                    str(pcap_path),
                ],
                stdout=capture_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            time.sleep(0.6)
            if self.capture_process.poll() is not None:
                raise RecorderError("tcpdump exited before the trial began.")

            self.traffic_process = subprocess.Popen(
                [
                    "sudo",
                    "-n",
                    "ping",
                    "-D",
                    "-O",
                    "-I",
                    self.config["traffic_interface"],
                    "-i",
                    str(self.config["ping_interval_s"]),
                    "-s",
                    str(self.config["ping_payload_bytes"]),
                    str(self.config.get("probe_target") or radio["gateway"]),
                ],
                stdout=traffic_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            time.sleep(0.4)
            if self.traffic_process.poll() is not None:
                raise RecorderError("Probe traffic stopped before the trial began.")

            total = sum(float(item["duration_s"]) for item in self.config["schedule"])
            trial_start = time.monotonic()

            for index, phase in enumerate(self.config["schedule"]):
                if self.stop_requested.is_set():
                    break
                phase_epoch = time.time()
                phase_start = time.monotonic()
                duration = float(phase["duration_s"])

                self.emit(
                    "phase",
                    display=phase.get("display", phase["label"]).upper(),
                    instruction=phase.get("instruction", ""),
                )

                while not self.stop_requested.is_set():
                    if self.capture_process.poll() is not None:
                        raise RecorderError("CSI capture stopped unexpectedly.")
                    if self.traffic_process.poll() is not None:
                        raise RecorderError("Probe traffic stopped unexpectedly.")
                    elapsed_phase = time.monotonic() - phase_start
                    if elapsed_phase >= duration:
                        break
                    elapsed = min(total, time.monotonic() - trial_start)
                    self.emit(
                        "tick",
                        remaining=max(0.0, duration - elapsed_phase),
                        elapsed=elapsed,
                        total=total,
                        file_size=pcap_path.stat().st_size if pcap_path.exists() else 0,
                    )
                    time.sleep(0.1)

                completed = (
                    not self.stop_requested.is_set()
                    and time.monotonic() - phase_start >= duration - 0.15
                )
                events.append(
                    {
                        "phase_index": index,
                        "label": phase["label"],
                        "start_epoch": phase_epoch,
                        "end_epoch": time.time(),
                        "start_s": 0.0,
                        "end_s": 0.0,
                        "valid_for_training": bool(phase.get("valid_for_training", True))
                        and completed,
                        "instruction": phase.get("instruction", ""),
                    }
                )
                if not completed:
                    break

            self.emit("status", text="Finalizing capture and checking quality…", finalizing=True)
            stop_process(self.traffic_process)
            stop_process(self.capture_process)
            self.traffic_process = self.capture_process = None
            capture_log.close()
            traffic_log.close()
            capture_log = traffic_log = None

            run_command(
                [
                    "sudo",
                    "-n",
                    "chown",
                    "-R",
                    f"{os.getuid()}:{os.getgid()}",
                    str(session_dir),
                ]
            )
            parsed = parse_pcap(pcap_path)
            write_labels(labels_path, events, parsed["first_packet_epoch"])
            quality = build_quality(
                parsed,
                self.config,
                radio["bssid"],
                parse_tcpdump_summary(capture_log_path),
            )
            quality["trial_completed"] = not self.stop_requested.is_set()
            if self.stop_requested.is_set():
                quality["quality_pass"] = False
                quality["quality_status"] = "INCOMPLETE"

            (session_dir / "quality.json").write_text(
                json.dumps(quality, indent=2), encoding="utf-8"
            )

            # Update trial record in SQLite database
            self.db.update_trial_quality(
                session_id=session_id,
                quality_status=quality["quality_status"],
                packet_count=quality["packet_count"],
                packet_rate_hz=quality["packet_rate_hz"],
                maximum_gap_s=quality["maximum_gap_s"],
            )

            self.emit(
                "complete",
                session_dir=str(session_dir),
                session_id=session_id,
                packet_count=quality["packet_count"],
                packet_rate=quality["packet_rate_hz"],
                maximum_gap=quality["maximum_gap_s"],
                median_rssi=quality["median_rssi_dbm"],
                duration=quality["duration_s"],
                kernel_drops=quality["tcpdump_summary"].get("packets_dropped_by_kernel"),
                status=quality["quality_status"],
                checks=quality["checks"],
            )
        except Exception as exc:
            # Update DB with failure if session was created
            if session_id:
                try:
                    self.db.update_trial_quality(
                        session_id=session_id,
                        quality_status="ERROR",
                    )
                except Exception:
                    pass
            self.emit("error", text=str(exc), session_dir=str(session_dir or ""))
        finally:
            stop_process(self.traffic_process)
            stop_process(self.capture_process)
            if capture_log is not None:
                capture_log.close()
            if traffic_log is not None:
                traffic_log.close()


class RecorderController:
    ACTIVE = {"preparing", "running", "stopping", "finalizing"}

    def __init__(self, config: dict[str, Any], db: AerisDatabase):
        self.config = config
        self.db = db
        self.lock = threading.RLock()
        self.runner: TrialRunner | None = None
        self.worker: threading.Thread | None = None
        self.state = self._idle()

    @staticmethod
    def _idle() -> dict[str, Any]:
        return {
            "version": VERSION,
            "state": "idle",
            "phase": "READY",
            "instruction": "Keep the monitored path empty, then tap Start trial.",
            "status_text": "System ready",
            "remaining_s": None,
            "progress": 0,
            "file_size_bytes": 0,
            "session_id": None,
            "session_dir": None,
            "error": None,
            "packet_count": None,
            "packet_rate_hz": None,
            "maximum_gap_s": None,
            "median_rssi_dbm": None,
            "duration_s": None,
            "kernel_drops": None,
            "quality_status": None,
            "quality_checks": {},
            "active_participant_code": None,
            "active_location_code": None,
            "active_walking_type": None,
            "active_clothing": None,
        }

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return dict(self.state)

    def emit(self, kind: str, **payload: Any) -> None:
        with self.lock:
            if kind == "status":
                self.state["status_text"] = payload["text"]
                if payload.get("finalizing"):
                    self.state["state"] = "finalizing"
            elif kind == "session":
                self.state.update(payload)
            elif kind == "phase":
                self.state.update(
                    state="running",
                    phase=payload["display"],
                    instruction=payload["instruction"],
                    status_text="Capturing CSI",
                )
            elif kind == "tick":
                self.state.update(
                    remaining_s=payload["remaining"],
                    progress=100 * payload["elapsed"] / payload["total"],
                    elapsed_s=payload["elapsed"],
                    file_size_bytes=payload["file_size"],
                )
            elif kind == "complete":
                self.state.update(
                    state="complete",
                    phase=payload["status"],
                    remaining_s=0,
                    progress=100,
                    status_text="Session saved",
                    session_dir=payload["session_dir"],
                    session_id=payload["session_id"],
                    packet_count=payload["packet_count"],
                    packet_rate_hz=payload["packet_rate"],
                    maximum_gap_s=payload["maximum_gap"],
                    median_rssi_dbm=payload["median_rssi"],
                    duration_s=payload["duration"],
                    kernel_drops=payload["kernel_drops"],
                    quality_status=payload["status"],
                    quality_checks=payload["checks"],
                    instruction="Trial complete. Review the quality checks below.",
                )
            elif kind == "error":
                self.state.update(
                    state="error",
                    phase="ERROR",
                    status_text="Trial could not complete",
                    instruction=payload["text"],
                    error=payload["text"],
                    session_dir=payload.get("session_dir") or self.state.get("session_dir"),
                )

    def start(
        self,
        participant_id: int,
        location_id: int,
        walking_type: str,
        clothing: str,
        human_count_inside: int = 0,
        human_count_outside: int = 0,
    ) -> dict[str, Any]:
        with self.lock:
            if self.state["state"] in self.ACTIVE:
                raise RecorderError("A trial is already active.")

            participant = self.db.get_participant(participant_id)
            if not participant:
                raise NotFoundError(f"Participant with ID {participant_id} not found.")
            if not participant["active"]:
                raise ValidationError(
                    f"Participant {participant['participant_code']} is deactivated."
                )

            location = self.db.get_location(location_id)
            if not location:
                raise NotFoundError(f"Location with ID {location_id} not found.")
            if not location["active"]:
                raise ValidationError(f"Location {location['location_code']} is deactivated.")

            if walking_type not in WALKING_TYPES:
                raise ValidationError(f"Invalid walking_type '{walking_type}'.")
            if clothing not in CLOTHING_TYPES:
                raise ValidationError(f"Invalid clothing '{clothing}'.")

            now = datetime.now().astimezone()
            now_str = now.strftime("%Y%m%d-%H%M%S")
            session_id = "-".join(
                [
                    now_str,
                    location["location_code"],
                    participant["participant_code"],
                    safe_id(walking_type, "walk"),
                ]
            )
            session_dir = str(Path(self.config["data_directory"]).expanduser() / session_id)

            # Insert trial row into SQLite
            self.db.create_trial(
                session_id=session_id,
                participant_id=participant["id"],
                location_id=location["id"],
                walking_type=walking_type,
                clothing=clothing,
                session_directory=session_dir,
                human_count_inside=human_count_inside,
                human_count_outside=human_count_outside,
            )

            self.state = self._idle()
            self.state.update(
                state="preparing",
                phase="PREPARING",
                status_text="Starting",
                instruction="Checking Wi-Fi radios and configuring Nexmon CSI…",
                session_id=session_id,
                session_dir=session_dir,
                active_participant_code=participant["participant_code"],
                active_location_code=location["location_code"],
                active_walking_type=walking_type,
                active_clothing=clothing,
            )

            self.runner = TrialRunner(self.config, self.db, self.emit)
            self.worker = threading.Thread(
                target=self.runner.run,
                args=(participant, location, walking_type, clothing, session_id),
                daemon=True,
                name="aeris-trial",
            )
            self.worker.start()
            return dict(self.state)

    def stop(self) -> None:
        with self.lock:
            if self.runner and self.state["state"] in self.ACTIVE:
                self.state.update(state="stopping", status_text="Stopping safely…")
                self.runner.stop()

    def reset(self) -> dict[str, Any]:
        with self.lock:
            if self.state["state"] in self.ACTIVE:
                raise RecorderError("Cannot reset while a trial is actively recording.")
            self.state = self._idle()
            return dict(self.state)


def get_network_info(port: int = 8765) -> dict[str, Any]:
    raw_host = socket.gethostname()
    clean_host = raw_host[:-6] if raw_host.endswith(".local") else raw_host
    ips: list[str] = []
    try:
        res = subprocess.run(
            ["hostname", "-I"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=2,
            check=False,
        )
        if res.stdout:
            for item in res.stdout.strip().split():
                if ":" not in item and item != "127.0.0.1" and item not in ips:
                    ips.append(item)
    except Exception:
        pass
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and ip != "127.0.0.1" and ip not in ips:
                ips.append(ip)
            s.close()
        except Exception:
            pass

    mdns_url = f"http://{clean_host}.local:{port}" if clean_host else f"http://aeris.local:{port}"
    ip_urls = [f"http://{ip}:{port}" for ip in ips]
    primary_url = ip_urls[0] if ip_urls else mdns_url

    return {
        "hostname": clean_host,
        "port": port,
        "mdns_url": mdns_url,
        "ip_urls": ip_urls,
        "primary_url": primary_url,
        "ips": ips,
    }


def get_sessions_list(config: dict[str, Any], db: AerisDatabase) -> list[dict[str, Any]]:
    sessions_dir = Path(config["data_directory"]).expanduser()
    db_trials = {t["session_id"]: t for t in db.get_trials(limit=10000)}
    sessions: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    if sessions_dir.exists():
        for s_dir in sorted(sessions_dir.iterdir(), reverse=True):
            if s_dir.is_dir() and not s_dir.name.startswith("."):
                session_id = s_dir.name
                seen_ids.add(session_id)
                db_trial = db_trials.get(session_id, {})

                files_info = {}
                total_bytes = 0
                for f in s_dir.iterdir():
                    if f.is_file() and not f.name.startswith("."):
                        sz = f.stat().st_size
                        files_info[f.name] = sz
                        total_bytes += sz

                meta = {}
                qual = {}
                meta_path = s_dir / "metadata.json"
                qual_path = s_dir / "quality.json"
                if meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                if qual_path.exists():
                    try:
                        qual = json.loads(qual_path.read_text(encoding="utf-8"))
                    except Exception:
                        pass

                sessions.append(
                    {
                        "session_id": session_id,
                        "session_directory": str(s_dir),
                        "created_at": meta.get("created_at_local")
                        or db_trial.get("started_at")
                        or "",
                        "created_at_utc": meta.get("created_at_utc") or "",
                        "participant_code": meta.get("participant_code")
                        or db_trial.get("participant_code")
                        or "—",
                        "participant_name": db_trial.get("participant_name") or "",
                        "height_cm": meta.get("height_cm") or db_trial.get("height_cm"),
                        "body_type": meta.get("body_type") or db_trial.get("body_type"),
                        "gender": meta.get("gender") or db_trial.get("gender"),
                        "location_code": meta.get("location_code")
                        or db_trial.get("location_code")
                        or "—",
                        "location_name": db_trial.get("location_name") or "",
                        "scenario": meta.get("scenario") or db_trial.get("scenario") or "LOS",
                        "pi_hotspot_distance_m": meta.get("pi_hotspot_distance_m")
                        or db_trial.get("pi_hotspot_distance_m"),
                        "walking_type": meta.get("walking_type")
                        or db_trial.get("walking_type")
                        or "enter",
                        "clothing": meta.get("clothing")
                        or db_trial.get("clothing")
                        or "normal",
                        "quality_status": qual.get("quality_status")
                        or db_trial.get("quality_status")
                        or "UNKNOWN",
                        "quality_pass": qual.get("quality_pass", False),
                        "packet_count": qual.get("packet_count")
                        or db_trial.get("packet_count")
                        or 0,
                        "packet_rate_hz": qual.get("packet_rate_hz")
                        or db_trial.get("packet_rate_hz")
                        or 0.0,
                        "maximum_gap_s": qual.get("maximum_gap_s")
                        or db_trial.get("maximum_gap_s")
                        or 0.0,
                        "duration_s": qual.get("duration_s") or 0.0,
                        "median_rssi_dbm": qual.get("median_rssi_dbm"),
                        "files": files_info,
                        "total_size_bytes": total_bytes,
                    }
                )

    for session_id, db_trial in db_trials.items():
        if session_id not in seen_ids:
            sessions.append(
                {
                    "session_id": session_id,
                    "session_directory": db_trial.get("session_directory", ""),
                    "created_at": db_trial.get("started_at", ""),
                    "participant_code": db_trial.get("participant_code", "—"),
                    "participant_name": db_trial.get("participant_name", ""),
                    "height_cm": db_trial.get("height_cm"),
                    "body_type": db_trial.get("body_type"),
                    "gender": db_trial.get("gender"),
                    "location_code": db_trial.get("location_code", "—"),
                    "location_name": db_trial.get("location_name", ""),
                    "scenario": db_trial.get("scenario", "LOS"),
                    "pi_hotspot_distance_m": db_trial.get("pi_hotspot_distance_m"),
                    "walking_type": db_trial.get("walking_type", "enter"),
                    "clothing": db_trial.get("clothing", "normal"),
                    "quality_status": db_trial.get("quality_status", "UNKNOWN"),
                    "quality_pass": db_trial.get("quality_status") == "PASS",
                    "packet_count": db_trial.get("packet_count", 0),
                    "packet_rate_hz": db_trial.get("packet_rate_hz", 0.0),
                    "maximum_gap_s": db_trial.get("maximum_gap_s", 0.0),
                    "duration_s": 0.0,
                    "median_rssi_dbm": None,
                    "files": {},
                    "total_size_bytes": 0,
                }
            )

    return sessions


def get_session_details(
    session_id: str, config: dict[str, Any], db: AerisDatabase
) -> dict[str, Any]:
    sessions_dir = Path(config["data_directory"]).expanduser()
    s_dir = sessions_dir / session_id
    db_trial = db.get_trial_by_session_id(session_id) or {}

    metadata = {}
    quality = {}
    labels = []
    files_info = {}

    if s_dir.exists() and s_dir.is_dir():
        for f in s_dir.iterdir():
            if f.is_file() and not f.name.startswith("."):
                files_info[f.name] = f.stat().st_size

        meta_p = s_dir / "metadata.json"
        qual_p = s_dir / "quality.json"
        lab_p = s_dir / "labels.csv"

        if meta_p.exists():
            try:
                metadata = json.loads(meta_p.read_text(encoding="utf-8"))
            except Exception:
                pass
        if qual_p.exists():
            try:
                quality = json.loads(qual_p.read_text(encoding="utf-8"))
            except Exception:
                pass
        if lab_p.exists():
            try:
                with lab_p.open(encoding="utf-8") as h:
                    reader = csv.DictReader(h)
                    labels = [dict(row) for row in reader]
            except Exception:
                pass

    if not files_info and not db_trial:
        raise NotFoundError(f"Session '{session_id}' was not found.")

    return {
        "session_id": session_id,
        "database_record": db_trial,
        "metadata": metadata,
        "quality": quality,
        "labels": labels,
        "files": files_info,
    }


def generate_session_zip(session_id: str, config: dict[str, Any]) -> tuple[bytes, str]:
    sessions_dir = Path(config["data_directory"]).expanduser()
    s_dir = sessions_dir / session_id
    if not s_dir.exists() or not s_dir.is_dir():
        raise NotFoundError(f"Session directory '{session_id}' not found.")

    zip_filename = f"{session_id}.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(s_dir.iterdir()):
            if f.is_file() and not f.name.startswith("."):
                zf.write(f, arcname=f"{session_id}/{f.name}")
    return buf.getvalue(), zip_filename


def generate_dataset_zip(config: dict[str, Any], db: AerisDatabase) -> tuple[bytes, str]:
    now = datetime.now().astimezone()
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    zip_filename = f"aeris-csi-dataset-{timestamp}.zip"

    sessions_dir = Path(config["data_directory"]).expanduser()
    db_trials = {t["session_id"]: t for t in db.get_trials(limit=10000)}
    participants = db.get_participants()
    locations = db.get_locations()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        manifest_rows: list[dict[str, Any]] = []
        if sessions_dir.exists():
            for s_dir in sorted(sessions_dir.iterdir()):
                if s_dir.is_dir() and not s_dir.name.startswith("."):
                    session_id = s_dir.name
                    meta_path = s_dir / "metadata.json"
                    qual_path = s_dir / "quality.json"
                    meta = {}
                    qual = {}
                    if meta_path.exists():
                        try:
                            meta = json.loads(meta_path.read_text(encoding="utf-8"))
                        except Exception:
                            pass
                    if qual_path.exists():
                        try:
                            qual = json.loads(qual_path.read_text(encoding="utf-8"))
                        except Exception:
                            pass

                    db_trial = db_trials.get(session_id, {})
                    p_code = (
                        meta.get("participant_code")
                        or db_trial.get("participant_code")
                        or "unknown"
                    )
                    l_code = (
                        meta.get("location_code")
                        or db_trial.get("location_code")
                        or "unknown"
                    )
                    w_type = (
                        meta.get("walking_type") or db_trial.get("walking_type") or "unknown"
                    )
                    cloth = meta.get("clothing") or db_trial.get("clothing") or "unknown"
                    q_status = (
                        qual.get("quality_status")
                        or db_trial.get("quality_status")
                        or "UNKNOWN"
                    )

                    manifest_rows.append(
                        {
                            "session_id": session_id,
                            "created_at": meta.get("created_at_local")
                            or db_trial.get("started_at")
                            or "",
                            "participant_code": p_code,
                            "participant_name": db_trial.get("participant_name") or "",
                            "height_cm": meta.get("height_cm")
                            or db_trial.get("height_cm")
                            or "",
                            "body_type": meta.get("body_type")
                            or db_trial.get("body_type")
                            or "",
                            "gender": meta.get("gender") or db_trial.get("gender") or "",
                            "location_code": l_code,
                            "location_name": db_trial.get("location_name") or "",
                            "scenario": meta.get("scenario") or db_trial.get("scenario") or "",
                            "pi_hotspot_distance_m": meta.get("pi_hotspot_distance_m")
                            or db_trial.get("pi_hotspot_distance_m")
                            or "",
                            "walking_type": w_type,
                            "clothing": cloth,
                            "quality_status": q_status,
                            "packet_count": qual.get("packet_count")
                            or db_trial.get("packet_count")
                            or "",
                            "packet_rate_hz": qual.get("packet_rate_hz")
                            or db_trial.get("packet_rate_hz")
                            or "",
                            "maximum_gap_s": qual.get("maximum_gap_s")
                            or db_trial.get("maximum_gap_s")
                            or "",
                            "median_rssi_dbm": qual.get("median_rssi_dbm") or "",
                            "duration_s": qual.get("duration_s") or "",
                            "pcap_file": f"sessions/{session_id}/capture.pcap",
                            "labels_file": f"sessions/{session_id}/labels.csv",
                        }
                    )

                    for f in sorted(s_dir.iterdir()):
                        if f.is_file() and not f.name.startswith("."):
                            zf.write(f, arcname=f"sessions/{session_id}/{f.name}")

        if manifest_rows:
            manifest_buf = io.StringIO()
            writer = csv.DictWriter(manifest_buf, fieldnames=list(manifest_rows[0].keys()))
            writer.writeheader()
            writer.writerows(manifest_rows)
            zf.writestr("dataset_manifest.csv", manifest_buf.getvalue().encode("utf-8"))

        if participants:
            p_buf = io.StringIO()
            writer = csv.DictWriter(p_buf, fieldnames=list(participants[0].keys()))
            writer.writeheader()
            writer.writerows(participants)
            zf.writestr("participants.csv", p_buf.getvalue().encode("utf-8"))

        if locations:
            l_buf = io.StringIO()
            writer = csv.DictWriter(l_buf, fieldnames=list(locations[0].keys()))
            writer.writeheader()
            writer.writerows(locations)
            zf.writestr("locations.csv", l_buf.getvalue().encode("utf-8"))

        db_file = Path(config.get("database_path", Path.home() / "aeris-data" / "aeris.db"))
        if db_file.exists():
            zf.write(db_file, arcname="aeris.db")

        readme_content = f"""# AERIS Wi-Fi CSI Dataset for AI / Deep Learning

Generated on: {now.isoformat()}
AERIS Recorder Version: {VERSION}

## Overview
This archive contains labeled Wi-Fi Channel State Information (CSI) recording sessions gathered using Nexmon CSI on Raspberry Pi 4 (Broadcom BCM43455 Wi-Fi chip).

## Archive Structure
- `dataset_manifest.csv`: Comprehensive index of all sessions, participant codes, locations, walking actions, and quality scores.
- `participants.csv`: Demographics (height, body type, gender) keyed by `participant_code`.
- `locations.csv`: Environment metadata (LOS/NLOS scenarios, distances, room notes) keyed by `location_code`.
- `aeris.db`: Complete SQLite database snapshot.
- `sessions/<session_id>/`:
  - `capture.pcap`: Raw UDP packets (destination port 5500) containing 64-subcarrier OFDM CSI matrices.
  - `labels.csv`: Timestamp-synchronized phase labels (`start_s`, `end_s`, `valid_for_training`, `label`).
  - `metadata.json`: Radio parameters (channel, bandwidth, BSSID, MACs), experiment schedule, and device info.
  - `quality.json`: Quality check benchmarks (packet rates, gap statistics, drop rates).

## Label Semantics
- `empty_calibration` / `empty`: Background baseline (no subject present).
- `movement`: Subject performing target activity (`enter`, `exit`, `across_left_to_right`, `across_right_to_left`, `toward_pi`, `away_from_pi`).
- `warmup` / `transition_enter` / `transition_exit`: Buffer periods (`valid_for_training = false`).
"""
        zf.writestr("README_AI_DATASET.md", readme_content.encode("utf-8"))

    return buf.getvalue(), zip_filename


class RecorderHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        controller: RecorderController,
        db: AerisDatabase,
    ):
        super().__init__(address, RecorderHandler)
        self.controller = controller
        self.db = db


class RecorderHandler(BaseHTTPRequestHandler):
    server: RecorderHTTPServer

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def send_json(self, data: Any, status: int = 200) -> None:
        try:
            body = json.dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_zip(self, data: bytes, filename: str) -> None:
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Expose-Headers", "Content-Disposition")
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def read_json(self) -> dict[str, Any]:
        length = min(int(self.headers.get("Content-Length", "0")), 65536)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception as exc:
            raise ValidationError(f"Invalid JSON payload: {exc}") from exc

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query = parse_qs(parsed_url.query)

        if path in {"/api/status", "/api/health"}:
            self.send_json(self.server.controller.snapshot())
            return

        if path == "/api/network-info":
            self.send_json(get_network_info(self.server.server_port))
            return

        if path in {"/api/export", "/api/export/zip", "/api/dataset/export.zip"}:
            try:
                data, filename = generate_dataset_zip(self.server.controller.config, self.server.db)
                self.send_zip(data, filename)
            except Exception as exc:
                self.send_json({"error": f"Failed to generate dataset export: {exc}"}, 500)
            return

        if path == "/api/sessions":
            sessions = get_sessions_list(self.server.controller.config, self.server.db)
            self.send_json(sessions)
            return

        match_session_zip = re.match(r"^/api/sessions/([^/]+)/zip$", path)
        if match_session_zip:
            session_id = match_session_zip.group(1)
            try:
                data, filename = generate_session_zip(session_id, self.server.controller.config)
                self.send_zip(data, filename)
            except NotFoundError as exc:
                self.send_json({"error": str(exc)}, 404)
            except Exception as exc:
                self.send_json({"error": f"Failed to generate session zip: {exc}"}, 500)
            return

        match_session = re.match(r"^/api/sessions/([^/]+)$", path)
        if match_session:
            session_id = match_session.group(1)
            try:
                details = get_session_details(session_id, self.server.controller.config, self.server.db)
                self.send_json(details)
            except NotFoundError as exc:
                self.send_json({"error": str(exc)}, 404)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return

        if path == "/api/participants":
            active_only = query.get("active", ["0"])[0] in {"1", "true", "yes"}
            participants = self.server.db.get_participants(active_only=active_only)
            self.send_json(participants)
            return

        if path == "/api/locations":
            active_only = query.get("active", ["0"])[0] in {"1", "true", "yes"}
            locations = self.server.db.get_locations(active_only=active_only)
            self.send_json(locations)
            return

        if path == "/api/trials":
            limit = int(query.get("limit", ["100"])[0])
            trials = self.server.db.get_trials(limit=limit)
            self.send_json(trials)
            return

        match_trial = re.match(r"^/api/trials/([^/]+)$", path)
        if match_trial:
            session_id = match_trial.group(1)
            trial = self.server.db.get_trial_by_session_id(session_id)
            if not trial:
                self.send_json({"error": f"Trial '{session_id}' not found."}, 404)
                return
            self.send_json(trial)
            return

        if path in {"/", "/index.html", "/aeris-recorder.html"}:
            try:
                body = UI_PATH.read_bytes()
            except OSError as exc:
                self.send_json({"error": str(exc)}, 500)
                return
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        self.send_json({"error": "Not found"}, 404)

    def do_POST(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path

        try:
            if path == "/api/start":
                data = self.read_json()
                if not isinstance(data, dict):
                    self.send_json({"error": "Request body must be a JSON object."}, 400)
                    return
                for req_key in ("participant_id", "location_id", "walking_type", "clothing"):
                    if req_key not in data:
                        self.send_json({"error": f"Missing required field: '{req_key}'."}, 400)
                        return

                try:
                    p_id = int(data["participant_id"])
                    l_id = int(data["location_id"])
                    hc_in = int(data.get("human_count_inside", 0))
                    hc_out = int(data.get("human_count_outside", 0))
                except (ValueError, TypeError):
                    self.send_json({"error": "participant_id, location_id, human_count_inside, human_count_outside must be integers."}, 400)
                    return

                res = self.server.controller.start(
                    participant_id=p_id,
                    location_id=l_id,
                    walking_type=str(data["walking_type"]).strip(),
                    clothing=str(data["clothing"]).strip(),
                    human_count_inside=hc_in,
                    human_count_outside=hc_out,
                )
                self.send_json(res, 202)
                return

            if path == "/api/participants":
                data = self.read_json()
                name = data.get("name")
                if not name or not str(name).strip():
                    self.send_json({"error": "Participant name is required."}, 400)
                    return
                created = self.server.db.create_participant(
                    name=str(name),
                    height_cm=data.get("height_cm"),
                    body_type=data.get("body_type", "prefer_not_to_say"),
                    gender=data.get("gender", "prefer_not_to_say"),
                )
                self.send_json(created, 201)
                return

            if path == "/api/locations":
                data = self.read_json()
                name = data.get("name")
                if not name or not str(name).strip():
                    self.send_json({"error": "Location name is required."}, 400)
                    return
                scenario = data.get("scenario", "LOS")
                created = self.server.db.create_location(
                    name=str(name),
                    pi_hotspot_distance_m=data.get("pi_hotspot_distance_m"),
                    scenario=str(scenario),
                    notes=data.get("notes"),
                )
                self.send_json(created, 201)
                return

            if path == "/api/stop":
                self.server.controller.stop()
                self.send_json(self.server.controller.snapshot())
                return

            if path == "/api/reset":
                res = self.server.controller.reset()
                self.send_json(res, 200)
                return

            if path == "/api/shutdown":
                self.server.controller.stop()
                self.send_json({"status": "closing"})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return

            self.send_json({"error": "Not found"}, 404)

        except ValidationError as exc:
            self.send_json({"error": str(exc)}, 400)
        except NotFoundError as exc:
            self.send_json({"error": str(exc)}, 404)
        except RecorderError as exc:
            self.send_json({"error": str(exc)}, 409)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)

    def do_PATCH(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path

        try:
            match_part = re.match(r"^/api/participants/(\d+)$", path)
            if match_part:
                p_id = int(match_part.group(1))
                data = self.read_json()
                updated = self.server.db.update_participant(p_id, **data)
                self.send_json(updated, 200)
                return

            match_loc = re.match(r"^/api/locations/(\d+)$", path)
            if match_loc:
                l_id = int(match_loc.group(1))
                data = self.read_json()
                updated = self.server.db.update_location(l_id, **data)
                self.send_json(updated, 200)
                return

            self.send_json({"error": "Not found"}, 404)

        except ValidationError as exc:
            self.send_json({"error": str(exc)}, 400)
        except NotFoundError as exc:
            self.send_json({"error": str(exc)}, 404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)


def self_test(path: Path | None, config: dict[str, Any], db: AerisDatabase) -> int:
    print(f"AERIS Recorder v{VERSION}")
    print("Configuration: PASS")
    print(f"Database: {db.db_path} (user_version={db.migrate()})")
    print(f"Participants registered: {len(db.get_participants())}")
    print(f"Locations registered: {len(db.get_locations())}")
    print(f"Schedule duration: {sum(float(p['duration_s']) for p in config['schedule']):.1f} s")
    if path:
        parsed = parse_pcap(path)
        print(f"Decoded packets: {parsed['packet_count']}")
        print(f"Duration: {parsed['duration_s']:.3f} s")
        print(f"Rate: {parsed['packet_rate_hz']:.2f} packets/s")
        print(f"Maximum gap: {parsed['maximum_gap_s']:.3f} s")
        print(f"Frame controls: {parsed['frame_controls']}")
        print(f"Source MACs: {parsed['source_macs']}")
    print("Self-test: PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", nargs="?", const="", metavar="PCAP")
    parser.add_argument("--init-db", action="store_true", help="Initialize SQLite database schema and exit")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        config = load_config()
        db_path = config.get("database_path", str(Path.home() / "aeris-data" / "aeris.db"))
        db = AerisDatabase(db_path)

        if args.init_db:
            v = db.migrate()
            print(f"AERIS Database initialized at {db.db_path} (schema v{v})")
            return 0

        if args.self_test is not None:
            return self_test(Path(args.self_test) if args.self_test else None, config, db)

        if not args.serve:
            parser.error("use --serve, --init-db, or --self-test")

        if not UI_PATH.exists():
            raise RecorderError(f"Interface file is missing: {UI_PATH}")

        controller = RecorderController(config, db)
        server = RecorderHTTPServer((args.host, args.port), controller, db)
        print(f"AERIS Recorder v{VERSION} listening on http://{args.host}:{args.port}", flush=True)
        try:
            server.serve_forever(poll_interval=0.2)
        finally:
            server.controller.stop()
            server.server_close()
        return 0
    except (RecorderError, OSError, json.JSONDecodeError, DatabaseError) as exc:
        print(f"AERIS Recorder error: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
