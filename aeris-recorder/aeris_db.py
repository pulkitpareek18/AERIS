"""AERIS Recorder — SQLite database and metadata management."""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BODY_TYPES = {"slim", "average", "broad", "prefer_not_to_say"}
GENDERS = {"male", "female", "non_binary", "other", "prefer_not_to_say"}
SCENARIOS = {"LOS", "NLOS_ONE_WALL", "NLOS_MULTIPLE_WALLS"}
WALKING_TYPES = {
    "enter",
    "exit",
    "across_left_to_right",
    "across_right_to_left",
    "toward_pi",
    "away_from_pi",
}
CLOTHING_TYPES = {"light", "normal", "heavy", "woollen_winter"}


def iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class DatabaseError(Exception):
    pass


class ValidationError(DatabaseError):
    pass


class NotFoundError(DatabaseError):
    pass


class AerisDatabase:
    """Manages SQLite storage for participants, locations, and trials."""

    def __init__(self, db_path: Path | str):
        target = Path(db_path).expanduser()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            self.db_path = target.resolve()
        except OSError:
            fallback = Path.home() / "aeris-data" / "aeris.db"
            fallback.parent.mkdir(parents=True, exist_ok=True)
            self.db_path = fallback.resolve()
        self._lock = threading.RLock()
        self.migrate()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=5.0,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

    def migrate(self) -> int:
        """Runs idempotent schema migrations using PRAGMA user_version."""
        with self._lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("PRAGMA user_version;")
                version = cursor.fetchone()[0]

                if version < 1:
                    cursor.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS code_sequences (
                            prefix TEXT PRIMARY KEY,
                            next_val INTEGER NOT NULL
                        );

                        CREATE TABLE IF NOT EXISTS participants (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            participant_code TEXT UNIQUE NOT NULL,
                            name TEXT NOT NULL,
                            height_cm REAL,
                            body_type TEXT CHECK (body_type IN ('slim', 'average', 'broad', 'prefer_not_to_say')) NOT NULL DEFAULT 'prefer_not_to_say',
                            gender TEXT CHECK (gender IN ('male', 'female', 'non_binary', 'other', 'prefer_not_to_say')) NOT NULL DEFAULT 'prefer_not_to_say',
                            active INTEGER NOT NULL DEFAULT 1,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        );

                        CREATE TABLE IF NOT EXISTS locations (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            location_code TEXT UNIQUE NOT NULL,
                            name TEXT NOT NULL,
                            pi_hotspot_distance_m REAL,
                            scenario TEXT CHECK (scenario IN ('LOS', 'NLOS_ONE_WALL', 'NLOS_MULTIPLE_WALLS')) NOT NULL,
                            notes TEXT,
                            active INTEGER NOT NULL DEFAULT 1,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        );

                        CREATE TABLE IF NOT EXISTS trials (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            session_id TEXT UNIQUE NOT NULL,
                            participant_id INTEGER NOT NULL REFERENCES participants(id),
                            location_id INTEGER NOT NULL REFERENCES locations(id),
                            walking_type TEXT CHECK (walking_type IN ('enter', 'exit', 'across_left_to_right', 'across_right_to_left', 'toward_pi', 'away_from_pi')) NOT NULL,
                            clothing TEXT CHECK (clothing IN ('light', 'normal', 'heavy', 'woollen_winter')) NOT NULL,
                            human_count_inside INTEGER NOT NULL DEFAULT 0,
                            human_count_outside INTEGER NOT NULL DEFAULT 0,
                            quality_status TEXT,
                            packet_count INTEGER,
                            packet_rate_hz REAL,
                            maximum_gap_s REAL,
                            session_directory TEXT NOT NULL,
                            started_at TEXT NOT NULL,
                            completed_at TEXT
                        );

                        CREATE INDEX IF NOT EXISTS idx_participants_active ON participants(active);
                        CREATE INDEX IF NOT EXISTS idx_locations_active ON locations(active);
                        CREATE INDEX IF NOT EXISTS idx_trials_session_id ON trials(session_id);
                        CREATE INDEX IF NOT EXISTS idx_trials_participant ON trials(participant_id);
                        CREATE INDEX IF NOT EXISTS idx_trials_location ON trials(location_id);

                        INSERT INTO code_sequences (prefix, next_val) VALUES ('P', 1)
                            ON CONFLICT(prefix) DO NOTHING;
                        INSERT INTO code_sequences (prefix, next_val) VALUES ('L', 1)
                            ON CONFLICT(prefix) DO NOTHING;

                        PRAGMA user_version = 1;
                        """
                    )
                    conn.commit()
                    version = 1

                if version < 2:
                    cursor.executescript(
                        """
                        ALTER TABLE trials ADD COLUMN human_count_inside INTEGER NOT NULL DEFAULT 0;
                        ALTER TABLE trials ADD COLUMN human_count_outside INTEGER NOT NULL DEFAULT 0;
                        PRAGMA user_version = 2;
                        """
                    )
                    conn.commit()
                    version = 2

                # Re-sync sequences with existing tables in case data was inserted externally
                cursor.execute(
                    """
                    SELECT COALESCE(MAX(CAST(substr(participant_code, 2) AS INTEGER)), 0) + 1
                    FROM participants WHERE participant_code LIKE 'P%';
                    """
                )
                p_next = cursor.fetchone()[0]
                cursor.execute(
                    """
                    INSERT INTO code_sequences (prefix, next_val) VALUES ('P', ?)
                    ON CONFLICT(prefix) DO UPDATE SET next_val = MAX(code_sequences.next_val, excluded.next_val);
                    """,
                    (p_next,),
                )

                cursor.execute(
                    """
                    SELECT COALESCE(MAX(CAST(substr(location_code, 2) AS INTEGER)), 0) + 1
                    FROM locations WHERE location_code LIKE 'L%';
                    """
                )
                l_next = cursor.fetchone()[0]
                cursor.execute(
                    """
                    INSERT INTO code_sequences (prefix, next_val) VALUES ('L', ?)
                    ON CONFLICT(prefix) DO UPDATE SET next_val = MAX(code_sequences.next_val, excluded.next_val);
                    """,
                    (l_next,),
                )
                conn.commit()

                if version < 3:
                    # V2 digital-twin schema: room geometry, anchor grid, device
                    # placement (pi + csi hotspot), door, and volunteers for the
                    # Pi-hotspot auto-labeling path. See implementation_plan.md V2.
                    cursor.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS rooms (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            name TEXT NOT NULL,
                            width_m REAL NOT NULL,
                            depth_m REAL NOT NULL,
                            height_m REAL NOT NULL DEFAULT 2.6,
                            active INTEGER NOT NULL DEFAULT 1,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        );

                        CREATE TABLE IF NOT EXISTS anchors (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            room_id INTEGER NOT NULL REFERENCES rooms(id),
                            label TEXT NOT NULL, -- e.g. corner_nw, edge_n_center, center
                            x_m REAL NOT NULL,
                            y_m REAL NOT NULL,
                            UNIQUE(room_id, label)
                        );

                        CREATE TABLE IF NOT EXISTS devices (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            room_id INTEGER NOT NULL REFERENCES rooms(id),
                            kind TEXT CHECK (kind IN ('pi', 'csi_hotspot')) NOT NULL,
                            anchor_id INTEGER REFERENCES anchors(id),
                            x_m REAL, -- free placement overrides anchor
                            y_m REAL,
                            UNIQUE(room_id, kind)
                        );

                        CREATE TABLE IF NOT EXISTS doors (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            room_id INTEGER NOT NULL REFERENCES rooms(id),
                            edge TEXT CHECK (edge IN ('N', 'E', 'S', 'W')) NOT NULL,
                            position_m REAL NOT NULL DEFAULT 0.5
                        );

                        CREATE TABLE IF NOT EXISTS volunteers (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            mac TEXT UNIQUE NOT NULL,
                            display_tag TEXT,
                            current_region TEXT CHECK (current_region IN ('inside', 'outside')) DEFAULT 'outside',
                            active INTEGER NOT NULL DEFAULT 1,
                            joined_at TEXT,
                            last_seen_at TEXT
                        );

                        CREATE TABLE IF NOT EXISTS label_events (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            trial_session_id TEXT,
                            volunteer_id INTEGER NOT NULL REFERENCES volunteers(id),
                            region TEXT CHECK (region IN ('inside', 'outside')) NOT NULL,
                            rssi_dbm REAL,
                            source TEXT CHECK (source IN ('ap_assoc', 'csi_rssi', 'manual')) NOT NULL,
                            t_epoch REAL NOT NULL
                        );

                        CREATE INDEX IF NOT EXISTS idx_anchors_room ON anchors(room_id);
                        CREATE INDEX IF NOT EXISTS idx_devices_room ON devices(room_id);
                        CREATE INDEX IF NOT EXISTS idx_doors_room ON doors(room_id);
                        CREATE INDEX IF NOT EXISTS idx_label_events_trial ON label_events(trial_session_id);
                        CREATE INDEX IF NOT EXISTS idx_label_events_time ON label_events(t_epoch);

                        PRAGMA user_version = 3;
                        """
                    )
                    conn.commit()
                    version = 3

            return version

    def _next_code(self, conn: sqlite3.Connection, prefix: str) -> str:
        cursor = conn.cursor()
        cursor.execute("SELECT next_val FROM code_sequences WHERE prefix = ?", (prefix,))
        row = cursor.fetchone()
        val = row[0] if row else 1
        cursor.execute(
            "UPDATE code_sequences SET next_val = ? WHERE prefix = ?",
            (val + 1, prefix),
        )
        return f"{prefix}{val:03d}"

    # -------------------------------------------------------------------------
    # Participants
    # -------------------------------------------------------------------------

    def get_participants(self, active_only: bool = False) -> list[dict[str, Any]]:
        with self.get_connection() as conn:
            query = "SELECT * FROM participants"
            params: tuple[Any, ...] = ()
            if active_only:
                query += " WHERE active = 1"
            query += " ORDER BY id ASC"
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_participant(self, participant_id: int) -> dict[str, Any] | None:
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM participants WHERE id = ?", (participant_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_participant_by_code(self, code: str) -> dict[str, Any] | None:
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM participants WHERE participant_code = ?", (code,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def create_participant(
        self,
        name: str,
        height_cm: float | None = None,
        body_type: str = "prefer_not_to_say",
        gender: str = "prefer_not_to_say",
    ) -> dict[str, Any]:
        cleaned_name = (name or "").strip()
        if not cleaned_name:
            raise ValidationError("Participant name is required.")

        body_type = body_type or "prefer_not_to_say"
        if body_type not in BODY_TYPES:
            raise ValidationError(
                f"Invalid body_type '{body_type}'. Allowed: {', '.join(sorted(BODY_TYPES))}"
            )

        gender = gender or "prefer_not_to_say"
        if gender not in GENDERS:
            raise ValidationError(
                f"Invalid gender '{gender}'. Allowed: {', '.join(sorted(GENDERS))}"
            )

        parsed_height: float | None = None
        if height_cm is not None and str(height_cm).strip() != "":
            try:
                parsed_height = float(height_cm)
                if parsed_height <= 0 or parsed_height > 300:
                    raise ValidationError("height_cm must be between 1 and 300 cm.")
            except (ValueError, TypeError) as exc:
                raise ValidationError(f"Invalid height_cm value: {height_cm}") from exc

        now = iso_utc_now()
        with self._lock:
            with self.get_connection() as conn:
                code = self._next_code(conn, "P")
                cursor = conn.execute(
                    """
                    INSERT INTO participants (
                        participant_code, name, height_cm, body_type, gender, active, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (code, cleaned_name, parsed_height, body_type, gender, now, now),
                )
                new_id = cursor.lastrowid
                conn.commit()
                return self.get_participant(new_id)  # type: ignore[return-value]

    def update_participant(self, participant_id: int, **fields: Any) -> dict[str, Any]:
        existing = self.get_participant(participant_id)
        if not existing:
            raise NotFoundError(f"Participant with ID {participant_id} not found.")

        updates: list[str] = []
        params: list[Any] = []

        if "name" in fields:
            name = (fields["name"] or "").strip()
            if not name:
                raise ValidationError("Participant name cannot be empty.")
            updates.append("name = ?")
            params.append(name)

        if "height_cm" in fields:
            h = fields["height_cm"]
            if h is None or str(h).strip() == "":
                updates.append("height_cm = NULL")
            else:
                try:
                    parsed_h = float(h)
                    if parsed_h <= 0 or parsed_h > 300:
                        raise ValidationError("height_cm must be between 1 and 300 cm.")
                    updates.append("height_cm = ?")
                    params.append(parsed_h)
                except (ValueError, TypeError) as exc:
                    raise ValidationError(f"Invalid height_cm: {h}") from exc

        if "body_type" in fields:
            bt = fields["body_type"] or "prefer_not_to_say"
            if bt not in BODY_TYPES:
                raise ValidationError(f"Invalid body_type '{bt}'.")
            updates.append("body_type = ?")
            params.append(bt)

        if "gender" in fields:
            g = fields["gender"] or "prefer_not_to_say"
            if g not in GENDERS:
                raise ValidationError(f"Invalid gender '{g}'.")
            updates.append("gender = ?")
            params.append(g)

        if "active" in fields:
            updates.append("active = ?")
            params.append(1 if fields["active"] else 0)

        if not updates:
            return existing

        updates.append("updated_at = ?")
        params.append(iso_utc_now())
        params.append(participant_id)

        with self._lock:
            with self.get_connection() as conn:
                conn.execute(
                    f"UPDATE participants SET {', '.join(updates)} WHERE id = ?",
                    params,
                )
                conn.commit()
            return self.get_participant(participant_id)  # type: ignore[return-value]

    # -------------------------------------------------------------------------
    # Locations
    # -------------------------------------------------------------------------

    def get_locations(self, active_only: bool = False) -> list[dict[str, Any]]:
        with self.get_connection() as conn:
            query = "SELECT * FROM locations"
            params: tuple[Any, ...] = ()
            if active_only:
                query += " WHERE active = 1"
            query += " ORDER BY id ASC"
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_location(self, location_id: int) -> dict[str, Any] | None:
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM locations WHERE id = ?", (location_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_location_by_code(self, code: str) -> dict[str, Any] | None:
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM locations WHERE location_code = ?", (code,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def create_location(
        self,
        name: str,
        pi_hotspot_distance_m: float | None = None,
        scenario: str = "LOS",
        notes: str | None = None,
    ) -> dict[str, Any]:
        cleaned_name = (name or "").strip()
        if not cleaned_name:
            raise ValidationError("Location name is required.")

        scenario = (scenario or "").strip().upper()
        if scenario not in SCENARIOS:
            raise ValidationError(
                f"Invalid scenario '{scenario}'. Allowed: {', '.join(sorted(SCENARIOS))}"
            )

        parsed_dist: float | None = None
        if pi_hotspot_distance_m is not None and str(pi_hotspot_distance_m).strip() != "":
            try:
                parsed_dist = float(pi_hotspot_distance_m)
                if parsed_dist < 0 or parsed_dist > 1000:
                    raise ValidationError("pi_hotspot_distance_m must be between 0 and 1000 m.")
            except (ValueError, TypeError) as exc:
                raise ValidationError(f"Invalid pi_hotspot_distance_m: {pi_hotspot_distance_m}") from exc

        cleaned_notes = (notes or "").strip() or None
        now = iso_utc_now()

        with self._lock:
            with self.get_connection() as conn:
                code = self._next_code(conn, "L")
                cursor = conn.execute(
                    """
                    INSERT INTO locations (
                        location_code, name, pi_hotspot_distance_m, scenario, notes, active, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (code, cleaned_name, parsed_dist, scenario, cleaned_notes, now, now),
                )
                new_id = cursor.lastrowid
                conn.commit()
                return self.get_location(new_id)  # type: ignore[return-value]

    def update_location(self, location_id: int, **fields: Any) -> dict[str, Any]:
        existing = self.get_location(location_id)
        if not existing:
            raise NotFoundError(f"Location with ID {location_id} not found.")

        updates: list[str] = []
        params: list[Any] = []

        if "name" in fields:
            name = (fields["name"] or "").strip()
            if not name:
                raise ValidationError("Location name cannot be empty.")
            updates.append("name = ?")
            params.append(name)

        if "pi_hotspot_distance_m" in fields:
            dist = fields["pi_hotspot_distance_m"]
            if dist is None or str(dist).strip() == "":
                updates.append("pi_hotspot_distance_m = NULL")
            else:
                try:
                    parsed_d = float(dist)
                    if parsed_d < 0 or parsed_d > 1000:
                        raise ValidationError("pi_hotspot_distance_m must be between 0 and 1000 m.")
                    updates.append("pi_hotspot_distance_m = ?")
                    params.append(parsed_d)
                except (ValueError, TypeError) as exc:
                    raise ValidationError(f"Invalid pi_hotspot_distance_m: {dist}") from exc

        if "scenario" in fields:
            sc = (fields["scenario"] or "").strip().upper()
            if sc not in SCENARIOS:
                raise ValidationError(f"Invalid scenario '{sc}'.")
            updates.append("scenario = ?")
            params.append(sc)

        if "notes" in fields:
            notes = (fields["notes"] or "").strip() or None
            updates.append("notes = ?")
            params.append(notes)

        if "active" in fields:
            updates.append("active = ?")
            params.append(1 if fields["active"] else 0)

        if not updates:
            return existing

        updates.append("updated_at = ?")
        params.append(iso_utc_now())
        params.append(location_id)

        with self._lock:
            with self.get_connection() as conn:
                conn.execute(
                    f"UPDATE locations SET {', '.join(updates)} WHERE id = ?",
                    params,
                )
                conn.commit()
            return self.get_location(location_id)  # type: ignore[return-value]

    # -------------------------------------------------------------------------
    # Trials
    # -------------------------------------------------------------------------

    def get_trials(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT t.*,
                       p.participant_code, p.name AS participant_name, p.height_cm, p.body_type, p.gender,
                       l.location_code, l.name AS location_name, l.pi_hotspot_distance_m, l.scenario, l.notes AS location_notes
                FROM trials t
                JOIN participants p ON t.participant_id = p.id
                JOIN locations l ON t.location_id = l.id
                ORDER BY t.id DESC
                LIMIT ?
                """,
                (max(1, min(limit, 1000)),),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_trial_by_session_id(self, session_id: str) -> dict[str, Any] | None:
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT t.*,
                       p.participant_code, p.name AS participant_name, p.height_cm, p.body_type, p.gender,
                       l.location_code, l.name AS location_name, l.pi_hotspot_distance_m, l.scenario, l.notes AS location_notes
                FROM trials t
                JOIN participants p ON t.participant_id = p.id
                JOIN locations l ON t.location_id = l.id
                WHERE t.session_id = ?
                """,
                (session_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def create_trial(
        self,
        session_id: str,
        participant_id: int,
        location_id: int,
        walking_type: str,
        clothing: str,
        session_directory: str,
        human_count_inside: int = 0,
        human_count_outside: int = 0,
        started_at: str | None = None,
    ) -> dict[str, Any]:
        participant = self.get_participant(participant_id)
        if not participant:
            raise NotFoundError(f"Participant ID {participant_id} not found.")
        if not participant["active"]:
            raise ValidationError(f"Participant {participant['participant_code']} is inactive.")

        location = self.get_location(location_id)
        if not location:
            raise NotFoundError(f"Location ID {location_id} not found.")
        if not location["active"]:
            raise ValidationError(f"Location {location['location_code']} is inactive.")

        if walking_type not in WALKING_TYPES:
            raise ValidationError(
                f"Invalid walking_type '{walking_type}'. Allowed: {', '.join(sorted(WALKING_TYPES))}"
            )

        if clothing not in CLOTHING_TYPES:
            raise ValidationError(
                f"Invalid clothing '{clothing}'. Allowed: {', '.join(sorted(CLOTHING_TYPES))}"
            )

        started_at = started_at or iso_utc_now()

        with self._lock:
            with self.get_connection() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO trials (
                        session_id, participant_id, location_id, walking_type, clothing,
                        human_count_inside, human_count_outside,
                        quality_status, session_directory, started_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'RECORDING', ?, ?)
                    """,
                    (
                        session_id,
                        participant_id,
                        location_id,
                        walking_type,
                        clothing,
                        human_count_inside,
                        human_count_outside,
                        str(session_directory),
                        started_at,
                    ),
                )
                conn.commit()
            return self.get_trial_by_session_id(session_id)  # type: ignore[return-value]

    def update_trial_quality(
        self,
        session_id: str,
        quality_status: str,
        packet_count: int | None = None,
        packet_rate_hz: float | None = None,
        maximum_gap_s: float | None = None,
        completed_at: str | None = None,
    ) -> dict[str, Any] | None:
        completed_at = completed_at or iso_utc_now()
        with self._lock:
            with self.get_connection() as conn:
                conn.execute(
                    """
                    UPDATE trials SET
                        quality_status = ?,
                        packet_count = ?,
                        packet_rate_hz = ?,
                        maximum_gap_s = ?,
                        completed_at = ?
                    WHERE session_id = ?
                    """,
                    (
                        quality_status,
                        packet_count,
                        packet_rate_hz,
                        maximum_gap_s,
                        completed_at,
                        session_id,
                    ),
                )
                conn.commit()
            return self.get_trial_by_session_id(session_id)
