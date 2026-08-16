"""Educational tests for the Mission Control PoC package.

Purpose:
- Demonstrate how to use each Python file in `mission_control/`.
- Act as executable documentation: read tests from top to bottom.

Run from `mission_control_poc/`:
    python -m unittest -v tests.test_mission_control_educational
"""

from __future__ import annotations

import asyncio
import math
import threading
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError


# Ensure `import mission_control...` works whether tests are run from repo root
# or from `mission_control_poc/`.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mission_control  # noqa: E402
from mission_control.api_models import (  # noqa: E402
    CreateMissionRequest,
    MissionCommandRequest,
    RobotGoalPoseRequest,
    RobotInitialPoseRequest,
    RobotMapDeleteRequest,
    RobotMapSaveRequest,
    RobotManualDriveRequest,
    RobotSystemCommandRequest,
    RobotTelemetryIn,
    TempDestinationRequest,
)
from mission_control.config_loader import DestinationConfig  # noqa: E402
from mission_control.robot_adapter import (  # noqa: E402
    Ros2AdapterConfig,
    Ros2RobotAdapter,
    SimRobotAdapter,
    battery_percent_from_voltage,
    _localization_footprint_map_fault,
    _localization_seed_map_fault,
    _load_map_preview_from_yaml,
)
from mission_control.scheduler import MissionControl, MissionCreate  # noqa: E402
from mission_control.storage import Storage  # noqa: E402
from mission_control.types import (  # noqa: E402
    CommandSource,
    MissionOutcome,
    MissionState,
    RobotMode,
)


def write_demo_destinations(path: Path) -> None:
    """Create a minimal YAML config for tests and examples."""
    path.write_text(
        (
            "destinations:\n"
            '  - name: "Storage"\n'
            "    pose: {x: 0.0, y: 0.0, yaw: 0.0}\n"
            '  - name: "Hall_A"\n'
            "    pose: {x: 5.2, y: 1.1, yaw: 1.57}\n"
            '  - name: "Ballroom"\n'
            "    pose: {x: 12.4, y: -3.0, yaw: 3.14}\n"
            'home_destination: "Storage"\n'
        ),
        encoding="utf-8",
    )


def set_free_localization_maps(adapter: Ros2RobotAdapter) -> None:
    """Give isolated adapter tests the physical maps present in production."""
    width = 100
    snapshot = {
        "width": width,
        "height": width,
        "resolution": 1.0,
        "origin": {"x": -25.0, "y": -25.0, "yaw": 0.0},
        "data": [0] * (width * width),
    }
    adapter._map_snapshot = snapshot
    adapter._keepout_map_snapshot = snapshot


class Test00PackageAndTypes(unittest.TestCase):
    """Covers: `mission_control/__init__.py` and `mission_control/types.py`."""

    def test_package_import_and_shared_types(self) -> None:
        # __init__.py: package import should succeed.
        self.assertTrue(hasattr(mission_control, "__package__"))

        # types.py: enums + CommandSource are used throughout API and scheduler.
        self.assertEqual(MissionState.REQUESTED.value, "Requested")
        self.assertEqual(MissionState.IDLE.value, "Idle")
        self.assertEqual(MissionState.EN_ROUTE.value, "En-route")
        self.assertEqual(MissionState.WAITING_FOR_RETURN.value, "WaitingForReturn")
        self.assertEqual(MissionState.RETURNING.value, "Returning")
        self.assertEqual(RobotMode.MANUAL_OVERRIDE.value, "ManualOverride")

        source = CommandSource(source_type="user", source_id="tablet-1", meta={"screen": "dispatch"})
        self.assertEqual(
            source.to_dict(),
            {"type": "user", "id": "tablet-1", "meta": {"screen": "dispatch"}},
        )


class Test01ConfigLoader(unittest.TestCase):
    """Covers: `mission_control/config_loader.py`."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tmpdir.name) / "destinations.yaml"
        write_demo_destinations(self.config_path)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_load_validate_and_home_destination(self) -> None:
        # Usage pattern: create loader -> load YAML -> validate user inputs.
        config = DestinationConfig(self.config_path)
        destinations, home = config.load()

        self.assertIn("Hall_A", destinations)
        self.assertTrue(config.validate("Ballroom"))
        self.assertFalse(config.validate("UnknownRoom"))
        self.assertEqual(home, "Storage")
        self.assertEqual(config.home(), "Storage")
        self.assertEqual(len(config.list()), 3)

    def test_upsert_destination_overwrites_existing_entry(self) -> None:
        config = DestinationConfig(self.config_path)
        config.load()

        destination = config.upsert_destination(
            "Temp Destination",
            {"x": 1.25, "y": -0.75, "yaw": 0.5},
            notes="Updated from map panel",
        )

        self.assertEqual(destination.pose["x"], 1.25)
        self.assertTrue(config.validate("Temp Destination"))
        self.assertEqual(config.list()[-1].name, "Temp Destination")


class Test02Storage(unittest.TestCase):
    """Covers: `mission_control/storage.py`."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "mission_control.sqlite3"
        self.storage = Storage(self.db_path)
        self.storage.init()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_mission_crud_and_event_timeline(self) -> None:
        now = time.time()
        mission = {
            "id": "mission-edu-1",
            "created_at": now,
            "requested_by": "student",
            "command_source": '{"type":"user","id":"student"}',
            "from_dest": None,
            "to_dest": "Hall_A",
            "schedule_type": "single",
            "state": MissionState.IDLE.value,
            "assigned_robot_id": None,
            "started_at": None,
            "completed_at": None,
            "outcome": MissionOutcome.NONE.value,
            "retries": 0,
            "help_required": 0,
            "last_update_at": now,
            "notes": "educational test",
        }

        # create/get/update/list are the core storage operations.
        self.storage.create_mission(mission)
        loaded = self.storage.get_mission("mission-edu-1")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["to_dest"], "Hall_A")

        self.storage.update_mission("mission-edu-1", state=MissionState.EN_ROUTE.value, assigned_robot_id="robot-1")
        loaded = self.storage.get_mission("mission-edu-1")
        self.assertEqual(loaded["state"], MissionState.EN_ROUTE.value)
        self.assertEqual(loaded["assigned_robot_id"], "robot-1")

        # mission_events is your audit trail.
        self.storage.append_event("mission-edu-1", "dispatched", {"robot_id": "robot-1"})
        events = self.storage.list_events("mission-edu-1")
        self.assertEqual(events[0]["event"], "dispatched")
        self.assertEqual(events[0]["details"]["robot_id"], "robot-1")

    def test_robot_upsert(self) -> None:
        # upsert_robot inserts first, then updates on later calls.
        self.storage.upsert_robot(
            "robot-1",
            state=MissionState.IDLE.value,
            mode=RobotMode.AUTO.value,
            current_mission_id=None,
            last_heartbeat_at=time.time(),
            connection_ok=1,
            localization_valid=1,
            obstacle_stop=0,
            blocked=0,
            battery_v=24.0,
            x=0.0,
            y=0.0,
            yaw=0.0,
        )
        self.storage.upsert_robot("robot-1", blocked=1, battery_v=23.4)
        robot = self.storage.get_robot("robot-1")
        self.assertEqual(robot["blocked"], 1)
        self.assertEqual(robot["battery_v"], 23.4)

    def test_delete_completed_and_all_missions(self) -> None:
        now = time.time()
        completed = {
            "id": "mission-complete-1",
            "created_at": now,
            "requested_by": "student",
            "command_source": '{"type":"user","id":"student"}',
            "from_dest": None,
            "to_dest": "Hall_A",
            "schedule_type": "single",
            "state": MissionState.COMPLETED.value,
            "assigned_robot_id": "robot-1",
            "started_at": now,
            "completed_at": now,
            "outcome": MissionOutcome.SUCCESS.value,
            "retries": 0,
            "help_required": 0,
            "last_update_at": now,
            "notes": "",
        }
        queued = {
            "id": "mission-queued-1",
            "created_at": now + 1,
            "requested_by": "student",
            "command_source": '{"type":"user","id":"student"}',
            "from_dest": None,
            "to_dest": "Ballroom",
            "schedule_type": "single",
            "state": MissionState.IDLE.value,
            "assigned_robot_id": None,
            "started_at": None,
            "completed_at": None,
            "outcome": MissionOutcome.NONE.value,
            "retries": 0,
            "help_required": 0,
            "last_update_at": now + 1,
            "notes": "",
        }

        self.storage.create_mission(completed)
        self.storage.create_mission(queued)
        self.storage.append_event("mission-complete-1", "mission_completed", {"robot_id": "robot-1"})
        self.storage.append_event("mission-queued-1", "mission_created", {"robot_id": None})

        deleted_completed = self.storage.delete_completed_missions()
        self.assertEqual(deleted_completed, 1)
        self.assertIsNone(self.storage.get_mission("mission-complete-1"))
        self.assertEqual(self.storage.list_events("mission-complete-1"), [])
        self.assertIsNotNone(self.storage.get_mission("mission-queued-1"))

        deleted_all = self.storage.delete_all_missions()
        self.assertEqual(deleted_all, 1)
        self.assertEqual(self.storage.list_missions(), [])

    def test_delete_all_missions_keeps_pending_requests(self) -> None:
        now = time.time()
        request = {
            "id": "request-1",
            "created_at": now,
            "requested_by": "student",
            "command_source": '{"type":"user","id":"student"}',
            "from_dest": None,
            "to_dest": "Hall_A",
            "schedule_type": "single",
            "state": MissionState.REQUESTED.value,
            "assigned_robot_id": None,
            "started_at": None,
            "completed_at": None,
            "outcome": MissionOutcome.NONE.value,
            "retries": 0,
            "help_required": 0,
            "last_update_at": now,
            "notes": "",
        }
        queued = {**request, "id": "started-1", "state": MissionState.IDLE.value, "created_at": now + 1}

        self.storage.create_mission(request)
        self.storage.create_mission(queued)
        deleted = self.storage.delete_all_missions()

        self.assertEqual(deleted, 1)
        self.assertIsNotNone(self.storage.get_mission("request-1"))
        self.assertIsNone(self.storage.get_mission("started-1"))

    def test_delete_requested_missions_clears_pending_requests(self) -> None:
        now = time.time()
        request = {
            "id": "request-1",
            "created_at": now,
            "requested_by": "student",
            "command_source": '{"type":"user","id":"student"}',
            "from_dest": None,
            "to_dest": "Hall_A",
            "schedule_type": "single",
            "state": MissionState.REQUESTED.value,
            "assigned_robot_id": None,
            "started_at": None,
            "completed_at": None,
            "outcome": MissionOutcome.NONE.value,
            "retries": 0,
            "help_required": 0,
            "last_update_at": now,
            "notes": "",
        }
        queued = {**request, "id": "started-1", "state": MissionState.IDLE.value, "created_at": now + 1}

        self.storage.create_mission(request)
        self.storage.create_mission(queued)
        self.storage.append_event("request-1", "request_created", {})
        deleted = self.storage.delete_requested_missions()

        self.assertEqual(deleted, 1)
        self.assertIsNone(self.storage.get_mission("request-1"))
        self.assertEqual(self.storage.list_events("request-1"), [])
        self.assertIsNotNone(self.storage.get_mission("started-1"))


class Test03ApiModels(unittest.TestCase):
    """Covers: `mission_control/api_models.py`."""

    def test_valid_request_models(self) -> None:
        # This is how app.py validates incoming JSON payloads.
        create_req = CreateMissionRequest(
            requested_by="alice",
            command_source={"type": "user", "id": "alice"},
            to_destination="Hall_A",
            schedule_type="single",
        )
        self.assertEqual(create_req.schedule_type, "single")

        command_req = MissionCommandRequest(command_source={"type": "operator", "id": "supervisor-1"})
        self.assertEqual(command_req.command_source.type, "operator")

        manual_req = RobotManualDriveRequest(
            linear=0.5,
            angular=-0.25,
            command_source={"type": "operator", "id": "dashboard-1"},
        )
        self.assertEqual(manual_req.linear, 0.5)
        self.assertEqual(manual_req.angular, -0.25)

        sys_req = RobotSystemCommandRequest(
            command="launch_robot",
            command_source={"type": "operator", "id": "dashboard-1"},
        )
        self.assertEqual(sys_req.command, "launch_robot")

        nav_req = RobotSystemCommandRequest(
            command="launch_nav",
            map_name="test_map1",
            command_source={"type": "operator", "id": "dashboard-1"},
        )
        self.assertEqual(nav_req.map_name, "test_map1")

        pose_req = RobotInitialPoseRequest(
            x=1.2,
            y=-0.4,
            yaw=0.75,
            command_source={"type": "operator", "id": "dashboard-1"},
        )
        self.assertEqual(pose_req.yaw, 0.75)

        goal_req = RobotGoalPoseRequest(
            x=2.4,
            y=3.5,
            yaw=1.2,
            command_source={"type": "operator", "id": "dashboard-1"},
        )
        self.assertEqual(goal_req.yaw, 1.2)

        save_req = RobotMapSaveRequest(
            map_name="Office",
            command_source={"type": "operator", "id": "dashboard-1"},
        )
        self.assertEqual(save_req.map_name, "Office")

        delete_req = RobotMapDeleteRequest(
            map_name="Office",
            command_source={"type": "operator", "id": "dashboard-1"},
        )
        self.assertEqual(delete_req.map_name, "Office")

        temp_req = TempDestinationRequest(
            x=1.0,
            y=2.0,
            yaw=0.0,
            command_source={"type": "operator", "id": "dashboard-1"},
        )
        self.assertEqual(temp_req.x, 1.0)

        tel = RobotTelemetryIn(blocked=True, manual_override_active=True, battery_v=23.8)
        self.assertTrue(tel.blocked)
        self.assertTrue(tel.manual_override_active)
        self.assertEqual(tel.battery_v, 23.8)

    def test_invalid_schedule_type_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            CreateMissionRequest(
                requested_by="alice",
                command_source={"type": "user", "id": "alice"},
                to_destination="Hall_A",
                schedule_type="loop_forever",
            )


