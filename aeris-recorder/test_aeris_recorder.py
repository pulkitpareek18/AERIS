"""Comprehensive automated test suite for AERIS Recorder v3."""

from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from aeris_db import AerisDatabase, ValidationError, NotFoundError
import aeris_recorder_server as server


class TestAerisDatabase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test_aeris.db"
        self.db = AerisDatabase(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_schema_migration(self):
        version = self.db.migrate()
        self.assertEqual(version, 1)

    def test_participant_crud(self):
        # Create
        p1 = self.db.create_participant("Alice Smith", 168.5, "average", "female")
        self.assertEqual(p1["participant_code"], "P001")
        self.assertEqual(p1["name"], "Alice Smith")
        self.assertEqual(p1["height_cm"], 168.5)
        self.assertEqual(p1["body_type"], "average")
        self.assertEqual(p1["gender"], "female")
        self.assertEqual(p1["active"], 1)

        p2 = self.db.create_participant("Bob Jones", 180, "broad", "male")
        self.assertEqual(p2["participant_code"], "P002")

        # Get
        fetched = self.db.get_participant(p1["id"])
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["name"], "Alice Smith")

        fetched_code = self.db.get_participant_by_code("P001")
        self.assertEqual(fetched_code["id"], p1["id"])

        # Update
        updated = self.db.update_participant(p1["id"], name="Alice Johnson", height_cm=170.0)
        self.assertEqual(updated["name"], "Alice Johnson")
        self.assertEqual(updated["height_cm"], 170.0)

        # Deactivate
        self.db.update_participant(p2["id"], active=False)
        active_list = self.db.get_participants(active_only=True)
        self.assertEqual(len(active_list), 1)
        self.assertEqual(active_list[0]["id"], p1["id"])

        all_list = self.db.get_participants(active_only=False)
        self.assertEqual(len(all_list), 2)

    def test_participant_validation(self):
        with self.assertRaises(ValidationError):
            self.db.create_participant("")

        with self.assertRaises(ValidationError):
            self.db.create_participant("Test", body_type="invalid_type")

        with self.assertRaises(ValidationError):
            self.db.create_participant("Test", height_cm=-10)

    def test_location_crud(self):
        # Create
        l1 = self.db.create_location("Hallway North", 5.2, "LOS", "Tile floor")
        self.assertEqual(l1["location_code"], "L001")
        self.assertEqual(l1["name"], "Hallway North")
        self.assertEqual(l1["pi_hotspot_distance_m"], 5.2)
        self.assertEqual(l1["scenario"], "LOS")

        l2 = self.db.create_location("Office 301", 3.0, "NLOS_ONE_WALL")
        self.assertEqual(l2["location_code"], "L002")

        # Update
        updated = self.db.update_location(l1["id"], notes="Updated notes")
        self.assertEqual(updated["notes"], "Updated notes")

        # Deactivate
        self.db.update_location(l2["id"], active=False)
        self.assertEqual(len(self.db.get_locations(active_only=True)), 1)

    def test_trial_lifecycle(self):
        p = self.db.create_participant("Rahul", 175, "average", "male")
        loc = self.db.create_location("Lab A", 4.0, "LOS")

        session_id = "20260822-120000-L001-P001-enter"
        trial = self.db.create_trial(
            session_id=session_id,
            participant_id=p["id"],
            location_id=loc["id"],
            walking_type="enter",
            clothing="normal",
            session_directory="/tmp/sessions/" + session_id,
        )
        self.assertIsNotNone(trial)
        self.assertEqual(trial["quality_status"], "RECORDING")

        # Update quality
        updated_trial = self.db.update_trial_quality(
            session_id=session_id,
            quality_status="PASS",
            packet_count=4200,
            packet_rate_hz=66.7,
            maximum_gap_s=0.08,
        )
        self.assertEqual(updated_trial["quality_status"], "PASS")
        self.assertEqual(updated_trial["packet_count"], 4200)

        trials = self.db.get_trials()
        self.assertEqual(len(trials), 1)
        self.assertEqual(trials[0]["participant_code"], "P001")
        self.assertEqual(trials[0]["location_code"], "L001")


