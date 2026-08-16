from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from .config_loader import DestinationConfig
from .robot_adapter import RobotAdapter, RobotTelemetry, SimRobotAdapter
from .storage import Storage
from .types import CommandSource, MissionOutcome, MissionState, RobotMode


ACTIVE_MOTION_STATES = {MissionState.EN_ROUTE.value, MissionState.RETURNING.value}


@dataclass
class MissionCreate:
    requested_by: str
    command_source: CommandSource
    to_destination: str
    schedule_type: str = "single"  # "single" or "round_trip"
    from_destination: Optional[str] = None
    assigned_robot_id: Optional[str] = None
    notes: str = ""


class MissionControl:
    """Mission control + scheduling + system interfaces (ROS2-independent).

    This is the "mission backbone" described in your requirements:
    - Mission request validation (S3.2.2)
    - Action states (S3.2.1)
    - Manual safe pause (S3.2.3)
    - Scheduling policy / conflict prevention (S3.2.4)
    - Blocked/trapped telemetry without interrupting Nav2 control (S3.2.5)
    - Mission logging (S3.2.6) + command-source metadata (S3.2.12)
    - Status interface (S3.2.8) + subsystem status inputs (S3.2.9)
    - Config-driven destinations (S3.2.10)
    """

    def __init__(self, storage: Storage, dest_config: DestinationConfig):
        self.storage = storage
        self.dest_config = dest_config

        self._robots: Dict[str, RobotAdapter] = {}
        self._blocked_since: Dict[str, float] = {}  # robot_id -> ts
        self._blocked_reported: Dict[str, str] = {}  # robot_id -> mission_id

        self._loop_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    # ---------------- Setup ----------------

    def register_robot(self, adapter: RobotAdapter) -> None:
        self._robots[adapter.robot_id] = adapter
        # Seed robot record.
        tel = adapter.snapshot()
        self.storage.upsert_robot(
            adapter.robot_id,
            state=tel.state.value,
            mode=tel.mode.value,
            current_mission_id=tel.current_mission_id,
            last_heartbeat_at=tel.last_heartbeat_at,
            connection_ok=int(tel.connection_ok),
            localization_valid=int(tel.localization_valid),
            obstacle_stop=int(tel.obstacle_stop),
            blocked=int(tel.blocked),
            battery_v=tel.battery_v,
            x=tel.pose.get("x"),
            y=tel.pose.get("y"),
            yaw=tel.pose.get("yaw"),
        )

    def registered_robot_ids(self) -> List[str]:
        return sorted(self._robots)

    async def start(self) -> None:
        if self._loop_task and not self._loop_task.done():
            return
        self._stop_event.clear()
        self._loop_task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._loop_task:
            await self._loop_task
        for adapter in self._robots.values():
            adapter.shutdown()

    # ---------------- Mission API ----------------

    def create_mission(self, req: MissionCreate) -> str:
        return self._create_mission_record(req, MissionState.IDLE)

    def create_request(self, req: MissionCreate) -> str:
        return self._create_mission_record(req, MissionState.REQUESTED)

    def _create_mission_record(self, req: MissionCreate, state: MissionState) -> str:
        # Validate destinations (S3.2.2 + S3.2.10)
        if not self.dest_config.validate(req.to_destination):
            raise ValueError(f"Invalid destination: {req.to_destination}")

        if req.from_destination and not self.dest_config.validate(req.from_destination):
            raise ValueError(f"Invalid from_destination: {req.from_destination}")

        if req.schedule_type not in ("single", "round_trip"):
            raise ValueError("schedule_type must be 'single' or 'round_trip'")
        if req.schedule_type == "round_trip" and not req.from_destination and not self.dest_config.home():
            raise ValueError("Round trip requests need a return destination.")

        mission_id = str(uuid.uuid4())
        now = time.time()
        mission = {
            "id": mission_id,
            "created_at": now,
            "requested_by": req.requested_by,
            "command_source": json.dumps(req.command_source.to_dict()),
            "from_dest": req.from_destination,
            "to_dest": req.to_destination,
            "schedule_type": req.schedule_type,
            "state": state.value,
            "assigned_robot_id": req.assigned_robot_id,
            "started_at": None,
            "completed_at": None,
            "outcome": MissionOutcome.NONE.value,
            "retries": 0,
            "help_required": 0,
            "last_update_at": now,
            "notes": req.notes,
        }
        self.storage.create_mission(mission)
        event_name = "request_created" if state == MissionState.REQUESTED else "mission_created"
        self.storage.append_event(
            mission_id,
            event_name,
            details={
                "requested_by": req.requested_by,
                "command_source": req.command_source.to_dict(),
                "from": req.from_destination,
                "to": req.to_destination,
                "schedule_type": req.schedule_type,
                "assigned_robot_id": req.assigned_robot_id,
            },
        )
        return mission_id

    def start_request(
        self,
        mission_id: str,
        command_source: CommandSource,
        assigned_robot_id: Optional[str] = None,
    ) -> None:
        mission = self.storage.get_mission(mission_id)
        if not mission:
            raise KeyError("request not found")

        if mission["state"] == MissionState.COMPLETED.value:
            raise RuntimeError("Cannot start a completed request.")
        if mission["state"] not in {MissionState.REQUESTED.value, MissionState.IDLE.value}:
            raise RuntimeError("Request has already been started.")

        updates: Dict[str, Any] = {"state": MissionState.IDLE.value}
        if assigned_robot_id:
            updates["assigned_robot_id"] = assigned_robot_id
        self.storage.update_mission(mission_id, **updates)
        self.storage.append_event(
            mission_id,
            "request_started",
            {
                "command_source": command_source.to_dict(),
                "assigned_robot_id": assigned_robot_id,
            },
        )

    async def start_return_trip(self, mission_id: str, command_source: CommandSource) -> Dict[str, Any]:
        mission = self.storage.get_mission(mission_id)
        if not mission:
            raise KeyError("mission not found")
        if mission["schedule_type"] != "round_trip":
            raise RuntimeError("Only round trip missions can be returned.")
        if mission["state"] != MissionState.WAITING_FOR_RETURN.value:
            raise RuntimeError("Mission is not waiting for return.")

        return_destination = self._return_destination_for(mission)
        if not return_destination or not self.dest_config.validate(return_destination):
            raise RuntimeError("Return destination is missing.")

        robot_id = mission.get("assigned_robot_id")
        if not robot_id or robot_id not in self._robots:
            raise RuntimeError("Assigned robot is not available.")

        tel = self._robots[robot_id].snapshot()
        if tel.current_mission_id is not None or tel.state not in (MissionState.IDLE, MissionState.COMPLETED):
            raise RuntimeError("Robot is not ready to return yet.")
        if not tel.connection_ok:
            raise RuntimeError("Robot is not connected.")
        if not tel.localization_valid:
            raise RuntimeError("Robot start position is not available.")
        if not self._robots[robot_id].navigation_ready():
            raise RuntimeError(
                "Nav2 is still starting; wait for Navigation to report unlocked."
            )

        await self._robots[robot_id].start_mission(mission_id, [return_destination])

        self.storage.update_mission(mission_id, state=MissionState.RETURNING.value)
        self.storage.append_event(
            mission_id,
            "return_started",
            {
                "robot_id": robot_id,
                "return_destination": return_destination,
                "command_source": command_source.to_dict(),
            },
        )
        return {"robot_id": robot_id, "return_destination": return_destination}

    async def pause_mission(self, mission_id: str, command_source: CommandSource) -> None:
        mission = self.storage.get_mission(mission_id)
        if not mission:
            raise KeyError("mission not found")

        if mission["state"] in {MissionState.COMPLETED.value, MissionState.WAITING_FOR_RETURN.value}:
            return
        if mission["state"] not in ACTIVE_MOTION_STATES:
            raise RuntimeError("Mission is not moving.")

        robot_id = mission.get("assigned_robot_id")
        if robot_id and robot_id in self._robots:
            await self._robots[robot_id].pause()

        previous_state = mission["state"]
        self.storage.update_mission(mission_id, state=MissionState.PAUSED.value)
        self.storage.append_event(
            mission_id,
            "paused",
            {
                "previous_state": previous_state,
                "command_source": command_source.to_dict(),
            },
        )

    async def resume_mission(self, mission_id: str, command_source: CommandSource) -> None:
        mission = self.storage.get_mission(mission_id)
        if not mission:
            raise KeyError("mission not found")

        if mission["state"] == MissionState.COMPLETED.value:
            return
        if mission["state"] != MissionState.PAUSED.value:
            raise RuntimeError("Mission is not paused.")

        robot_id = mission.get("assigned_robot_id")
        if robot_id and robot_id in self._robots:
            telemetry = self._robots[robot_id].snapshot()
            if not telemetry.connection_ok:
                raise RuntimeError("Robot is disconnected; the mission cannot resume.")
            if not telemetry.localization_valid:
                raise RuntimeError(
                    "AMCL is not ready; check Robot brain before resuming navigation."
                )
            await self._robots[robot_id].resume()

        resume_state = self._state_before_pause(mission_id) or MissionState.EN_ROUTE.value
        self.storage.update_mission(mission_id, state=resume_state)
        self.storage.append_event(
            mission_id,
            "resumed",
            {
                "state": resume_state,
                "command_source": command_source.to_dict(),
            },
        )

    async def cancel_mission(self, mission_id: str, command_source: CommandSource) -> None:
        mission = self.storage.get_mission(mission_id)
        if not mission:
            raise KeyError("mission not found")

        if mission["state"] == MissionState.COMPLETED.value:
            return

        robot_id = mission.get("assigned_robot_id")
        if robot_id and robot_id in self._robots:
            await self._robots[robot_id].cancel()

        now = time.time()
        self.storage.update_mission(
            mission_id,
            state=MissionState.COMPLETED.value,
            outcome=MissionOutcome.CANCELED.value,
            completed_at=now,
        )
        self.storage.append_event(mission_id, "canceled", {"command_source": command_source.to_dict()})

    # ---------------- Robot telemetry input (S3.2.9) ----------------

    def ingest_robot_telemetry(self, robot_id: str, telemetry: Dict[str, Any]) -> None:
        """Accepts subsystem status inputs.

        In a real system, your robot-side code would call this endpoint (or publish to a broker).
        For the PoC, we also map a few fields onto SimRobotAdapter so you can test blocked/pause flows.
        """
        # Update DB robot record
        fields: Dict[str, Any] = {}
        if "connection_ok" in telemetry:
            fields["connection_ok"] = int(bool(telemetry["connection_ok"]))
        if "localization_valid" in telemetry:
            fields["localization_valid"] = int(bool(telemetry["localization_valid"]))
        if "obstacle_stop" in telemetry:
            fields["obstacle_stop"] = int(bool(telemetry["obstacle_stop"]))
        if "blocked" in telemetry:
            fields["blocked"] = int(bool(telemetry["blocked"]))
        if "manual_override_active" in telemetry:
            fields["mode"] = (
                RobotMode.MANUAL_OVERRIDE.value
                if bool(telemetry["manual_override_active"])
                else RobotMode.AUTO.value
            )
        if "battery_v" in telemetry:
            fields["battery_v"] = float(telemetry["battery_v"])
        for k in ("x", "y", "yaw"):
            if k in telemetry:
                fields[k] = float(telemetry[k])

        if fields:
            fields["last_heartbeat_at"] = time.time()
            self.storage.upsert_robot(robot_id, **fields)

        # If it's a SimRobotAdapter, keep sim state in sync for demos/tests.
        adapter = self._robots.get(robot_id)
        if isinstance(adapter, SimRobotAdapter):
            if "blocked" in telemetry:
                adapter.set_blocked(bool(telemetry["blocked"]))
            if "localization_valid" in telemetry:
                adapter.set_localization_valid(bool(telemetry["localization_valid"]))
            if "obstacle_stop" in telemetry:
                adapter.set_obstacle_stop(bool(telemetry["obstacle_stop"]))
            if "manual_override_active" in telemetry:
                adapter.set_manual_override(bool(telemetry["manual_override_active"]))

    # ---------------- Snapshot for UI ----------------

    def snapshot(self) -> Dict[str, Any]:
        now = time.time()
        robots = {robot["id"]: dict(robot) for robot in self.storage.list_robots()}
        for rid, adapter in self._robots.items():
            robot = robots.setdefault(rid, {"id": rid})
            robot["power"] = asdict(adapter.power_snapshot())
        for robot in robots.values():
            last_heartbeat_at = robot.get("last_heartbeat_at")
            if last_heartbeat_at is None:
                robot["online"] = False
                robot["connection_ok"] = 0
                continue
            online = (now - float(last_heartbeat_at)) <= 5.0
            robot["online"] = online
            if not online:
                robot["connection_ok"] = 0
        return {
            "server_time": now,
            "destinations": [d.__dict__ for d in self.dest_config.list()],
            "robots": [robots[rid] for rid in sorted(robots)],
            "missions": self.storage.list_missions(),
        }

    def mission_detail(self, mission_id: str) -> Dict[str, Any]:
        mission = self.storage.get_mission(mission_id)
        if not mission:
            raise KeyError("mission not found")
        events = self.storage.list_events(mission_id)
        return {"mission": mission, "events": events}

    async def clear_completed_history(self) -> int:
        return self.storage.delete_completed_missions()

    async def clear_pending_requests(self) -> int:
        return self.storage.delete_requested_missions()

    async def clear_all_missions(self) -> int:
        # Flush any just-finished robot state so completed missions can be cleared safely.
        snapshots = self._poll_robot_adapters()
        await self._handle_completions(snapshots)
        snapshots = self._poll_robot_adapters()

        active_robot_ids = [
            rid
            for rid, tel in snapshots.items()
            if tel.current_mission_id is not None
        ]
        if active_robot_ids:
            robots = ", ".join(sorted(active_robot_ids))
            raise RuntimeError(
                f"Cannot clear queue while robots still have active missions: {robots}. "
                "Pause or cancel those missions first."
            )

        waiting_return = [
            mission
            for mission in self.storage.list_missions(limit=200)
            if mission["state"] == MissionState.WAITING_FOR_RETURN.value
        ]
        if waiting_return:
            raise RuntimeError("Cannot clear queue while a round trip is waiting for return.")

        self._blocked_since.clear()
        self._blocked_reported.clear()
        self.storage.clear_robot_assignments()
        return self.storage.delete_all_missions()

    async def send_robot_manual_drive_command(
        self,
        robot_id: str,
        linear: float,
        angular: float,
        command_source: CommandSource,
    ) -> Dict[str, Any]:
        adapter = self._robots.get(robot_id)
        if adapter is None:
            raise KeyError("robot not found")

        linear_value = float(linear)
        angular_value = float(angular)
        is_motion_command = abs(linear_value) >= 1e-4 or abs(angular_value) >= 1e-4
        paused_mission_id: Optional[str] = None

        if is_motion_command:
            manual_drive = adapter.manual_drive_snapshot()
            if not manual_drive.get("ready"):
                raise RuntimeError(
                    str(
                        manual_drive.get("message")
                        or "Manual recovery drive is not ready."
                    )
                )

            telemetry = adapter.snapshot()
            mission_id = telemetry.current_mission_id
            mission = self.storage.get_mission(mission_id) if mission_id else None
            if mission and mission["state"] in ACTIVE_MOTION_STATES:
                await self.pause_mission(mission_id, command_source)
                paused_mission_id = mission_id

        await adapter.send_manual_drive_command(linear_value, angular_value)

        if paused_mission_id:
            self.storage.append_event(
                paused_mission_id,
                "manual_recovery_started",
                {
                    "robot_id": robot_id,
                    "linear": linear_value,
                    "angular": angular_value,
                    "command_source": command_source.to_dict(),
                },
            )

        return {
            "robot_id": robot_id,
            "linear": linear_value,
            "angular": angular_value,
            "paused_mission_id": paused_mission_id,
            "command_source": command_source.to_dict(),
        }

    async def localize_robot(self, robot_id: str, command_source: CommandSource) -> Dict[str, Any]:
        adapter = self._robots.get(robot_id)
        if adapter is None:
            raise KeyError("robot not found")

        result = await adapter.localize()
        result["command_source"] = command_source.to_dict()
        return result

    async def send_robot_system_command(
        self,
        robot_id: str,
        command: str,
        command_source: CommandSource,
        map_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        adapter = self._robots.get(robot_id)
        if adapter is None:
            raise KeyError("robot not found")

        await adapter.send_system_command(command, map_name=map_name)
        return {
            "robot_id": robot_id,
            "command": command,
            "map_name": map_name,
            "command_source": command_source.to_dict(),
        }

    async def set_robot_initial_pose(
        self,
        robot_id: str,
        x: float,
        y: float,
        yaw: float,
        command_source: CommandSource,
    ) -> Dict[str, Any]:
        adapter = self._robots.get(robot_id)
        if adapter is None:
            raise KeyError("robot not found")

        await adapter.set_initial_pose(x, y, yaw)
        operator_snapshot = adapter.operator_snapshot()
        initial_pose = operator_snapshot.get("initial_pose") or {
            "x": float(x),
            "y": float(y),
            "yaw": 0.0,
        }
        return {
            "robot_id": robot_id,
            "pose": initial_pose,
            "localization": operator_snapshot.get("localization"),
            "command_source": command_source.to_dict(),
        }

    async def set_robot_goal_pose(
        self,
        robot_id: str,
        x: float,
        y: float,
        yaw: float,
        command_source: CommandSource,
    ) -> Dict[str, Any]:
        adapter = self._robots.get(robot_id)
        if adapter is None:
            raise KeyError("robot not found")

        await adapter.set_goal_pose(x, y, yaw)
        return {
            "robot_id": robot_id,
            "pose": {"x": float(x), "y": float(y), "yaw": float(yaw)},
            "command_source": command_source.to_dict(),
        }

    async def save_robot_map(self, robot_id: str, map_name: str, command_source: CommandSource) -> Dict[str, Any]:
        adapter = self._robots.get(robot_id)
        if adapter is None:
            raise KeyError("robot not found")

        response = await adapter.save_map(map_name)
        response["robot_id"] = robot_id
        response["command_source"] = command_source.to_dict()
        return response

    async def delete_robot_map(self, robot_id: str, map_name: str, command_source: CommandSource) -> Dict[str, Any]:
        adapter = self._robots.get(robot_id)
        if adapter is None:
            raise KeyError("robot not found")

        response = await adapter.delete_map(map_name)
        response["robot_id"] = robot_id
        response["command_source"] = command_source.to_dict()
        return response

    async def load_robot_map_preview(self, robot_id: str, map_name: str) -> Dict[str, Any]:
        adapter = self._robots.get(robot_id)
        if adapter is None:
            raise KeyError("robot not found")
        return await adapter.load_map_preview(map_name)

    def robot_operator_snapshot(
        self,
        robot_id: str,
        *,
        include_map: bool = True,
    ) -> Dict[str, Any]:
        adapter = self._robots.get(robot_id)
        if adapter is None:
            raise KeyError("robot not found")

        tel = adapter.snapshot()
        snapshot = adapter.operator_snapshot(include_map=include_map)
        snapshot["robot_pose"] = dict(tel.pose)
        snapshot["robot_mode"] = tel.mode.value
        snapshot["robot_state"] = tel.state.value
        snapshot["robot_id"] = robot_id
        return snapshot

    # ---------------- Internal loop ----------------

    async def _loop(self) -> None:
        # tick rate for control logic (~5 Hz)
        tick_s = 0.2
        while not self._stop_event.is_set():
            try:
                await self._tick_once()
            except Exception as e:
                # swallow to keep loop alive, but record
                # (in a production system you'd send this to a logger)
                print(f"[MissionControl] loop error: {e}")
            await asyncio.sleep(tick_s)

    async def _tick_once(self) -> None:
        # 1) Update robots from adapters (or external telemetry)
        snapshots = self._poll_robot_adapters()

        # 2) Handle mission completions
        await self._handle_completions(snapshots)

        # 3) Connection watchdog (optional safety)
        await self._connection_watchdog(snapshots)

        # 4) Localization watchdog
        await self._localization_watchdog(snapshots)

        # 5) Blocked/trapped detection + retries
        await self._blocked_detection(snapshots)

        # 6) Schedule pending missions
        await self._schedule_pending(snapshots)

    def _poll_robot_adapters(self) -> Dict[str, RobotTelemetry]:
        out: Dict[str, RobotTelemetry] = {}
        for rid, adapter in self._robots.items():
            tel = adapter.snapshot()
            out[rid] = tel
            self.storage.upsert_robot(
                rid,
                state=tel.state.value,
                mode=tel.mode.value,
                current_mission_id=tel.current_mission_id,
                last_heartbeat_at=tel.last_heartbeat_at,
                connection_ok=int(tel.connection_ok),
                localization_valid=int(tel.localization_valid),
                obstacle_stop=int(tel.obstacle_stop),
                blocked=int(tel.blocked),
                battery_v=tel.battery_v,
                x=tel.pose.get("x"),
                y=tel.pose.get("y"),
                yaw=tel.pose.get("yaw"),
            )
        return out

    async def _handle_completions(self, snapshots: Dict[str, RobotTelemetry]) -> None:
        for rid, tel in snapshots.items():
            if tel.state != MissionState.COMPLETED:
                continue
            if not tel.current_mission_id:
                continue

            mission_id = tel.current_mission_id
            mission = self.storage.get_mission(mission_id)
            if not mission:
                # Unknown mission; clear robot anyway.
                await self._robots[rid].reset_to_idle()
                continue

            if mission["state"] == MissionState.COMPLETED.value:
                # Already recorded; just clear robot.
                await self._robots[rid].reset_to_idle()
                continue

            now = time.time()
            outcome = mission.get("outcome", MissionOutcome.NONE.value)
            if outcome == MissionOutcome.NONE.value:
                adapter_outcome = tel.outcome.value if tel.outcome is not None else MissionOutcome.SUCCESS.value
                outcome = adapter_outcome

            if (
                mission.get("schedule_type") == "round_trip"
                and mission["state"] == MissionState.EN_ROUTE.value
                and outcome == MissionOutcome.SUCCESS.value
            ):
                return_destination = self._return_destination_for(mission)
                self.storage.update_mission(
                    mission_id,
                    state=MissionState.WAITING_FOR_RETURN.value,
                    outcome=MissionOutcome.NONE.value,
                )
                self.storage.append_event(
                    mission_id,
                    "arrived_waiting_for_return",
                    {
                        "robot_id": rid,
                        "arrived_at": mission["to_dest"],
                        "return_destination": return_destination,
                    },
                )
                await self._robots[rid].reset_to_idle()
                continue

            self.storage.update_mission(
                mission_id,
                state=MissionState.COMPLETED.value,
                outcome=outcome,
                completed_at=now,
            )
            self.storage.append_event(
                mission_id,
                "mission_completed",
                {"robot_id": rid, "outcome": outcome},
            )
            await self._robots[rid].reset_to_idle()

    async def _connection_watchdog(self, snapshots: Dict[str, RobotTelemetry]) -> None:
        # Example policy: if a robot loses connection for >2s while en-route, pause mission.
        # (This is aligned with the EP deliverable about connection-loss stop,
        # but you can disable/adjust for PoC.)
        now = time.time()
        for rid, tel in snapshots.items():
            if not tel.current_mission_id:
                continue
            if tel.state != MissionState.EN_ROUTE:
                continue
            if (not tel.connection_ok) or (now - tel.last_heartbeat_at > 2.0):
                mission_id = tel.current_mission_id
                mission = self.storage.get_mission(mission_id)
                if mission and mission["state"] in ACTIVE_MOTION_STATES:
                    await self._robots[rid].pause()
                    previous_state = mission["state"]
                    self.storage.update_mission(mission_id, state=MissionState.PAUSED.value)
                    self.storage.append_event(
                        mission_id,
                        "auto_paused_connection_lost",
                        {"robot_id": rid, "previous_state": previous_state},
                    )

    async def _localization_watchdog(self, snapshots: Dict[str, RobotTelemetry]) -> None:
        for rid, tel in snapshots.items():
            if not tel.current_mission_id:
                continue
            adapter = self._robots[rid]
            localization_details: Dict[str, Any] = {}
            try:
                operator_snapshot = adapter.operator_snapshot(include_map=False)
                localization = operator_snapshot.get("localization")
                if isinstance(localization, dict):
                    localization_details = dict(localization)
            except Exception:
                # Telemetry still provides enough information to enforce the
                # pause even if optional diagnostics are temporarily unavailable.
                localization_details = {}
            safety_pause_active = bool(
                localization_details.get("safety_pause_active", False)
            )
            # Keep reconciling a safety pause even if AMCL recovered before
            # this scheduler poll. The adapter deliberately leaves the route
            # paused until the database records it and an operator resumes.
            if tel.localization_valid and not safety_pause_active:
                continue
            if tel.state not in {
                MissionState.EN_ROUTE,
                MissionState.RETURNING,
                MissionState.PAUSED,
            }:
                continue

            mission_id = tel.current_mission_id
            mission = self.storage.get_mission(mission_id)
            if mission and mission["state"] in ACTIVE_MOTION_STATES:
                # A ROS adapter can synchronously enter its localization safety
                # pause before this scheduler poll. Avoid issuing a second
                # pause, but still reconcile persistent mission state and log
                # the reason exactly once.
                if tel.state in {MissionState.EN_ROUTE, MissionState.RETURNING}:
                    await adapter.pause()
                previous_state = mission["state"]
                self.storage.update_mission(mission_id, state=MissionState.PAUSED.value)
                self.storage.append_event(
                    mission_id,
                    "auto_paused_localization_lost",
                    {
                        "robot_id": rid,
                        "previous_state": previous_state,
                        "localization": localization_details,
                    },
                )

    async def _blocked_detection(self, snapshots: Dict[str, RobotTelemetry]) -> None:
        now = time.time()
        for rid, tel in snapshots.items():
            mission_id = tel.current_mission_id
            if not mission_id:
                self._blocked_since.pop(rid, None)
                self._blocked_reported.pop(rid, None)
                continue

            # Only report blocked telemetry during an active motion state.
            mission = self.storage.get_mission(mission_id)
            if not mission or mission["state"] not in ACTIVE_MOTION_STATES:
                self._blocked_since.pop(rid, None)
                self._blocked_reported.pop(rid, None)
                continue
            if tel.mode == RobotMode.MANUAL_OVERRIDE:
                self._blocked_since.pop(rid, None)
                self._blocked_reported.pop(rid, None)
                continue

            blocked_now = bool(tel.blocked) or bool(tel.obstacle_stop)

            if not blocked_now:
                self._blocked_since.pop(rid, None)
                self._blocked_reported.pop(rid, None)
                continue

            # Start or continue timer
            if rid not in self._blocked_since:
                self._blocked_since[rid] = now
                continue

            blocked_for = now - self._blocked_since[rid]
            if blocked_for < 5.0:
                continue  # within detection window (≤5s target)

            # Report a continuous blocked period once. Nav2 continues owning
            # the active goal and may replan or run its collision-checked
            # recoveries while the Pi safety layer independently suppresses
            # unsafe motion. Mission Control must never create a cancel/resume
            # loop behind the operator's back.
            if self._blocked_reported.get(rid) == mission_id:
                continue

            self.storage.append_event(
                mission_id,
                "blocked_detected",
                {
                    "robot_id": rid,
                    "blocked_for_s": blocked_for,
                    "navigation_action": "unchanged",
                },
            )
            self._blocked_reported[rid] = mission_id

    async def _schedule_pending(self, snapshots: Dict[str, RobotTelemetry]) -> None:
        # Very simple FIFO: oldest created mission first.
        # We interpret "pending" missions as state=Idle and outcome=None and started_at is null.
        missions = self.storage.list_missions(limit=200)
        pending = [
            m
            for m in reversed(missions)  # list_missions returns newest first
            if m["state"] == MissionState.IDLE.value and m["outcome"] == MissionOutcome.NONE.value and m["started_at"] is None
        ]
        if not pending:
            return

        # Build list of available robots
        available: List[str] = []
        for rid, tel in snapshots.items():
            # Treat IDLE as available; also allow COMPLETED if reset didn't happen yet.
            if tel.state not in (MissionState.IDLE, MissionState.COMPLETED):
                continue
            if tel.current_mission_id is not None:
                continue
            if not tel.connection_ok:
                continue
            if not tel.localization_valid:
                continue
            if not self._robots[rid].navigation_ready():
                continue
            available.append(rid)

        if not available:
            return

        for mission in pending:
            target_robot = mission.get("assigned_robot_id")
            if target_robot is None:
                # pick first available
                rid = available[0]
            else:
                rid = target_robot
                if rid not in available:
                    continue  # wait for that robot to free up

            # Build plan
            plan = self._build_plan(mission)

            try:
                await self._robots[rid].start_mission(mission["id"], plan)
            except Exception as e:
                self.storage.append_event(
                    mission["id"],
                    "dispatch_failed",
                    {"robot_id": rid, "error": str(e)},
                )
                continue

            now = time.time()
            self.storage.update_mission(
                mission["id"],
                assigned_robot_id=rid,
                state=MissionState.EN_ROUTE.value,
                started_at=now,
            )
            self.storage.append_event(
                mission["id"],
                "dispatched",
                {"robot_id": rid, "plan": plan},
            )

            # remove robot from available list so we don't assign two missions
            if rid in available:
                available.remove(rid)
            if not available:
                break

    def _build_plan(self, mission: Dict[str, Any]) -> List[str]:
        to_dest = mission["to_dest"]
        schedule_type = mission["schedule_type"]
        if schedule_type == "single":
            return [to_dest]

        # Round trips stop at the destination and wait for an explicit Return command.
        if schedule_type == "round_trip":
            return [to_dest]

        return [to_dest]

    def _return_destination_for(self, mission: Dict[str, Any]) -> Optional[str]:
        from_dest = mission.get("from_dest")
        if from_dest:
            return from_dest

        home = self.dest_config.home()
        if home:
            return home

        return None

    def _state_before_pause(self, mission_id: str) -> Optional[str]:
        for event in reversed(self.storage.list_events(mission_id)):
            details = event.get("details") or {}
            previous_state = details.get("previous_state")
            if previous_state in ACTIVE_MOTION_STATES:
                return str(previous_state)
        return None
