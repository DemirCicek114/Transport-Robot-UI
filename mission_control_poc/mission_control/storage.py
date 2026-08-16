from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .types import MissionState


class Storage:
    """SQLite storage for missions, mission events, and robot status.

    Why SQLite?
    - single-file persistence (easy PoC)
    - zero external services
    - good enough for a few robots and a UI

    Note: This class is thread-safe via a coarse lock.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    def init(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS missions (
                    id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    requested_by TEXT NOT NULL,
                    command_source TEXT NOT NULL,
                    from_dest TEXT,
                    to_dest TEXT NOT NULL,
                    schedule_type TEXT NOT NULL,
                    state TEXT NOT NULL,
                    assigned_robot_id TEXT,
                    started_at REAL,
                    completed_at REAL,
                    outcome TEXT NOT NULL,
                    retries INTEGER NOT NULL DEFAULT 0,
                    help_required INTEGER NOT NULL DEFAULT 0,
                    last_update_at REAL NOT NULL,
                    notes TEXT
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS mission_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL,
                    ts REAL NOT NULL,
                    event TEXT NOT NULL,
                    details TEXT,
                    FOREIGN KEY (mission_id) REFERENCES missions (id)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS robots (
                    id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    current_mission_id TEXT,
                    last_heartbeat_at REAL,
                    connection_ok INTEGER NOT NULL DEFAULT 1,
                    localization_valid INTEGER NOT NULL DEFAULT 1,
                    obstacle_stop INTEGER NOT NULL DEFAULT 0,
                    blocked INTEGER NOT NULL DEFAULT 0,
                    battery_v REAL,
                    x REAL,
                    y REAL,
                    yaw REAL
                )
                """
            )
            self._conn.commit()

    # ---------------- Missions ----------------

    def create_mission(self, mission: Dict[str, Any]) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO missions (
                    id, created_at, requested_by, command_source, from_dest, to_dest,
                    schedule_type, state, assigned_robot_id, started_at, completed_at,
                    outcome, retries, help_required, last_update_at, notes
                ) VALUES (
                    :id, :created_at, :requested_by, :command_source, :from_dest, :to_dest,
                    :schedule_type, :state, :assigned_robot_id, :started_at, :completed_at,
                    :outcome, :retries, :help_required, :last_update_at, :notes
                )
                """,
                mission,
            )
            self._conn.commit()

    def get_mission(self, mission_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.cursor()
            row = cur.execute("SELECT * FROM missions WHERE id = ?", (mission_id,)).fetchone()
            return dict(row) if row else None

    def list_missions(self, limit: int = 200) -> List[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.cursor()
            rows = cur.execute(
                "SELECT * FROM missions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_mission(self, mission_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["last_update_at"] = time.time()
        keys = list(fields.keys())
        set_clause = ", ".join([f"{k} = :{k}" for k in keys])
        fields["id"] = mission_id
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                f"UPDATE missions SET {set_clause} WHERE id = :id",
                fields,
            )
            self._conn.commit()

    def append_event(self, mission_id: str, event: str, details: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO mission_events (mission_id, ts, event, details)
                VALUES (?, ?, ?, ?)
                """,
                (mission_id, time.time(), event, json.dumps(details) if details else None),
            )
            self._conn.commit()

    def list_events(self, mission_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.cursor()
            rows = cur.execute(
                """
                SELECT * FROM mission_events
                WHERE mission_id = ?
                ORDER BY ts ASC
                LIMIT ?
                """,
                (mission_id, limit),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                if d.get("details"):
                    try:
                        d["details"] = json.loads(d["details"])
                    except Exception:
                        pass
                out.append(d)
            return out

    def delete_completed_missions(self) -> int:
        with self._lock:
            cur = self._conn.cursor()
            rows = cur.execute(
                "SELECT id FROM missions WHERE state = ?",
                (MissionState.COMPLETED.value,),
            ).fetchall()
            mission_ids = [str(row[0]) for row in rows]
            if not mission_ids:
                return 0

            placeholders = ", ".join("?" for _ in mission_ids)
            cur.execute(
                f"DELETE FROM mission_events WHERE mission_id IN ({placeholders})",
                mission_ids,
            )
            cur.execute(
                "DELETE FROM missions WHERE state = ?",
                (MissionState.COMPLETED.value,),
            )
            self._conn.commit()
            return len(mission_ids)

    def delete_requested_missions(self) -> int:
        with self._lock:
            cur = self._conn.cursor()
            rows = cur.execute(
                "SELECT id FROM missions WHERE state = ?",
                (MissionState.REQUESTED.value,),
            ).fetchall()
            mission_ids = [str(row[0]) for row in rows]
            if not mission_ids:
                return 0

            placeholders = ", ".join("?" for _ in mission_ids)
            cur.execute(
                f"DELETE FROM mission_events WHERE mission_id IN ({placeholders})",
                mission_ids,
            )
            cur.execute(
                "DELETE FROM missions WHERE state = ?",
                (MissionState.REQUESTED.value,),
            )
            self._conn.commit()
            return len(mission_ids)

    def delete_all_missions(self) -> int:
        with self._lock:
            cur = self._conn.cursor()
            rows = cur.execute(
                "SELECT id FROM missions WHERE state != ?",
                (MissionState.REQUESTED.value,),
            ).fetchall()
            mission_ids = [str(row[0]) for row in rows]
            if not mission_ids:
                return 0

            placeholders = ", ".join("?" for _ in mission_ids)
            cur.execute(
                f"DELETE FROM mission_events WHERE mission_id IN ({placeholders})",
                mission_ids,
            )
            cur.execute(
                f"DELETE FROM missions WHERE id IN ({placeholders})",
                mission_ids,
            )
            self._conn.commit()
            return len(mission_ids)

    def clear_robot_assignments(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("UPDATE robots SET current_mission_id = NULL")
            self._conn.commit()

    # ---------------- Robots ----------------

    def upsert_robot(self, robot_id: str, **fields: Any) -> None:
        if not fields:
            return
        # Ensure required fields exist for insert.
        existing = self.get_robot(robot_id)
        if existing is None:
            base = {
                "id": robot_id,
                "state": fields.get("state", "Idle"),
                "mode": fields.get("mode", "Auto"),
                "current_mission_id": fields.get("current_mission_id"),
                "last_heartbeat_at": fields.get("last_heartbeat_at"),
                "connection_ok": int(fields.get("connection_ok", 1)),
                "localization_valid": int(fields.get("localization_valid", 1)),
                "obstacle_stop": int(fields.get("obstacle_stop", 0)),
                "blocked": int(fields.get("blocked", 0)),
                "battery_v": fields.get("battery_v"),
                "x": fields.get("x"),
                "y": fields.get("y"),
                "yaw": fields.get("yaw"),
            }
            with self._lock:
                cur = self._conn.cursor()
                cur.execute(
                    """
                    INSERT INTO robots (
                        id, state, mode, current_mission_id, last_heartbeat_at,
                        connection_ok, localization_valid, obstacle_stop, blocked,
                        battery_v, x, y, yaw
                    ) VALUES (
                        :id, :state, :mode, :current_mission_id, :last_heartbeat_at,
                        :connection_ok, :localization_valid, :obstacle_stop, :blocked,
                        :battery_v, :x, :y, :yaw
                    )
                    """,
                    base,
                )
                self._conn.commit()
            return

        # Update only provided fields.
        keys = list(fields.keys())
        set_clause = ", ".join([f"{k} = :{k}" for k in keys])
        fields["id"] = robot_id
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                f"UPDATE robots SET {set_clause} WHERE id = :id",
                fields,
            )
            self._conn.commit()

    def get_robot(self, robot_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.cursor()
            row = cur.execute("SELECT * FROM robots WHERE id = ?", (robot_id,)).fetchone()
            return dict(row) if row else None

    def list_robots(self) -> List[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.cursor()
            rows = cur.execute("SELECT * FROM robots ORDER BY id ASC").fetchall()
            return [dict(r) for r in rows]