class TestAerisServerAndController(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test_server.db"
        self.db = AerisDatabase(self.db_path)

        self.config = json.loads(json.dumps(server.DEFAULT_CONFIG))
        self.config["data_directory"] = str(Path(self.temp_dir) / "sessions")
        self.config["database_path"] = str(self.db_path)

        self.controller = server.RecorderController(self.config, self.db)

        # Find free port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("", 0))
        self.port = sock.getsockname()[1]
        sock.close()

        self.httpd = server.RecorderHTTPServer(("127.0.0.1", self.port), self.controller, self.db)
        self.server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.server_thread.start()
        self.base_url = f"http://127.0.0.1:{self.port}"
        time.sleep(0.1)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _request(self, path: str, method: str = "GET", data: dict | None = None) -> tuple[int, dict | str]:
        url = self.base_url + path
        req = Request(url, method=method)
        req.add_header("Content-Type", "application/json")
        encoded_data = json.dumps(data).encode("utf-8") if data is not None else None
        try:
            with urlopen(req, data=encoded_data, timeout=5) as response:
                body = response.read().decode("utf-8")
                try:
                    return response.status, json.loads(body)
                except json.JSONDecodeError:
                    return response.status, body
        except HTTPError as exc:
            body = exc.read().decode("utf-8")
            try:
                return exc.code, json.loads(body)
            except json.JSONDecodeError:
                return exc.code, body

    def test_health_and_status(self):
        status_code, data = self._request("/api/health")
        self.assertEqual(status_code, 200)
        self.assertEqual(data["state"], "idle")
        self.assertEqual(data["version"], server.VERSION)

        status_code, net_info = self._request("/api/network-info")
        self.assertEqual(status_code, 200)
        self.assertIn("mdns_url", net_info)
        self.assertIn("primary_url", net_info)

    def test_participant_and_location_endpoints(self):
        # Create participant
        status, p_data = self._request(
            "/api/participants",
            method="POST",
            data={"name": "Priya", "height_cm": 165, "body_type": "slim", "gender": "female"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(p_data["participant_code"], "P001")

        # Create location
        status, l_data = self._request(
            "/api/locations",
            method="POST",
            data={"name": "Room 101", "pi_hotspot_distance_m": 3.5, "scenario": "LOS"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(l_data["location_code"], "L001")

        # List
        status, p_list = self._request("/api/participants")
        self.assertEqual(status, 200)
        self.assertEqual(len(p_list), 1)

        status, l_list = self._request("/api/locations")
        self.assertEqual(status, 200)
        self.assertEqual(len(l_list), 1)

        # Patch participant
        status, patched_p = self._request(
            f"/api/participants/{p_data['id']}",
            method="PATCH",
            data={"name": "Priya Sharma"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(patched_p["name"], "Priya Sharma")

    def test_state_machine_and_reset_workflow(self):
        # 1. State is initially idle
        status, snap = self._request("/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(snap["state"], "idle")

        # 2. Mock trial completion
        self.controller.emit(
            "complete",
            session_dir="/tmp/test-session",
            session_id="20260822-test-session",
            packet_count=3500,
            packet_rate=65.0,
            maximum_gap=0.05,
            median_rssi=-42.0,
            duration=62.0,
            kernel_drops=0,
            status="PASS",
            checks={"capture_duration": {"pass": True, "value_s": 62.0}},
        )

        status, snap_comp = self._request("/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(snap_comp["state"], "complete")
        self.assertEqual(snap_comp["quality_status"], "PASS")

        # 3. Call POST /api/reset (simulating clicking "NEW TRIAL" or "SETUP")
        status, snap_reset = self._request("/api/reset", method="POST")
        self.assertEqual(status, 200)
        self.assertEqual(snap_reset["state"], "idle")

        # 4. Polling /api/status now returns idle!
        status, snap_poll = self._request("/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(snap_poll["state"], "idle")

    def test_error_state_and_reset(self):
        self.controller.emit("error", text="Interface missing", session_dir="")
        status, snap_err = self._request("/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(snap_err["state"], "error")
        self.assertEqual(snap_err["error"], "Interface missing")

        # Reset back to idle
        status, snap_reset = self._request("/api/reset", method="POST")
        self.assertEqual(status, 200)
        self.assertEqual(snap_reset["state"], "idle")

    def test_pcap_decoding(self):
        pcap_file = Path("/Users/pulkitpareek18/Desktop/AERIS/controlled-motion-20260822-013941.pcap")
        if pcap_file.exists():
            parsed = server.parse_pcap(pcap_file)
            self.assertGreater(parsed["packet_count"], 0)
            self.assertIn("0x88", parsed["frame_controls"])
            self.assertGreater(parsed["duration_s"], 0)

            quality = server.build_quality(
                parsed,
                self.config,
                "06:c5:98:2f:ea:cc",
                {"packets_dropped_by_kernel": 0},
            )
            self.assertIn("quality_status", quality)
            self.assertIn("checks", quality)

    def test_dataset_sessions_and_inspection(self):
        # 1. Create a dummy session directory
        sessions_dir = Path(self.config["data_directory"])
        sessions_dir.mkdir(parents=True, exist_ok=True)

        session_id = "20260823-140000-L001-P001-enter"
        s_dir = sessions_dir / session_id
        s_dir.mkdir(parents=True, exist_ok=True)

        # Write metadata.json
        (s_dir / "metadata.json").write_text(json.dumps({
            "session_id": session_id,
            "participant_code": "P001",
            "location_code": "L001",
            "walking_type": "enter",
            "clothing": "normal",
            "height_cm": 175,
            "body_type": "average",
            "gender": "male",
            "scenario": "LOS",
            "pi_hotspot_distance_m": 4.5,
            "created_at_local": "2026-08-23 14:00:00",
        }), encoding="utf-8")

        # Write quality.json
        (s_dir / "quality.json").write_text(json.dumps({
            "quality_status": "PASS",
            "quality_pass": True,
            "packet_count": 4000,
            "packet_rate_hz": 63.5,
            "maximum_gap_s": 0.12,
            "duration_s": 63.0,
            "checks": {
                "packet_rate": {"pass": True, "value_hz": 63.5},
            }
        }), encoding="utf-8")

        # Write labels.csv
        (s_dir / "labels.csv").write_text(
            "phase_index,label,start_s,end_s,valid_for_training,instruction\n"
            "0,warmup,0.0,2.0,False,Keep monitored path empty\n"
            "1,movement,2.0,17.0,True,Walk naturally\n",
            encoding="utf-8"
        )
        (s_dir / "capture.pcap").write_bytes(b"\xd4\xc3\xb2\xa1\x02\x00\x04\x00" + b"\x00" * 100)

        # 2. Test GET /api/sessions
        status, sessions = self._request("/api/sessions")
        self.assertEqual(status, 200)
        self.assertIsInstance(sessions, list)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["session_id"], session_id)
        self.assertEqual(sessions[0]["participant_code"], "P001")
        self.assertEqual(sessions[0]["quality_status"], "PASS")
        self.assertIn("capture.pcap", sessions[0]["files"])

        # 3. Test GET /api/sessions/<session_id>
        status, details = self._request(f"/api/sessions/{session_id}")
        self.assertEqual(status, 200)
        self.assertEqual(details["session_id"], session_id)
        self.assertEqual(details["metadata"]["walking_type"], "enter")
        self.assertEqual(len(details["labels"]), 2)
        self.assertEqual(details["labels"][1]["label"], "movement")
        self.assertEqual(details["quality"]["checks"]["packet_rate"]["pass"], True)

    def test_zip_exports(self):
        import zipfile
        import io

        # 1. Create a dummy session
        sessions_dir = Path(self.config["data_directory"])
        sessions_dir.mkdir(parents=True, exist_ok=True)
        session_id = "20260823-141000-L001-P001-exit"
        s_dir = sessions_dir / session_id
        s_dir.mkdir(parents=True, exist_ok=True)
        (s_dir / "metadata.json").write_text(json.dumps({"session_id": session_id, "participant_code": "P001"}), encoding="utf-8")
        (s_dir / "labels.csv").write_text("phase_index,label\n0,warmup\n", encoding="utf-8")
        (s_dir / "quality.json").write_text(json.dumps({"quality_status": "PASS"}), encoding="utf-8")

        # 2. Test single session ZIP
        req_single = Request(f"{self.base_url}/api/sessions/{session_id}/zip")
        with urlopen(req_single, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Content-Type"), "application/zip")
            zip_bytes = resp.read()
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                namelist = zf.namelist()
                self.assertIn(f"{session_id}/metadata.json", namelist)
                self.assertIn(f"{session_id}/labels.csv", namelist)

        # 3. Test full dataset ZIP export
        req_all = Request(f"{self.base_url}/api/export/zip")
        with urlopen(req_all, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Content-Type"), "application/zip")
            zip_bytes = resp.read()
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                namelist = zf.namelist()
                self.assertIn("dataset_manifest.csv", namelist)
                self.assertIn("README_AI_DATASET.md", namelist)
                self.assertIn(f"sessions/{session_id}/metadata.json", namelist)


if __name__ == "__main__":
    unittest.main()