class Test04RobotAdapter(unittest.IsolatedAsyncioTestCase):
    """Covers: `mission_control/robot_adapter.py`."""

    async def test_manual_override_does_not_block_start(self) -> None:
        # Manual command priority is handled by the velocity mux, not by blocking mission dispatch.
        adapter = SimRobotAdapter("robot-1", speed_scale=1.0)
        adapter.set_manual_override(True)
        await adapter.start_mission("mission-1", ["Hall_A"])
        self.assertEqual(adapter.snapshot().current_mission_id, "mission-1")

    async def test_start_cancel_and_reset(self) -> None:
        # Typical adapter lifecycle used by the scheduler.
        adapter = SimRobotAdapter("robot-1", speed_scale=1.0)
        await adapter.start_mission("mission-2", ["Hall_A"])
        self.assertEqual(adapter.snapshot().state, MissionState.EN_ROUTE)

        await adapter.cancel()

        deadline = time.time() + 2.0
        while time.time() < deadline:
            if adapter.snapshot().state == MissionState.COMPLETED:
                break
            await asyncio.sleep(0.05)

        self.assertEqual(adapter.snapshot().state, MissionState.COMPLETED)

        await adapter.reset_to_idle()
        snapshot = adapter.snapshot()
        self.assertEqual(snapshot.state, MissionState.IDLE)
        self.assertIsNone(snapshot.current_mission_id)

    async def test_sim_adapter_has_no_software_power_mode_interface(self) -> None:
        adapter = SimRobotAdapter("robot-1", speed_scale=1.0)

        self.assertFalse(hasattr(adapter, "set_power_mode"))
        self.assertEqual(adapter.snapshot().mode, RobotMode.AUTO)

    def test_battery_percentage_uses_the_6s_lipo_discharge_curve(self) -> None:
        self.assertIsNone(battery_percent_from_voltage(0.0))
        self.assertEqual(battery_percent_from_voltage(20.4), 0.0)
        self.assertAlmostEqual(battery_percent_from_voltage(22.2), 20.0)
        self.assertAlmostEqual(battery_percent_from_voltage(23.4), 60.0)
        self.assertAlmostEqual(battery_percent_from_voltage(24.0), 80.0)
        self.assertAlmostEqual(battery_percent_from_voltage(25.2), 100.0)

        adapter = SimRobotAdapter("robot-1", speed_scale=1.0)
        self.assertAlmostEqual(adapter.power_snapshot().battery_percent, 80.0)

    async def test_manual_drive_uses_priority_without_manual_mode(self) -> None:
        adapter = SimRobotAdapter("robot-1", speed_scale=1.0)

        start_pose = dict(adapter.snapshot().pose)

        await adapter.send_manual_drive_command(0.5, 0.0)
        moved_pose = adapter.snapshot().pose
        self.assertNotEqual(start_pose["x"], moved_pose["x"])
        self.assertEqual(adapter.snapshot().mode, RobotMode.MANUAL_OVERRIDE)

        await adapter.send_manual_drive_command(0.0, 0.0)
        self.assertIn("stopped", adapter.power_snapshot().recent_log.lower())

    async def test_ros2_manual_drive_rejects_motion_when_safety_is_not_ready_but_allows_stop(self) -> None:
        class FakeTwist:
            def __init__(self) -> None:
                self.linear = SimpleNamespace(x=0.0, y=0.0, z=0.0)
                self.angular = SimpleNamespace(x=0.0, y=0.0, z=0.0)

        published = []

        class RecordingPublisher:
            def publish(self, message) -> None:
                published.append(message)

        adapter = object.__new__(Ros2RobotAdapter)
        adapter._config = Ros2AdapterConfig(launcher_mode="disabled")
        adapter._lock = threading.RLock()
        adapter._ros = {"Twist": FakeTwist}
        adapter._manual_command_publisher = RecordingPublisher()
        adapter._last_heartbeat_at = 0.0
        adapter._last_pi_signal_at = 0.0
        adapter._last_joy_cmd_at = 0.0
        adapter._last_filtered_scan_at = 0.0
        adapter._last_odom_at = 0.0
        adapter._power_recent_log = None
        adapter._health_values = {
            "pi_ready": False,
            "hardware": False,
            "lidar": False,
            "odometry": False,
            "controller": False,
            "obstacle_safety": False,
            "startup_gate": False,
        }
        adapter._health_updated_at = {
            name: 0.0 for name in adapter._health_values
        }
        adapter._tf_buffer = None

        with self.assertRaisesRegex(RuntimeError, "Raspberry Pi"):
            await adapter.send_manual_drive_command(0.5, 0.0)
        self.assertEqual(published, [])

        await adapter.send_manual_drive_command(0.0, 0.0)
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0].linear.x, 0.0)
        self.assertEqual(published[0].angular.z, 0.0)

        now = time.time()
        adapter._last_pi_signal_at = now
        adapter._last_filtered_scan_at = now
        adapter._last_odom_at = now
        adapter._health_values = {
            name: True for name in adapter._health_values
        }
        adapter._health_updated_at = {
            name: now for name in adapter._health_values
        }
        adapter._tf_buffer = SimpleNamespace(
            can_transform=lambda _target, _source, _time: True,
        )

        await adapter.send_manual_drive_command(0.5, 0.0)
        self.assertEqual(len(published), 2)
        self.assertEqual(published[1].linear.x, 0.5)

    async def test_initial_pose_and_system_command_in_sim_adapter(self) -> None:
        adapter = SimRobotAdapter("robot-1", speed_scale=1.0)

        await adapter.set_initial_pose(1.0, 2.0, 0.5)
        await adapter.send_system_command("launch_robot")
        await adapter.set_goal_pose(2.5, -0.75, 0.25)

        operator = adapter.operator_snapshot()
        self.assertEqual(operator["initial_pose"]["x"], 1.0)
        self.assertEqual(operator["initial_pose"]["y"], 2.0)
        self.assertEqual(operator["goal_pose"]["x"], 2.5)
        self.assertEqual(operator["last_system_command"], "launch_robot")
        self.assertTrue(operator["system_commands_available"])
        self.assertEqual(operator["initial_pose"]["yaw"], 0.0)
        self.assertTrue(operator["localization"]["ready"])

    async def test_ros2_goal_selection_is_state_only_and_never_publishes_goal_pose(self) -> None:
        class FailIfPublished:
            def publish(self, _message) -> None:
                raise AssertionError("/goal_pose must not be used to command Nav2")

        adapter = object.__new__(Ros2RobotAdapter)
        adapter._lock = threading.RLock()
        adapter._goal_pose_publisher = FailIfPublished()
        adapter._last_goal_pose = None
        adapter._last_heartbeat_at = 0.0
        adapter._power_recent_log = None

        await adapter.set_goal_pose(2.5, -0.75, 0.25)

        self.assertEqual(
            adapter._last_goal_pose,
            {"x": 2.5, "y": -0.75, "yaw": 0.25},
        )
        self.assertIn("waiting for mission dispatch", adapter._power_recent_log.lower())

    def test_ros2_dispatch_sends_one_action_and_no_goal_pose_topic_command(self) -> None:
        class ImmediateFuture:
            def __init__(self, value) -> None:
                self._value = value

            def result(self):
                return self._value

            def add_done_callback(self, callback) -> None:
                callback(self)

        class PendingFuture:
            def __init__(self) -> None:
                self.callback = None

            def add_done_callback(self, callback) -> None:
                self.callback = callback

        class FakeGoalHandle:
            accepted = True

            def __init__(self) -> None:
                self.result_future = PendingFuture()

            def get_result_async(self) -> PendingFuture:
                return self.result_future

        class FakeActionClient:
            def __init__(self) -> None:
                self.sent_goals = []
                self.goal_handle = FakeGoalHandle()

            @staticmethod
            def wait_for_server(timeout_sec: float) -> bool:
                return True

            def send_goal_async(self, goal, feedback_callback):
                self.sent_goals.append((goal, feedback_callback))
                return ImmediateFuture(self.goal_handle)

        class FailIfPublished:
            def publish(self, _message) -> None:
                raise AssertionError("/goal_pose must not be used to command Nav2")

        action_client = FakeActionClient()
        adapter = object.__new__(Ros2RobotAdapter)
        adapter._config = Ros2AdapterConfig()
        adapter._navigate_client = action_client
        adapter._node = object()
        adapter._lock = threading.RLock()
        adapter._shutdown_requested = False
        adapter._goal_pose_publisher = FailIfPublished()
        adapter._goal_result_status = None
        adapter._goal_result_error = None
        adapter._active_goal_handle = None
        adapter._goal_active_since = 0.0
        adapter._last_motion_at = 0.0
        adapter._current_goal_pose = None
        adapter._last_goal_pose = None
        adapter._last_heartbeat_at = 0.0
        adapter._power_recent_log = None
        adapter._state = MissionState.REQUESTED
        adapter._build_goal = lambda _destination: (
            object(),
            {"x": 2.5, "y": -0.75, "yaw": 0.25},
        )

        adapter._dispatch_goal("Storage")

        self.assertEqual(len(action_client.sent_goals), 1)
        self.assertIs(adapter._active_goal_handle, action_client.goal_handle)
        self.assertEqual(adapter._goal_response_drains_pending, 0)
        self.assertEqual(adapter._state, MissionState.EN_ROUTE)
        self.assertEqual(
            adapter._current_goal_pose,
            {"x": 2.5, "y": -0.75, "yaw": 0.25},
        )

    def test_ros2_dispatch_stops_waiting_when_localization_pauses(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter._config = Ros2AdapterConfig(action_server_timeout_s=1.0)
        adapter._lock = threading.RLock()
        adapter._node = object()
        adapter._state = MissionState.REQUESTED
        adapter._pause_requested = False
        adapter._cancel_requested = False
        adapter._localization_valid = True
        adapter._localization_safety_pause_active = False
        adapter._localization_stop_in_progress = False
        adapter._last_heartbeat_at = 0.0
        adapter._power_recent_log = None

        class WaitingActionClient:
            def __init__(self) -> None:
                self.wait_calls = 0
                self.sent_goals = []

            def wait_for_server(self, timeout_sec: float) -> bool:
                self.wait_calls += 1
                if self.wait_calls == 2:
                    with adapter._lock:
                        adapter._pause_requested = True
                        adapter._state = MissionState.PAUSED
                        adapter._localization_safety_pause_active = True
                return False

            def send_goal_async(self, goal, feedback_callback):
                self.sent_goals.append((goal, feedback_callback))
                raise AssertionError("No goal may be sent after the safety pause")

        adapter._navigate_client = WaitingActionClient()
        adapter._build_goal = lambda _destination: (object(), {})

        self.assertEqual(adapter._dispatch_goal("Storage"), "paused")
        self.assertEqual(adapter._navigate_client.sent_goals, [])

    def test_ros2_late_goal_acceptance_is_canceled_after_localization_pause(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter._config = Ros2AdapterConfig(action_server_timeout_s=1.0)
        adapter._lock = threading.RLock()
        adapter._node = object()
        adapter._state = MissionState.REQUESTED
        adapter._pause_requested = False
        adapter._cancel_requested = False
        adapter._localization_valid = True
        adapter._localization_safety_pause_active = False
        adapter._localization_stop_in_progress = False
        adapter._shutdown_requested = False
        adapter._last_heartbeat_at = 0.0
        adapter._power_recent_log = None
        late_stops = []
        adapter._finish_navigation_stop = (
            lambda reason, **_kwargs: late_stops.append(reason)
        )

        class DeferredFuture:
            def __init__(self) -> None:
                self.callback = None

            def add_done_callback(self, callback) -> None:
                self.callback = callback

            def complete(self, value) -> None:
                self._value = value
                self.callback(self)

            def result(self):
                return self._value

        class LateGoalHandle:
            accepted = True

            def __init__(self) -> None:
                self.cancel_calls = 0
                self.result_future = DeferredFuture()

            def cancel_goal_async(self):
                self.cancel_calls += 1
                return object()

            def get_result_async(self):
                return self.result_future

        class ActionClient:
            @staticmethod
            def wait_for_server(timeout_sec: float) -> bool:
                return True

            def __init__(self) -> None:
                self.future = DeferredFuture()

            def send_goal_async(self, _goal, feedback_callback):
                return self.future

        action_client = ActionClient()
        goal_handle = LateGoalHandle()
        adapter._navigate_client = action_client
        adapter._build_goal = lambda _destination: (object(), {})
        dispatch_result = {}

        dispatch_thread = threading.Thread(
            target=lambda: dispatch_result.setdefault(
                "status",
                adapter._dispatch_goal("Storage"),
            ),
        )
        dispatch_thread.start()
        deadline = time.time() + 1.0
        while action_client.future.callback is None and time.time() < deadline:
            time.sleep(0.01)
        self.assertIsNotNone(action_client.future.callback)

        with adapter._lock:
            adapter._pause_requested = True
            adapter._state = MissionState.PAUSED
            adapter._localization_safety_pause_active = True
        dispatch_thread.join(timeout=1.0)
        self.assertFalse(dispatch_thread.is_alive())
        self.assertEqual(dispatch_result["status"], "paused")
        self.assertEqual(adapter._goal_response_drains_pending, 1)
        self.assertTrue(adapter._navigation_goal_drain_pending_locked())

        # Nav2 replies after the dispatch worker has already returned. The
        # installed callback must cancel that late accepted goal and retain
        # the resume guard until this exact goal reports a terminal result.
        action_client.future.complete(goal_handle)
        deadline = time.time() + 1.0
        while (
            (not goal_handle.cancel_calls or not late_stops)
            and time.time() < deadline
        ):
            time.sleep(0.01)
        self.assertEqual(goal_handle.cancel_calls, 1)
        self.assertEqual(late_stops, ["Late accepted navigation goal safety stop."])
        self.assertEqual(adapter._late_goal_stop_workers, 1)

        goal_handle.result_future.complete(
            SimpleNamespace(status=5),
        )
        deadline = time.time() + 1.0
        while getattr(adapter, "_late_goal_stop_workers", 0) and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(adapter._late_goal_stop_workers, 0)
        self.assertEqual(adapter._goal_response_drains_pending, 0)
        self.assertEqual(adapter._state, MissionState.PAUSED)
        self.assertFalse(hasattr(adapter, "_active_goal_handle"))

    async def test_ros2_resume_requires_localization_and_completed_safety_stop(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter._config = Ros2AdapterConfig(localization_timeout_s=0.0)
        adapter._lock = threading.RLock()
        adapter._state = MissionState.PAUSED
        adapter._pause_requested = True
        adapter._resume_event = threading.Event()
        adapter._localization_safety_pause_active = True
        adapter._localization_safety_pause_reason = "Footprint overlaps a wall."
        adapter._localization_valid = False
        adapter._localization_stop_in_progress = False

        with self.assertRaisesRegex(RuntimeError, "AMCL localization is not ready"):
            await adapter.resume()

        adapter._localization_valid = True
        adapter._localization_stop_in_progress = True
        adapter._latest_odom_pose = {"x": 0.0, "y": 0.0, "yaw": 0.0}
        adapter._localization_anchor_odom_pose = dict(adapter._latest_odom_pose)
        adapter._localization_anchor_map_pose = {"x": 1.0, "y": 1.0, "yaw": 0.0}
        with self.assertRaisesRegex(RuntimeError, "safety stop is still completing"):
            await adapter.resume()

        adapter._localization_stop_in_progress = False
        adapter._localization_candidate_fault = "Footprint overlaps a wall."
        adapter._localization_map_fault_samples = 1
        with self.assertRaisesRegex(RuntimeError, "not map-free"):
            await adapter.resume()

        adapter._localization_candidate_fault = None
        adapter._localization_map_fault_samples = 0
        adapter._late_goal_stop_workers = 1
        with self.assertRaisesRegex(RuntimeError, "late navigation-goal safety stop"):
            await adapter.resume()

        adapter._late_goal_stop_workers = 0
        adapter._active_goal_handle = object()
        adapter._goal_done_event = threading.Event()
        with self.assertRaisesRegex(RuntimeError, "cancellation is still being drained"):
            await adapter.resume()

        adapter._active_goal_handle = None
        adapter._goal_done_event.set()
        with self.assertRaisesRegex(RuntimeError, "cancellation is still being drained"):
            await adapter.resume()

        adapter._goal_done_event.clear()
        await adapter.resume()
        self.assertEqual(adapter._state, MissionState.EN_ROUTE)
        self.assertFalse(adapter._pause_requested)
        self.assertTrue(adapter._resume_event.is_set())
        self.assertFalse(adapter._localization_safety_pause_active)

    def test_ros2_old_cancel_callback_cannot_clear_new_goal_cancel_guard(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter._lock = threading.RLock()
        adapter._goal_done_event = threading.Event()
        adapter._cancel_future_in_flight = False
        adapter._cancel_future_goal_handle = None
        adapter._last_heartbeat_at = 0.0

        class CancelFuture:
            def __init__(self) -> None:
                self.callback = None

            def add_done_callback(self, callback) -> None:
                self.callback = callback

            def complete(self) -> None:
                self.callback(self)

        class GoalHandle:
            def __init__(self) -> None:
                self.cancel_future = CancelFuture()
                self.cancel_calls = 0

            def cancel_goal_async(self):
                self.cancel_calls += 1
                return self.cancel_future

        old_goal = GoalHandle()
        adapter._active_goal_handle = old_goal
        adapter._cancel_active_goal()
        self.assertIs(adapter._cancel_future_goal_handle, old_goal)

        adapter._handle_goal_result(
            SimpleNamespace(
                result=lambda: SimpleNamespace(status=5),
            )
        )
        self.assertIsNone(adapter._cancel_future_goal_handle)
        self.assertFalse(adapter._cancel_future_in_flight)

        adapter._goal_done_event.clear()
        new_goal = GoalHandle()
        adapter._active_goal_handle = new_goal
        adapter._cancel_active_goal()
        self.assertIs(adapter._cancel_future_goal_handle, new_goal)
        self.assertTrue(adapter._cancel_future_in_flight)

        old_goal.cancel_future.complete()
        self.assertIs(adapter._cancel_future_goal_handle, new_goal)
        self.assertTrue(adapter._cancel_future_in_flight)

        new_goal.cancel_future.complete()
        self.assertIs(adapter._cancel_future_goal_handle, new_goal)
        self.assertFalse(adapter._cancel_future_in_flight)

    def test_ros2_result_transport_failure_keeps_goal_undrained(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter._lock = threading.RLock()
        adapter._goal_done_event = threading.Event()
        adapter._goal_status_unknown = 0
        adapter._last_heartbeat_at = 0.0
        adapter._cancel_future_goal_handle = None
        adapter._cancel_future_in_flight = False
        goal_handle = object()
        adapter._active_goal_handle = goal_handle
        drain_calls = []
        adapter._cancel_late_goal_handle = (
            lambda handle, **kwargs: drain_calls.append((handle, kwargs))
        )

        class FailedResultFuture:
            @staticmethod
            def result():
                raise RuntimeError("result transport failed")

        adapter._handle_goal_result(FailedResultFuture(), goal_handle)

        self.assertIs(adapter._active_goal_handle, goal_handle)
        self.assertTrue(adapter._goal_done_event.is_set())
        self.assertEqual(adapter._goal_result_status, 0)
        self.assertIn("result transport failed", adapter._goal_result_error)
        self.assertEqual(
            drain_calls,
            [(goal_handle, {"release_active_on_terminal": True})],
        )

    async def test_ros2_start_mission_rejects_latest_map_invalid_candidate(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter._config = Ros2AdapterConfig(localization_timeout_s=0.0)
        adapter._lock = threading.RLock()
        adapter._state = MissionState.IDLE
        adapter._localization_valid = True
        adapter._localization_candidate_fault = "Footprint overlaps a keepout cell."
        adapter._localization_map_fault_samples = 1
        adapter._localization_stop_in_progress = False

        with self.assertRaisesRegex(RuntimeError, "latest AMCL footprint"):
            await adapter.start_mission("mission-1", ["Storage"])

    def test_ros2_localization_pause_wins_over_succeeded_action_result(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter._lock = threading.RLock()
        adapter._shutdown_requested = False
        adapter._pause_requested = False
        adapter._cancel_requested = False
        adapter._localization_valid = True
        adapter._localization_candidate_fault = None
        adapter._localization_map_fault_samples = 0
        adapter._localization_safety_pause_active = False
        adapter._localization_stop_in_progress = False
        adapter._state = MissionState.EN_ROUTE
        adapter._last_outcome = None
        adapter._goal_result_status = 4
        adapter._goal_status_succeeded = 4
        adapter._goal_status_aborted = 6
        adapter._goal_status_canceled = 5
        adapter._dispatch_goal = lambda _destination: None
        adapter._cancel_active_goal = lambda: None

        class PauseOnResult:
            def wait(self, timeout: float) -> bool:
                with adapter._lock:
                    adapter._pause_requested = True
                    adapter._state = MissionState.PAUSED
                    adapter._localization_safety_pause_active = True
                return True

            @staticmethod
            def clear() -> None:
                return None

        adapter._goal_done_event = PauseOnResult()

        self.assertEqual(adapter._send_goal_and_wait("Storage"), "paused")

    async def test_ros2_initial_pose_leaves_heading_to_amcl(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter.robot_id = "robot-1"
        adapter._config = Ros2AdapterConfig()
        adapter._lock = threading.RLock()
        adapter._current_mission_id = None
        adapter._state = MissionState.IDLE
        adapter._manual_command_publisher = None
        adapter._initial_pose_refinement_generation = 0

        class FakeMessage:
            def __init__(self) -> None:
                self.header = SimpleNamespace(
                    frame_id=None,
                    stamp=SimpleNamespace(sec=0, nanosec=0),
                )
                self.pose = SimpleNamespace(
                    pose=SimpleNamespace(
                        position=SimpleNamespace(x=0.0, y=0.0, z=0.0),
                        orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=0.0),
                    ),
                    covariance=[],
                )

        class FakeSetInitialPose:
            class Request:
                def __init__(self) -> None:
                    self.pose = None

        class FakeFuture:
            def result(self) -> object:
                return object()

            def add_done_callback(self, callback) -> None:
                callback(self)

        class FakeClient:
            def __init__(self) -> None:
                self.requests = []

            def wait_for_service(self, timeout_sec: float) -> bool:
                return True

            def call_async(self, request) -> FakeFuture:
                self.requests.append(request)
                return FakeFuture()

        async def run_in_place(function, *args):
            return function(*args)

        client = FakeClient()
        adapter._ros = {
            "PoseWithCovarianceStamped": FakeMessage,
            "SetInitialPose": FakeSetInitialPose,
        }
        adapter._initial_pose_publisher = None
        adapter._set_initial_pose_client = client
        adapter._node = object()
        set_free_localization_maps(adapter)

        with (
            patch.object(threading.Thread, "start"),
            patch(
                "mission_control.robot_adapter.asyncio.to_thread",
                new=run_in_place,
            ),
        ):
            await adapter.set_initial_pose(1.25, -0.75, 2.4)

        self.assertEqual(len(client.requests), 1)
        message = client.requests[0].pose
        self.assertEqual(message.header.stamp.sec, 0)
        self.assertEqual(message.header.stamp.nanosec, 0)
        self.assertEqual(message.pose.pose.orientation.z, 0.0)
        self.assertEqual(message.pose.pose.orientation.w, 1.0)
        self.assertAlmostEqual(message.pose.covariance[35], math.pi ** 2)
        self.assertEqual(adapter._last_initial_pose["yaw"], 0.0)
        self.assertTrue(adapter._initial_pose_refinement_active)
        self.assertIn("stationary lidar scans", adapter._power_recent_log.lower())

        adapter._state = MissionState.REQUESTED
        with self.assertRaisesRegex(RuntimeError, "Pause or stop navigation"):
            await adapter.set_initial_pose(1.25, -0.75, 0.0)

    def test_ros2_rviz_initial_pose_is_normalized_and_refined(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter.robot_id = "robot-1"
        adapter._config = Ros2AdapterConfig()
        adapter._lock = threading.RLock()
        adapter._initial_pose_refinement_generation = 0
        adapter._set_initial_pose_client = object()
        message = SimpleNamespace(
            header=SimpleNamespace(
                frame_id="map",
                stamp=SimpleNamespace(sec=123, nanosec=456),
            ),
            pose=SimpleNamespace(
                pose=SimpleNamespace(
                    position=SimpleNamespace(x=2.0, y=3.0),
                    orientation=SimpleNamespace(x=0.0, y=0.0, z=0.7, w=0.7),
                ),
                covariance=[0.0] * 36,
            ),
        )

        with patch.object(threading.Thread, "start"):
            adapter._handle_initial_pose(message)

        self.assertEqual(message.header.stamp.sec, 0)
        self.assertEqual(message.header.stamp.nanosec, 0)
        self.assertEqual(message.pose.pose.orientation.z, 0.0)
        self.assertEqual(message.pose.pose.orientation.w, 1.0)
        self.assertAlmostEqual(message.pose.covariance[35], math.pi ** 2)
        self.assertEqual(adapter._last_initial_pose, {"x": 2.0, "y": 3.0, "yaw": 0.0})
        self.assertTrue(adapter._initial_pose_refinement_active)
        self.assertIn("rviz initial position", adapter._power_recent_log.lower())

    def test_ros2_rviz_initial_pose_is_ignored_during_goal_dispatch(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter._config = Ros2AdapterConfig()
        adapter._lock = threading.RLock()
        adapter._state = MissionState.REQUESTED
        adapter._last_heartbeat_at = 0.0
        adapter._last_initial_pose = {"x": 1.0, "y": 1.0, "yaw": 0.0}
        message = SimpleNamespace(
            header=SimpleNamespace(
                frame_id="map",
                stamp=SimpleNamespace(sec=123, nanosec=456),
            ),
            pose=SimpleNamespace(
                pose=SimpleNamespace(
                    position=SimpleNamespace(x=8.0, y=9.0),
                    orientation=SimpleNamespace(x=0.0, y=0.0, z=0.7, w=0.7),
                ),
                covariance=[0.0] * 36,
            ),
        )

        with patch.object(threading.Thread, "start") as start_thread:
            adapter._handle_initial_pose(message)

        start_thread.assert_not_called()
        self.assertEqual(adapter._last_initial_pose["x"], 1.0)
        self.assertEqual(message.header.stamp.sec, 123)
        self.assertIn("ignored", adapter._power_recent_log.lower())

    def test_ros2_rviz_initial_pose_rechecks_dispatch_state_before_reset(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter._config = Ros2AdapterConfig()
        adapter._lock = threading.RLock()
        adapter._state = MissionState.IDLE
        adapter._last_heartbeat_at = 0.0
        adapter._localization_requested = False

        class RacingPoseEnvelope:
            def __init__(self) -> None:
                object.__setattr__(
                    self,
                    "pose",
                    SimpleNamespace(
                        position=SimpleNamespace(x=2.0, y=3.0),
                        orientation=SimpleNamespace(x=0.0, y=0.0, z=0.7, w=0.7),
                    ),
                )
                object.__setattr__(self, "covariance", [0.0] * 36)
                object.__setattr__(self, "armed", True)

            def __setattr__(self, name, value) -> None:
                if name == "covariance" and getattr(self, "armed", False):
                    with adapter._lock:
                        adapter._state = MissionState.REQUESTED
                object.__setattr__(self, name, value)

        message = SimpleNamespace(
            header=SimpleNamespace(
                frame_id="map",
                stamp=SimpleNamespace(sec=123, nanosec=456),
            ),
            pose=RacingPoseEnvelope(),
        )

        with patch.object(threading.Thread, "start") as start_thread:
            adapter._handle_initial_pose(message)

        start_thread.assert_not_called()
        self.assertFalse(adapter._localization_requested)
        self.assertEqual(adapter._state, MissionState.REQUESTED)
        self.assertIn("ignored", adapter._power_recent_log.lower())

    async def test_ros2_supervised_launcher_attaches_without_owning_ros_processes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            maps_dir = Path(tmp)
            (maps_dir / "atrium_navigation.yaml").write_text(
                "image: atrium_navigation.pgm\n",
                encoding="utf-8",
            )
            adapter = object.__new__(Ros2RobotAdapter)
            adapter._config = Ros2AdapterConfig(
                launcher_mode="supervised",
                external_map_name="atrium_navigation",
                map_directory=str(maps_dir),
            )
            adapter._lock = threading.RLock()
            adapter._saved_map_names = []
            adapter._current_map_name = None
            adapter._maps_directory = None
            adapter._launcher_processes = {}
            adapter._launcher_message = None

            adapter._initialize_launcher_state()

        self.assertEqual(adapter._current_map_name, "atrium_navigation")
        self.assertEqual(adapter._saved_map_names, ["atrium_navigation"])
        self.assertTrue(adapter._launcher_processes["nav"])
        self.assertIn("central supervisor", adapter._launcher_message)
        with self.assertRaisesRegex(RuntimeError, "central supervisor"):
            await adapter.send_system_command("launch_nav", "atrium_navigation")

    def test_ros2_map_profile_change_clears_stale_physical_layers(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter._config = Ros2AdapterConfig(map_directory="/tmp/maps")
        adapter._current_map_name = "legacy_map"
        adapter._keepout_map_required = False
        adapter._map_snapshot = {"data": [0]}
        adapter._keepout_map_snapshot = {"data": [0]}
        adapter._display_map_snapshot = {"data": [0]}
        adapter._localization_valid = True
        adapter._initial_pose_refinement_generation = 0

        adapter._apply_launcher_status_locked({
            "current_map": "atrium_navigation",
        })

        self.assertEqual(adapter._current_map_name, "atrium_navigation")
        self.assertTrue(adapter._requires_keepout_map())
        self.assertIsNone(adapter._map_snapshot)
        self.assertIsNone(adapter._keepout_map_snapshot)
        self.assertIsNone(adapter._display_map_snapshot)
        self.assertFalse(adapter._localization_valid)

        adapter._apply_launcher_status_locked({"current_map": "legacy_map"})
        self.assertFalse(adapter._requires_keepout_map())

    def test_ros2_tracks_pi_health_without_a_software_power_state(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter._lock = threading.RLock()
        adapter._health_values = {"pi_ready": False}
        adapter._health_updated_at = {"pi_ready": 0.0}
        adapter._last_pi_signal_at = 0.0
        adapter._last_pi_ready_at = 0.0
        adapter._last_heartbeat_at = 0.0

        adapter._handle_health_signal("pi_ready", SimpleNamespace(data=True))
        self.assertTrue(adapter._health_values["pi_ready"])
        self.assertGreater(adapter._last_pi_ready_at, 0.0)
        self.assertEqual(adapter._last_heartbeat_at, adapter._last_pi_signal_at)

    async def test_ros2_navigation_stop_sends_bounded_zeros_and_confirms_odometry(self) -> None:
        class FakeTwist:
            def __init__(self) -> None:
                self.linear = SimpleNamespace(x=1.0, y=1.0, z=1.0)
                self.angular = SimpleNamespace(x=1.0, y=1.0, z=1.0)

        adapter = object.__new__(Ros2RobotAdapter)
        adapter._config = Ros2AdapterConfig(
            stop_zero_count=3,
            stop_zero_interval_s=0.0,
            stop_confirmation_timeout_s=0.2,
        )
        adapter._lock = threading.RLock()
        adapter._state = MissionState.EN_ROUTE
        adapter._last_outcome = None
        adapter._cancel_requested = False
        adapter._pause_requested = False
        adapter._resume_event = threading.Event()
        adapter._power_recent_log = None
        adapter._last_odom_at = 0.0
        adapter._linear_speed = 0.2
        adapter._angular_speed = 0.1
        adapter._ros = {"Twist": FakeTwist}
        published = []

        class RecordingPublisher:
            def publish(self, message) -> None:
                published.append(message)
                with adapter._lock:
                    adapter._last_odom_at = time.time()
                    adapter._linear_speed = 0.0
                    adapter._angular_speed = 0.0

        adapter._navigation_zero_publisher = RecordingPublisher()
        adapter._cancel_active_goal = lambda: None

        async def run_in_place(function, *args):
            return function(*args)

        with patch(
            "mission_control.robot_adapter.asyncio.to_thread",
            new=run_in_place,
        ):
            await adapter.cancel()

        self.assertEqual(len(published), 3)
        self.assertTrue(all(message.linear.x == 0.0 for message in published))
        self.assertTrue(adapter._last_stop_status["confirmed"])
        self.assertEqual(adapter._last_stop_status["zero_commands_sent"], 3)

    def test_ros2_initial_pose_refinement_stays_still_and_keeps_navigation_locked_when_uncertain(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter._config = Ros2AdapterConfig(
            localization_nomotion_updates=2,
            localization_nomotion_interval_s=0.0,
            localization_confirmation_timeout_s=0.0,
        )
        adapter._lock = threading.RLock()
        adapter._initial_pose_refinement_generation = 1
        adapter._initial_pose_refinement_active = True
        adapter._shutdown_requested = False
        adapter._localization_valid = False
        adapter._last_localization_at = 0.0
        adapter._localization_xy_std_m = 0.8
        adapter._localization_yaw_std_rad = 1.2
        adapter._last_joy_cmd_at = 0.0
        adapter._last_heartbeat_at = 0.0
        adapter._power_recent_log = None
        adapter._localization_degraded = False

        class FakeEmpty:
            class Request:
                pass

        class FakeFuture:
            def result(self) -> object:
                return object()

            def add_done_callback(self, callback) -> None:
                callback(self)

        class FakeClient:
            calls = 0

            def wait_for_service(self, timeout_sec: float) -> bool:
                return True

            def call_async(self, request) -> FakeFuture:
                self.calls += 1
                return FakeFuture()

        client = FakeClient()
        adapter._ros = {"Empty": FakeEmpty}
        adapter._nomotion_update_client = client
        adapter._publish_manual_twist = lambda *_args: self.fail(
            "Stationary localization must not publish a velocity command."
        )

        adapter._run_initial_pose_refinement(1, time.time())

        self.assertEqual(client.calls, 2)
        self.assertFalse(adapter._initial_pose_refinement_active)
        self.assertFalse(adapter._localization_valid)
        self.assertIn("amcl localization failed", adapter._power_recent_log.lower())
        self.assertIn("move the robot to a clearer space", adapter._power_recent_log.lower())
        self.assertIn("navigation remains locked", adapter._power_recent_log.lower())

    async def test_global_localization_action_in_sim_adapter(self) -> None:
        adapter = SimRobotAdapter("robot-1", speed_scale=1.0)
        adapter.set_localization_valid(False)

        result = await adapter.localize()

        self.assertTrue(result["ok"])
        self.assertTrue(adapter.snapshot().localization_valid)
        self.assertIn("localization", adapter.power_snapshot().recent_log.lower())

    async def test_ros2_localize_runs_stationary_global_search(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter.robot_id = "robot-1"
        adapter._config = Ros2AdapterConfig(
            localization_nomotion_updates=2,
            localization_nomotion_interval_s=0.0,
        )
        adapter._lock = threading.RLock()
        adapter._localization_valid = False
        adapter._last_localization_at = 0.0
        adapter._localization_requested = False
        adapter._last_initial_pose = None
        adapter._last_heartbeat_at = 0.0
        adapter._last_joy_cmd_at = 0.0
        adapter._power_recent_log = None
        adapter._shutdown_requested = False

        class FakeEmpty:
            class Request:
                pass

        class FakeFuture:
            def result(self) -> object:
                return object()

            def add_done_callback(self, callback) -> None:
                callback(self)

        class FakeClient:
            calls = 0

            def wait_for_service(self, timeout_sec: float) -> bool:
                return True

            def call_async(self, request) -> FakeFuture:
                self.calls += 1
                with adapter._lock:
                    adapter._localization_valid = True
                    adapter._last_localization_at = time.time()
                return FakeFuture()

        client = FakeClient()
        adapter._ros = {"Empty": FakeEmpty}
        adapter._global_localization_client = client
        adapter._nomotion_update_client = client
        adapter._manual_command_publisher = None
        adapter._publish_manual_twist = lambda *_args: self.fail(
            "Stationary global localization must not publish a velocity command."
        )

        result = adapter._run_global_localization_search()

        self.assertGreaterEqual(client.calls, 2)
        self.assertFalse(result["robot_moved"])
        self.assertIn("global localization", result["message"].lower())

    def test_ros2_localization_does_not_expire_after_one_time_initialization(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter._config = Ros2AdapterConfig(localization_timeout_s=0.0)
        adapter._localization_valid = True
        adapter._last_localization_at = 1.0

        self.assertTrue(adapter._compute_localization_ok_locked(time.time()))

    def test_ros2_localization_accepts_covariance_roundoff_from_amcl(self) -> None:
        covariance = [0.0] * 36
        covariance[0] = -2.2e-12
        covariance[7] = -8.1e-11
        covariance[35] = -6.7e-14

        self.assertTrue(
            Ros2RobotAdapter._localization_covariance_within_limits(
                covariance,
                0.5,
                0.5,
            )
        )

        covariance[0] = -1e-3
        self.assertFalse(
            Ros2RobotAdapter._localization_covariance_within_limits(
                covariance,
                0.5,
                0.5,
            )
        )

    def test_ros2_initial_pose_requires_three_usable_amcl_poses_by_default(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter._config = Ros2AdapterConfig()
        adapter._lock = threading.RLock()
        adapter._localization_requested = True
        adapter._localization_seeded_from_initial_pose = True
        adapter._localization_valid = False
        adapter._localization_confident_samples = 0
        adapter._localization_usable_samples = 0
        adapter._localization_unconfident_samples = 0
        adapter._localization_degraded = False
        adapter._last_localization_at = 0.0
        adapter._last_heartbeat_at = 0.0
        adapter._power_recent_log = None
        adapter._pose = {"x": 0.0, "y": 0.0, "yaw": 0.0}
        set_free_localization_maps(adapter)

        covariance = [0.0] * 36
        covariance[0] = 0.81
        covariance[7] = 0.81
        covariance[35] = 0.81
        usable_pose = SimpleNamespace(
            pose=SimpleNamespace(
                pose=SimpleNamespace(
                    position=SimpleNamespace(x=3.0, y=4.0),
                    orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
                ),
                covariance=covariance,
            ),
        )

        adapter._handle_localization_pose(usable_pose)
        self.assertFalse(adapter._localization_valid)
        adapter._handle_localization_pose(usable_pose)
        self.assertFalse(adapter._localization_valid)
        adapter._handle_localization_pose(usable_pose)

        self.assertEqual(adapter._config.localization_required_samples, 3)
        self.assertTrue(adapter._localization_valid)
        self.assertTrue(adapter._localization_degraded)
        self.assertIn("keep refining while moving", adapter._power_recent_log.lower())

    def test_ros2_initial_pose_confirmation_restarts_when_pose_moves(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter._config = Ros2AdapterConfig(localization_required_samples=3)
        adapter._lock = threading.RLock()
        adapter._localization_requested = True
        adapter._localization_seeded_from_initial_pose = False
        adapter._localization_valid = False
        adapter._localization_confident_samples = 0
        adapter._localization_usable_samples = 0
        adapter._localization_unconfident_samples = 0
        adapter._localization_degraded = False
        adapter._last_localization_at = 0.0
        adapter._last_heartbeat_at = 0.0
        adapter._power_recent_log = None
        adapter._pose = {"x": 0.0, "y": 0.0, "yaw": 0.0}
        set_free_localization_maps(adapter)

        def pose_message(x: float) -> SimpleNamespace:
            covariance = [0.0] * 36
            covariance[0] = 0.04
            covariance[7] = 0.04
            covariance[35] = 0.04
            return SimpleNamespace(
                pose=SimpleNamespace(
                    pose=SimpleNamespace(
                        position=SimpleNamespace(x=x, y=2.0),
                        orientation=SimpleNamespace(
                            x=0.0,
                            y=0.0,
                            z=0.0,
                            w=1.0,
                        ),
                    ),
                    covariance=covariance,
                ),
            )

        adapter._handle_localization_pose(pose_message(2.0))
        adapter._handle_localization_pose(pose_message(2.25))
        adapter._handle_localization_pose(pose_message(2.50))
        self.assertFalse(adapter._localization_valid)
        self.assertEqual(adapter._localization_confident_samples, 1)
        self.assertIn("restarting pose confirmation", adapter._power_recent_log.lower())

        adapter._handle_localization_pose(pose_message(2.50))
        adapter._handle_localization_pose(pose_message(2.50))
        self.assertTrue(adapter._localization_valid)

    def test_ros2_localization_rejects_static_map_footprint_overlap(self) -> None:
        width = 80
        height = 80
        data = [0] * (width * height)
        map_snapshot = {
            "width": width,
            "height": height,
            "resolution": 0.1,
            "origin": {"x": 0.0, "y": 0.0, "yaw": 0.0},
            "data": data,
        }
        pose = {"x": 2.0, "y": 2.0, "yaw": 0.0}

        self.assertIsNone(
            _localization_footprint_map_fault(map_snapshot, pose)
        )
        self.assertIsNone(_localization_seed_map_fault(map_snapshot, 2.0, 2.0))

        # The base point remains in a free cell, but the long front of the
        # trolley clips an occupied cell and must invalidate localization.
        data[20 * width + 29] = 100
        fault = _localization_footprint_map_fault(map_snapshot, pose)
        self.assertIn("robot footprint", fault.lower())
        self.assertIn("wall or obstacle", fault.lower())

        # The same asymmetric footprint check must rotate with AMCL yaw.
        data[20 * width + 29] = 0
        data[29 * width + 20] = 100
        rotated_fault = _localization_footprint_map_fault(
            map_snapshot,
            {"x": 2.0, "y": 2.0, "yaw": math.pi / 2.0},
        )
        self.assertIn("robot footprint", rotated_fault.lower())

    def test_ros2_localization_rejects_unknown_off_map_and_rotated_map(self) -> None:
        width = 80
        height = 80
        data = [0] * (width * height)
        map_snapshot = {
            "width": width,
            "height": height,
            "resolution": 0.1,
            "origin": {"x": 10.0, "y": 5.0, "yaw": math.pi / 2.0},
            "data": data,
        }
        pose = {"x": 8.0, "y": 7.0, "yaw": math.pi / 2.0}
        self.assertIsNone(
            _localization_footprint_map_fault(map_snapshot, pose)
        )

        # In the rotated map frame this is the footprint's front cell.
        data[20 * width + 29] = -1
        fault = _localization_footprint_map_fault(map_snapshot, pose)
        self.assertIn("unknown", fault.lower())

        off_map_fault = _localization_footprint_map_fault(
            map_snapshot,
            {"x": 10.0, "y": 5.0, "yaw": 0.0},
        )
        self.assertIn("outside", off_map_fault.lower())

    def test_ros2_localization_ignores_wall_pose_until_map_free(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter.robot_id = "robot-1"
        adapter._config = Ros2AdapterConfig(localization_required_samples=1)
        adapter._lock = threading.RLock()
        adapter._localization_requested = True
        adapter._localization_seeded_from_initial_pose = True
        adapter._localization_valid = False
        adapter._localization_confident_samples = 0
        adapter._localization_usable_samples = 0
        adapter._localization_unconfident_samples = 0
        adapter._localization_degraded = False
        adapter._last_localization_at = 0.0
        adapter._last_heartbeat_at = 0.0
        adapter._power_recent_log = None
        adapter._pose = {"x": 1.0, "y": 1.0, "yaw": 0.0}
        adapter._localization_plausibility_fault = None
        adapter._localization_candidate_fault = None
        adapter._initial_pose_refinement_active = True
        width = 80
        navigation_data = [0] * (width * width)
        keepout_data = [0] * (width * width)
        keepout_data[20 * width + 29] = 100
        adapter._map_snapshot = {
            "width": width,
            "height": width,
            "resolution": 0.1,
            "origin": {"x": 0.0, "y": 0.0, "yaw": 0.0},
            "data": navigation_data,
        }
        # The navigation map may contain a hollow lidar outline while the
        # hard keepout mask fills the physical object interior.
        adapter._keepout_map_snapshot = {
            "width": width,
            "height": width,
            "resolution": 0.1,
            "origin": {"x": 0.0, "y": 0.0, "yaw": 0.0},
            "data": keepout_data,
        }

        covariance = [0.0] * 36
        covariance[0] = 0.04
        covariance[7] = 0.04
        covariance[35] = 0.04
        pose_message = SimpleNamespace(
            pose=SimpleNamespace(
                pose=SimpleNamespace(
                    position=SimpleNamespace(x=2.0, y=2.0),
                    orientation=SimpleNamespace(
                        x=0.0,
                        y=0.0,
                        z=0.0,
                        w=1.0,
                    ),
                ),
                covariance=covariance,
            ),
        )

        adapter._handle_localization_pose(pose_message)
        self.assertFalse(adapter._localization_valid)
        self.assertEqual(adapter._pose, {"x": 1.0, "y": 1.0, "yaw": 0.0})
        self.assertTrue(adapter._initial_pose_refinement_active)
        self.assertIn("navigation remains locked", adapter._power_recent_log.lower())

        keepout_data[20 * width + 29] = 0
        adapter._handle_localization_pose(pose_message)
        self.assertTrue(adapter._localization_valid)
        self.assertEqual(adapter._pose, {"x": 2.0, "y": 2.0, "yaw": 0.0})
        self.assertIsNone(adapter._localization_candidate_fault)

    def test_ros2_runtime_wall_jitter_is_debounced_and_recovers_without_reseed(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter.robot_id = "robot-1"
        adapter._config = Ros2AdapterConfig(
            keepout_map_topic=None,
            localization_required_samples=3,
            localization_map_fault_samples=3,
        )
        adapter._lock = threading.RLock()
        adapter._localization_requested = True
        adapter._localization_valid = True
        adapter._localization_plausibility_fault = None
        adapter._localization_candidate_fault = None
        adapter._localization_failure_message = None
        adapter._localization_map_fault_samples = 0
        adapter._localization_confident_samples = 3
        adapter._localization_usable_samples = 0
        adapter._localization_unconfident_samples = 0
        adapter._localization_seeded_from_initial_pose = False
        adapter._localization_degraded = False
        adapter._localization_confirmation_pose = None
        adapter._localization_stop_in_progress = False
        adapter._state = MissionState.EN_ROUTE
        adapter._cancel_requested = False
        adapter._pause_requested = False
        adapter._resume_event = threading.Event()
        adapter._resume_event.set()
        adapter._last_outcome = None
        adapter._pose = {"x": 1.0, "y": 1.0, "yaw": 0.0}

        width = 40
        data = [0] * (width * width)
        data[20 * width + 29] = 100
        adapter._map_snapshot = {
            "width": width,
            "height": width,
            "resolution": 0.1,
            "origin": {"x": 0.0, "y": 0.0, "yaw": 0.0},
            "data": data,
        }

        calls = []
        adapter._cancel_active_goal = lambda: calls.append("cancel")
        adapter._finish_navigation_stop = lambda reason, **_kwargs: calls.append(reason)

        class ImmediateThread:
            def __init__(self, *, target, args, **_kwargs):
                self._target = target
                self._args = args

            def start(self) -> None:
                self._target(*self._args)

        covariance = [0.0] * 36
        covariance[0] = 0.04
        covariance[7] = 0.04
        covariance[35] = 0.04
        pose_message = SimpleNamespace(
            pose=SimpleNamespace(
                pose=SimpleNamespace(
                    position=SimpleNamespace(x=2.0, y=2.0),
                    orientation=SimpleNamespace(
                        x=0.0,
                        y=0.0,
                        z=0.0,
                        w=1.0,
                    ),
                ),
                covariance=covariance,
            ),
        )

        with patch(
            "mission_control.robot_adapter.threading.Thread",
            ImmediateThread,
        ):
            adapter._handle_localization_pose(pose_message)
            self.assertTrue(adapter._localization_valid)
            self.assertEqual(
                calls,
                ["cancel", "Localization safety fault stopped navigation."],
            )
            self.assertEqual(adapter._state, MissionState.PAUSED)
            adapter._handle_localization_pose(pose_message)
            self.assertTrue(adapter._localization_valid)
            self.assertEqual(len(calls), 2)
            self.assertEqual(adapter._pose, {"x": 1.0, "y": 1.0, "yaw": 0.0})
            adapter._handle_localization_pose(pose_message)

        self.assertEqual(
            calls,
            ["cancel", "Localization safety fault stopped navigation."],
        )
        self.assertFalse(adapter._localization_valid)
        self.assertEqual(adapter._state, MissionState.PAUSED)
        self.assertIsNone(adapter._last_outcome)
        self.assertFalse(adapter._cancel_requested)
        self.assertTrue(adapter._pause_requested)
        self.assertFalse(adapter._resume_event.is_set())
        self.assertFalse(adapter._initial_pose_refinement_active)
        self.assertEqual(adapter._pose, {"x": 1.0, "y": 1.0, "yaw": 0.0})
        self.assertIsNone(adapter._localization_plausibility_fault)
        self.assertIn(
            "will not resume automatically",
            adapter._power_recent_log.lower(),
        )

        # Three stable map-free updates recover localization automatically,
        # but never restart a route without an operator decision.
        data[20 * width + 29] = 0
        adapter._handle_localization_pose(pose_message)
        adapter._handle_localization_pose(pose_message)
        self.assertFalse(adapter._localization_valid)
        adapter._handle_localization_pose(pose_message)
        self.assertTrue(adapter._localization_valid)
        self.assertEqual(adapter._state, MissionState.PAUSED)
        self.assertTrue(adapter._pause_requested)
        self.assertIsNone(adapter._localization_failure_message)
        self.assertIsNone(adapter._localization_candidate_fault)
        self.assertIn("remains paused", adapter._power_recent_log.lower())

    async def test_ros2_initial_pose_rejects_occupied_seed_cell(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter._config = Ros2AdapterConfig(keepout_map_topic=None)
        adapter._node = object()
        adapter._set_initial_pose_client = object()
        adapter._initial_pose_publisher = None
        adapter._lock = threading.RLock()
        width = 20
        data = [0] * (width * width)
        data[5 * width + 5] = 100
        adapter._map_snapshot = {
            "width": width,
            "height": width,
            "resolution": 1.0,
            "origin": {"x": 0.0, "y": 0.0, "yaw": 0.0},
            "data": data,
        }

        with self.assertRaisesRegex(RuntimeError, "static wall or obstacle"):
            await adapter.set_initial_pose(5.5, 5.5, 0.0)

    def test_ros2_localization_requires_confidence_and_detects_loss(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter._config = Ros2AdapterConfig(
            localization_max_xy_std_m=0.5,
            localization_max_yaw_std_rad=0.5,
            localization_loss_xy_std_m=0.6,
            localization_loss_yaw_std_rad=0.6,
            localization_required_samples=2,
            localization_loss_samples=2,
        )
        adapter._lock = threading.RLock()
        adapter._localization_requested = True
        adapter._localization_valid = False
        adapter._localization_confident_samples = 0
        adapter._localization_usable_samples = 0
        adapter._localization_unconfident_samples = 0
        adapter._localization_seeded_from_initial_pose = False
        adapter._localization_degraded = False
        adapter._last_localization_at = 0.0
        adapter._last_heartbeat_at = 0.0
        adapter._power_recent_log = None
        adapter._pose = {"x": 0.0, "y": 0.0, "yaw": 0.0}
        set_free_localization_maps(adapter)

        def pose_message(xy_variance: float, yaw_variance: float) -> SimpleNamespace:
            covariance = [0.0] * 36
            covariance[0] = xy_variance
            covariance[7] = xy_variance
            covariance[35] = yaw_variance
            pose = SimpleNamespace(
                position=SimpleNamespace(x=1.0, y=2.0),
                orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
            )
            return SimpleNamespace(
                pose=SimpleNamespace(pose=pose, covariance=covariance),
            )

        confident = pose_message(0.04, 0.04)
        adapter._handle_localization_pose(confident)
        self.assertFalse(adapter._localization_valid)
        adapter._handle_localization_pose(confident)
        self.assertTrue(adapter._localization_valid)

        uncertain = pose_message(1.0, 1.0)
        adapter._handle_localization_pose(uncertain)
        self.assertTrue(adapter._localization_valid)
        adapter._handle_localization_pose(uncertain)
        self.assertFalse(adapter._localization_valid)
        self.assertIn("severely uncertain", adapter._power_recent_log.lower())

        odom = SimpleNamespace(
            twist=SimpleNamespace(
                twist=SimpleNamespace(
                    linear=SimpleNamespace(x=0.0),
                    angular=SimpleNamespace(z=0.0),
                ),
            ),
            pose=SimpleNamespace(
                pose=SimpleNamespace(
                    position=SimpleNamespace(x=99.0, y=99.0),
                    orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
                ),
            ),
        )
        adapter._handle_odom(odom)
        self.assertEqual(adapter._pose["x"], 1.0)
        self.assertEqual(adapter._pose["y"], 2.0)

    def test_ros2_initial_pose_accepts_stable_usable_amcl_updates(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter._config = Ros2AdapterConfig(
            localization_max_xy_std_m=0.5,
            localization_max_yaw_std_rad=0.5,
            localization_usable_xy_std_m=0.9,
            localization_usable_yaw_std_rad=0.9,
            localization_required_samples=3,
            localization_loss_samples=2,
        )
        adapter._lock = threading.RLock()
        adapter._localization_requested = True
        adapter._localization_seeded_from_initial_pose = True
        adapter._localization_valid = False
        adapter._localization_confident_samples = 0
        adapter._localization_usable_samples = 0
        adapter._localization_unconfident_samples = 0
        adapter._localization_degraded = False
        adapter._last_localization_at = 0.0
        adapter._last_heartbeat_at = 0.0
        adapter._power_recent_log = None
        adapter._pose = {"x": 0.0, "y": 0.0, "yaw": 0.0}
        set_free_localization_maps(adapter)

        covariance = [0.0] * 36
        covariance[0] = 0.64
        covariance[7] = 0.64
        covariance[35] = 0.64
        usable = SimpleNamespace(
            pose=SimpleNamespace(
                pose=SimpleNamespace(
                    position=SimpleNamespace(x=3.0, y=-1.0),
                    orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
                ),
                covariance=covariance,
            ),
        )

        adapter._handle_localization_pose(usable)
        self.assertFalse(adapter._localization_valid)
        adapter._handle_localization_pose(usable)
        self.assertFalse(adapter._localization_valid)
        adapter._handle_localization_pose(usable)

        self.assertTrue(adapter._localization_valid)
        self.assertTrue(adapter._localization_degraded)
        self.assertEqual(adapter._pose["x"], 3.0)
        self.assertEqual(adapter._pose["y"], -1.0)
        self.assertFalse(adapter._localization_seeded_from_initial_pose)

    def test_ros2_localization_holds_pose_through_temporary_confidence_drop(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter._config = Ros2AdapterConfig(
            localization_max_xy_std_m=0.5,
            localization_max_yaw_std_rad=0.5,
            localization_loss_xy_std_m=1.5,
            localization_loss_yaw_std_rad=1.5,
            localization_required_samples=1,
            localization_loss_samples=2,
        )
        adapter._lock = threading.RLock()
        adapter._localization_requested = True
        adapter._localization_seeded_from_initial_pose = False
        adapter._localization_valid = False
        adapter._localization_confident_samples = 0
        adapter._localization_usable_samples = 0
        adapter._localization_unconfident_samples = 0
        adapter._localization_degraded = False
        adapter._last_localization_at = 0.0
        adapter._last_heartbeat_at = 0.0
        adapter._power_recent_log = None
        adapter._pose = {"x": 0.0, "y": 0.0, "yaw": 0.0}
        set_free_localization_maps(adapter)

        def pose_message(variance: float) -> SimpleNamespace:
            covariance = [0.0] * 36
            covariance[0] = variance
            covariance[7] = variance
            covariance[35] = variance
            return SimpleNamespace(
                pose=SimpleNamespace(
                    pose=SimpleNamespace(
                        position=SimpleNamespace(x=1.0, y=2.0),
                        orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
                    ),
                    covariance=covariance,
                ),
            )

        adapter._handle_localization_pose(pose_message(0.04))
        self.assertTrue(adapter._localization_valid)

        adapter._handle_localization_pose(pose_message(1.0))
        self.assertTrue(adapter._localization_valid)
        self.assertTrue(adapter._localization_degraded)
        self.assertIn("keep refining", adapter._power_recent_log.lower())

        adapter._handle_localization_pose(pose_message(4.0))
        self.assertTrue(adapter._localization_valid)
        adapter._handle_localization_pose(pose_message(4.0))
        self.assertFalse(adapter._localization_valid)

    def test_ros2_localization_guard_accepts_odom_motion_and_rejects_teleport(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter.robot_id = "robot-1"
        adapter._config = Ros2AdapterConfig(
            localization_required_samples=1,
            localization_max_seed_distance_m=2.0,
            localization_max_pose_residual_m=1.0,
            localization_max_yaw_residual_rad=0.75,
        )
        adapter._lock = threading.RLock()
        adapter._localization_requested = True
        adapter._localization_seeded_from_initial_pose = True
        adapter._localization_valid = False
        adapter._localization_confident_samples = 0
        adapter._localization_usable_samples = 0
        adapter._localization_unconfident_samples = 0
        adapter._localization_degraded = False
        adapter._localization_xy_std_m = None
        adapter._localization_yaw_std_rad = None
        adapter._last_localization_at = 0.0
        adapter._last_localization_pose_at = 0.0
        adapter._last_heartbeat_at = 0.0
        adapter._power_recent_log = None
        adapter._last_initial_pose = {"x": 15.8, "y": 28.94, "yaw": 0.0}
        adapter._pose = {"x": 15.8, "y": 28.94, "yaw": math.pi}
        adapter._latest_odom_pose = {"x": 0.0, "y": 0.0, "yaw": 0.0}
        adapter._localization_anchor_map_pose = None
        adapter._localization_anchor_odom_pose = None
        adapter._localization_plausibility_fault = None
        adapter._localization_stop_in_progress = False
        adapter._initial_pose_refinement_active = False
        adapter._state = MissionState.EN_ROUTE
        adapter._cancel_requested = False
        adapter._pause_requested = False
        adapter._resume_event = threading.Event()
        adapter._last_outcome = None
        set_free_localization_maps(adapter)

        def pose_message(x: float, y: float, yaw: float) -> SimpleNamespace:
            covariance = [0.0] * 36
            covariance[0] = 0.04
            covariance[7] = 0.04
            covariance[35] = 0.04
            return SimpleNamespace(
                pose=SimpleNamespace(
                    pose=SimpleNamespace(
                        position=SimpleNamespace(x=x, y=y),
                        orientation=SimpleNamespace(
                            x=0.0,
                            y=0.0,
                            z=math.sin(yaw / 2.0),
                            w=math.cos(yaw / 2.0),
                        ),
                    ),
                    covariance=covariance,
                ),
            )

        adapter._handle_localization_pose(pose_message(15.8, 28.94, math.pi))
        self.assertTrue(adapter._localization_valid)

        adapter._latest_odom_pose = {"x": 1.0, "y": 0.0, "yaw": 0.0}
        adapter._handle_localization_pose(pose_message(14.8, 28.94, math.pi))
        self.assertTrue(adapter._localization_valid)
        self.assertAlmostEqual(adapter._pose["x"], 14.8)

        # The impossible jump also lands in an occupied cell. Odometry
        # plausibility must run first so wall overlap cannot downgrade a true
        # teleport into the recoverable map-jitter path.
        adapter._map_snapshot["data"][44 * 100 + 66] = 100
        adapter._latest_odom_pose = {"x": 5.18, "y": 0.0, "yaw": 0.0}
        with patch.object(threading.Thread, "start") as start_thread:
            adapter._handle_localization_pose(
                pose_message(41.01, 19.48, -3.13),
            )

        self.assertFalse(adapter._localization_valid)
        self.assertAlmostEqual(adapter._pose["x"], 14.8)
        self.assertEqual(adapter._state, MissionState.COMPLETED)
        self.assertEqual(adapter._last_outcome, MissionOutcome.FAILED)
        self.assertTrue(adapter._cancel_requested)
        self.assertIn(
            "odometry predicted",
            adapter._localization_plausibility_fault.lower(),
        )
        start_thread.assert_called_once()

    def test_ros2_recovery_uses_odom_anchor_instead_of_stale_starting_seed(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter._config = Ros2AdapterConfig(
            localization_required_samples=3,
            localization_max_seed_distance_m=2.0,
            localization_max_pose_residual_m=1.0,
            localization_max_yaw_residual_rad=0.75,
        )
        adapter._lock = threading.RLock()
        adapter._localization_requested = True
        adapter._localization_valid = False
        adapter._localization_plausibility_fault = None
        adapter._localization_candidate_fault = None
        adapter._localization_failure_message = "Localization temporarily lost."
        adapter._localization_map_fault_samples = 0
        adapter._localization_confident_samples = 0
        adapter._localization_usable_samples = 0
        adapter._localization_unconfident_samples = 0
        adapter._localization_seeded_from_initial_pose = False
        adapter._localization_degraded = True
        adapter._localization_confirmation_pose = None
        adapter._initial_pose_refinement_active = False
        adapter._last_localization_at = 10.0
        adapter._last_localization_pose_at = 10.0
        adapter._last_heartbeat_at = 10.0
        adapter._last_initial_pose = {"x": 0.0, "y": 0.0, "yaw": 0.0}
        adapter._pose = {"x": 5.0, "y": 2.0, "yaw": 0.0}
        adapter._localization_anchor_map_pose = {
            "x": 5.0,
            "y": 2.0,
            "yaw": 0.0,
        }
        adapter._localization_anchor_odom_pose = {
            "x": 4.0,
            "y": 0.0,
            "yaw": 0.0,
        }
        adapter._latest_odom_pose = {"x": 4.2, "y": 0.0, "yaw": 0.0}
        adapter._state = MissionState.PAUSED
        set_free_localization_maps(adapter)

        covariance = [0.0] * 36
        covariance[0] = 0.04
        covariance[7] = 0.04
        covariance[35] = 0.04
        recovered_pose = SimpleNamespace(
            pose=SimpleNamespace(
                pose=SimpleNamespace(
                    position=SimpleNamespace(x=5.2, y=2.0),
                    orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
                ),
                covariance=covariance,
            ),
        )

        # The correct current pose is over five metres from the original seed
        # but matches motion from the last trusted map/odom anchor.
        for _ in range(3):
            adapter._handle_localization_pose(recovered_pose)

        self.assertTrue(adapter._localization_valid)
        self.assertAlmostEqual(adapter._pose["x"], 5.2)
        self.assertIsNone(adapter._localization_plausibility_fault)
        self.assertIsNone(adapter._localization_failure_message)

    def test_ros2_localization_fault_worker_cancels_and_stops_navigation(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter._lock = threading.RLock()
        adapter._localization_stop_in_progress = True
        adapter._localization_stop_generation = 7
        adapter._localization_plausibility_fault = "AMCL teleport rejected."
        adapter._localization_safety_pause_active = False
        adapter._power_recent_log = None
        calls = []
        adapter._cancel_active_goal = lambda: calls.append("cancel")
        adapter._finish_navigation_stop = lambda reason, **_kwargs: calls.append(reason)

        adapter._stop_for_localization_fault(7)

        self.assertEqual(
            calls,
            ["cancel", "Localization safety fault stopped navigation."],
        )
        self.assertFalse(adapter._localization_stop_in_progress)
        self.assertIn("remains locked", adapter._power_recent_log.lower())

    def test_ros2_stale_localization_stop_worker_cannot_replace_new_pose_status(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter._lock = threading.RLock()
        adapter._localization_stop_in_progress = True
        adapter._localization_stop_generation = 8
        adapter._localization_plausibility_fault = None
        adapter._localization_safety_pause_active = False
        adapter._power_recent_log = "> AMCL accepted the new initial position."
        adapter._cancel_active_goal = lambda: None
        adapter._finish_navigation_stop = lambda _reason, **_kwargs: None

        # Worker 7 was invalidated when a newer initial-pose generation began.
        adapter._stop_for_localization_fault(7)

        self.assertFalse(adapter._localization_stop_in_progress)
        self.assertEqual(
            adapter._power_recent_log,
            "> AMCL accepted the new initial position.",
        )

    def test_ros2_late_canceled_goal_cannot_mask_localization_failure(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter._lock = threading.RLock()
        adapter._state = MissionState.COMPLETED
        adapter._last_outcome = MissionOutcome.FAILED
        adapter._power_recent_log = "!!! AMCL teleport rejected."

        adapter._mark_completed_locked(MissionOutcome.CANCELED)

        self.assertEqual(adapter._state, MissionState.COMPLETED)
        self.assertEqual(adapter._last_outcome, MissionOutcome.FAILED)
        self.assertIn("teleport rejected", adapter._power_recent_log.lower())

    def test_ros2_stale_mission_worker_cannot_resurrect_reset_adapter(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter._lock = threading.RLock()
        adapter._shutdown_requested = False
        adapter._current_mission_id = None
        adapter._current_leg_index = 0
        adapter._current_destination = None
        adapter._state = MissionState.IDLE
        adapter._last_outcome = None
        adapter._cancel_requested = False
        adapter._resume_event = threading.Event()
        adapter._resume_event.set()
        adapter._send_goal_and_wait = lambda _destination: "succeeded"

        adapter._run_plan("stale-mission", ["Storage"])

        self.assertEqual(adapter._state, MissionState.IDLE)
        self.assertIsNone(adapter._last_outcome)
        self.assertIsNone(adapter._current_destination)

    def test_ros2_succeeded_result_cannot_overwrite_localization_failure(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter._lock = threading.RLock()
        adapter._shutdown_requested = False
        adapter._current_mission_id = "mission-1"
        adapter._current_leg_index = 0
        adapter._current_destination = None
        adapter._state = MissionState.EN_ROUTE
        adapter._last_outcome = None
        adapter._cancel_requested = False
        adapter._pause_requested = False
        adapter._resume_event = threading.Event()
        adapter._resume_event.set()

        def succeed_after_fault(_destination):
            with adapter._lock:
                adapter._state = MissionState.COMPLETED
                adapter._last_outcome = MissionOutcome.FAILED
                adapter._cancel_requested = True
                adapter._power_recent_log = "!!! AMCL teleport rejected."
            return "succeeded"

        adapter._send_goal_and_wait = succeed_after_fault
        adapter._run_plan("mission-1", ["Storage"])

        self.assertEqual(adapter._state, MissionState.COMPLETED)
        self.assertEqual(adapter._last_outcome, MissionOutcome.FAILED)
        self.assertIn("teleport rejected", adapter._power_recent_log.lower())

    async def test_map_catalog_save_delete_and_launch_nav_in_sim_adapter(self) -> None:
        adapter = SimRobotAdapter("robot-1", speed_scale=1.0)

        await adapter.save_map("Office")
        self.assertIn("Office", adapter.operator_snapshot()["saved_maps"])

        await adapter.send_system_command("launch_nav", map_name="Office")
        self.assertEqual(adapter.operator_snapshot()["current_map_name"], "Office")

        with self.assertRaises(RuntimeError):
            await adapter.delete_map("Office")

        await adapter.send_system_command("kill_all")
        await adapter.delete_map("Office")
        self.assertNotIn("Office", adapter.operator_snapshot()["saved_maps"])

    def test_operator_snapshot_can_omit_the_large_map_payload(self) -> None:
        adapter = SimRobotAdapter("robot-1", speed_scale=1.0)

        lightweight = adapter.operator_snapshot(include_map=False)
        complete = adapter.operator_snapshot()

        self.assertTrue(lightweight["map_available"])
        self.assertIsNone(lightweight["map"])
        self.assertEqual(lightweight["map_updated_at"], complete["map"]["updated_at"])
        self.assertIsNotNone(complete["map"])
        self.assertGreater(len(complete["map"]["data"]), 0)

    def test_ros2_operator_readiness_enforces_startup_localization_navigation_order(self) -> None:
        class ReadyClient:
            @staticmethod
            def service_is_ready() -> bool:
                return True

        class ReadyAction:
            @staticmethod
            def server_is_ready() -> bool:
                return True

        adapter = object.__new__(Ros2RobotAdapter)
        adapter._config = Ros2AdapterConfig(launcher_mode="disabled")
        adapter._lock = threading.RLock()
        adapter._navigate_client = ReadyAction()
        adapter._nav2_lifecycle_states = {
            name: None for name in adapter._config.nav2_lifecycle_nodes
        }
        adapter._set_initial_pose_client = ReadyClient()
        adapter._initial_pose_publisher = None
        adapter._manual_command_publisher = object()
        adapter._system_command_publisher = None
        adapter._display_map_snapshot = None
        adapter._map_snapshot = {
            "width": 1,
            "height": 1,
            "resolution": 0.05,
            "origin": {"x": 0.0, "y": 0.0, "yaw": 0.0},
            "data": [0],
            "updated_at": time.time(),
        }
        adapter._keepout_map_snapshot = adapter._map_snapshot
        adapter._current_map_name = "atrium_navigation"
        adapter._current_goal_pose = None
        adapter._last_goal_pose = None
        adapter._last_initial_pose = None
        adapter._last_system_command = None
        adapter._saved_map_names = ["atrium_navigation"]
        adapter._maps_directory = "/tmp/maps"
        adapter._launcher_message = None
        adapter._launcher_processes = {}
        adapter._last_heartbeat_at = 0.0
        adapter._last_pi_signal_at = 0.0
        adapter._last_pi_ready_at = 0.0
        adapter._last_odom_at = 0.0
        adapter._last_filtered_scan_at = 0.0
        adapter._health_values = {
            "pi_ready": False,
            "hardware": False,
            "lidar": False,
            "odometry": False,
            "controller": False,
            "obstacle_safety": False,
            "startup_gate": False,
        }
        adapter._health_updated_at = {
            name: 0.0 for name in adapter._health_values
        }
        adapter._tf_buffer = SimpleNamespace(
            can_transform=lambda _target, _source, _time: True,
        )
        adapter._ros = {}
        adapter._localization_valid = False
        adapter._last_localization_at = 0.0
        adapter._last_localization_pose_at = 0.0
        adapter._initial_pose_refinement_active = False
        adapter._localization_requested = False
        adapter._localization_degraded = False
        adapter._localization_confident_samples = 0
        adapter._localization_usable_samples = 0
        adapter._localization_unconfident_samples = 0
        adapter._localization_xy_std_m = None
        adapter._localization_yaw_std_rad = None

        waiting = adapter.operator_snapshot(include_map=False)
        self.assertFalse(waiting["startup"]["ready"])
        self.assertFalse(waiting["initial_pose_available"])
        self.assertFalse(waiting["navigation_available"])
        self.assertFalse(waiting["manual_drive_available"])
        self.assertFalse(waiting["localization"]["accepted_map_pose_available"])

        now = time.time()
        adapter._last_heartbeat_at = now
        adapter._last_pi_signal_at = now
        adapter._last_pi_ready_at = now
        adapter._last_odom_at = now
        adapter._last_filtered_scan_at = now
        adapter._health_values = {
            name: True for name in adapter._health_values
        }
        adapter._health_updated_at = {
            name: now for name in adapter._health_values
        }

        map_snapshot = adapter._map_snapshot
        adapter._map_snapshot = None
        adapter._current_map_name = None
        adapter._set_initial_pose_client = None
        manual_only = adapter.operator_snapshot(include_map=False)
        self.assertTrue(manual_only["manual_drive_available"])
        self.assertTrue(manual_only["manual_drive"]["ready"])
        self.assertFalse(manual_only["startup"]["ready"])
        self.assertFalse(manual_only["navigation_available"])

        adapter._map_snapshot = map_snapshot
        adapter._current_map_name = "atrium_navigation"
        adapter._set_initial_pose_client = ReadyClient()
        startup_ready = adapter.operator_snapshot(include_map=False)
        self.assertTrue(startup_ready["startup"]["ready"])
        self.assertTrue(startup_ready["initial_pose_available"])
        self.assertFalse(startup_ready["navigation_available"])

        adapter._localization_valid = True
        adapter._last_localization_at = now
        adapter._latest_odom_pose = {"x": 0.0, "y": 0.0, "yaw": 0.0}
        adapter._localization_anchor_odom_pose = dict(adapter._latest_odom_pose)
        adapter._localization_anchor_map_pose = {"x": 1.0, "y": 1.0, "yaw": 0.0}
        nav2_activating = adapter.operator_snapshot(include_map=False)
        self.assertFalse(nav2_activating["navigation_available"])
        self.assertFalse(nav2_activating["nav2_lifecycle"]["ready"])
        self.assertIn("timers", nav2_activating["navigation"]["message"])

        adapter._nav2_lifecycle_states = {
            name: "active" for name in adapter._config.nav2_lifecycle_nodes
        }
        navigation_ready = adapter.operator_snapshot(include_map=False)
        self.assertTrue(navigation_ready["navigation_action_available"])
        self.assertTrue(navigation_ready["nav2_lifecycle"]["ready"])
        self.assertTrue(navigation_ready["navigation_available"])
        self.assertTrue(navigation_ready["goal_pose_available"])
        self.assertTrue(
            navigation_ready["localization"]["accepted_map_pose_available"]
        )

        adapter._state = MissionState.EN_ROUTE
        adapter._current_mission_id = "mission-active"
        adapter._active_goal_handle = object()
        active_navigation = adapter.operator_snapshot(include_map=False)
        self.assertTrue(
            active_navigation["navigation"]["checks"]["previous_goal_drained"]
        )
        self.assertTrue(active_navigation["navigation_available"])

        adapter._state = MissionState.PAUSED
        paused_navigation = adapter.operator_snapshot(include_map=False)
        self.assertFalse(
            paused_navigation["navigation"]["checks"]["previous_goal_drained"]
        )
        self.assertFalse(paused_navigation["navigation_available"])
        self.assertIn(
            "terminal result",
            paused_navigation["navigation"]["message"],
        )

        adapter._state = MissionState.IDLE
        adapter._current_mission_id = None
        adapter._active_goal_handle = None

        adapter._localization_stop_in_progress = True
        stopping = adapter.operator_snapshot(include_map=False)
        self.assertFalse(stopping["navigation_available"])
        self.assertIn("safety stop", stopping["navigation"]["message"].lower())

        adapter._localization_stop_in_progress = False
        adapter._localization_candidate_fault = "Footprint overlaps a wall."
        adapter._localization_map_fault_samples = 1
        map_invalid = adapter.operator_snapshot(include_map=False)
        self.assertFalse(map_invalid["navigation_available"])
        self.assertIn("map-free", map_invalid["navigation"]["message"].lower())

    def test_map_preview_loader_reads_catering_bot_maps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pgm = root / "tiny_map.pgm"
            pgm.write_bytes(b"P5\n3 2\n255\n\xfe\xcd\x00\x00\xfe\xcd")
            yaml_path = root / "tiny_map.yaml"
            yaml_path.write_text(
                (
                    "image: tiny_map.pgm\n"
                    "mode: trinary\n"
                    "resolution: 0.05\n"
                    "origin: [-2.0, -3.0, 0.5]\n"
                    "negate: 0\n"
                    "occupied_thresh: 0.65\n"
                    "free_thresh: 0.25\n"
                ),
                encoding="utf-8",
            )

            preview = _load_map_preview_from_yaml(yaml_path)

        self.assertEqual(preview["name"], "tiny_map")
        self.assertEqual(preview["width"], 3)
        self.assertEqual(preview["height"], 2)
        self.assertEqual(preview["origin"]["x"], -2.0)
        self.assertEqual(len(preview["data"]), 6)
        self.assertEqual(preview["data"], [100, 0, 0, 0, 0, 100])
        self.assertIn(100, preview["data"])

    def test_map_preview_loader_preserves_scaled_display_grays(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pgm = root / "display_map.pgm"
            pgm.write_bytes(b"P5\n3 1\n255\n\xff\xd9\x00")
            yaml_path = root / "display_map.yaml"
            yaml_path.write_text(
                (
                    "image: display_map.pgm\n"
                    "mode: scale\n"
                    "resolution: 0.05\n"
                    "origin: [0.0, 0.0, 0.0]\n"
                    "negate: 0\n"
                    "occupied_thresh: 1.0\n"
                    "free_thresh: 0.0\n"
                ),
                encoding="utf-8",
            )

            preview = _load_map_preview_from_yaml(yaml_path)

        self.assertEqual(preview["data"], [0, 15, 100])

    def test_ui_only_profile_uses_display_map_without_launcher_processes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "atrium_navigation.pgm").write_bytes(b"P5\n2 2\n255\n\xff\xff\xff\xff")
            (root / "atrium_keepout.pgm").write_bytes(b"P5\n2 2\n255\n\xff\x00\xff\xff")
            (root / "atrium_display.pgm").write_bytes(b"P5\n2 2\n255\n\xff\xcd\x00\xff")
            map_yaml = (
                "mode: trinary\n"
                "resolution: 0.05\n"
                "origin: [0.0, 0.0, 0.0]\n"
                "negate: 0\n"
                "occupied_thresh: 0.65\n"
                "free_thresh: 0.25\n"
            )
            for layer in ("navigation", "keepout", "display"):
                (root / f"atrium_{layer}.yaml").write_text(
                    f"image: atrium_{layer}.pgm\n{map_yaml}",
                    encoding="utf-8",
                )

            adapter = SimRobotAdapter(
                "robot-1",
                ui_map_path=str(root / "atrium_navigation.yaml"),
            )
            operator = adapter.operator_snapshot()

        self.assertEqual(operator["current_map_name"], "atrium_navigation")
        self.assertEqual(operator["map"]["name"], "atrium_display")
        self.assertEqual(operator["saved_maps"], ["atrium_navigation"])
        self.assertFalse(any(operator["launcher_processes"].values()))
        self.assertIn("no ROS or Nav2", operator["launcher_message"])


class Test05Scheduler(unittest.IsolatedAsyncioTestCase):
    """Covers: `mission_control/scheduler.py` by integrating with config/storage/adapter."""

    async def asyncSetUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "mission_control.sqlite3"
        self.config_path = Path(self.tmpdir.name) / "destinations.yaml"
        write_demo_destinations(self.config_path)

        self.storage = Storage(self.db_path)
        self.storage.init()

        self.dest_config = DestinationConfig(self.config_path)
        self.dest_config.load()

        self.mc = MissionControl(storage=self.storage, dest_config=self.dest_config)
        self.adapter = SimRobotAdapter("robot-1", speed_scale=100.0)
        self.mc.register_robot(self.adapter)

    async def asyncTearDown(self) -> None:
        await self.mc.stop()
        self.tmpdir.cleanup()

    async def test_create_dispatch_complete_flow(self) -> None:
        # End-to-end path:
        # create mission -> scheduler dispatches -> adapter completes -> scheduler records success.
        mission_id = self.mc.create_mission(
            MissionCreate(
                requested_by="alice",
                command_source=CommandSource("user", "alice"),
                to_destination="Hall_A",
                schedule_type="single",
            )
        )

        await self.mc._tick_once()  # dispatch pending mission
        mission = self.storage.get_mission(mission_id)
        self.assertEqual(mission["state"], MissionState.EN_ROUTE.value)
        self.assertEqual(mission["assigned_robot_id"], "robot-1")

        deadline = time.time() + 3.0
        while time.time() < deadline:
            await asyncio.sleep(0.05)
            await self.mc._tick_once()
            mission = self.storage.get_mission(mission_id)
            if mission["state"] == MissionState.COMPLETED.value:
                break

        self.assertEqual(mission["state"], MissionState.COMPLETED.value)
        self.assertEqual(mission["outcome"], MissionOutcome.SUCCESS.value)

    async def test_request_waits_until_started(self) -> None:
        request_id = self.mc.create_request(
            MissionCreate(
                requested_by="alice",
                command_source=CommandSource("user", "alice"),
                to_destination="Hall_A",
                schedule_type="single",
            )
        )

        await self.mc._tick_once()
        request = self.storage.get_mission(request_id)
        self.assertEqual(request["state"], MissionState.REQUESTED.value)
        self.assertIsNone(self.adapter.snapshot().current_mission_id)

        self.mc.start_request(
            request_id,
            CommandSource("operator", "dashboard-1"),
            assigned_robot_id="robot-1",
        )
        await self.mc._tick_once()

        mission = self.storage.get_mission(request_id)
        self.assertEqual(mission["state"], MissionState.EN_ROUTE.value)
        self.assertEqual(mission["assigned_robot_id"], "robot-1")

    async def test_round_trip_waits_for_return_confirmation(self) -> None:
        mission_id = self.mc.create_mission(
            MissionCreate(
                requested_by="alice",
                command_source=CommandSource("user", "alice"),
                to_destination="Hall_A",
                schedule_type="round_trip",
                from_destination="Storage",
                assigned_robot_id="robot-1",
            )
        )

        await self.mc._tick_once()
        mission = self.storage.get_mission(mission_id)
        self.assertEqual(mission["state"], MissionState.EN_ROUTE.value)

        deadline = time.time() + 3.0
        while time.time() < deadline:
            await asyncio.sleep(0.05)
            await self.mc._tick_once()
            mission = self.storage.get_mission(mission_id)
            if mission["state"] == MissionState.WAITING_FOR_RETURN.value:
                break

        self.assertEqual(mission["state"], MissionState.WAITING_FOR_RETURN.value)
        self.assertEqual(mission["outcome"], MissionOutcome.NONE.value)
        self.assertEqual(self.adapter.snapshot().state, MissionState.IDLE)
        self.assertIsNone(self.adapter.snapshot().current_mission_id)

        result = await self.mc.start_return_trip(mission_id, CommandSource("operator", "dashboard-1"))
        self.assertEqual(result["return_destination"], "Storage")

        mission = self.storage.get_mission(mission_id)
        self.assertEqual(mission["state"], MissionState.RETURNING.value)

        deadline = time.time() + 3.0
        while time.time() < deadline:
            await asyncio.sleep(0.05)
            await self.mc._tick_once()
            mission = self.storage.get_mission(mission_id)
            if mission["state"] == MissionState.COMPLETED.value:
                break

        self.assertEqual(mission["state"], MissionState.COMPLETED.value)
        self.assertEqual(mission["outcome"], MissionOutcome.SUCCESS.value)

    async def test_ingest_telemetry_updates_storage_and_adapter(self) -> None:
        # This is how external robot/bridge inputs can update mission control state.
        self.mc.ingest_robot_telemetry(
            "robot-1",
            {
                "blocked": True,
                "manual_override_active": True,
                "battery_v": 23.5,
                "x": 1.2,
                "y": 3.4,
                "yaw": 0.5,
            },
        )

        robot = self.storage.get_robot("robot-1")
        self.assertEqual(robot["blocked"], 1)
        self.assertEqual(robot["mode"], RobotMode.MANUAL_OVERRIDE.value)
        self.assertEqual(robot["battery_v"], 23.5)
        self.assertEqual(robot["x"], 1.2)
        self.assertTrue(self.adapter.snapshot().blocked)
        self.assertEqual(self.adapter.snapshot().mode, RobotMode.MANUAL_OVERRIDE)

    async def test_completed_adapter_outcome_is_preserved(self) -> None:
        mission_id = self.mc.create_mission(
            MissionCreate(
                requested_by="alice",
                command_source=CommandSource("user", "alice"),
                to_destination="Hall_A",
                schedule_type="single",
                assigned_robot_id="robot-1",
            )
        )
        self.storage.update_mission(
            mission_id,
            assigned_robot_id="robot-1",
            state=MissionState.EN_ROUTE.value,
            started_at=time.time(),
        )

        self.adapter._current_mission_id = mission_id
        self.adapter._state = MissionState.COMPLETED
        self.adapter._last_outcome = MissionOutcome.FAILED

        await self.mc._handle_completions({"robot-1": self.adapter.snapshot()})

        mission = self.storage.get_mission(mission_id)
        self.assertEqual(mission["state"], MissionState.COMPLETED.value)
        self.assertEqual(mission["outcome"], MissionOutcome.FAILED.value)

    async def test_blocked_detection_does_not_interrupt_navigation(self) -> None:
        # A blocked report is telemetry only. Nav2 and the Pi safety layer keep
        # control until the operator explicitly stops the mission.
        mission_id = self.mc.create_mission(
            MissionCreate(
                requested_by="alice",
                command_source=CommandSource("user", "alice"),
                to_destination="Ballroom",
                schedule_type="single",
                assigned_robot_id="robot-1",
            )
        )
        self.storage.update_mission(
            mission_id,
            assigned_robot_id="robot-1",
            state=MissionState.EN_ROUTE.value,
            started_at=time.time(),
        )

        self.adapter._current_mission_id = mission_id
        self.adapter._state = MissionState.EN_ROUTE
        self.adapter.set_blocked(True)
        self.mc._blocked_since["robot-1"] = time.time() - 10.0
        snapshot = self.adapter.snapshot()

        with (
            patch.object(self.adapter, "pause", wraps=self.adapter.pause) as pause,
            patch.object(self.adapter, "resume", wraps=self.adapter.resume) as resume,
        ):
            await self.mc._blocked_detection({"robot-1": snapshot})
            await self.mc._blocked_detection({"robot-1": snapshot})
            pause.assert_not_awaited()
            resume.assert_not_awaited()

        mission = self.storage.get_mission(mission_id)
        self.assertEqual(mission["retries"], 0)
        self.assertEqual(mission["help_required"], 0)
        self.assertEqual(mission["state"], MissionState.EN_ROUTE.value)

        events = self.storage.list_events(mission_id)
        blocked_events = [
            event for event in events if event["event"] == "blocked_detected"
        ]
        self.assertEqual(len(blocked_events), 1)
        details = blocked_events[0]["details"]
        self.assertEqual(details["navigation_action"], "unchanged")

    async def test_blocked_detection_ignores_manual_override(self) -> None:
        mission_id = self.mc.create_mission(
            MissionCreate(
                requested_by="alice",
                command_source=CommandSource("user", "alice"),
                to_destination="Ballroom",
                schedule_type="single",
                assigned_robot_id="robot-1",
            )
        )
        self.storage.update_mission(
            mission_id,
            assigned_robot_id="robot-1",
            state=MissionState.EN_ROUTE.value,
            started_at=time.time(),
        )
        self.mc._blocked_since["robot-1"] = time.time() - 10.0

        snapshot = self.adapter.snapshot()
        snapshot.current_mission_id = mission_id
        snapshot.state = MissionState.EN_ROUTE
        snapshot.mode = RobotMode.MANUAL_OVERRIDE
        snapshot.blocked = True
        snapshot.obstacle_stop = True

        await self.mc._blocked_detection({"robot-1": snapshot})

        self.assertNotIn("robot-1", self.mc._blocked_since)
        events = self.storage.list_events(mission_id)
        self.assertFalse(any(event["event"] == "blocked_detected" for event in events))

    async def test_localization_watchdog_pauses_active_mission(self) -> None:
        mission_id = self.mc.create_mission(
            MissionCreate(
                requested_by="alice",
                command_source=CommandSource("user", "alice"),
                to_destination="Ballroom",
                schedule_type="single",
                assigned_robot_id="robot-1",
            )
        )
        self.storage.update_mission(
            mission_id,
            assigned_robot_id="robot-1",
            state=MissionState.EN_ROUTE.value,
            started_at=time.time(),
        )
        self.adapter._current_mission_id = mission_id
        self.adapter._state = MissionState.EN_ROUTE
        self.adapter.set_localization_valid(False)

        await self.mc._localization_watchdog({"robot-1": self.adapter.snapshot()})

        mission = self.storage.get_mission(mission_id)
        self.assertEqual(mission["state"], MissionState.PAUSED.value)
        events = self.storage.list_events(mission_id)
        self.assertTrue(any(event["event"] == "auto_paused_localization_lost" for event in events))
        self.assertFalse(self.adapter._paused.is_set())
        with self.assertRaisesRegex(RuntimeError, "AMCL is not ready"):
            await self.mc.resume_mission(
                mission_id,
                CommandSource("operator", "dashboard-1"),
            )

    async def test_localization_watchdog_records_recovered_safety_pause_once(self) -> None:
        mission_id = self.mc.create_mission(
            MissionCreate(
                requested_by="alice",
                command_source=CommandSource("user", "alice"),
                to_destination="Ballroom",
                schedule_type="single",
                assigned_robot_id="robot-1",
            )
        )
        self.storage.update_mission(
            mission_id,
            assigned_robot_id="robot-1",
            state=MissionState.EN_ROUTE.value,
            started_at=time.time(),
        )
        self.adapter._current_mission_id = mission_id
        self.adapter._state = MissionState.PAUSED
        # AMCL recovered before the scheduler's next poll, but the adapter's
        # safety pause must still be persisted before Resume can be offered.
        self.adapter.set_localization_valid(True)
        localization = {
            "phase": "safety_paused",
            "quality": "good",
            "message": "Localization recovered; route remains paused.",
            "candidate_fault": "Robot footprint overlaps a keepout cell.",
            "map_fault_samples": 3,
            "safety_pause_active": True,
        }

        with (
            patch.object(self.adapter, "pause", wraps=self.adapter.pause) as pause,
            patch.object(
                self.adapter,
                "operator_snapshot",
                return_value={"localization": localization},
            ),
        ):
            snapshot = self.adapter.snapshot()
            await self.mc._localization_watchdog({"robot-1": snapshot})
            await self.mc._localization_watchdog({"robot-1": snapshot})

        pause.assert_not_awaited()
        mission = self.storage.get_mission(mission_id)
        self.assertEqual(mission["state"], MissionState.PAUSED.value)
        matching_events = [
            event
            for event in self.storage.list_events(mission_id)
            if event["event"] == "auto_paused_localization_lost"
        ]
        self.assertEqual(len(matching_events), 1)
        self.assertEqual(
            matching_events[0]["details"]["localization"]["message"],
            localization["message"],
        )
        self.assertEqual(
            matching_events[0]["details"]["localization"]["map_fault_samples"],
            3,
        )

    async def test_build_plan_for_single_and_round_trip(self) -> None:
        # _build_plan dispatches only the active leg. Round trips wait for a Return command.
        single = self.mc._build_plan({"to_dest": "Hall_A", "schedule_type": "single"})
        self.assertEqual(single, ["Hall_A"])

        round_trip = {"to_dest": "Hall_A", "from_dest": "Ballroom", "schedule_type": "round_trip"}
        self.assertEqual(self.mc._build_plan(round_trip), ["Hall_A"])
        self.assertEqual(self.mc._return_destination_for(round_trip), "Ballroom")

        round_trip_home = {"to_dest": "Hall_A", "from_dest": None, "schedule_type": "round_trip"}
        self.assertEqual(self.mc._build_plan(round_trip_home), ["Hall_A"])
        self.assertEqual(self.mc._return_destination_for(round_trip_home), "Storage")

    async def test_clear_all_missions_requires_inactive_robot(self) -> None:
        mission_id = self.mc.create_mission(
            MissionCreate(
                requested_by="alice",
                command_source=CommandSource("user", "alice"),
                to_destination="Hall_A",
                schedule_type="single",
                assigned_robot_id="robot-1",
            )
        )
        self.storage.update_mission(
            mission_id,
            assigned_robot_id="robot-1",
            state=MissionState.EN_ROUTE.value,
            started_at=time.time(),
        )

        self.adapter._current_mission_id = mission_id
        self.adapter._state = MissionState.EN_ROUTE

        with self.assertRaises(RuntimeError):
            await self.mc.clear_all_missions()

    async def test_cancel_navigation_does_not_create_a_software_power_lock(self) -> None:
        mission_id = self.mc.create_mission(
            MissionCreate(
                requested_by="alice",
                command_source=CommandSource("user", "alice"),
                to_destination="Hall_A",
                schedule_type="single",
                assigned_robot_id="robot-1",
            )
        )
        self.storage.update_mission(
            mission_id,
            assigned_robot_id="robot-1",
            state=MissionState.EN_ROUTE.value,
            started_at=time.time(),
        )
        self.adapter._current_mission_id = mission_id
        self.adapter._state = MissionState.EN_ROUTE
        power_before = self.adapter.power_snapshot()

        await self.mc.cancel_mission(mission_id, CommandSource("operator", "dashboard-1"))

        mission = self.storage.get_mission(mission_id)
        power_after = self.adapter.power_snapshot()
        self.assertEqual(mission["state"], MissionState.COMPLETED.value)
        self.assertEqual(mission["outcome"], MissionOutcome.CANCELED.value)
        self.assertEqual(power_after.mode, power_before.mode)

    async def test_manual_drive_command_uses_priority_without_manual_mode(self) -> None:
        result = await self.mc.send_robot_manual_drive_command(
            "robot-1",
            0.5,
            0.0,
            CommandSource("operator", "dashboard-1"),
        )

        self.assertEqual(result["robot_id"], "robot-1")
        self.assertEqual(result["linear"], 0.5)
        self.assertEqual(result["angular"], 0.0)
        self.assertIsNone(result["paused_mission_id"])

    async def test_manual_recovery_pauses_active_navigation_until_explicit_resume(self) -> None:
        mission_id = self.mc.create_mission(
            MissionCreate(
                requested_by="alice",
                command_source=CommandSource("user", "alice"),
                to_destination="Hall_A",
                schedule_type="single",
                assigned_robot_id="robot-1",
            )
        )
        self.storage.update_mission(
            mission_id,
            assigned_robot_id="robot-1",
            state=MissionState.EN_ROUTE.value,
            started_at=time.time(),
        )
        self.adapter._current_mission_id = mission_id
        self.adapter._state = MissionState.EN_ROUTE

        result = await self.mc.send_robot_manual_drive_command(
            "robot-1",
            0.5,
            0.0,
            CommandSource("operator", "phone-1"),
        )

        self.assertEqual(result["paused_mission_id"], mission_id)
        self.assertEqual(
            self.storage.get_mission(mission_id)["state"],
            MissionState.PAUSED.value,
        )
        self.assertFalse(self.adapter._paused.is_set())
        self.assertEqual(
            [event["event"] for event in self.storage.list_events(mission_id)][-2:],
            ["paused", "manual_recovery_started"],
        )

        stop_result = await self.mc.send_robot_manual_drive_command(
            "robot-1",
            0.0,
            0.0,
            CommandSource("operator", "phone-1"),
        )
        self.assertIsNone(stop_result["paused_mission_id"])
        self.assertEqual(
            self.storage.get_mission(mission_id)["state"],
            MissionState.PAUSED.value,
        )

    async def test_operator_snapshot_tracks_initial_pose_and_system_commands(self) -> None:
        await self.mc.set_robot_initial_pose(
            "robot-1",
            0.75,
            -1.25,
            0.33,
            CommandSource("operator", "dashboard-1"),
        )
        await self.mc.send_robot_system_command(
            "robot-1",
            "launch_nav",
            CommandSource("operator", "dashboard-1"),
            map_name="test_map1",
        )

        snapshot = self.mc.robot_operator_snapshot("robot-1")
        self.assertEqual(snapshot["initial_pose"]["x"], 0.75)
        self.assertEqual(snapshot["initial_pose"]["y"], -1.25)
        self.assertEqual(snapshot["last_system_command"], "launch_nav")
        self.assertEqual(snapshot["robot_id"], "robot-1")

    async def test_map_management_and_goal_pose_through_scheduler(self) -> None:
        await self.mc.save_robot_map("robot-1", "Office", CommandSource("operator", "dashboard-1"))
        await self.mc.send_robot_system_command(
            "robot-1",
            "launch_nav",
            CommandSource("operator", "dashboard-1"),
            map_name="Office",
        )
        await self.mc.set_robot_goal_pose(
            "robot-1",
            4.0,
            -2.0,
            0.5,
            CommandSource("operator", "dashboard-1"),
        )

        snapshot = self.mc.robot_operator_snapshot("robot-1")
        self.assertIn("Office", snapshot["saved_maps"])
        self.assertEqual(snapshot["current_map_name"], "Office")
        self.assertEqual(snapshot["goal_pose"]["x"], 4.0)


class Test06UiControls(unittest.TestCase):
    """Covers the browser interaction contract for map controls."""

    def test_ctrl_wheel_zooms_map_and_navigation_stop_matches_the_pi_contract(self) -> None:
        app_js = (PROJECT_ROOT / "ui" / "app.js").read_text(encoding="utf-8")
        index_html = (PROJECT_ROOT / "ui" / "index.html").read_text(encoding="utf-8")

        self.assertIn(
            'document.addEventListener("wheel", handleDashboardMapWheel, { capture: true, passive: false });',
            app_js,
        )
        self.assertIn("event.preventDefault();", app_js)
        self.assertIn('data-navigation-stop="true"', index_html)
        self.assertNotIn('data-power-mode="STOP"', index_html)
        self.assertIn('title="Stop navigation"', index_html)
        self.assertIn('"Navigation stopped."', app_js)
        self.assertIn("operator-panel?include_map=", app_js)
        self.assertIn("payload.map = previousMap;", app_js)

        styles_css = (PROJECT_ROOT / "ui" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("overscroll-behavior: none;", styles_css)
        self.assertIn("position: fixed;", styles_css)
        self.assertIn("map-destination-chip", app_js)
        self.assertIn(".map-destination-chip", styles_css)
        self.assertIn(
            'elements.destinationOverlay.addEventListener("click", handleLocationClick);',
            app_js,
        )
        self.assertNotIn('id="localize-robot-button"', index_html)
        self.assertNotIn("handleLocalizeRobot", app_js)
        self.assertNotIn("/localize`", app_js)
        self.assertIn('id="set-initial-position-button"', index_html)
        self.assertNotIn('id="map-confirm-heading"', index_html)
        self.assertIn('id="robot-brain-panel"', index_html)
        self.assertIn('id="robot-state-pi"', index_html)
        self.assertIn('id="robot-state-battery"', index_html)
        self.assertIn('id="robot-state-latency"', index_html)
        self.assertIn("getRobotReadiness", app_js)
        self.assertIn("startupReady", app_js)
        self.assertIn("initialPoseReady", app_js)
        self.assertIn("navigationReady", app_js)
        self.assertIn(
            'elements.setInitialPositionButton.addEventListener("click", toggleInitialPoseMode);',
            app_js,
        )
        self.assertIn('kind: "initial_pose"', app_js)
        self.assertIn("drawUrdfRobotModel", app_js)
        self.assertNotIn("drawInitialPoseMarker", app_js)
        self.assertNotIn("drawGoalMarker", app_js)
        self.assertIn('phase: "stationary_refinement"', app_js)
        self.assertIn(
            '!["failed", "safety_paused", "invalid_jump"].includes(localization.phase)',
            app_js,
        )
        self.assertIn("move the robot to a clearer space", app_js)
        self.assertIn(
            "fetch(`/robots/${encodeURIComponent(robotId)}/initial-pose`",
            app_js,
        )
        self.assertNotIn("/goal-pose", app_js)
        self.assertNotIn("sendGoalPose", app_js)

    def test_robot_service_performs_a_clean_start_in_a_dedicated_process_group(self) -> None:
        robot_root = PROJECT_ROOT.parents[1] / "src" / "my_bot"
        start_script = (robot_root / "scripts" / "start_robot_stack.sh").read_text(
            encoding="utf-8"
        )
        service_template = (
            robot_root / "systemd" / "my-bot-robot.service.in"
        ).read_text(encoding="utf-8")

        self.assertIn("perform_initial_clean_start", start_script)
        self.assertIn('ROBOT_INITIAL_CLEAN_START="${ROBOT_INITIAL_CLEAN_START:-once}"', start_script)
        self.assertIn("ROBOT_INITIAL_CLEAN_MARKER", start_script)
        self.assertIn("collect_device_owner_pids", start_script)
        self.assertIn("signal_pid_list KILL", start_script)
        self.assertIn("setsid ros2 launch", start_script)
        self.assertIn("EnvironmentFile=-/etc/default/my-bot-robot", service_template)
        self.assertIn("KillMode=mixed", service_template)


class Test07MappingNotes(unittest.TestCase):
    """Covers: `mission_control/mapping.txt` project-progress helper notes."""

    def test_mapping_file_contains_step_summary(self) -> None:
        mapping_path = PROJECT_ROOT / "mission_control" / "mapping.txt"
        self.assertTrue(mapping_path.exists())

        text = mapping_path.read_text(encoding="utf-8")
        self.assertIn("Step 1", text)
        self.assertIn("Step 6", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
