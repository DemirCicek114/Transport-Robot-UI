from __future__ import annotations

import asyncio
import json
import math
import os
import random
import shlex
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .config_loader import DestinationConfig
from .types import MissionOutcome, MissionState, RobotMode


_COVARIANCE_ROUNDOFF_EPSILON = 1e-8
_LOCALIZATION_FOOTPRINT_REAR_M = -0.15
_LOCALIZATION_FOOTPRINT_FRONT_M = 0.917
_LOCALIZATION_FOOTPRINT_HALF_WIDTH_M = 0.305
_LOCALIZATION_MAP_OCCUPIED_THRESHOLD = 65

# Approximate rested/load voltage curve for the trolley's 6S LiPo pack.
# Interpolation avoids the old 20-24 V linear estimate, which displayed 100%
# through much of the useful 24.0-25.2 V discharge range.
_BATTERY_DISCHARGE_CURVE = (
    (20.4, 0.0),
    (21.0, 5.0),
    (21.6, 10.0),
    (22.2, 20.0),
    (22.5, 30.0),
    (22.8, 40.0),
    (23.1, 50.0),
    (23.4, 60.0),
    (23.7, 70.0),
    (24.0, 80.0),
    (24.3, 88.0),
    (24.6, 94.0),
    (24.9, 98.0),
    (25.2, 100.0),
)


def battery_percent_from_voltage(voltage: Any) -> Optional[float]:
    """Estimate 6S LiPo charge from pack voltage using linear interpolation."""
    try:
        numeric_voltage = float(voltage)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric_voltage) or numeric_voltage <= 0.0:
        return None
    if numeric_voltage <= _BATTERY_DISCHARGE_CURVE[0][0]:
        return 0.0
    if numeric_voltage >= _BATTERY_DISCHARGE_CURVE[-1][0]:
        return 100.0
    for (lower_v, lower_percent), (upper_v, upper_percent) in zip(
        _BATTERY_DISCHARGE_CURVE,
        _BATTERY_DISCHARGE_CURVE[1:],
    ):
        if numeric_voltage <= upper_v:
            fraction = (numeric_voltage - lower_v) / (upper_v - lower_v)
            return lower_percent + fraction * (upper_percent - lower_percent)
    return 100.0


@dataclass
class RobotTelemetry:
    robot_id: str
    state: MissionState
    mode: RobotMode
    current_mission_id: Optional[str]
    last_heartbeat_at: float
    connection_ok: bool
    localization_valid: bool
    obstacle_stop: bool
    blocked: bool
    battery_v: float
    pose: Dict[str, float]
    outcome: Optional[MissionOutcome] = None


@dataclass
class RobotPowerStatus:
    available: bool
    mode: str
    battery_percent: Optional[float] = None
    latency_ms: Optional[float] = None
    recent_log: Optional[str] = None


@dataclass(frozen=True)
class Ros2AdapterConfig:
    node_name: str = "mission_control_ros2_adapter"
    navigate_action_name: str = "navigate_to_pose"
    map_frame: str = "map"
    map_topic: Optional[str] = "/map"
    keepout_map_topic: Optional[str] = "/keepout_filter_mask"
    keepout_map_required: bool = True
    display_map_topic: Optional[str] = "/display_map"
    goal_pose_topic: Optional[str] = "/goal_pose"
    localization_topic: str = "/amcl_pose"
    odom_topic: str = "/diff_cont/odom"
    battery_topic: Optional[str] = "/battery_state"
    joystick_topic: Optional[str] = "/cmd_vel_joy"
    initial_pose_topic: Optional[str] = "/initialpose"
    set_initial_pose_service: Optional[str] = "/set_initial_pose"
    global_localization_service: Optional[str] = "/reinitialize_global_localization"
    nomotion_update_service: Optional[str] = "/request_nomotion_update"
    system_command_topic: Optional[str] = None
    system_status_topic: Optional[str] = None
    filtered_scan_topic: Optional[str] = "/scan_filtered"
    pi_ready_topic: Optional[str] = "/robot_health/ready"
    hardware_healthy_topic: Optional[str] = "/robot_health/hardware_healthy"
    lidar_healthy_topic: Optional[str] = "/robot_health/lidar_healthy"
    odometry_healthy_topic: Optional[str] = "/robot_health/odometry_healthy"
    controller_healthy_topic: Optional[str] = "/robot_health/controller_healthy"
    obstacle_healthy_topic: Optional[str] = "/robot_health/obstacle_health"
    startup_gate_topic: Optional[str] = "/robot_health/startup_gate_open"
    health_log_topic: Optional[str] = "/robot_health/log"
    navigation_command_topic: Optional[str] = "/cmd_vel_nav_raw"
    launcher_mode: str = "local"
    external_map_name: Optional[str] = None
    package_name: str = "my_bot"
    robot_workspace: str = "$HOME/robot_ws"
    central_workspace: str = "$HOME/dev_ws"
    mapping_workspace: str = "$HOME/dev_ws"
    nav_workspace: str = "$HOME/robot_ws"
    map_directory: str = "$HOME/dev_ws/src/my_bot/maps"
    robot_launch_file: str = "rpi_robot.launch.py"
    central_launch_file: str = "central_compute.launch.py"
    mapping_use_joystick: bool = True
    nav_use_joystick: bool = False
    launch_rviz: bool = False
    launcher_request_timeout_s: float = 8.0
    action_server_timeout_s: float = 30.0
    nav2_lifecycle_nodes: tuple[str, ...] = (
        "controller_server",
        "smoother_server",
        "planner_server",
        "behavior_server",
        "bt_navigator",
        "waypoint_follower",
        "velocity_smoother",
    )
    nav2_lifecycle_poll_s: float = 0.5
    connection_timeout_s: float = 2.5
    readiness_signal_timeout_s: float = 3.0
    stop_zero_count: int = 8
    stop_zero_interval_s: float = 0.05
    stop_confirmation_timeout_s: float = 1.5
    localization_timeout_s: float = 0.0
    localization_max_xy_std_m: float = 0.75
    localization_max_yaw_std_rad: float = 0.75
    localization_usable_xy_std_m: float = 1.00
    localization_usable_yaw_std_rad: float = 0.95
    localization_loss_xy_std_m: float = 1.50
    localization_loss_yaw_std_rad: float = 1.20
    # Require multiple stationary AMCL results before motion. The refinement
    # worker explicitly requests no-motion updates, giving beam skipping time
    # to reject nearby unmapped obstacles instead of trusting the first match.
    localization_required_samples: int = 3
    localization_loss_samples: int = 35
    # A single 5 cm map-cell overlap can be ordinary AMCL jitter beside a
    # wall. Stop an active goal immediately, but require a short consecutive
    # run before declaring localization lost and requiring stable recovery.
    localization_map_fault_samples: int = 3
    localization_confirmation_timeout_s: float = 5.0
    localization_nomotion_updates: int = 12
    localization_nomotion_interval_s: float = 0.35
    initial_pose_xy_covariance: float = 0.25
    # The dashboard asks only for approximate X/Y. A pi-radian standard
    # deviation lets AMCL search the full heading range from lidar instead of
    # making the operator guess the robot's orientation.
    initial_pose_yaw_covariance: float = math.pi ** 2
    localization_max_seed_distance_m: float = 2.0
    localization_max_pose_residual_m: float = 1.0
    localization_max_yaw_residual_rad: float = 0.75
    localization_confirmation_max_translation_m: float = 0.30
    localization_confirmation_max_yaw_delta_rad: float = 0.45
    manual_override_timeout_s: float = 0.75
    stall_speed_epsilon: float = 0.02
    stall_angular_speed_epsilon: float = 0.05
    stall_detect_after_s: float = 0.5
    goal_tolerance_m: float = 0.35

    @classmethod
    def from_env(cls) -> "Ros2AdapterConfig":
        return cls(
            node_name=os.getenv("MISSION_CONTROL_ROS2_NODE_NAME", cls.node_name),
            navigate_action_name=os.getenv("MISSION_CONTROL_ROS2_NAVIGATE_ACTION", cls.navigate_action_name),
            map_frame=os.getenv("MISSION_CONTROL_ROS2_MAP_FRAME", cls.map_frame),
            map_topic=_env_optional_str("MISSION_CONTROL_ROS2_MAP_TOPIC", cls.map_topic),
            keepout_map_topic=_env_optional_str(
                "MISSION_CONTROL_ROS2_KEEPOUT_MAP_TOPIC",
                cls.keepout_map_topic,
            ),
            keepout_map_required=_env_bool(
                "MISSION_CONTROL_ROS2_KEEPOUT_MAP_REQUIRED",
                cls.keepout_map_required,
            ),
            display_map_topic=_env_optional_str(
                "MISSION_CONTROL_ROS2_DISPLAY_MAP_TOPIC",
                cls.display_map_topic,
            ),
            goal_pose_topic=_env_optional_str("MISSION_CONTROL_ROS2_GOAL_POSE_TOPIC", cls.goal_pose_topic),
            localization_topic=os.getenv("MISSION_CONTROL_ROS2_LOCALIZATION_TOPIC", cls.localization_topic),
            odom_topic=os.getenv("MISSION_CONTROL_ROS2_ODOM_TOPIC", cls.odom_topic),
            battery_topic=_env_optional_str("MISSION_CONTROL_ROS2_BATTERY_TOPIC", cls.battery_topic),
            joystick_topic=_env_optional_str("MISSION_CONTROL_ROS2_JOYSTICK_TOPIC", cls.joystick_topic),
            initial_pose_topic=_env_optional_str("MISSION_CONTROL_ROS2_INITIAL_POSE_TOPIC", cls.initial_pose_topic),
            set_initial_pose_service=_env_optional_str(
                "MISSION_CONTROL_ROS2_SET_INITIAL_POSE_SERVICE",
                cls.set_initial_pose_service,
            ),
            global_localization_service=_env_optional_str(
                "MISSION_CONTROL_ROS2_GLOBAL_LOCALIZATION_SERVICE",
                cls.global_localization_service,
            ),
            nomotion_update_service=_env_optional_str(
                "MISSION_CONTROL_ROS2_NOMOTION_UPDATE_SERVICE",
                cls.nomotion_update_service,
            ),
            system_command_topic=_env_optional_str("MISSION_CONTROL_ROS2_SYSTEM_COMMAND_TOPIC", cls.system_command_topic),
            system_status_topic=_env_optional_str("MISSION_CONTROL_ROS2_SYSTEM_STATUS_TOPIC", cls.system_status_topic),
            filtered_scan_topic=_env_optional_str(
                "MISSION_CONTROL_ROS2_FILTERED_SCAN_TOPIC",
                cls.filtered_scan_topic,
            ),
            pi_ready_topic=_env_optional_str(
                "MISSION_CONTROL_ROS2_PI_READY_TOPIC",
                cls.pi_ready_topic,
            ),
            hardware_healthy_topic=_env_optional_str(
                "MISSION_CONTROL_ROS2_HARDWARE_HEALTHY_TOPIC",
                cls.hardware_healthy_topic,
            ),
            lidar_healthy_topic=_env_optional_str(
                "MISSION_CONTROL_ROS2_LIDAR_HEALTHY_TOPIC",
                cls.lidar_healthy_topic,
            ),
            odometry_healthy_topic=_env_optional_str(
                "MISSION_CONTROL_ROS2_ODOMETRY_HEALTHY_TOPIC",
                cls.odometry_healthy_topic,
            ),
            controller_healthy_topic=_env_optional_str(
                "MISSION_CONTROL_ROS2_CONTROLLER_HEALTHY_TOPIC",
                cls.controller_healthy_topic,
            ),
            obstacle_healthy_topic=_env_optional_str(
                "MISSION_CONTROL_ROS2_OBSTACLE_HEALTHY_TOPIC",
                cls.obstacle_healthy_topic,
            ),
            startup_gate_topic=_env_optional_str(
                "MISSION_CONTROL_ROS2_STARTUP_GATE_TOPIC",
                cls.startup_gate_topic,
            ),
            health_log_topic=_env_optional_str(
                "MISSION_CONTROL_ROS2_HEALTH_LOG_TOPIC",
                cls.health_log_topic,
            ),
            navigation_command_topic=_env_optional_str(
                "MISSION_CONTROL_ROS2_NAVIGATION_COMMAND_TOPIC",
                cls.navigation_command_topic,
            ),
            launcher_mode=os.getenv("MISSION_CONTROL_ROS2_LAUNCHER_MODE", cls.launcher_mode).strip().lower(),
            external_map_name=_env_optional_str(
                "MISSION_CONTROL_ROS2_EXTERNAL_MAP_NAME",
                cls.external_map_name,
            ),
            package_name=os.getenv("MISSION_CONTROL_ROS2_PACKAGE_NAME", cls.package_name).strip() or cls.package_name,
            robot_workspace=os.getenv("MISSION_CONTROL_ROS2_ROBOT_WORKSPACE", cls.robot_workspace),
            central_workspace=os.getenv("MISSION_CONTROL_ROS2_CENTRAL_WORKSPACE", cls.central_workspace),
            mapping_workspace=os.getenv(
                "MISSION_CONTROL_ROS2_MAPPING_WORKSPACE",
                os.getenv("MISSION_CONTROL_ROS2_CENTRAL_WORKSPACE", cls.mapping_workspace),
            ),
            nav_workspace=os.getenv(
                "MISSION_CONTROL_ROS2_NAV_WORKSPACE",
                os.getenv("MISSION_CONTROL_ROS2_CENTRAL_WORKSPACE", cls.nav_workspace),
            ),
            map_directory=os.getenv("MISSION_CONTROL_ROS2_MAP_DIRECTORY", cls.map_directory),
            robot_launch_file=os.getenv("MISSION_CONTROL_ROS2_ROBOT_LAUNCH_FILE", cls.robot_launch_file).strip() or cls.robot_launch_file,
            central_launch_file=os.getenv("MISSION_CONTROL_ROS2_CENTRAL_LAUNCH_FILE", cls.central_launch_file).strip() or cls.central_launch_file,
            mapping_use_joystick=_env_bool("MISSION_CONTROL_ROS2_MAPPING_USE_JOYSTICK", cls.mapping_use_joystick),
            nav_use_joystick=_env_bool("MISSION_CONTROL_ROS2_NAV_USE_JOYSTICK", cls.nav_use_joystick),
            launch_rviz=_env_bool("MISSION_CONTROL_ROS2_LAUNCH_RVIZ", cls.launch_rviz),
            launcher_request_timeout_s=_env_float("MISSION_CONTROL_ROS2_LAUNCHER_TIMEOUT_S", cls.launcher_request_timeout_s),
            action_server_timeout_s=_env_float("MISSION_CONTROL_ROS2_ACTION_TIMEOUT_S", cls.action_server_timeout_s),
            nav2_lifecycle_nodes=tuple(
                node.strip().strip("/")
                for node in os.getenv(
                    "MISSION_CONTROL_ROS2_NAV2_LIFECYCLE_NODES",
                    ",".join(cls.nav2_lifecycle_nodes),
                ).split(",")
                if node.strip().strip("/")
            ),
            nav2_lifecycle_poll_s=max(
                0.1,
                _env_float(
                    "MISSION_CONTROL_ROS2_NAV2_LIFECYCLE_POLL_S",
                    cls.nav2_lifecycle_poll_s,
                ),
            ),
            connection_timeout_s=_env_float("MISSION_CONTROL_ROS2_CONNECTION_TIMEOUT_S", cls.connection_timeout_s),
            readiness_signal_timeout_s=_env_float(
                "MISSION_CONTROL_ROS2_READINESS_SIGNAL_TIMEOUT_S",
                cls.readiness_signal_timeout_s,
            ),
            stop_zero_count=max(
                1,
                int(_env_float(
                    "MISSION_CONTROL_ROS2_STOP_ZERO_COUNT",
                    cls.stop_zero_count,
                )),
            ),
            stop_zero_interval_s=max(
                0.0,
                _env_float(
                    "MISSION_CONTROL_ROS2_STOP_ZERO_INTERVAL_S",
                    cls.stop_zero_interval_s,
                ),
            ),
            stop_confirmation_timeout_s=max(
                0.0,
                _env_float(
                    "MISSION_CONTROL_ROS2_STOP_CONFIRMATION_TIMEOUT_S",
                    cls.stop_confirmation_timeout_s,
                ),
            ),
            localization_timeout_s=_env_float("MISSION_CONTROL_ROS2_LOCALIZATION_TIMEOUT_S", cls.localization_timeout_s),
            localization_max_xy_std_m=_env_float(
                "MISSION_CONTROL_ROS2_LOCALIZATION_MAX_XY_STD_M",
                cls.localization_max_xy_std_m,
            ),
            localization_max_yaw_std_rad=_env_float(
                "MISSION_CONTROL_ROS2_LOCALIZATION_MAX_YAW_STD_RAD",
                cls.localization_max_yaw_std_rad,
            ),
            localization_usable_xy_std_m=_env_float(
                "MISSION_CONTROL_ROS2_LOCALIZATION_USABLE_XY_STD_M",
                cls.localization_usable_xy_std_m,
            ),
            localization_usable_yaw_std_rad=_env_float(
                "MISSION_CONTROL_ROS2_LOCALIZATION_USABLE_YAW_STD_RAD",
                cls.localization_usable_yaw_std_rad,
            ),
            localization_loss_xy_std_m=_env_float(
                "MISSION_CONTROL_ROS2_LOCALIZATION_LOSS_XY_STD_M",
                cls.localization_loss_xy_std_m,
            ),
            localization_loss_yaw_std_rad=_env_float(
                "MISSION_CONTROL_ROS2_LOCALIZATION_LOSS_YAW_STD_RAD",
                cls.localization_loss_yaw_std_rad,
            ),
            localization_required_samples=max(
                1,
                int(_env_float(
                    "MISSION_CONTROL_ROS2_LOCALIZATION_REQUIRED_SAMPLES",
                    cls.localization_required_samples,
                )),
            ),
            localization_loss_samples=max(
                1,
                int(_env_float(
                    "MISSION_CONTROL_ROS2_LOCALIZATION_LOSS_SAMPLES",
                    cls.localization_loss_samples,
                )),
            ),
            localization_map_fault_samples=max(
                1,
                int(_env_float(
                    "MISSION_CONTROL_ROS2_LOCALIZATION_MAP_FAULT_SAMPLES",
                    cls.localization_map_fault_samples,
                )),
            ),
            localization_confirmation_timeout_s=_env_float(
                "MISSION_CONTROL_ROS2_LOCALIZATION_CONFIRMATION_TIMEOUT_S",
                cls.localization_confirmation_timeout_s,
            ),
            localization_nomotion_updates=max(
                1,
                int(_env_float(
                    "MISSION_CONTROL_ROS2_LOCALIZATION_NOMOTION_UPDATES",
                    cls.localization_nomotion_updates,
                )),
            ),
            localization_nomotion_interval_s=_env_float(
                "MISSION_CONTROL_ROS2_LOCALIZATION_NOMOTION_INTERVAL_S",
                cls.localization_nomotion_interval_s,
            ),
            initial_pose_xy_covariance=_env_float(
                "MISSION_CONTROL_ROS2_INITIAL_POSE_XY_COVARIANCE",
                cls.initial_pose_xy_covariance,
            ),
            initial_pose_yaw_covariance=_env_float(
                "MISSION_CONTROL_ROS2_INITIAL_POSE_YAW_COVARIANCE",
                cls.initial_pose_yaw_covariance,
            ),
            localization_max_seed_distance_m=_env_float(
                "MISSION_CONTROL_ROS2_LOCALIZATION_MAX_SEED_DISTANCE_M",
                cls.localization_max_seed_distance_m,
            ),
            localization_max_pose_residual_m=_env_float(
                "MISSION_CONTROL_ROS2_LOCALIZATION_MAX_POSE_RESIDUAL_M",
                cls.localization_max_pose_residual_m,
            ),
            localization_max_yaw_residual_rad=_env_float(
                "MISSION_CONTROL_ROS2_LOCALIZATION_MAX_YAW_RESIDUAL_RAD",
                cls.localization_max_yaw_residual_rad,
            ),
            localization_confirmation_max_translation_m=_env_float(
                "MISSION_CONTROL_ROS2_LOCALIZATION_CONFIRMATION_MAX_TRANSLATION_M",
                cls.localization_confirmation_max_translation_m,
            ),
            localization_confirmation_max_yaw_delta_rad=_env_float(
                "MISSION_CONTROL_ROS2_LOCALIZATION_CONFIRMATION_MAX_YAW_DELTA_RAD",
                cls.localization_confirmation_max_yaw_delta_rad,
            ),
            manual_override_timeout_s=_env_float("MISSION_CONTROL_ROS2_MANUAL_OVERRIDE_TIMEOUT_S", cls.manual_override_timeout_s),
            stall_speed_epsilon=_env_float("MISSION_CONTROL_ROS2_STALL_SPEED_EPSILON", cls.stall_speed_epsilon),
            stall_angular_speed_epsilon=_env_float("MISSION_CONTROL_ROS2_STALL_ANGULAR_EPSILON", cls.stall_angular_speed_epsilon),
            stall_detect_after_s=_env_float("MISSION_CONTROL_ROS2_STALL_TIMEOUT_S", cls.stall_detect_after_s),
            goal_tolerance_m=_env_float("MISSION_CONTROL_ROS2_GOAL_TOLERANCE_M", cls.goal_tolerance_m),
        )


class RobotAdapter:
    """Abstract robot adapter.

    The Mission Control layer talks to *this* interface.

    Later, your ROS2 integration becomes an implementation of this class
    (e.g., a Nav2 Action client) without changing the mission scheduler/API.
    """

    def __init__(self, robot_id: str):
        self.robot_id = robot_id

    async def start_mission(self, mission_id: str, plan: List[str]) -> None:
        raise NotImplementedError

    async def pause(self) -> None:
        raise NotImplementedError

    async def resume(self) -> None:
        raise NotImplementedError

    async def cancel(self) -> None:
        raise NotImplementedError

    async def reset_to_idle(self) -> None:
        """Clear any mission context after mission manager records completion."""
        raise NotImplementedError

    def snapshot(self) -> RobotTelemetry:
        raise NotImplementedError

    def navigation_ready(self) -> bool:
        """Return whether a new autonomous mission may be dispatched now."""
        telemetry = self.snapshot()
        return bool(telemetry.connection_ok and telemetry.localization_valid)

    async def send_manual_drive_command(self, linear: float, angular: float) -> None:
        raise NotImplementedError

    def manual_drive_snapshot(self) -> Dict[str, Any]:
        return {
            "ready": False,
            "message": "Manual recovery drive is unavailable for this robot.",
            "checks": {"manual_command_topic": False},
            "missing": ["manual_command_topic"],
        }

    async def localize(self) -> Dict[str, Any]:
        raise NotImplementedError

    async def send_system_command(self, command: str, map_name: Optional[str] = None) -> None:
        raise NotImplementedError

    async def set_initial_pose(self, x: float, y: float, yaw: float) -> None:
        raise NotImplementedError

    async def set_goal_pose(self, x: float, y: float, yaw: float) -> None:
        raise NotImplementedError

    async def save_map(self, map_name: str) -> Dict[str, Any]:
        raise NotImplementedError

    async def delete_map(self, map_name: str) -> Dict[str, Any]:
        raise NotImplementedError

    async def load_map_preview(self, map_name: str) -> Dict[str, Any]:
        raise NotImplementedError

    def operator_snapshot(self, include_map: bool = True) -> Dict[str, Any]:
        return {
            "map_available": False,
            "map": None,
            "map_updated_at": None,
            "goal_pose": None,
            "initial_pose": None,
            "system_commands_available": False,
            "initial_pose_available": False,
            "goal_pose_available": False,
            "navigation_available": False,
            "navigation_action_available": False,
            "manual_drive_available": False,
            "manual_drive": self.manual_drive_snapshot(),
            "last_system_command": None,
            "saved_maps": [],
            "current_map_name": None,
            "maps_directory": None,
            "launcher_message": None,
            "launcher_processes": {},
            "startup": {
                "phase": "waiting_for_robot",
                "ready": False,
                "message": "Waiting for the robot stack.",
                "checks": {
                    "pi_discovered": False,
                    "pi_ready": False,
                    "hardware": False,
                    "lidar_health": False,
                    "odometry_health": False,
                    "controller": False,
                    "obstacle_safety": False,
                    "startup_gate": False,
                    "filtered_scan": False,
                    "map": False,
                    "odometry": False,
                    "odom_to_base_link": False,
                    "amcl": False,
                },
            },
            "navigation": {
                "ready": False,
                "message": "Waiting for the robot stack.",
                "checks": {},
                "missing": [],
            },
            "localization": {
                "phase": "not_started",
                "requested": False,
                "ready": False,
                "failed": False,
                "message": None,
                "refinement_active": False,
                "degraded": False,
                "safety_pause_active": False,
                "safety_pause_reason": None,
                "stop_in_progress": False,
                "accepted_map_pose_available": False,
                "quality": "unknown",
                "confident_samples": 0,
                "usable_samples": 0,
                "required_samples": 1,
                "unconfident_samples": 0,
                "map_fault_samples": 0,
                "required_map_fault_samples": 1,
                "xy_std_m": None,
                "yaw_std_rad": None,
                "last_pose_at": None,
            },
        }

    def power_snapshot(self) -> RobotPowerStatus:
        return RobotPowerStatus(available=False, mode="Unavailable")

    def shutdown(self) -> None:
        """Release adapter resources during server shutdown."""
        return None


class SimRobotAdapter(RobotAdapter):
    """A simple simulated robot.

    - Takes a mission plan: list of destination names.
    - "Drives" by sleeping.
    - Supports pause/resume/cancel.
    - Can be forced into a blocked condition for testing.
    """

    def __init__(
        self,
        robot_id: str,
        speed_scale: float = 1.0,
        ui_map_path: Optional[str] = None,
    ):
        super().__init__(robot_id)
        self._state: MissionState = MissionState.IDLE
        self._mode: RobotMode = RobotMode.AUTO
        self._current_mission_id: Optional[str] = None
        self._power_latency_ms = 12.0
        self._power_recent_log = "Simulated robot telemetry ready."

        self._connection_ok = True
        self._localization_valid = True
        self._obstacle_stop = False
        self._blocked = False

        self._battery_v = 24.0
        self._pose = {"x": 0.0, "y": 0.0, "yaw": 0.0}
        self._last_initial_pose: Optional[Dict[str, float]] = None
        self._last_goal_pose: Optional[Dict[str, float]] = None
        self._last_system_command: Optional[str] = None
        self._current_map_name: Optional[str] = None
        self._maps_directory = "/sim/maps"
        self._launcher_message = "Launcher ready."
        self._launcher_processes: Dict[str, bool] = {"robot": False, "slam": False, "nav": False}
        self._saved_maps: Dict[str, Dict[str, Any]] = {
            "test_map1": {
                "name": "test_map1",
                "width": 32,
                "height": 24,
                "resolution": 0.1,
                "origin": {"x": -1.6, "y": -1.2, "yaw": 0.0},
                "data": [0] * (32 * 24),
                "updated_at": time.time(),
            }
        }
        for preview_map in _load_packaged_demo_maps():
            self._saved_maps.setdefault(preview_map["name"], preview_map)
        if ui_map_path:
            navigation_map_path = _expanded_path(ui_map_path)
            if not navigation_map_path.exists():
                raise RuntimeError(f"UI map '{navigation_map_path}' was not found.")
            preview_path = navigation_map_path
            map_layers = _map_layer_paths(navigation_map_path)
            if map_layers is not None:
                missing_layers = [
                    path.name
                    for path in map_layers.values()
                    if not path.exists()
                ]
                if missing_layers:
                    raise RuntimeError(
                        f"UI map profile '{navigation_map_path.stem}' is missing: "
                        + ", ".join(missing_layers)
                    )
                preview_path = map_layers["display"]
            preview_map = _load_map_preview_from_yaml(preview_path)
            self._saved_maps = {navigation_map_path.stem: preview_map}
            self._current_map_name = navigation_map_path.stem
            self._maps_directory = str(navigation_map_path.parent)
            self._launcher_message = (
                f"UI-only preview loaded from {preview_path.name}; no ROS or Nav2 processes started."
            )
        elif "atrium_navigation" in self._saved_maps:
            self._current_map_name = "atrium_navigation"
            self._launcher_processes["nav"] = True

        self._paused = asyncio.Event()
        self._paused.set()
        self._cancel_requested = False
        self._task: Optional[asyncio.Task] = None
        self._last_outcome: Optional[MissionOutcome] = None

        self._speed_scale = max(0.1, float(speed_scale))

    def set_manual_override(self, enabled: bool) -> None:
        self._mode = RobotMode.MANUAL_OVERRIDE if enabled else RobotMode.AUTO

    def set_blocked(self, blocked: bool) -> None:
        self._blocked = bool(blocked)

    def set_localization_valid(self, ok: bool) -> None:
        self._localization_valid = bool(ok)

    def set_obstacle_stop(self, stop: bool) -> None:
        self._obstacle_stop = bool(stop)

    async def start_mission(self, mission_id: str, plan: List[str]) -> None:
        if self._task and not self._task.done():
            raise RuntimeError("Robot already executing a mission.")
        self._current_mission_id = mission_id
        self._cancel_requested = False
        self._last_outcome = None
        self._state = MissionState.EN_ROUTE
        self._paused.set()
        self._task = asyncio.create_task(self._run_plan(plan))

    async def _run_plan(self, plan: List[str]) -> None:
        # Very rough: each leg takes 4-10 seconds scaled by speed_scale
        try:
            for _i, _dest in enumerate(plan):
                leg_time = random.uniform(4.0, 10.0) / self._speed_scale
                started = time.time()
                while time.time() - started < leg_time:
                    # Heartbeat + battery drain
                    self._battery_v = max(20.0, self._battery_v - 0.005)
                    self._pose["x"] += random.uniform(-0.02, 0.05)
                    self._pose["y"] += random.uniform(-0.02, 0.05)
                    self._pose["yaw"] += random.uniform(-0.01, 0.01)

                    # Pause handling
                    await self._paused.wait()

                    # Cancel handling
                    if self._cancel_requested:
                        self._state = MissionState.COMPLETED
                        self._last_outcome = MissionOutcome.CANCELED
                        return

                    if self._mode == RobotMode.MANUAL_OVERRIDE:
                        await asyncio.sleep(0.2)
                        continue

                    # Blocked handling: if blocked, just sit until unblocked or canceled.
                    if self._blocked or self._obstacle_stop:
                        await asyncio.sleep(0.2)
                        continue

                    await asyncio.sleep(0.2)

            self._state = MissionState.COMPLETED
            self._last_outcome = MissionOutcome.SUCCESS
        finally:
            # Keep current_mission_id until mission manager clears it
            pass

    async def pause(self) -> None:
        # Pausing affects motion; mission manager controls mission state separately.
        self._paused.clear()

    async def resume(self) -> None:
        self._paused.set()

    async def cancel(self) -> None:
        self._cancel_requested = True
        self._paused.set()

    async def reset_to_idle(self) -> None:
        # Note: in a real robot, you'd also clear navigation goals, etc.
        self._state = MissionState.IDLE
        self._current_mission_id = None
        self._cancel_requested = False
        self._last_outcome = None
        self._paused.set()

    def snapshot(self) -> RobotTelemetry:
        return RobotTelemetry(
            robot_id=self.robot_id,
            state=self._state,
            mode=self._mode,
            current_mission_id=self._current_mission_id,
            last_heartbeat_at=time.time(),
            connection_ok=self._connection_ok,
            localization_valid=self._localization_valid,
            obstacle_stop=self._obstacle_stop,
            blocked=self._blocked,
            battery_v=self._battery_v,
            pose=dict(self._pose),
            outcome=self._last_outcome,
        )

    async def send_manual_drive_command(self, linear: float, angular: float) -> None:
        linear = _clamp(float(linear), -1.0, 1.0)
        angular = _clamp(float(angular), -1.0, 1.0)

        self._mode = RobotMode.MANUAL_OVERRIDE
        if abs(linear) < 1e-4 and abs(angular) < 1e-4:
            self._mode = RobotMode.AUTO
            self._power_recent_log = "Manual drive stopped."
            return

        next_yaw = self._pose["yaw"] + (angular * 0.12)
        self._pose["yaw"] = next_yaw
        self._pose["x"] += math.cos(next_yaw) * linear * 0.18
        self._pose["y"] += math.sin(next_yaw) * linear * 0.18
        self._power_recent_log = f"Manual drive command: linear={linear:.2f}, angular={angular:.2f}"

    def manual_drive_snapshot(self) -> Dict[str, Any]:
        return {
            "ready": True,
            "message": "Simulated manual recovery drive is ready.",
            "checks": {"manual_command_topic": True},
            "missing": [],
        }

    async def localize(self) -> Dict[str, Any]:
        self._localization_valid = True
        self._power_recent_log = "Stationary global localization simulated."
        return {
            "robot_id": self.robot_id,
            "ok": True,
            "message": "Stationary global localization simulated.",
        }

    async def send_system_command(self, command: str, map_name: Optional[str] = None) -> None:
        normalized = command.strip().lower()
        if normalized not in {"launch_robot", "launch_slam", "launch_nav", "save_map", "kill_all"}:
            raise ValueError(f"Unsupported system command: {command}")
        if normalized == "launch_nav":
            if not map_name:
                raise RuntimeError("Select a map before launching navigation.")
            if map_name not in self._saved_maps:
                raise RuntimeError(f"Saved map '{map_name}' was not found.")
            self._current_map_name = map_name
            self._launcher_processes["nav"] = True
            self._launcher_message = f"Navigation launched with map {map_name}."
        elif normalized == "launch_robot":
            self._launcher_processes["robot"] = True
            self._launcher_message = "Robot launch command sent."
        elif normalized == "launch_slam":
            self._launcher_processes["slam"] = True
            self._current_map_name = None
            self._launcher_message = "Mapping mode launched."
        elif normalized == "kill_all":
            self._launcher_processes = {"robot": False, "slam": False, "nav": False}
            self._launcher_message = "All launcher processes stopped."
        elif normalized == "save_map":
            self._launcher_message = "Use named map save to persist a map."
        self._last_system_command = normalized
        self._power_recent_log = f"System command sent: {normalized}"

    async def set_initial_pose(self, x: float, y: float, yaw: float) -> None:
        self._last_initial_pose = {"x": float(x), "y": float(y), "yaw": 0.0}
        self._pose = dict(self._last_initial_pose)
        self._localization_valid = True
        self._power_recent_log = (
            f"Approximate position set to x={x:.2f}, y={y:.2f}; simulated AMCL heading is ready."
        )

    async def set_goal_pose(self, x: float, y: float, yaw: float) -> None:
        self._last_goal_pose = {"x": float(x), "y": float(y), "yaw": float(yaw)}
        self._power_recent_log = f"Goal pose set to x={x:.2f}, y={y:.2f}, yaw={yaw:.2f}"

    async def save_map(self, map_name: str) -> Dict[str, Any]:
        normalized = map_name.strip()
        if not normalized:
            raise RuntimeError("Map name is required.")
        existing = {name.lower() for name in self._saved_maps}
        if normalized.lower() in existing:
            raise RuntimeError(f"Map '{normalized}' already exists.")

        self._saved_maps[normalized] = {
            "name": normalized,
            "width": 32,
            "height": 24,
            "resolution": 0.1,
            "origin": {"x": -1.6, "y": -1.2, "yaw": 0.0},
            "data": [0] * (32 * 24),
            "updated_at": time.time(),
        }
        self._launcher_message = f"Saved map {normalized}."
        return {"maps": sorted(self._saved_maps), "current_map_name": self._current_map_name}

    async def delete_map(self, map_name: str) -> Dict[str, Any]:
        normalized = map_name.strip()
        existing = next((name for name in self._saved_maps if name.lower() == normalized.lower()), None)
        if existing is None:
            raise RuntimeError(f"Map '{map_name}' was not found.")
        if existing == self._current_map_name and self._launcher_processes.get("nav"):
            raise RuntimeError("Cannot delete the current active navigation map.")
        del self._saved_maps[existing]
        self._launcher_message = f"Deleted map {existing}."
        return {"maps": sorted(self._saved_maps), "current_map_name": self._current_map_name}

    async def load_map_preview(self, map_name: str) -> Dict[str, Any]:
        existing = next((name for name in self._saved_maps if name.lower() == map_name.strip().lower()), None)
        if existing is None:
            raise RuntimeError(f"Map '{map_name}' was not found.")
        return dict(self._saved_maps[existing])

    def operator_snapshot(self, include_map: bool = True) -> Dict[str, Any]:
        current_map = self._saved_maps.get(self._current_map_name) if self._current_map_name else None
        localization_ready = bool(self._localization_valid)
        navigation_ready = localization_ready
        return {
            "map_available": current_map is not None,
            "map": dict(current_map) if include_map and current_map is not None else None,
            "map_updated_at": current_map.get("updated_at") if current_map is not None else None,
            "goal_pose": dict(self._last_goal_pose) if self._last_goal_pose else None,
            "initial_pose": dict(self._last_initial_pose) if self._last_initial_pose else None,
            "system_commands_available": True,
            "initial_pose_available": True,
            "goal_pose_available": navigation_ready,
            "navigation_available": navigation_ready,
            "navigation_action_available": True,
            "manual_drive_available": True,
            "manual_drive": self.manual_drive_snapshot(),
            "last_system_command": self._last_system_command,
            "saved_maps": sorted(self._saved_maps),
            "current_map_name": self._current_map_name,
            "maps_directory": self._maps_directory,
            "launcher_message": self._launcher_message,
            "launcher_processes": dict(self._launcher_processes),
            "startup": {
                "phase": "ready",
                "ready": True,
                "message": "Robot stack is ready for an initial position.",
                "checks": {
                    "pi_discovered": True,
                    "pi_ready": True,
                    "hardware": True,
                    "lidar_health": True,
                    "odometry_health": True,
                    "controller": True,
                    "obstacle_safety": True,
                    "startup_gate": True,
                    "filtered_scan": True,
                    "map": True,
                    "odometry": True,
                    "odom_to_base_link": True,
                    "amcl": True,
                },
            },
            "navigation": {
                "ready": navigation_ready,
                "message": (
                    "Navigation is unlocked."
                    if navigation_ready
                    else "Waiting for simulated localization."
                ),
                "checks": {
                    "localization": navigation_ready,
                    "map_to_odom": True,
                    "navigate_to_pose": True,
                },
                "missing": [] if navigation_ready else ["localization"],
            },
            "localization": {
                "phase": "ready" if localization_ready else "not_started",
                "requested": self._last_initial_pose is not None,
                "ready": localization_ready,
                "failed": False,
                "message": None,
                "refinement_active": False,
                "degraded": False,
                "safety_pause_active": False,
                "safety_pause_reason": None,
                "stop_in_progress": False,
                "accepted_map_pose_available": localization_ready,
                "quality": "good" if localization_ready else "unknown",
                "confident_samples": 1 if localization_ready else 0,
                "usable_samples": 0,
                "required_samples": 1,
                "unconfident_samples": 0,
                "map_fault_samples": 0,
                "required_map_fault_samples": 1,
                "xy_std_m": 0.0 if localization_ready else None,
                "yaw_std_rad": 0.0 if localization_ready else None,
                "last_pose_at": time.time() if localization_ready else None,
            },
        }

    def power_snapshot(self) -> RobotPowerStatus:
        percent = battery_percent_from_voltage(self._battery_v)
        return RobotPowerStatus(
            available=True,
            mode=self._mode.value,
            battery_percent=percent,
            latency_ms=self._power_latency_ms,
            recent_log=self._power_recent_log,
        )


class Ros2RobotAdapter(RobotAdapter):
    """ROS 2 / Nav2-backed adapter used by the mission-control scheduler."""

    def __init__(
        self,
        robot_id: str,
        dest_config: DestinationConfig,
        config: Optional[Ros2AdapterConfig] = None,
    ):
        super().__init__(robot_id)
        self._dest_config = dest_config
        self._config = config or Ros2AdapterConfig.from_env()
        self._ros = _import_ros2_modules()

        self._lock = threading.RLock()
        self._resume_event = threading.Event()
        self._resume_event.set()
        self._goal_done_event = threading.Event()
        self._shutdown_requested = False
        self._pause_requested = False
        self._cancel_requested = False
        self._cancel_future_in_flight = False
        self._cancel_future_goal_handle = None

        self._state: MissionState = MissionState.IDLE
        self._mode: RobotMode = RobotMode.AUTO
        self._current_mission_id: Optional[str] = None
        self._current_plan: List[str] = []
        self._current_leg_index = 0
        self._current_destination: Optional[str] = None
        self._current_goal_pose: Optional[Dict[str, float]] = None
        self._last_outcome: Optional[MissionOutcome] = None

        self._connection_ok = False
        self._localization_valid = False
        self._obstacle_stop = False
        self._blocked = False
        self._battery_v = 0.0
        self._power_battery_percent: Optional[float] = None
        self._power_latency_ms: Optional[float] = None
        self._power_recent_log: Optional[str] = None
        self._map_snapshot: Optional[Dict[str, Any]] = None
        self._keepout_map_snapshot: Optional[Dict[str, Any]] = None
        self._keepout_map_required = bool(
            self._config.keepout_map_topic
            and self._config.keepout_map_required
        )
        self._display_map_snapshot: Optional[Dict[str, Any]] = None
        self._last_initial_pose: Optional[Dict[str, float]] = None
        self._last_goal_pose: Optional[Dict[str, float]] = None
        self._last_system_command: Optional[str] = None
        self._saved_map_names: List[str] = []
        self._current_map_name: Optional[str] = None
        self._maps_directory: Optional[str] = None
        self._launcher_message: Optional[str] = None
        self._launcher_processes: Dict[str, bool] = {}
        self._map_preview_cache: Dict[str, Dict[str, Any]] = {}
        self._pending_launcher_requests: Dict[str, Dict[str, Any]] = {}
        self._local_processes: Dict[str, subprocess.Popen[Any]] = {}
        self._pose = {"x": 0.0, "y": 0.0, "yaw": 0.0}
        self._linear_speed = 0.0
        self._angular_speed = 0.0
        now = time.time()
        # Starting the laptop adapter does not mean the Pi is connected.
        # Live Pi messages establish readiness.
        self._last_heartbeat_at = 0.0
        self._last_pi_signal_at = 0.0
        self._last_pi_ready_at = 0.0
        self._last_odom_at = 0.0
        self._last_filtered_scan_at = 0.0
        self._health_values: Dict[str, bool] = {
            "pi_ready": False,
            "hardware": False,
            "lidar": False,
            "odometry": False,
            "controller": False,
            "obstacle_safety": False,
            "startup_gate": False,
        }
        self._health_updated_at: Dict[str, float] = {
            key: 0.0 for key in self._health_values
        }
        self._last_localization_at = 0.0
        self._last_localization_pose_at = 0.0
        self._localization_requested = False
        self._localization_confident_samples = 0
        self._localization_usable_samples = 0
        self._localization_unconfident_samples = 0
        self._localization_seeded_from_initial_pose = False
        self._localization_xy_std_m: Optional[float] = None
        self._localization_yaw_std_rad: Optional[float] = None
        self._localization_degraded = False
        self._latest_odom_pose: Optional[Dict[str, float]] = None
        self._localization_anchor_map_pose: Optional[Dict[str, float]] = None
        self._localization_anchor_odom_pose: Optional[Dict[str, float]] = None
        self._localization_plausibility_fault: Optional[str] = None
        self._localization_candidate_fault: Optional[str] = None
        self._localization_failure_message: Optional[str] = None
        self._localization_map_fault_samples = 0
        self._localization_confirmation_pose: Optional[Dict[str, float]] = None
        self._localization_stop_in_progress = False
        self._late_goal_stop_workers = 0
        self._late_goal_handles_draining: set[int] = set()
        self._late_goal_release_active_on_terminal: set[int] = set()
        self._goal_response_drains_pending = 0
        self._localization_stop_generation = 0
        self._localization_safety_pause_active = False
        self._localization_safety_pause_reason: Optional[str] = None
        self._accepted_map_pose_available = False
        self._initial_pose_refinement_generation = 0
        self._initial_pose_refinement_active = False
        self._last_motion_at = now
        self._last_joy_cmd_at = 0.0
        self._goal_active_since = 0.0

        self._send_goal_future = None
        self._active_goal_handle = None
        self._goal_result_status: Optional[int] = None
        self._goal_result_error: Optional[str] = None

        self._mission_thread: Optional[threading.Thread] = None
        self._spin_thread: Optional[threading.Thread] = None
        self._initial_pose_refinement_thread: Optional[threading.Thread] = None
        self._executor = None
        self._node = None
        self._navigate_client = None
        self._nav2_lifecycle_clients: Dict[str, Any] = {}
        self._nav2_lifecycle_states: Dict[str, Optional[str]] = {
            name: None for name in self._config.nav2_lifecycle_nodes
        }
        self._nav2_lifecycle_requests: Dict[str, Any] = {}
        self._navigation_zero_publisher = None
        self._manual_command_publisher = None
        self._global_localization_client = None
        self._nomotion_update_client = None
        self._set_initial_pose_client = None
        self._initial_pose_publisher = None
        self._system_command_publisher = None
        self._system_status_subscription = None
        self._tf_buffer = None
        self._tf_listener = None
        self._last_stop_status: Dict[str, Any] = {
            "requested_at": None,
            "confirmed": None,
            "message": "No navigation stop has been requested.",
        }
        self._context = None

        self._goal_status_succeeded = self._ros["GoalStatus"].STATUS_SUCCEEDED
        self._goal_status_aborted = self._ros["GoalStatus"].STATUS_ABORTED
        self._goal_status_canceled = self._ros["GoalStatus"].STATUS_CANCELED
        self._goal_status_unknown = self._ros["GoalStatus"].STATUS_UNKNOWN

        self._initialize_launcher_state()
        self._init_ros()

    def _local_launcher_enabled(self) -> bool:
        return self._config.launcher_mode == "local"

    def _external_launcher_enabled(self) -> bool:
        return self._config.launcher_mode in {"external", "supervised"}

    def _catalog_launcher_enabled(self) -> bool:
        return self._local_launcher_enabled() or self._external_launcher_enabled()

    def _map_profile_requires_keepout(self, map_name: Optional[str]) -> bool:
        if not (
            self._config.keepout_map_topic
            and self._config.keepout_map_required
        ):
            return False
        if not map_name:
            return True
        return _map_layer_paths(self._local_map_yaml_path(map_name)) is not None

    def _requires_keepout_map(self) -> bool:
        return bool(
            getattr(
                self,
                "_keepout_map_required",
                self._map_profile_requires_keepout(
                    getattr(self, "_current_map_name", None),
                ),
            )
        )

    def _initialize_launcher_state(self) -> None:
        if not self._catalog_launcher_enabled():
            return
        maps_dir = _expanded_path(self._config.map_directory)
        with self._lock:
            self._maps_directory = str(maps_dir)
            self._refresh_local_maps_locked()
            if self._external_launcher_enabled():
                self._current_map_name = self._config.external_map_name
                self._keepout_map_required = self._map_profile_requires_keepout(
                    self._current_map_name,
                )
                self._launcher_processes = {
                    "nav": bool(self._config.external_map_name),
                    "slam": False,
                }
                self._launcher_message = (
                    "The central supervisor owns ROS processes; Mission Control is attached."
                )
            else:
                self._launcher_processes = {"robot": False, "slam": False, "nav": False}
                self._launcher_message = "Local catering_bot launcher ready."

    def _refresh_local_maps_locked(self) -> List[str]:
        maps_dir = _expanded_path(self._config.map_directory)
        self._maps_directory = str(maps_dir)
        if not maps_dir.exists():
            self._saved_map_names = []
            return []
        self._saved_map_names = sorted(
            path.stem
            for path in maps_dir.glob("*.yaml")
            if path.is_file() and not path.stem.endswith(("_keepout", "_display"))
        )
        return list(self._saved_map_names)

    def _local_map_yaml_path(self, map_name: str) -> Path:
        maps_dir = _expanded_path(self._config.map_directory)
        clean_name = Path(str(map_name).strip()).stem
        if not clean_name:
            raise RuntimeError("Map name is required.")
        return maps_dir / f"{clean_name}.yaml"

    def _start_local_process_locked(self, key: str, workspace: str, ros_args: List[str]) -> None:
        self._stop_local_process_locked(key)
        command = _ros_workspace_command(_expanded_path(workspace), ros_args)
        process = subprocess.Popen(
            command,
            start_new_session=True,
            stderr=subprocess.STDOUT,
        )
        self._local_processes[key] = process
        self._launcher_processes[key] = True

    def _stop_local_process_locked(self, key: str) -> None:
        process = self._local_processes.pop(key, None)
        if process is None:
            self._launcher_processes[key] = False
            if key == "nav":
                self._reset_localization_locked()
                self._clear_navigation_maps_locked()
            return
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=2.0)
        self._launcher_processes[key] = False
        if key == "nav":
            self._reset_localization_locked()
            self._clear_navigation_maps_locked()

    def _prune_local_processes_locked(self) -> None:
        for key, process in list(self._local_processes.items()):
            if process.poll() is not None:
                self._local_processes.pop(key, None)
                self._launcher_processes[key] = False
                if key == "nav":
                    self._reset_localization_locked()
                    self._clear_navigation_maps_locked()

    def _clear_navigation_maps_locked(self) -> None:
        """Discard latched layers so a new launch cannot use a stale map."""
        self._map_snapshot = None
        self._keepout_map_snapshot = None
        self._display_map_snapshot = None

    def _reset_localization_locked(self) -> None:
        self._localization_valid = False
        self._last_localization_at = 0.0
        self._last_localization_pose_at = 0.0
        self._localization_requested = False
        self._localization_confident_samples = 0
        self._localization_usable_samples = 0
        self._localization_unconfident_samples = 0
        self._localization_seeded_from_initial_pose = False
        self._localization_xy_std_m = None
        self._localization_yaw_std_rad = None
        self._localization_degraded = False
        self._localization_anchor_map_pose = None
        self._localization_anchor_odom_pose = None
        self._localization_plausibility_fault = None
        self._localization_candidate_fault = None
        self._localization_failure_message = None
        self._localization_map_fault_samples = 0
        self._localization_confirmation_pose = None
        self._localization_stop_generation = (
            getattr(self, "_localization_stop_generation", 0) + 1
        )
        self._localization_safety_pause_active = False
        self._localization_safety_pause_reason = None
        self._accepted_map_pose_available = False
        self._initial_pose_refinement_generation = (
            getattr(self, "_initial_pose_refinement_generation", 0) + 1
        )
        self._initial_pose_refinement_active = False
        self._last_initial_pose = None

    def _send_local_launcher_command(self, command: str, map_name: Optional[str] = None) -> Dict[str, Any]:
        normalized = command.strip().lower()
        config = self._config
        with self._lock:
            self._prune_local_processes_locked()
            if normalized == "launch_robot":
                self._start_local_process_locked(
                    "robot",
                    config.robot_workspace,
                    ["ros2", "launch", config.package_name, config.robot_launch_file],
                )
                self._last_system_command = normalized
                self._launcher_message = "Robot stack launched."
            elif normalized == "launch_slam":
                self._stop_local_process_locked("nav")
                self._current_map_name = None
                self._start_local_process_locked(
                    "slam",
                    config.mapping_workspace,
                    [
                        "ros2",
                        "launch",
                        config.package_name,
                        config.central_launch_file,
                        "use_slam:=true",
                        "use_nav2:=false",
                        f"use_joystick:={_bool_arg(config.mapping_use_joystick)}",
                        f"use_rviz:={_bool_arg(config.launch_rviz)}",
                    ],
                )
                self._last_system_command = normalized
                self._launcher_message = "Mapping mode launched."
            elif normalized == "launch_nav":
                if not map_name:
                    raise RuntimeError("Select a saved map before launching navigation.")
                map_path = self._local_map_yaml_path(map_name)
                if not map_path.exists():
                    raise RuntimeError(f"Saved map '{map_name}' was not found.")
                map_layers = _map_layer_paths(map_path)
                if map_layers is not None:
                    missing_layers = [
                        path.name
                        for path in map_layers.values()
                        if not path.exists()
                    ]
                    if missing_layers:
                        raise RuntimeError(
                            f"Navigation map profile '{map_path.stem}' is missing: "
                            + ", ".join(missing_layers)
                        )
                self._keepout_map_required = bool(
                    map_layers is not None
                    and config.keepout_map_topic
                    and config.keepout_map_required
                )
                self._stop_local_process_locked("slam")
                self._stop_local_process_locked("nav")
                self._current_map_name = map_path.stem
                nav_args = [
                    "ros2",
                    "launch",
                    config.package_name,
                    config.central_launch_file,
                    "use_slam:=false",
                    "use_nav2:=true",
                    f"use_joystick:={_bool_arg(config.nav_use_joystick)}",
                    f"use_rviz:={_bool_arg(config.launch_rviz)}",
                    f"map:={str(map_path)}",
                ]
                if map_layers is not None:
                    nav_args.extend([
                        "use_keepout:=true",
                        f"keepout_mask:={str(map_layers['keepout'])}",
                        "use_display_map:=true",
                        f"display_map:={str(map_layers['display'])}",
                    ])
                else:
                    nav_args.extend([
                        "use_keepout:=false",
                        "use_display_map:=false",
                    ])
                self._start_local_process_locked(
                    "nav",
                    config.nav_workspace,
                    nav_args,
                )
                self._last_system_command = normalized
                self._launcher_message = f"Navigation launched with map {map_path.stem}."
            elif normalized == "kill_all":
                for key in ("nav", "slam", "robot"):
                    self._stop_local_process_locked(key)
                self._last_system_command = normalized
                self._launcher_message = "Launcher processes stopped."
            else:
                raise ValueError(f"Unsupported local launcher command: {command}")

            maps = self._refresh_local_maps_locked()
            self._last_heartbeat_at = time.time()
            return self._local_launcher_status_locked(maps=maps)

    def _local_launcher_status_locked(self, maps: Optional[List[str]] = None) -> Dict[str, Any]:
        self._prune_local_processes_locked()
        return {
            "ok": True,
            "maps": list(maps if maps is not None else self._refresh_local_maps_locked()),
            "current_map": self._current_map_name,
            "map_directory": self._maps_directory,
            "processes": dict(self._launcher_processes),
            "last_command": self._last_system_command,
            "message": self._launcher_message,
        }

    def _save_local_map(self, map_name: str) -> Dict[str, Any]:
        map_path = self._local_map_yaml_path(map_name)
        map_path.parent.mkdir(parents=True, exist_ok=True)
        output_base = map_path.with_suffix("")
        command = _ros_workspace_command(
            _expanded_path(self._config.mapping_workspace),
            ["ros2", "run", "nav2_map_server", "map_saver_cli", "-f", str(output_base)],
        )
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=20.0)
        if result.returncode != 0:
            raise RuntimeError(result.stdout.strip() or "Map save failed.")

        with self._lock:
            maps = self._refresh_local_maps_locked()
            self._launcher_message = f"Saved map {map_path.stem}."
            self._last_system_command = "save_map"
            return self._local_launcher_status_locked(maps=maps)

    def _delete_local_map(self, map_name: str) -> Dict[str, Any]:
        map_path = self._local_map_yaml_path(map_name)
        if not map_path.exists():
            raise RuntimeError(f"Map '{map_name}' was not found.")
        image_path = _image_path_from_map_yaml(map_path)
        if map_path.stem == self._current_map_name and self._launcher_processes.get("nav"):
            raise RuntimeError("Cannot delete the current active navigation map.")
        map_path.unlink()
        if image_path and image_path.exists():
            image_path.unlink()

        with self._lock:
            self._map_preview_cache.pop(map_path.stem, None)
            maps = self._refresh_local_maps_locked()
            self._launcher_message = f"Deleted map {map_path.stem}."
            self._last_system_command = "delete_map"
            return self._local_launcher_status_locked(maps=maps)

    def _load_local_map_preview_response(self, map_name: str) -> Dict[str, Any]:
        map_path = self._local_map_yaml_path(map_name)
        if not map_path.exists():
            raise RuntimeError(f"Map '{map_name}' was not found.")
        preview_path = map_path
        map_layers = _map_layer_paths(map_path)
        if map_layers is not None and map_layers["display"].exists():
            preview_path = map_layers["display"]
        preview_map = _load_map_preview_from_yaml(preview_path)
        with self._lock:
            self._map_preview_cache[map_path.stem] = dict(preview_map)
            maps = self._refresh_local_maps_locked()
            return {
                **self._local_launcher_status_locked(maps=maps),
                "preview_map": preview_map,
            }

    def _init_ros(self) -> None:
        rclpy = self._ros["rclpy"]
        self._context = self._ros["Context"]()
        rclpy.init(args=None, context=self._context)

        config = self._config
        node_name = f"{config.node_name}_{self.robot_id.replace('-', '_')}"
        adapter = self
        modules = self._ros

        class MissionBridgeNode(modules["Node"]):
            def __init__(self) -> None:
                super().__init__(node_name, context=adapter._context)
                map_qos = modules["QoSProfile"](
                    history=modules["HistoryPolicy"].KEEP_LAST,
                    depth=1,
                    reliability=modules["ReliabilityPolicy"].RELIABLE,
                    durability=modules["DurabilityPolicy"].TRANSIENT_LOCAL,
                )
                adapter._navigate_client = modules["ActionClient"](
                    self,
                    modules["NavigateToPose"],
                    config.navigate_action_name,
                )
                adapter._nav2_lifecycle_clients = {
                    name: self.create_client(
                        modules["GetState"],
                        f"/{name}/get_state",
                    )
                    for name in config.nav2_lifecycle_nodes
                }
                if adapter._nav2_lifecycle_clients:
                    self.create_timer(
                        config.nav2_lifecycle_poll_s,
                        adapter._poll_nav2_lifecycle_states,
                    )
                adapter._tf_buffer = modules["Buffer"]()
                adapter._tf_listener = modules["TransformListener"](
                    adapter._tf_buffer,
                    self,
                    spin_thread=False,
                )
                if config.map_topic:
                    self.create_subscription(
                        modules["OccupancyGrid"],
                        config.map_topic,
                        adapter._handle_map,
                        map_qos,
                    )
                if (
                    config.keepout_map_topic
                    and config.keepout_map_topic != config.map_topic
                ):
                    self.create_subscription(
                        modules["OccupancyGrid"],
                        config.keepout_map_topic,
                        adapter._handle_keepout_map,
                        map_qos,
                    )
                if config.display_map_topic and config.display_map_topic != config.map_topic:
                    self.create_subscription(
                        modules["OccupancyGrid"],
                        config.display_map_topic,
                        adapter._handle_display_map,
                        map_qos,
                    )
                if config.goal_pose_topic:
                    # Nav2's bt_navigator treats /goal_pose as a command and
                    # converts every publication into a NavigateToPose action.
                    # Observe external RViz goals, but never publish here:
                    # Mission Control dispatches exactly one action below.
                    self.create_subscription(
                        modules["PoseStamped"],
                        config.goal_pose_topic,
                        adapter._handle_goal_pose,
                        10,
                    )
                self.create_subscription(
                    modules["PoseWithCovarianceStamped"],
                    config.localization_topic,
                    adapter._handle_localization_pose,
                    10,
                )
                self.create_subscription(
                    modules["Odometry"],
                    config.odom_topic,
                    adapter._handle_odom,
                    10,
                )
                if config.filtered_scan_topic:
                    self.create_subscription(
                        modules["LaserScan"],
                        config.filtered_scan_topic,
                        adapter._handle_filtered_scan,
                        modules["qos_profile_sensor_data"],
                    )
                if config.battery_topic:
                    self.create_subscription(
                        modules["BatteryState"],
                        config.battery_topic,
                        adapter._handle_battery,
                        10,
                    )
                if config.joystick_topic:
                    adapter._manual_command_publisher = self.create_publisher(
                        modules["Twist"],
                        config.joystick_topic,
                        10,
                    )
                    self.create_subscription(
                        modules["Twist"],
                        config.joystick_topic,
                        adapter._handle_joy_cmd,
                        10,
                    )
                if config.initial_pose_topic:
                    adapter._initial_pose_publisher = self.create_publisher(
                        modules["PoseWithCovarianceStamped"],
                        config.initial_pose_topic,
                        10,
                    )
                    self.create_subscription(
                        modules["PoseWithCovarianceStamped"],
                        config.initial_pose_topic,
                        adapter._handle_initial_pose,
                        10,
                    )
                if config.set_initial_pose_service:
                    adapter._set_initial_pose_client = self.create_client(
                        modules["SetInitialPose"],
                        config.set_initial_pose_service,
                    )
                if config.global_localization_service:
                    adapter._global_localization_client = self.create_client(
                        modules["Empty"],
                        config.global_localization_service,
                    )
                if config.nomotion_update_service:
                    adapter._nomotion_update_client = self.create_client(
                        modules["Empty"],
                        config.nomotion_update_service,
                    )
                if config.system_command_topic:
                    adapter._system_command_publisher = self.create_publisher(
                        modules["String"],
                        config.system_command_topic,
                        10,
                    )
                if config.system_status_topic:
                    adapter._system_status_subscription = self.create_subscription(
                        modules["String"],
                        config.system_status_topic,
                        adapter._handle_system_status,
                        10,
                    )
                if config.health_log_topic:
                    self.create_subscription(
                        modules["String"],
                        config.health_log_topic,
                        adapter._handle_health_log,
                        10,
                    )
                health_topics = {
                    "pi_ready": config.pi_ready_topic,
                    "hardware": config.hardware_healthy_topic,
                    "lidar": config.lidar_healthy_topic,
                    "odometry": config.odometry_healthy_topic,
                    "controller": config.controller_healthy_topic,
                    "obstacle_safety": config.obstacle_healthy_topic,
                    "startup_gate": config.startup_gate_topic,
                }
                for health_name, health_topic in health_topics.items():
                    if not health_topic:
                        continue
                    self.create_subscription(
                        modules["Bool"],
                        health_topic,
                        lambda msg, name=health_name: adapter._handle_health_signal(name, msg),
                        10,
                    )
                if config.navigation_command_topic:
                    adapter._navigation_zero_publisher = self.create_publisher(
                        modules["Twist"],
                        config.navigation_command_topic,
                        10,
                    )

        self._node = MissionBridgeNode()
        self._executor = self._ros["SingleThreadedExecutor"](context=self._context)
        self._executor.add_node(self._node)
        self._spin_thread = threading.Thread(target=self._spin_executor, daemon=True)
        self._spin_thread.start()

    def _spin_executor(self) -> None:
        try:
            self._executor.spin()
        except Exception as exc:
            if not self._shutdown_requested:
                print(f"[Ros2RobotAdapter] executor stopped unexpectedly: {exc}")

    def _poll_nav2_lifecycle_states(self) -> None:
        """Track when every required Nav2 lifecycle node is actually active."""
        clients = dict(getattr(self, "_nav2_lifecycle_clients", {}))
        for name, client in clients.items():
            with self._lock:
                pending = getattr(self, "_nav2_lifecycle_requests", {}).get(name)
            if pending is not None and not pending.done():
                continue
            if not client.service_is_ready():
                with self._lock:
                    self._nav2_lifecycle_states[name] = None
                    self._nav2_lifecycle_requests.pop(name, None)
                continue
            try:
                future = client.call_async(self._ros["GetState"].Request())
            except Exception:
                with self._lock:
                    self._nav2_lifecycle_states[name] = None
                    self._nav2_lifecycle_requests.pop(name, None)
                continue
            with self._lock:
                self._nav2_lifecycle_requests[name] = future

            def _record_state(done_future: Any, node_name: str = name) -> None:
                state_label: Optional[str] = None
                try:
                    response = done_future.result()
                    state_label = str(response.current_state.label).strip().lower()
                except Exception:
                    state_label = None
                with self._lock:
                    self._nav2_lifecycle_states[node_name] = state_label
                    self._nav2_lifecycle_requests.pop(node_name, None)

            future.add_done_callback(_record_state)

    def _nav2_lifecycle_ready_locked(self) -> bool:
        required = tuple(getattr(self._config, "nav2_lifecycle_nodes", ()))
        if not required:
            return True
        # Isolated unit adapters created without ROS preserve their historical
        # behavior unless a lifecycle-state table is deliberately supplied.
        states = getattr(self, "_nav2_lifecycle_states", None)
        if states is None:
            return True
        return all(states.get(name) == "active" for name in required)

    def navigation_ready(self) -> bool:
        return bool(self.operator_snapshot(include_map=False)["navigation_available"])

    def _localization_pose_plausibility_locked(
        self,
        candidate: Dict[str, float],
    ) -> tuple[bool, Optional[str]]:
        existing_fault = getattr(self, "_localization_plausibility_fault", None)
        if existing_fault:
            return False, str(existing_fault)

        anchor_map = getattr(self, "_localization_anchor_map_pose", None)
        anchor_odom = getattr(self, "_localization_anchor_odom_pose", None)
        current_odom = getattr(self, "_latest_odom_pose", None)
        has_odometry_anchor = bool(
            anchor_map is not None
            and anchor_odom is not None
            and current_odom is not None
        )

        if not self._localization_valid and not has_odometry_anchor:
            # The operator seed is only an acquisition guard. Once a pose has
            # been accepted and the robot has driven away, recovery must be
            # checked against odometry from the last trusted map pose rather
            # than against the now-stale starting point.
            seed = getattr(self, "_last_initial_pose", None)
            if seed is not None:
                seed_distance = math.hypot(
                    candidate["x"] - float(seed["x"]),
                    candidate["y"] - float(seed["y"]),
                )
                seed_limit = max(
                    0.0,
                    float(self._config.localization_max_seed_distance_m),
                )
                if seed_distance > seed_limit:
                    return False, (
                        "AMCL pose rejected before navigation: "
                        f"{seed_distance:.2f} m from the operator seed exceeds "
                        f"the {seed_limit:.2f} m limit."
                    )
            return True, None

        if not has_odometry_anchor:
            return True, None

        odom_dx = current_odom["x"] - anchor_odom["x"]
        odom_dy = current_odom["y"] - anchor_odom["y"]
        anchor_odom_yaw = anchor_odom["yaw"]
        relative_x = (
            math.cos(anchor_odom_yaw) * odom_dx
            + math.sin(anchor_odom_yaw) * odom_dy
        )
        relative_y = (
            -math.sin(anchor_odom_yaw) * odom_dx
            + math.cos(anchor_odom_yaw) * odom_dy
        )
        map_yaw = anchor_map["yaw"]
        expected_x = (
            anchor_map["x"]
            + math.cos(map_yaw) * relative_x
            - math.sin(map_yaw) * relative_y
        )
        expected_y = (
            anchor_map["y"]
            + math.sin(map_yaw) * relative_x
            + math.cos(map_yaw) * relative_y
        )
        expected_yaw = _normalize_angle(
            map_yaw + current_odom["yaw"] - anchor_odom_yaw
        )
        position_residual = math.hypot(
            candidate["x"] - expected_x,
            candidate["y"] - expected_y,
        )
        yaw_residual = abs(_normalize_angle(candidate["yaw"] - expected_yaw))
        position_limit = max(
            0.0,
            float(self._config.localization_max_pose_residual_m),
        )
        yaw_limit = max(
            0.0,
            float(self._config.localization_max_yaw_residual_rad),
        )
        if position_residual > position_limit or yaw_residual > yaw_limit:
            return False, (
                "AMCL pose jump rejected: odometry predicted "
                f"({expected_x:.2f}, {expected_y:.2f}, {expected_yaw:.2f}) but "
                f"AMCL reported ({candidate['x']:.2f}, {candidate['y']:.2f}, "
                f"{candidate['yaw']:.2f}); residuals were "
                f"{position_residual:.2f} m and {yaw_residual:.2f} rad."
            )
        return True, None

    def _anchor_localization_to_odometry_locked(
        self,
        map_pose: Dict[str, float],
    ) -> None:
        odom_pose = getattr(self, "_latest_odom_pose", None)
        if odom_pose is None:
            return
        self._localization_anchor_map_pose = dict(map_pose)
        self._localization_anchor_odom_pose = dict(odom_pose)

    def _localization_candidate_is_map_free_locked(self) -> bool:
        return bool(
            getattr(self, "_localization_candidate_fault", None) is None
            and getattr(self, "_localization_map_fault_samples", 0) == 0
        )

    def _localization_has_motion_anchor_locked(self) -> bool:
        return bool(
            getattr(self, "_localization_anchor_map_pose", None) is not None
            and getattr(self, "_localization_anchor_odom_pose", None) is not None
            and getattr(self, "_latest_odom_pose", None) is not None
        )

    def _reject_localization_pose_locked(self, reason: str) -> None:
        if getattr(self, "_localization_plausibility_fault", None):
            return

        self._localization_plausibility_fault = reason
        self._localization_failure_message = (
            f"{reason} Navigation was stopped and is locked until a new "
            "initial position is set."
        )
        self._localization_valid = False
        self._localization_degraded = True
        self._localization_confident_samples = 0
        self._localization_usable_samples = 0
        self._localization_map_fault_samples = 0
        self._localization_seeded_from_initial_pose = False
        self._localization_confirmation_pose = None
        self._initial_pose_refinement_active = False
        self._localization_safety_pause_active = False
        self._localization_safety_pause_reason = None
        self._power_recent_log = (
            f"!!! {reason} Navigation is locked until a new initial position is set."
        )
        self._last_heartbeat_at = time.time()

        active_states = {
            MissionState.REQUESTED,
            MissionState.EN_ROUTE,
            MissionState.RETURNING,
            MissionState.PAUSED,
        }
        if getattr(self, "_state", MissionState.IDLE) in active_states:
            self._cancel_requested = True
            self._pause_requested = False
            resume_event = getattr(self, "_resume_event", None)
            if resume_event is not None:
                resume_event.set()
            self._state = MissionState.COMPLETED
            self._last_outcome = MissionOutcome.FAILED

        self._start_localization_stop_locked()

    def _pause_for_localization_fault_locked(self, reason: str) -> None:
        """Synchronously pause an active route before AMCL is trusted again."""
        already_active = bool(
            getattr(self, "_localization_safety_pause_active", False)
        )
        state = getattr(self, "_state", MissionState.IDLE)
        active_states = {
            MissionState.REQUESTED,
            MissionState.EN_ROUTE,
            MissionState.RETURNING,
        }
        paused_mission = bool(
            state == MissionState.PAUSED
            and getattr(self, "_current_mission_id", None)
        )
        if state not in active_states and not paused_mission:
            return

        self._localization_safety_pause_active = True
        self._localization_safety_pause_reason = reason
        self._pause_requested = True
        self._state = MissionState.PAUSED
        resume_event = getattr(self, "_resume_event", None)
        if resume_event is not None:
            resume_event.clear()
        if not already_active:
            self._start_localization_stop_locked()

    def _start_localization_stop_locked(self) -> None:
        """Start at most one bounded Nav2/velocity stop worker."""
        if getattr(self, "_localization_stop_in_progress", False):
            return
        self._localization_stop_in_progress = True
        self._localization_stop_generation = (
            getattr(self, "_localization_stop_generation", 0) + 1
        )
        generation = self._localization_stop_generation
        threading.Thread(
            target=self._stop_for_localization_fault,
            args=(generation,),
            daemon=True,
            name=f"{self.robot_id}-localization-safety-stop",
        ).start()

    def _quarantine_localization_locked(self, reason: str) -> None:
        """Pause on a recoverable AMCL fault without latching a new seed."""
        self._localization_valid = False
        self._localization_degraded = True
        self._localization_confident_samples = 0
        self._localization_usable_samples = 0
        self._localization_seeded_from_initial_pose = False
        self._localization_confirmation_pose = None
        self._initial_pose_refinement_active = False
        required_samples = max(
            1,
            int(self._config.localization_required_samples),
        )
        self._localization_failure_message = (
            f"Localization temporarily lost: {reason} Navigation is paused. "
            "The last accepted pose remains visible while AMCL waits for "
            f"{required_samples} stable, map-free updates; a paused route will "
            "not resume automatically."
        )
        self._power_recent_log = f"!!! {self._localization_failure_message}"
        self._last_heartbeat_at = time.time()
        self._pause_for_localization_fault_locked(reason)

    def _stop_for_localization_fault(self, generation: int) -> None:
        try:
            self._cancel_active_goal()
            self._finish_navigation_stop(
                "Localization safety fault stopped navigation.",
                localization_generation=generation,
            )
            # Do not advertise the safety stop as complete merely because the
            # bounded zero burst finished. The canceled Nav2 goal must first
            # publish a terminal result and the mission worker must consume it
            # as a pause; otherwise Resume can misclassify that old result as a
            # new cancellation or overlap a replacement goal with it.
            while not getattr(self, "_shutdown_requested", False):
                with self._lock:
                    drain_pending = self._navigation_goal_drain_pending_locked()
                if not drain_pending:
                    break
                self._publish_navigation_zero_once()
                time.sleep(max(0.05, float(self._config.stop_zero_interval_s)))
        finally:
            with self._lock:
                self._localization_stop_in_progress = False
                if generation == getattr(
                    self,
                    "_localization_stop_generation",
                    generation,
                ):
                    permanent_fault = getattr(
                        self,
                        "_localization_plausibility_fault",
                        None,
                    )
                    safety_pause_active = getattr(
                        self,
                        "_localization_safety_pause_active",
                        False,
                    )
                    if permanent_fault:
                        self._power_recent_log = (
                            f"!!! {permanent_fault} Navigation was stopped and remains "
                            "locked until a new initial position is set."
                        )
                    elif safety_pause_active:
                        reason = getattr(
                            self,
                            "_localization_safety_pause_reason",
                            None,
                        ) or "AMCL localization became unsafe."
                        self._power_recent_log = (
                            f"!!! Localization safety pause: {reason} The robot is "
                            "stopped and the route remains paused."
                        )
                    else:
                        # The stop is still the current operation, but its fault
                        # was explicitly cleared (for example by cancellation).
                        self._power_recent_log = (
                            "> Localization safety stop completed."
                        )

    def _handle_localization_pose(self, msg: Any) -> None:
        pose = msg.pose.pose
        covariance = list(getattr(msg.pose, "covariance", []))
        candidate_pose = {
            "x": float(pose.position.x),
            "y": float(pose.position.y),
            "yaw": _quaternion_to_yaw(
                pose.orientation.x,
                pose.orientation.y,
                pose.orientation.z,
                pose.orientation.w,
            ),
        }
        now = time.time()
        with self._lock:
            if not self._localization_requested:
                return
            self._last_localization_pose_at = now
            plausible, plausibility_fault = (
                self._localization_pose_plausibility_locked(candidate_pose)
            )
            if not plausible:
                # Run this before the map check so a true odometry-inconsistent
                # teleport cannot evade the permanent jump guard merely by
                # landing inside a wall or keepout cell.
                self._reject_localization_pose_locked(
                    plausibility_fault or "AMCL pose failed the plausibility check."
                )
                return
            map_fault = _localization_pose_maps_fault(
                getattr(self, "_map_snapshot", None),
                getattr(self, "_keepout_map_snapshot", None),
                candidate_pose,
                require_keepout=self._requires_keepout_map(),
            )
            if map_fault:
                self._localization_candidate_fault = map_fault
                if self._localization_valid:
                    # AMCL publishes map -> odom before this callback reaches
                    # Mission Control, so Nav2 may already have consumed the
                    # impossible transform. Stop the active goal on the first
                    # sample; debounce only whether localization itself is
                    # quarantined and requires stable recovery.
                    self._pause_for_localization_fault_locked(map_fault)
                    fault_samples = (
                        getattr(self, "_localization_map_fault_samples", 0) + 1
                    )
                    self._localization_map_fault_samples = fault_samples
                    required_fault_samples = max(
                        1,
                        self._config.localization_map_fault_samples,
                    )
                    self._localization_degraded = True
                    self._localization_confident_samples = 0
                    self._localization_usable_samples = 0
                    if fault_samples >= required_fault_samples:
                        self._quarantine_localization_locked(map_fault)
                    else:
                        # Do not publish the rejected candidate or move the
                        # accepted map/odom anchor. A one-cell overlap beside a
                        # wall is commonly a transient AMCL/map discretization
                        # disagreement, as seen in the physical startup logs.
                        self._power_recent_log = (
                            f"> AMCL map-validity warning {fault_samples}/"
                            f"{required_fault_samples}: {map_fault} Holding the "
                            "last accepted pose while checking the next update."
                        )
                        self._last_heartbeat_at = now
                else:
                    # During initial acquisition or a recoverable quarantine,
                    # ignore impossible candidates and wait for stable map-free
                    # results. True odometry-inconsistent teleports take the
                    # separate permanent plausibility-fault path.
                    self._localization_confident_samples = 0
                    self._localization_usable_samples = 0
                    self._localization_confirmation_pose = None
                    self._localization_unconfident_samples += 1
                    if getattr(self, "_localization_failure_message", None):
                        self._localization_degraded = True
                    else:
                        self._localization_degraded = False
                        self._power_recent_log = (
                            f"> {map_fault} Ignoring this AMCL result and continuing "
                            "stationary scan matching; navigation remains locked."
                        )
                    self._last_heartbeat_at = now
                return
            self._localization_candidate_fault = None
            self._localization_map_fault_samples = 0
            if len(covariance) >= 36:
                xy_variances = (float(covariance[0]), float(covariance[7]))
                yaw_variance = float(covariance[35])
                if all(
                    math.isfinite(value)
                    and value >= -_COVARIANCE_ROUNDOFF_EPSILON
                    for value in (*xy_variances, yaw_variance)
                ):
                    self._localization_xy_std_m = math.sqrt(
                        max(0.0, *xy_variances)
                    )
                    self._localization_yaw_std_rad = math.sqrt(
                        max(0.0, yaw_variance)
                    )
                else:
                    self._localization_xy_std_m = None
                    self._localization_yaw_std_rad = None
            acquisition_confident = self._localization_covariance_is_confident(covariance)
            usable_confident = self._localization_covariance_within_limits(
                covariance,
                self._config.localization_usable_xy_std_m,
                self._config.localization_usable_yaw_std_rad,
            )
            retention_confident = self._localization_covariance_within_limits(
                covariance,
                self._config.localization_loss_xy_std_m,
                self._config.localization_loss_yaw_std_rad,
            )
            countable_acquisition_pose = bool(
                acquisition_confident
                or (
                    usable_confident
                    and getattr(
                        self,
                        "_localization_seeded_from_initial_pose",
                        False,
                    )
                )
            )
            if not self._localization_valid:
                if countable_acquisition_pose:
                    previous_confirmation_pose = getattr(
                        self,
                        "_localization_confirmation_pose",
                        None,
                    )
                    if previous_confirmation_pose is not None:
                        confirmation_translation = math.hypot(
                            candidate_pose["x"] - previous_confirmation_pose["x"],
                            candidate_pose["y"] - previous_confirmation_pose["y"],
                        )
                        confirmation_yaw_delta = abs(_normalize_angle(
                            candidate_pose["yaw"]
                            - previous_confirmation_pose["yaw"]
                        ))
                        if (
                            confirmation_translation
                            > max(
                                0.0,
                                self._config.localization_confirmation_max_translation_m,
                            )
                            or confirmation_yaw_delta
                            > max(
                                0.0,
                                self._config.localization_confirmation_max_yaw_delta_rad,
                            )
                        ):
                            self._localization_confident_samples = 0
                            self._localization_usable_samples = 0
                            self._power_recent_log = (
                                "> AMCL's map-free pose is still moving between "
                                "stationary updates; restarting pose confirmation."
                            )
                            self._localization_confirmation_pose = dict(
                                candidate_pose,
                            )
                    else:
                        # Hold the first sample as the run anchor. Comparing
                        # only adjacent samples would allow a stationary pose
                        # to drift a little on every update and still unlock.
                        self._localization_confirmation_pose = dict(candidate_pose)
                else:
                    self._localization_confirmation_pose = None

            if self._localization_valid:
                if retention_confident:
                    self._pose = dict(candidate_pose)
                    self._accepted_map_pose_available = True
                self._localization_usable_samples = 0
                if retention_confident:
                    was_degraded = getattr(self, "_localization_degraded", False)
                    self._localization_degraded = not acquisition_confident
                    self._localization_unconfident_samples = 0
                    self._last_localization_at = now
                    if acquisition_confident:
                        self._localization_confident_samples = min(
                            self._localization_confident_samples + 1,
                            max(1, self._config.localization_required_samples),
                        )
                    else:
                        self._localization_confident_samples = 0
                        if not was_degraded:
                            self._power_recent_log = (
                                "> AMCL confidence is reduced, but the pose remains usable. "
                                "AMCL will keep refining while navigation runs."
                            )
                else:
                    self._localization_degraded = True
                    self._localization_confident_samples = 0
                    self._localization_unconfident_samples += 1
                    if self._localization_unconfident_samples == 1:
                        self._power_recent_log = (
                            "> AMCL uncertainty increased. Holding the last localization "
                            "while checking whether it recovers."
                        )
                    if self._localization_unconfident_samples >= max(
                        1,
                        self._config.localization_loss_samples,
                    ):
                        xy_text = (
                            f"{self._localization_xy_std_m:.2f} m"
                            if self._localization_xy_std_m is not None
                            else "unknown position uncertainty"
                        )
                        yaw_text = (
                            f"{math.degrees(self._localization_yaw_std_rad):.0f} deg"
                            if self._localization_yaw_std_rad is not None
                            else "unknown heading uncertainty"
                        )
                        self._quarantine_localization_locked(
                            "AMCL remained severely uncertain for "
                            f"{self._localization_unconfident_samples} consecutive "
                            f"updates ({xy_text}, {yaw_text})."
                        )
            elif acquisition_confident:
                self._localization_confident_samples += 1
                self._localization_usable_samples = 0
                self._localization_unconfident_samples = 0
                required_samples = max(1, self._config.localization_required_samples)
                if self._localization_confident_samples >= required_samples:
                    recovering = bool(
                        getattr(self, "_localization_failure_message", None)
                    )
                    self._localization_valid = True
                    self._pose = dict(candidate_pose)
                    self._accepted_map_pose_available = True
                    self._localization_failure_message = None
                    self._localization_degraded = False
                    self._last_localization_at = now
                    self._localization_seeded_from_initial_pose = False
                    self._initial_pose_refinement_active = False
                    if (
                        recovering
                        and getattr(self, "_state", MissionState.IDLE)
                        == MissionState.PAUSED
                    ):
                        self._power_recent_log = (
                            "> AMCL recovered a stable, map-free pose. Localization "
                            "is ready; the route remains paused until an operator resumes it."
                        )
                    else:
                        self._power_recent_log = (
                            "> AMCL pose is usable and stable. Navigation is unlocked."
                        )
            elif (
                usable_confident
                and getattr(self, "_localization_seeded_from_initial_pose", False)
            ):
                self._localization_confident_samples = 0
                self._localization_usable_samples = (
                    getattr(self, "_localization_usable_samples", 0) + 1
                )
                self._localization_unconfident_samples = 0
                if self._localization_usable_samples >= max(
                    1,
                    self._config.localization_required_samples,
                ):
                    self._localization_valid = True
                    self._pose = dict(candidate_pose)
                    self._accepted_map_pose_available = True
                    self._localization_failure_message = None
                    self._localization_degraded = True
                    self._last_localization_at = now
                    self._localization_seeded_from_initial_pose = False
                    self._initial_pose_refinement_active = False
                    self._power_recent_log = (
                        "> AMCL pose is stable and usable with reduced confidence. "
                        "Navigation is unlocked and AMCL will keep refining while moving."
                    )
            else:
                self._localization_degraded = False
                self._localization_confident_samples = 0
                self._localization_usable_samples = 0
                self._localization_confirmation_pose = None
                self._localization_unconfident_samples += 1
                if getattr(self, "_initial_pose_refinement_active", False):
                    xy_text = (
                        f"{self._localization_xy_std_m:.2f} m"
                        if self._localization_xy_std_m is not None
                        else "unknown"
                    )
                    yaw_text = (
                        f"{math.degrees(self._localization_yaw_std_rad):.0f} deg"
                        if self._localization_yaw_std_rad is not None
                        else "unknown"
                    )
                    self._power_recent_log = (
                        f"> AMCL is matching the stationary lidar scan to the map "
                        f"(position uncertainty {xy_text}, heading uncertainty {yaw_text})."
                    )
            if self._localization_valid and retention_confident:
                self._anchor_localization_to_odometry_locked(candidate_pose)
            self._last_heartbeat_at = now

    def _localization_covariance_is_confident(self, covariance: List[float]) -> bool:
        return self._localization_covariance_within_limits(
            covariance,
            self._config.localization_max_xy_std_m,
            self._config.localization_max_yaw_std_rad,
        )

    @staticmethod
    def _localization_covariance_within_limits(
        covariance: List[float],
        xy_std_limit_m: float,
        yaw_std_limit_rad: float,
    ) -> bool:
        if len(covariance) < 36:
            return False
        xy_limit = max(0.0, xy_std_limit_m) ** 2
        yaw_limit = max(0.0, yaw_std_limit_rad) ** 2
        variances = (float(covariance[0]), float(covariance[7]), float(covariance[35]))
        return (
            all(
                math.isfinite(value)
                and value >= -_COVARIANCE_ROUNDOFF_EPSILON
                for value in variances
            )
            and max(0.0, variances[0]) <= xy_limit
            and max(0.0, variances[1]) <= xy_limit
            and max(0.0, variances[2]) <= yaw_limit
        )

    def _handle_map(self, msg: Any) -> None:
        now = time.time()
        with self._lock:
            snapshot = _occupancy_grid_snapshot(msg, now)
            self._map_snapshot = snapshot
            if self._config.keepout_map_topic == self._config.map_topic:
                self._keepout_map_snapshot = snapshot
            self._last_heartbeat_at = now

    def _handle_keepout_map(self, msg: Any) -> None:
        now = time.time()
        with self._lock:
            self._keepout_map_snapshot = _occupancy_grid_snapshot(msg, now)
            self._last_heartbeat_at = now

    def _handle_display_map(self, msg: Any) -> None:
        now = time.time()
        with self._lock:
            self._display_map_snapshot = _occupancy_grid_snapshot(msg, now)
            self._last_heartbeat_at = now

    def _handle_goal_pose(self, msg: Any) -> None:
        pose = msg.pose
        now = time.time()
        with self._lock:
            self._last_goal_pose = {
                "x": float(pose.position.x),
                "y": float(pose.position.y),
                "yaw": _quaternion_to_yaw(
                    pose.orientation.x,
                    pose.orientation.y,
                    pose.orientation.z,
                    pose.orientation.w,
                ),
            }
            self._last_heartbeat_at = now

    def _handle_initial_pose(self, msg: Any) -> None:
        pose = msg.pose.pose
        now = time.time()

        with self._lock:
            if getattr(self, "_state", MissionState.IDLE) in {
                MissionState.REQUESTED,
                MissionState.EN_ROUTE,
                MissionState.RETURNING,
            }:
                self._power_recent_log = (
                    "!!! RViz initial position was ignored because navigation is "
                    "active. Pause or stop the route before relocalizing."
                )
                self._last_heartbeat_at = now
                return

        # RViz publishes /initialpose directly. Normalize that input to the
        # same X/Y-only seed as the dashboard, then acknowledge it through
        # Nav2's service and force stationary scan updates in a worker thread.
        pose.orientation.x = 0.0
        pose.orientation.y = 0.0
        pose.orientation.z = 0.0
        pose.orientation.w = 1.0
        msg.pose.covariance = [0.0] * 36
        msg.pose.covariance[0] = max(0.0, self._config.initial_pose_xy_covariance)
        msg.pose.covariance[7] = max(0.0, self._config.initial_pose_xy_covariance)
        msg.pose.covariance[35] = max(0.0, self._config.initial_pose_yaw_covariance)
        if getattr(msg.header, "stamp", None) is not None:
            msg.header.stamp.sec = 0
            msg.header.stamp.nanosec = 0

        with self._lock:
            # Recheck at the state transition boundary: mission dispatch can
            # enter REQUESTED after the early guard while this callback is
            # normalizing the incoming RViz message.
            if getattr(self, "_state", MissionState.IDLE) in {
                MissionState.REQUESTED,
                MissionState.EN_ROUTE,
                MissionState.RETURNING,
            }:
                self._power_recent_log = (
                    "!!! RViz initial position was ignored because navigation is "
                    "active. Pause or stop the route before relocalizing."
                )
                self._last_heartbeat_at = now
                return
            self._localization_requested = True
            self._localization_valid = False
            self._last_localization_at = 0.0
            self._last_localization_pose_at = 0.0
            self._localization_confident_samples = 0
            self._localization_usable_samples = 0
            self._localization_unconfident_samples = 0
            self._localization_seeded_from_initial_pose = True
            self._localization_xy_std_m = None
            self._localization_yaw_std_rad = None
            self._localization_degraded = False
            self._localization_anchor_map_pose = None
            self._localization_anchor_odom_pose = None
            self._accepted_map_pose_available = False
            self._localization_plausibility_fault = None
            self._localization_candidate_fault = None
            self._localization_failure_message = None
            self._localization_map_fault_samples = 0
            self._localization_confirmation_pose = None
            self._localization_stop_generation = (
                getattr(self, "_localization_stop_generation", 0) + 1
            )
            self._initial_pose_refinement_generation = (
                getattr(self, "_initial_pose_refinement_generation", 0) + 1
            )
            refinement_generation = self._initial_pose_refinement_generation
            self._initial_pose_refinement_active = True
            self._last_initial_pose = {
                "x": float(pose.position.x),
                "y": float(pose.position.y),
                "yaw": 0.0,
            }
            self._power_recent_log = (
                "> RViz initial position received. AMCL is matching stationary lidar scans."
            )
            self._last_heartbeat_at = now

        refinement_thread = threading.Thread(
            target=self._run_external_initial_pose_refinement,
            args=(msg, refinement_generation, now),
            daemon=True,
            name=f"{self.robot_id}-amcl-rviz-initial",
        )
        self._initial_pose_refinement_thread = refinement_thread
        refinement_thread.start()

    def _run_external_initial_pose_refinement(
        self,
        message: Any,
        generation: int,
        requested_at: float,
    ) -> None:
        client = getattr(self, "_set_initial_pose_client", None)
        if client is not None:
            try:
                self._set_amcl_initial_pose_sync(message)
            except Exception as exc:
                with self._lock:
                    if generation == self._initial_pose_refinement_generation:
                        self._power_recent_log = (
                            f"> AMCL topic pose was sent, but service acknowledgement failed: {exc}. "
                            "Continuing with stationary scan updates."
                        )
        self._run_initial_pose_refinement(generation, requested_at)

    def _handle_odom(self, msg: Any) -> None:
        twist = msg.twist.twist
        pose = msg.pose.pose
        linear_speed = float(twist.linear.x)
        angular_speed = float(twist.angular.z)
        odom_pose = {
            "x": float(pose.position.x),
            "y": float(pose.position.y),
            "yaw": _quaternion_to_yaw(
                pose.orientation.x,
                pose.orientation.y,
                pose.orientation.z,
                pose.orientation.w,
            ),
        }
        now = time.time()
        with self._lock:
            self._last_odom_at = now
            self._latest_odom_pose = odom_pose
            self._linear_speed = linear_speed
            self._angular_speed = angular_speed
            if (
                abs(linear_speed) >= self._config.stall_speed_epsilon
                or abs(angular_speed) >= self._config.stall_angular_speed_epsilon
            ):
                self._last_motion_at = now
            # Before localization, odometry gives the UI a useful relative pose.
            # Once localization has been requested, do not overwrite AMCL's map
            # pose with odom-frame coordinates while confidence is being acquired
            # or after confidence is lost.
            if not self._localization_requested:
                self._pose = dict(odom_pose)
            self._mark_pi_signal_locked(now)

    def _handle_filtered_scan(self, _msg: Any) -> None:
        now = time.time()
        with self._lock:
            self._last_filtered_scan_at = now
            self._mark_pi_signal_locked(now)

    def _handle_health_signal(self, name: str, msg: Any) -> None:
        if name not in self._health_values:
            return
        now = time.time()
        with self._lock:
            self._health_values[name] = bool(msg.data)
            self._health_updated_at[name] = now
            if name == "pi_ready":
                self._last_pi_ready_at = now
            self._mark_pi_signal_locked(now)

    def _mark_pi_signal_locked(self, timestamp: float) -> None:
        self._last_pi_signal_at = timestamp
        self._last_heartbeat_at = timestamp

    def _handle_battery(self, msg: Any) -> None:
        now = time.time()
        with self._lock:
            self._battery_v = float(getattr(msg, "voltage", 0.0) or 0.0)
            percentage = float(getattr(msg, "percentage", math.nan))
            self._power_battery_percent = (
                max(0.0, min(100.0, percentage * 100.0))
                if math.isfinite(percentage) and percentage >= 0.0
                else None
            )
            self._mark_pi_signal_locked(now)

    def _handle_joy_cmd(self, msg: Any) -> None:
        if abs(float(msg.linear.x)) < 1e-4 and abs(float(msg.angular.z)) < 1e-4:
            return
        now = time.time()
        with self._lock:
            self._last_joy_cmd_at = now

    def _handle_health_log(self, msg: Any) -> None:
        text = str(getattr(msg, "data", "") or "").strip()
        if not text:
            return
        now = time.time()
        with self._lock:
            self._power_recent_log = text
            self._mark_pi_signal_locked(now)

    def _handle_system_status(self, msg: Any) -> None:
        text = str(getattr(msg, "data", "") or "").strip()
        if not text:
            return
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            with self._lock:
                self._launcher_message = text
                self._last_heartbeat_at = time.time()
            return

        now = time.time()
        with self._lock:
            self._apply_launcher_status_locked(payload)
            request_id = str(payload.get("request_id", "") or "")
            pending = self._pending_launcher_requests.get(request_id)
            if pending is not None:
                pending["response"] = payload
                pending["event"].set()
            self._last_heartbeat_at = now

    def _apply_launcher_status_locked(self, payload: Dict[str, Any]) -> None:
        maps = payload.get("maps")
        if isinstance(maps, list):
            self._saved_map_names = sorted(str(name) for name in maps if str(name).strip())

        if "current_map" in payload:
            current_map = payload.get("current_map")
            next_map_name = str(current_map) if current_map else None
            if next_map_name != self._current_map_name:
                # An unsolicited supervisor-side profile change must not pair
                # a new map name with latched layers from the previous map.
                self._reset_localization_locked()
                self._clear_navigation_maps_locked()
            self._current_map_name = next_map_name
            self._keepout_map_required = self._map_profile_requires_keepout(
                self._current_map_name,
            )

        if "map_directory" in payload:
            map_directory = payload.get("map_directory")
            self._maps_directory = str(map_directory) if map_directory else None

        processes = payload.get("processes")
        if isinstance(processes, dict):
            self._launcher_processes = {str(key): bool(value) for key, value in processes.items()}

        if "last_command" in payload and payload.get("last_command"):
            self._last_system_command = str(payload["last_command"])

        if "message" in payload and payload.get("message"):
            self._launcher_message = str(payload["message"])

        preview_map = payload.get("preview_map")
        if isinstance(preview_map, dict) and preview_map.get("name"):
            map_name = str(preview_map["name"])
            self._map_preview_cache[map_name] = dict(preview_map)

        deleted_map = payload.get("deleted_map")
        if deleted_map:
            self._map_preview_cache.pop(str(deleted_map), None)

    def _publish_system_payload(self, payload: Dict[str, Any]) -> None:
        if self._system_command_publisher is None:
            raise RuntimeError("System command topic is not configured for this robot.")
        message = self._ros["String"]()
        message.data = json.dumps(payload)
        self._system_command_publisher.publish(message)

    def _send_launcher_request_sync(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        request_id = str(payload.get("request_id") or uuid.uuid4().hex)
        command = dict(payload)
        command["request_id"] = request_id
        event = threading.Event()

        with self._lock:
            self._pending_launcher_requests[request_id] = {"event": event}

        self._publish_system_payload(command)

        if not event.wait(timeout=self._config.launcher_request_timeout_s):
            with self._lock:
                self._pending_launcher_requests.pop(request_id, None)
            raise RuntimeError("Timed out waiting for launcher response.")

        with self._lock:
            pending = self._pending_launcher_requests.pop(request_id, None)
        if pending is None:
            raise RuntimeError("Launcher response was lost.")

        response = pending.get("response")
        if not isinstance(response, dict):
            raise RuntimeError("Launcher returned an invalid response.")
        if not bool(response.get("ok", False)):
            raise RuntimeError(str(response.get("message") or "Launcher request failed."))
        return response

    def _handle_nav_feedback(self, _feedback_msg: Any) -> None:
        return None

    def _goal_status_is_terminal(self, status: int) -> bool:
        return status in {
            getattr(self, "_goal_status_succeeded", 4),
            getattr(self, "_goal_status_canceled", 5),
            getattr(self, "_goal_status_aborted", 6),
        }

    def _handle_goal_result(
        self,
        future: Any,
        expected_goal_handle: Any = None,
    ) -> None:
        try:
            result = future.result()
            status = int(result.status)
            error: Optional[str] = (
                None
                if self._goal_status_is_terminal(status)
                else f"Nav2 returned non-terminal goal status {status}."
            )
        except Exception as exc:
            status = self._goal_status_unknown
            error = str(exc)
        with self._lock:
            completed_goal_handle = self._active_goal_handle
            if (
                expected_goal_handle is not None
                and completed_goal_handle is not expected_goal_handle
            ):
                return
            self._goal_result_status = status
            self._goal_result_error = error
            if error is None:
                self._active_goal_handle = None
                if (
                    getattr(self, "_cancel_future_goal_handle", None)
                    is completed_goal_handle
                ):
                    self._cancel_future_goal_handle = None
                    self._cancel_future_in_flight = False
            self._last_heartbeat_at = time.time()
            # Keep the handle transition and terminal/error event atomic.
            # A result-channel exception wakes the mission worker as a failure
            # but deliberately retains the active handle: transport failure is
            # not proof that the Pi stopped executing the goal.
            self._goal_done_event.set()
        if error is not None and completed_goal_handle is not None:
            self._cancel_late_goal_handle(
                completed_goal_handle,
                release_active_on_terminal=True,
            )

    def _navigation_goal_drain_pending_locked(self) -> bool:
        result_event = getattr(self, "_goal_done_event", None)
        return bool(
            getattr(self, "_active_goal_handle", None) is not None
            or getattr(self, "_goal_response_drains_pending", 0) > 0
            or (
                result_event is not None
                and hasattr(result_event, "is_set")
                and result_event.is_set()
            )
        )

    async def start_mission(self, mission_id: str, plan: List[str]) -> None:
        with self._lock:
            if self._state not in (MissionState.IDLE, MissionState.COMPLETED):
                raise RuntimeError("Robot already executing a mission.")
            mission_thread = getattr(self, "_mission_thread", None)
            if mission_thread and mission_thread.is_alive():
                raise RuntimeError("Mission worker is still active.")
            if not plan:
                raise RuntimeError("Mission plan is empty.")
            if not self._nav2_lifecycle_ready_locked():
                raise RuntimeError(
                    "Nav2 is still activating its planner, controller, and timers."
                )
            if not self._compute_localization_ok_locked(time.time()):
                raise RuntimeError("AMCL is not ready for navigation.")
            if not self._localization_candidate_is_map_free_locked():
                raise RuntimeError(
                    "The latest AMCL footprint overlaps a wall, unknown space, "
                    "or a keepout cell. Wait for a map-free localization update."
                )
            if not self._localization_has_motion_anchor_locked():
                raise RuntimeError(
                    "Waiting for one AMCL update synchronized with fresh odometry "
                    "before navigation can start."
                )
            if getattr(self, "_localization_stop_in_progress", False):
                raise RuntimeError(
                    "Localization safety stop is still completing; retry shortly."
                )
            if getattr(self, "_late_goal_stop_workers", 0) > 0:
                raise RuntimeError(
                    "A late navigation-goal safety stop is still completing; retry shortly."
                )
            if self._navigation_goal_drain_pending_locked():
                raise RuntimeError(
                    "The previous Nav2 goal has not reached and drained its terminal "
                    "result. Navigation remains locked."
                )
            self._current_mission_id = mission_id
            self._current_plan = list(plan)
            self._current_leg_index = 0
            self._current_destination = None
            self._current_goal_pose = None
            self._last_outcome = None
            self._cancel_requested = False
            self._pause_requested = False
            self._goal_result_status = None
            self._goal_result_error = None
            self._goal_done_event.clear()
            self._localization_safety_pause_active = False
            self._localization_safety_pause_reason = None
            # A mission is only en route after Nav2 accepts the goal. Until
            # then the worker may still be waiting for the Pi action server.
            self._state = MissionState.REQUESTED
            self._resume_event.set()

            self._mission_thread = threading.Thread(
                target=self._run_plan,
                args=(mission_id, list(plan)),
                daemon=True,
            )
            self._mission_thread.start()

    def _run_plan(self, mission_id: str, plan: List[str]) -> None:
        try:
            for index, destination in enumerate(plan):
                with self._lock:
                    if self._current_mission_id != mission_id:
                        return
                    self._current_leg_index = index
                    self._current_destination = destination

                while not self._shutdown_requested:
                    with self._lock:
                        if self._current_mission_id != mission_id:
                            return
                    if self._cancel_requested:
                        self._mark_completed_locked(MissionOutcome.CANCELED)
                        return

                    if not self._resume_event.wait(timeout=0.1):
                        continue

                    status = self._send_goal_and_wait(destination)
                    if status == "paused":
                        continue
                    if status == "succeeded":
                        with self._lock:
                            if self._current_mission_id != mission_id:
                                return
                            # A localization callback can pause or permanently
                            # fail the route as the action result arrives. That
                            # safety state wins over Nav2's success code.
                            interruption = self._navigation_interruption_locked()
                            terminal_failure = bool(
                                self._state == MissionState.COMPLETED
                                and self._last_outcome == MissionOutcome.FAILED
                            )
                            if terminal_failure:
                                return
                            if interruption == "paused":
                                continue
                            if interruption == "canceled":
                                self._mark_completed_locked(MissionOutcome.CANCELED)
                                return
                            if index + 1 == len(plan):
                                self._state = MissionState.COMPLETED
                                self._last_outcome = MissionOutcome.SUCCESS
                                self._power_recent_log = (
                                    "> Destination reached. The route completed successfully."
                                )
                                return
                        break
                    if status == "canceled":
                        self._mark_completed_locked(MissionOutcome.CANCELED)
                        return
                    if status == "aborted":
                        self._mark_completed_locked(MissionOutcome.ABORTED)
                        return
                    self._mark_completed_locked(MissionOutcome.FAILED)
                    return

        except Exception as exc:
            print(f"[Ros2RobotAdapter] mission worker error: {exc}")
            self._mark_completed_locked(MissionOutcome.FAILED)
            with self._lock:
                self._power_recent_log = (
                    f"!!! Navigation could not start: {exc} "
                    "No navigation goal is active."
                )

    def _send_goal_and_wait(self, destination_name: str) -> str:
        with self._lock:
            interruption = self._navigation_interruption_locked()
        if interruption is not None:
            return interruption

        dispatch_result = self._dispatch_goal(destination_name)
        if dispatch_result is not None:
            return dispatch_result

        while not self._shutdown_requested:
            if self._cancel_requested or self._pause_requested:
                self._cancel_active_goal()

            if self._goal_done_event.wait(timeout=0.1):
                break

        with self._lock:
            status = self._goal_result_status
            self._goal_result_status = None
            self._goal_done_event.clear()
            paused = self._pause_requested
            canceled = self._cancel_requested
            terminal_failure = bool(
                self._state == MissionState.COMPLETED
                and self._last_outcome == MissionOutcome.FAILED
            )

        if terminal_failure:
            return "failed"
        if canceled:
            return "canceled"
        if paused:
            return "paused"
        if status == self._goal_status_succeeded:
            return "succeeded"
        if status == self._goal_status_aborted:
            return "aborted"
        if status == self._goal_status_canceled:
            return "canceled"
        return "failed"

    def _navigation_interruption_locked(self) -> Optional[str]:
        if getattr(self, "_shutdown_requested", False):
            return "canceled"
        if getattr(self, "_cancel_requested", False):
            return "canceled"
        if getattr(self, "_state", MissionState.IDLE) == MissionState.COMPLETED:
            return "canceled"
        if (
            getattr(self, "_pause_requested", False)
            or getattr(self, "_state", MissionState.IDLE) == MissionState.PAUSED
            or getattr(self, "_localization_safety_pause_active", False)
            or getattr(self, "_localization_stop_in_progress", False)
            or getattr(self, "_late_goal_stop_workers", 0) > 0
            or not getattr(self, "_localization_valid", True)
            or not self._localization_candidate_is_map_free_locked()
        ):
            return "paused"
        return None

    def _cancel_late_goal_handle(
        self,
        goal_handle: Any,
        *,
        release_active_on_terminal: bool = False,
    ) -> None:
        if goal_handle is None or not getattr(goal_handle, "accepted", False):
            return
        handle_id = id(goal_handle)
        with self._lock:
            draining = getattr(self, "_late_goal_handles_draining", None)
            if draining is None:
                draining = set()
                self._late_goal_handles_draining = draining
            release_handles = getattr(
                self,
                "_late_goal_release_active_on_terminal",
                None,
            )
            if release_handles is None:
                release_handles = set()
                self._late_goal_release_active_on_terminal = release_handles
            if release_active_on_terminal:
                release_handles.add(handle_id)
            if handle_id in draining:
                return
            draining.add(handle_id)
            self._late_goal_stop_workers = (
                getattr(self, "_late_goal_stop_workers", 0) + 1
            )
        # This goal may have been accepted after an earlier pause worker sent
        # its final zero command. Count it as undrained before asking Nav2 to
        # cancel so Resume can never slip between those two operations.
        threading.Thread(
            target=self._stop_after_late_goal_acceptance,
            args=(goal_handle, handle_id),
            daemon=True,
            name=f"{getattr(self, 'robot_id', 'robot')}-late-goal-stop",
        ).start()

    def _stop_after_late_goal_acceptance(
        self,
        goal_handle: Any,
        handle_id: int,
    ) -> None:
        terminal_done = threading.Event()
        terminal_result: Dict[str, Any] = {}
        terminal_confirmed = False
        failure_logged = False

        def _record_terminal(done_future: Any) -> None:
            try:
                result = done_future.result()
                status = int(result.status)
                if not self._goal_status_is_terminal(status):
                    raise RuntimeError(
                        f"Nav2 returned non-terminal goal status {status}."
                    )
                terminal_result["status"] = status
            except Exception as exc:
                terminal_result["error"] = str(exc)
            finally:
                terminal_done.set()

        try:
            try:
                result_future = goal_handle.get_result_async()
                result_future.add_done_callback(_record_terminal)
            except Exception as exc:
                terminal_result["error"] = (
                    f"could not monitor the late Nav2 goal result: {exc}"
                )
                terminal_done.set()

            try:
                goal_handle.cancel_goal_async()
            except Exception as exc:
                terminal_result["cancel_error"] = str(exc)

            self._finish_navigation_stop(
                "Late accepted navigation goal safety stop.",
                preserve_power_log=True,
            )

            # A cancel request is asynchronous. Keep a zero-velocity backstop
            # active and keep Resume locked until Nav2 reports this exact goal
            # terminal. If the result channel fails, remain latched until the
            # robot service is restarted instead of guessing that motion ended.
            while not getattr(self, "_shutdown_requested", False):
                if terminal_done.wait(timeout=0.1):
                    if "error" not in terminal_result:
                        terminal_confirmed = True
                        break
                    if not failure_logged:
                        with self._lock:
                            self._power_recent_log = (
                                "!!! A late Nav2 goal could not be confirmed terminal. "
                                "Navigation remains locked; restart the robot service "
                                f"before retrying. ({terminal_result['error']})"
                            )
                        failure_logged = True
                self._publish_navigation_zero_once()
                if terminal_done.is_set():
                    time.sleep(0.1)
        finally:
            with self._lock:
                if terminal_confirmed or getattr(self, "_shutdown_requested", False):
                    release_handles = getattr(
                        self,
                        "_late_goal_release_active_on_terminal",
                        set(),
                    )
                    if (
                        terminal_confirmed
                        and handle_id in release_handles
                        and getattr(self, "_active_goal_handle", None) is goal_handle
                    ):
                        self._active_goal_handle = None
                        if (
                            getattr(self, "_cancel_future_goal_handle", None)
                            is goal_handle
                        ):
                            self._cancel_future_goal_handle = None
                            self._cancel_future_in_flight = False
                    self._late_goal_stop_workers = max(
                        0,
                        getattr(self, "_late_goal_stop_workers", 1) - 1,
                    )
                    getattr(
                        self,
                        "_late_goal_handles_draining",
                        set(),
                    ).discard(handle_id)
                    release_handles.discard(handle_id)
                else:
                    self._power_recent_log = (
                        "!!! A late Nav2 goal drain stopped unexpectedly. Navigation "
                        "remains locked until the robot service is restarted."
                    )

    def _start_unmonitored_goal_response_stop(self, reason: str) -> None:
        with self._lock:
            self._power_recent_log = (
                "!!! Mission Control could not monitor a Nav2 goal response. "
                "Navigation remains locked and held at zero until the robot "
                f"service is restarted. ({reason})"
            )
        threading.Thread(
            target=self._hold_navigation_zero_until_shutdown,
            daemon=True,
            name=f"{getattr(self, 'robot_id', 'robot')}-unmonitored-goal-stop",
        ).start()

    def _hold_navigation_zero_until_shutdown(self) -> None:
        self._finish_navigation_stop(
            "Unmonitored Nav2 goal safety stop.",
            preserve_power_log=True,
        )
        while not getattr(self, "_shutdown_requested", False):
            self._publish_navigation_zero_once()
            time.sleep(0.1)

    def _dispatch_goal(self, destination_name: str) -> Optional[str]:
        if self._navigate_client is None or self._node is None:
            raise RuntimeError("ROS 2 navigation client is not initialized.")
        if not self._navigate_client.wait_for_server(timeout_sec=0.0):
            with self._lock:
                self._power_recent_log = (
                    "> Destination received. Waiting for the Pi navigation servers to activate."
                )
                self._last_heartbeat_at = time.time()
            server_deadline = (
                time.monotonic() + max(0.0, self._config.action_server_timeout_s)
            )
            while True:
                with self._lock:
                    interruption = self._navigation_interruption_locked()
                if interruption is not None:
                    return interruption
                remaining = server_deadline - time.monotonic()
                if remaining <= 0.0:
                    raise RuntimeError(
                        "NavigateToPose action server did not become ready within "
                        f"{self._config.action_server_timeout_s:.0f} seconds."
                    )
                if self._navigate_client.wait_for_server(
                    timeout_sec=min(0.2, remaining)
                ):
                    break

        goal_msg, goal_pose = self._build_goal(destination_name)
        send_done = threading.Event()
        goal_response: Dict[str, Any] = {}
        goal_response_lock = threading.Lock()

        def _claim_goal_response_drain() -> bool:
            with goal_response_lock:
                if goal_response.get("drain_released"):
                    return False
                goal_response["drain_released"] = True
                return True

        def _release_goal_response_drain() -> None:
            if not _claim_goal_response_drain():
                return
            with self._lock:
                self._goal_response_drains_pending = max(
                    0,
                    getattr(self, "_goal_response_drains_pending", 1) - 1,
                )

        def _abandon_goal_response(reason: str) -> Optional[Any]:
            """Atomically hand an accepted late goal to the safety cancel path."""
            with goal_response_lock:
                goal_response["abandoned"] = (
                    goal_response.get("abandoned") or reason
                )
                goal_handle = goal_response.get("goal_handle")
                if goal_handle is None or goal_response.get("cancel_sent"):
                    return None
                goal_response["cancel_sent"] = True
                return goal_handle

        with self._lock:
            interruption = self._navigation_interruption_locked()
            if interruption is None:
                self._goal_response_drains_pending = (
                    getattr(self, "_goal_response_drains_pending", 0) + 1
                )
        if interruption is not None:
            return interruption

        try:
            future = self._navigate_client.send_goal_async(
                goal_msg,
                feedback_callback=self._handle_nav_feedback,
            )
        except Exception as exc:
            # A synchronous client exception still cannot prove the request
            # was never placed on the ROS graph. Preserve the response drain
            # and latch the zero-output stop conservatively.
            self._start_unmonitored_goal_response_stop(str(exc))
            raise

        def _on_goal_response(done_future: Any) -> None:
            try:
                goal_handle = done_future.result()
                if goal_handle is None:
                    raise RuntimeError("Nav2 returned an empty goal response.")
                with goal_response_lock:
                    goal_response["goal_handle"] = goal_handle
                    abandoned = goal_response.get("abandoned")
                with self._lock:
                    interruption = self._navigation_interruption_locked()
                if abandoned or interruption is not None:
                    cancel_handle = _abandon_goal_response(
                        str(abandoned or interruption)
                    )
                    if cancel_handle is not None:
                        self._cancel_late_goal_handle(cancel_handle)
                    _release_goal_response_drain()
                elif not getattr(goal_handle, "accepted", False):
                    _release_goal_response_drain()
            except Exception as exc:
                with goal_response_lock:
                    goal_response["error"] = exc
                # A response-channel exception does not prove that Nav2
                # rejected the request. Preserve the drain guard and latch a
                # zero-output stop until the service is restarted.
                self._start_unmonitored_goal_response_stop(str(exc))
            finally:
                send_done.set()

        try:
            future.add_done_callback(_on_goal_response)
        except Exception as exc:
            # The send request may already have reached Nav2. Keep its response
            # drain counter latched and hold navigation at zero because there
            # is no safe evidence that the request was rejected.
            self._start_unmonitored_goal_response_stop(str(exc))
            raise RuntimeError(
                f"Could not monitor the Nav2 goal response: {exc}"
            ) from exc
        while not send_done.wait(timeout=0.1):
            if self._shutdown_requested:
                cancel_handle = _abandon_goal_response("canceled")
                if cancel_handle is not None:
                    self._cancel_late_goal_handle(cancel_handle)
                    _release_goal_response_drain()
                return "canceled"
            with self._lock:
                interruption = self._navigation_interruption_locked()
            if interruption is not None:
                # The response callback remains installed and will cancel a
                # goal that Nav2 accepts after this dispatch was abandoned.
                cancel_handle = _abandon_goal_response(interruption)
                if cancel_handle is not None:
                    self._cancel_late_goal_handle(cancel_handle)
                    _release_goal_response_drain()
                return interruption

        with goal_response_lock:
            response_error = goal_response.get("error")
            goal_handle = goal_response.get("goal_handle")
            abandoned = goal_response.get("abandoned")

        if response_error is not None:
            raise RuntimeError(f"Failed to send goal to Nav2: {response_error}")

        if abandoned:
            cancel_handle = _abandon_goal_response(str(abandoned))
            if cancel_handle is not None:
                self._cancel_late_goal_handle(cancel_handle)
            _release_goal_response_drain()
            return str(abandoned)
        if goal_handle is None or not goal_handle.accepted:
            raise RuntimeError(f"Nav2 rejected destination '{destination_name}'.")

        with self._lock:
            interruption = self._navigation_interruption_locked()
            if interruption is None:
                release_response_drain = _claim_goal_response_drain()
                self._active_goal_handle = goal_handle
                if release_response_drain:
                    self._goal_response_drains_pending = max(
                        0,
                        getattr(self, "_goal_response_drains_pending", 1) - 1,
                    )
                self._goal_result_status = None
                self._goal_result_error = None
                self._goal_active_since = time.time()
                self._last_motion_at = self._goal_active_since
                self._current_goal_pose = goal_pose
                self._last_goal_pose = dict(goal_pose)
                self._state = MissionState.EN_ROUTE
                self._last_heartbeat_at = self._goal_active_since
                self._power_recent_log = (
                    f"> Nav2 accepted destination '{destination_name}'. Planning the route."
                )
        if interruption is not None:
            self._cancel_late_goal_handle(goal_handle)
            _release_goal_response_drain()
            return interruption

        try:
            result_future = goal_handle.get_result_async()
            result_future.add_done_callback(
                lambda done_future, handle=goal_handle: self._handle_goal_result(
                    done_future,
                    handle,
                )
            )
        except Exception as exc:
            # Nav2 accepted this goal, so a missing result monitor is an
            # undrained physical goal—not an ordinary dispatch failure.
            self._cancel_late_goal_handle(
                goal_handle,
                release_active_on_terminal=True,
            )
            with self._lock:
                self._power_recent_log = (
                    "!!! Nav2 accepted the destination but Mission Control could "
                    "not monitor its result. The goal is being canceled and "
                    "navigation remains locked."
                )
            raise RuntimeError(
                f"Could not monitor the accepted Nav2 goal result: {exc}"
            ) from exc
        return None

    def _build_goal(self, destination_name: str) -> Any:
        destinations, _ = self._dest_config.get()
        destination = destinations.get(destination_name)
        if destination is None:
            raise RuntimeError(f"Unknown destination '{destination_name}'.")

        pose = destination.pose
        x = float(pose.get("x", 0.0))
        y = float(pose.get("y", 0.0))
        yaw = float(pose.get("yaw", 0.0))
        qz, qw = _yaw_to_quaternion(yaw)

        pose_msg = self._ros["PoseStamped"]()
        pose_msg.header.frame_id = self._config.map_frame
        pose_msg.header.stamp = self._node.get_clock().now().to_msg()
        pose_msg.pose.position.x = x
        pose_msg.pose.position.y = y
        pose_msg.pose.position.z = 0.0
        pose_msg.pose.orientation.x = 0.0
        pose_msg.pose.orientation.y = 0.0
        pose_msg.pose.orientation.z = qz
        pose_msg.pose.orientation.w = qw

        goal_msg = self._ros["NavigateToPose"].Goal()
        goal_msg.pose = pose_msg
        return goal_msg, {"x": x, "y": y, "yaw": yaw}

    async def pause(self) -> None:
        with self._lock:
            if self._state == MissionState.COMPLETED:
                return
            self._pause_requested = True
            self._state = MissionState.PAUSED
            self._resume_event.clear()
        self._cancel_active_goal()
        await asyncio.to_thread(self._finish_navigation_stop, "Navigation paused.")

    async def resume(self) -> None:
        with self._lock:
            if self._state == MissionState.COMPLETED:
                return
            if not self._nav2_lifecycle_ready_locked():
                raise RuntimeError(
                    "Nav2 is still activating its planner, controller, and timers."
                )
            if not self._compute_localization_ok_locked(time.time()):
                raise RuntimeError(
                    "AMCL localization is not ready. Move to clearer space or "
                    "set the initial position again before resuming."
                )
            if not self._localization_candidate_is_map_free_locked():
                raise RuntimeError(
                    "The latest AMCL footprint is not map-free. Wait for a clear "
                    "localization update before resuming."
                )
            if not self._localization_has_motion_anchor_locked():
                raise RuntimeError(
                    "Waiting for AMCL and odometry to establish a motion anchor "
                    "before resuming."
                )
            if getattr(self, "_localization_stop_in_progress", False):
                raise RuntimeError(
                    "Localization safety stop is still completing; retry shortly."
                )
            if getattr(self, "_late_goal_stop_workers", 0) > 0:
                raise RuntimeError(
                    "A late navigation-goal safety stop is still completing; retry shortly."
                )
            if self._navigation_goal_drain_pending_locked():
                raise RuntimeError(
                    "The previous Nav2 goal cancellation is still being drained; "
                    "retry after Nav2 reports it terminal."
                )
            self._pause_requested = False
            self._state = MissionState.EN_ROUTE
            self._localization_safety_pause_active = False
            self._localization_safety_pause_reason = None
            self._resume_event.set()

    async def cancel(self) -> None:
        with self._lock:
            self._cancel_requested = True
            self._pause_requested = False
            self._resume_event.set()
            self._state = MissionState.COMPLETED
            self._last_outcome = MissionOutcome.CANCELED
            self._localization_safety_pause_active = False
            self._localization_safety_pause_reason = None
        self._cancel_active_goal()
        await asyncio.to_thread(self._finish_navigation_stop, "Navigation stopped.")

    def _cancel_active_goal(self) -> None:
        with self._lock:
            if self._active_goal_handle is None:
                return
            goal_handle = self._active_goal_handle
            if getattr(self, "_cancel_future_goal_handle", None) is goal_handle:
                return
            self._cancel_future_goal_handle = goal_handle
            self._cancel_future_in_flight = True

        try:
            cancel_future = goal_handle.cancel_goal_async()
        except Exception as exc:
            with self._lock:
                if getattr(self, "_cancel_future_goal_handle", None) is goal_handle:
                    self._cancel_future_goal_handle = None
                    self._cancel_future_in_flight = False
                self._power_recent_log = (
                    "> Nav2 goal cancellation could not be sent yet; the safety "
                    f"stop remains active and will retry. ({exc})"
                )
            return

        def _clear_cancel_flag(_done_future: Any) -> None:
            with self._lock:
                # A delayed response for an old terminal goal must not clear
                # the in-flight cancel guard belonging to a newer goal.
                if getattr(self, "_cancel_future_goal_handle", None) is goal_handle:
                    self._cancel_future_in_flight = False

        cancel_future.add_done_callback(_clear_cancel_flag)

    def _finish_navigation_stop(
        self,
        reason: str,
        *,
        localization_generation: Optional[int] = None,
        preserve_power_log: bool = False,
    ) -> None:
        requested_at = time.time()
        publisher = getattr(self, "_navigation_zero_publisher", None)
        ros_modules = getattr(self, "_ros", {})
        twist_type = ros_modules.get("Twist") if isinstance(ros_modules, dict) else None
        config = self._config
        zero_count = max(1, int(getattr(config, "stop_zero_count", 8)))
        zero_interval_s = max(
            0.0,
            float(getattr(config, "stop_zero_interval_s", 0.05)),
        )

        published = 0
        if publisher is not None and twist_type is not None:
            for index in range(zero_count):
                if self._publish_navigation_zero_once():
                    published += 1
                if zero_interval_s > 0.0 and index + 1 < zero_count:
                    time.sleep(zero_interval_s)

        confirmed = self._wait_for_stopped_odometry(requested_at)
        if confirmed is True:
            message_text = f"{reason} Stationary odometry confirmed."
        elif confirmed is False:
            message_text = (
                f"{reason} Zero commands were sent, but odometry did not confirm "
                "a stop before the timeout."
            )
        else:
            message_text = (
                f"{reason} {published} bounded zero commands were sent; "
                "fresh odometry was unavailable for confirmation."
            )
        with self._lock:
            self._last_stop_status = {
                "requested_at": requested_at,
                "confirmed": confirmed,
                "zero_commands_sent": published,
                "message": message_text,
            }
            if not preserve_power_log and (
                localization_generation is None
                or localization_generation
                == getattr(
                    self,
                    "_localization_stop_generation",
                    localization_generation,
                )
            ):
                self._power_recent_log = f"> {message_text}"

    def _publish_navigation_zero_once(self) -> bool:
        publisher = getattr(self, "_navigation_zero_publisher", None)
        ros_modules = getattr(self, "_ros", {})
        twist_type = ros_modules.get("Twist") if isinstance(ros_modules, dict) else None
        if publisher is None or twist_type is None:
            return False
        message = twist_type()
        message.linear.x = 0.0
        message.linear.y = 0.0
        message.linear.z = 0.0
        message.angular.x = 0.0
        message.angular.y = 0.0
        message.angular.z = 0.0
        publisher.publish(message)
        return True

    def _wait_for_stopped_odometry(self, requested_at: float) -> Optional[bool]:
        timeout_s = max(
            0.0,
            float(getattr(self._config, "stop_confirmation_timeout_s", 1.5)),
        )
        deadline = time.monotonic() + timeout_s
        saw_fresh_odom = False
        while True:
            with self._lock:
                odom_at = float(getattr(self, "_last_odom_at", 0.0))
                linear_speed = abs(float(getattr(self, "_linear_speed", 0.0)))
                angular_speed = abs(float(getattr(self, "_angular_speed", 0.0)))
                if odom_at >= requested_at:
                    saw_fresh_odom = True
                    if (
                        linear_speed < self._config.stall_speed_epsilon
                        and angular_speed < self._config.stall_angular_speed_epsilon
                    ):
                        return True
            if time.monotonic() >= deadline:
                return False if saw_fresh_odom else None
            time.sleep(0.05)

    def _mark_completed_locked(self, outcome: MissionOutcome) -> None:
        with self._lock:
            # Goal cancellation is also how pause/localization-stop interrupts
            # Nav2. A late canceled result must never overwrite a terminal
            # failure (or an explicit operator cancellation) already recorded
            # by the thread that initiated the stop.
            if (
                self._state == MissionState.COMPLETED
                and self._last_outcome is not None
            ):
                return
            self._state = MissionState.COMPLETED
            self._last_outcome = outcome
            self._localization_safety_pause_active = False
            self._localization_safety_pause_reason = None
            if outcome == MissionOutcome.ABORTED:
                self._power_recent_log = (
                    "!!! Nav2 aborted the route. No navigation goal is active; "
                    "check the Pi navigation status before retrying."
                )
            elif outcome == MissionOutcome.FAILED:
                self._power_recent_log = (
                    "!!! Navigation failed. No navigation goal is active."
                )
            elif outcome == MissionOutcome.CANCELED:
                self._power_recent_log = "> Route canceled. The robot is stopped."

    async def reset_to_idle(self) -> None:
        with self._lock:
            self._cancel_requested = True
            self._pause_requested = False
            self._resume_event.set()
        self._cancel_active_goal()

        if (
            self._mission_thread
            and self._mission_thread.is_alive()
            and threading.current_thread() is not self._mission_thread
        ):
            self._mission_thread.join(timeout=1.0)

        with self._lock:
            mission_worker_active = bool(
                self._mission_thread
                and self._mission_thread.is_alive()
                and threading.current_thread() is not self._mission_thread
            )
            if (
                mission_worker_active
                or self._navigation_goal_drain_pending_locked()
                or getattr(self, "_late_goal_stop_workers", 0) > 0
                or getattr(self, "_localization_stop_in_progress", False)
            ):
                self._power_recent_log = (
                    "> Waiting for the previous Nav2 goal and safety stop to drain "
                    "before returning the robot to idle."
                )
                return
            self._state = MissionState.IDLE
            self._current_mission_id = None
            self._current_plan = []
            self._current_leg_index = 0
            self._current_destination = None
            self._current_goal_pose = None
            self._last_outcome = None
            self._cancel_requested = False
            self._pause_requested = False
            self._goal_result_status = None
            self._goal_result_error = None
            self._goal_done_event.clear()
            self._active_goal_handle = None
            self._localization_safety_pause_active = False
            self._localization_safety_pause_reason = None

    def snapshot(self) -> RobotTelemetry:
        with self._lock:
            now = time.time()
            self._mode = self._compute_mode_locked(now)
            self._connection_ok = self._compute_connection_ok_locked(now)
            self._localization_valid = self._compute_localization_ok_locked(now)
            self._blocked = self._compute_blocked_locked(now, self._mode)
            self._obstacle_stop = self._blocked
            return RobotTelemetry(
                robot_id=self.robot_id,
                state=self._state,
                mode=self._mode,
                current_mission_id=self._current_mission_id,
                last_heartbeat_at=float(
                    getattr(self, "_last_pi_signal_at", self._last_heartbeat_at)
                ),
                connection_ok=self._connection_ok,
                localization_valid=self._localization_valid,
                obstacle_stop=self._obstacle_stop,
                blocked=self._blocked,
                battery_v=self._battery_v,
                pose=dict(self._pose),
                outcome=self._last_outcome,
            )

    async def send_manual_drive_command(self, linear: float, angular: float) -> None:
        if self._manual_command_publisher is None:
            raise RuntimeError("Manual drive topic is not configured for this robot.")

        linear = _clamp(float(linear), -1.0, 1.0)
        angular = _clamp(float(angular), -1.0, 1.0)
        is_motion_command = abs(linear) >= 1e-4 or abs(angular) >= 1e-4

        if is_motion_command:
            with self._lock:
                manual_drive = self._manual_drive_readiness_locked(time.time())
            if not manual_drive["ready"]:
                raise RuntimeError(manual_drive["message"])

        message = self._ros["Twist"]()
        message.linear.x = linear
        message.linear.y = 0.0
        message.linear.z = 0.0
        message.angular.x = 0.0
        message.angular.y = 0.0
        message.angular.z = angular
        self._manual_command_publisher.publish(message)

        with self._lock:
            self._last_heartbeat_at = time.time()
            if is_motion_command:
                self._last_joy_cmd_at = self._last_heartbeat_at
                self._power_recent_log = f"> Manual drive command: linear={linear:.2f}, angular={angular:.2f}"
            else:
                self._power_recent_log = "> Manual drive stopped."

    async def localize(self) -> Dict[str, Any]:
        if self._global_localization_client is None:
            raise RuntimeError("AMCL global localization service is not configured for this robot.")

        return await asyncio.to_thread(self._run_global_localization_search)

    def _run_global_localization_search(self) -> Dict[str, Any]:
        client = self._global_localization_client
        if client is None:
            raise RuntimeError("AMCL global localization service is not configured for this robot.")

        with self._lock:
            self._power_recent_log = "> Waiting for AMCL global localization."

        if not client.wait_for_service(timeout_sec=3.0):
            raise RuntimeError("AMCL global localization service is unavailable.")

        requested_at = time.time()
        with self._lock:
            self._reset_localization_locked()
            self._localization_requested = True
            self._initial_pose_refinement_active = True
            self._power_recent_log = (
                "> Starting stationary AMCL global search. The robot will not move."
            )

        request = self._ros["Empty"].Request()
        try:
            future = client.call_async(request)
        except Exception as exc:
            with self._lock:
                self._reset_localization_locked()
            raise RuntimeError(f"Could not start AMCL global localization: {exc}") from exc
        done = threading.Event()
        response: Dict[str, Any] = {}

        def _on_done(done_future: Any) -> None:
            try:
                response["result"] = done_future.result()
            except Exception as exc:
                response["error"] = exc
            finally:
                done.set()

        future.add_done_callback(_on_done)
        if not done.wait(timeout=3.0):
            with self._lock:
                self._reset_localization_locked()
            raise RuntimeError("Timed out calling AMCL global localization.")
        if "error" in response:
            with self._lock:
                self._reset_localization_locked()
            raise RuntimeError(f"AMCL global localization failed: {response['error']}")

        nomotion_client = self._nomotion_update_client
        service_error: Optional[str] = None
        if nomotion_client is None:
            service_error = "AMCL no-motion update service is not configured"
        else:
            try:
                if not nomotion_client.wait_for_service(timeout_sec=3.0):
                    service_error = "AMCL no-motion update service is unavailable"
            except Exception as exc:
                service_error = f"could not check AMCL no-motion update service: {exc}"

        updates_requested = 0
        if service_error is None:
            for _index in range(max(1, int(self._config.localization_nomotion_updates))):
                service_error = self._request_nomotion_update_sync(nomotion_client)
                if service_error is not None:
                    break
                updates_requested += 1
                with self._lock:
                    if self._localization_valid and self._last_localization_at >= requested_at:
                        break
                interval_s = max(0.0, float(self._config.localization_nomotion_interval_s))
                if interval_s > 0.0:
                    time.sleep(interval_s)

        if service_error is not None:
            with self._lock:
                self._power_recent_log = (
                    f"> {service_error}. Waiting for AMCL's normal stationary scan updates."
                )

        confirmation_deadline = (
            time.monotonic()
            + max(0.0, float(self._config.localization_confirmation_timeout_s))
        )
        while time.monotonic() < confirmation_deadline:
            with self._lock:
                if self._localization_valid and self._last_localization_at >= requested_at:
                    self._last_heartbeat_at = time.time()
                    self._last_joy_cmd_at = 0.0
                    self._initial_pose_refinement_active = False
                    self._power_recent_log = "> Stationary AMCL global localization is ready."
                    return {
                        "robot_id": self.robot_id,
                        "ok": True,
                        "message": (
                            "Stationary global localization complete. "
                            "Verify the robot marker before navigating."
                        ),
                        "nomotion_updates": updates_requested,
                        "robot_moved": False,
                    }
            time.sleep(0.05)

        with self._lock:
            candidate_fault = getattr(
                self,
                "_localization_candidate_fault",
                None,
            )
            self._reset_localization_locked()
        candidate_detail = (
            f" Last AMCL result was rejected: {candidate_fault}"
            if candidate_fault
            else ""
        )
        raise RuntimeError(
            "The stationary localization search finished, but AMCL did not reach a usable pose. "
            "Set an approximate position to narrow the search, then check /map, "
            "/scan_filtered, odometry, and map-to-scan alignment."
            f"{candidate_detail}"
        )

    def _publish_manual_twist(self, linear: float, angular: float) -> None:
        if self._manual_command_publisher is None:
            raise RuntimeError("Manual drive topic is not configured for this robot.")
        message = self._ros["Twist"]()
        message.linear.x = float(linear)
        message.linear.y = 0.0
        message.linear.z = 0.0
        message.angular.x = 0.0
        message.angular.y = 0.0
        message.angular.z = float(angular)
        self._manual_command_publisher.publish(message)

    async def send_system_command(self, command: str, map_name: Optional[str] = None) -> None:
        normalized = command.strip().lower()
        if normalized not in {"launch_robot", "launch_slam", "launch_nav", "save_map", "kill_all"}:
            raise ValueError(f"Unsupported system command: {command}")
        if self._external_launcher_enabled():
            raise RuntimeError(
                "The central supervisor owns ROS processes. Use the central start "
                "and stop commands instead of launching processes from the browser."
            )
        if self._local_launcher_enabled():
            response = await asyncio.to_thread(self._send_local_launcher_command, normalized, map_name)
            with self._lock:
                self._apply_launcher_status_locked(response)
                self._power_recent_log = str(response.get("message") or f"> System command sent: {normalized}")
            return
        if normalized == "launch_nav":
            if not map_name:
                raise RuntimeError("Select a saved map before launching navigation.")
            with self._lock:
                previous_map_name = self._current_map_name
                self._reset_localization_locked()
                self._clear_navigation_maps_locked()
                # Mark the expected profile before the launcher responds. A
                # new transient-local map can arrive before its status reply;
                # preselecting it prevents that new layer from being mistaken
                # for stale data and cleared again.
                self._current_map_name = Path(str(map_name)).stem
                self._keepout_map_required = self._map_profile_requires_keepout(
                    self._current_map_name,
                )
            try:
                response = await asyncio.to_thread(
                    self._send_launcher_request_sync,
                    {"action": normalized, "map_name": map_name},
                )
            except Exception:
                with self._lock:
                    self._current_map_name = previous_map_name
                    self._keepout_map_required = self._map_profile_requires_keepout(
                        previous_map_name,
                    )
                raise
            with self._lock:
                self._apply_launcher_status_locked(response)
                self._last_system_command = normalized
                self._power_recent_log = f"> Navigation launched with map: {map_name}"
                self._last_heartbeat_at = time.time()
            return

        if normalized in {"launch_slam", "kill_all"}:
            with self._lock:
                self._clear_navigation_maps_locked()
        self._publish_system_payload({"action": normalized})

        with self._lock:
            self._last_system_command = normalized
            self._power_recent_log = f"> System command sent: {normalized}"
            self._last_heartbeat_at = time.time()

    async def set_initial_pose(self, x: float, y: float, yaw: float) -> None:
        if (
            self._node is None
            or (
                getattr(self, "_set_initial_pose_client", None) is None
                and self._initial_pose_publisher is None
            )
        ):
            raise RuntimeError("AMCL initial pose input is not configured for this robot.")

        with self._lock:
            seed_fault = _localization_seed_maps_fault(
                getattr(self, "_map_snapshot", None),
                getattr(self, "_keepout_map_snapshot", None),
                float(x),
                float(y),
                require_keepout=self._requires_keepout_map(),
            )
        if seed_fault:
            raise RuntimeError(
                f"Initial position rejected: {seed_fault} Select a known-free "
                "point on the navigation map."
            )

        # Heading is intentionally only a neutral mean. The wide yaw
        # covariance below tells AMCL to determine orientation from lidar.
        # Keep the stamp at zero so AMCL uses the newest odom transform instead
        # of asking for a transform a few milliseconds into the future.
        seed_yaw = 0.0
        qz, qw = _yaw_to_quaternion(seed_yaw)
        message = self._ros["PoseWithCovarianceStamped"]()
        message.header.frame_id = self._config.map_frame
        message.pose.pose.position.x = float(x)
        message.pose.pose.position.y = float(y)
        message.pose.pose.position.z = 0.0
        message.pose.pose.orientation.x = 0.0
        message.pose.pose.orientation.y = 0.0
        message.pose.pose.orientation.z = qz
        message.pose.pose.orientation.w = qw
        message.pose.covariance = [0.0] * 36
        message.pose.covariance[0] = max(0.0, self._config.initial_pose_xy_covariance)
        message.pose.covariance[7] = max(0.0, self._config.initial_pose_xy_covariance)
        message.pose.covariance[35] = max(0.0, self._config.initial_pose_yaw_covariance)

        with self._lock:
            if self._state in {
                MissionState.REQUESTED,
                MissionState.EN_ROUTE,
                MissionState.RETURNING,
            }:
                raise RuntimeError("Pause or stop navigation before changing the initial position.")
            self._localization_requested = True
            self._localization_valid = False
            self._last_localization_at = 0.0
            self._last_localization_pose_at = 0.0
            self._localization_confident_samples = 0
            self._localization_usable_samples = 0
            self._localization_unconfident_samples = 0
            self._localization_seeded_from_initial_pose = True
            self._localization_xy_std_m = None
            self._localization_yaw_std_rad = None
            self._localization_degraded = False
            self._localization_anchor_map_pose = None
            self._localization_anchor_odom_pose = None
            self._accepted_map_pose_available = False
            self._localization_plausibility_fault = None
            self._localization_candidate_fault = None
            self._localization_failure_message = None
            self._localization_map_fault_samples = 0
            self._localization_confirmation_pose = None
            self._localization_stop_generation = (
                getattr(self, "_localization_stop_generation", 0) + 1
            )
            self._initial_pose_refinement_generation = (
                getattr(self, "_initial_pose_refinement_generation", 0) + 1
            )
            refinement_generation = self._initial_pose_refinement_generation
            self._initial_pose_refinement_active = True
            requested_at = time.time()
            self._last_initial_pose = {
                "x": float(x),
                "y": float(y),
                "yaw": seed_yaw,
            }
            self._power_recent_log = (
                "> Sending the approximate position to AMCL."
            )
            self._last_heartbeat_at = requested_at

        try:
            await asyncio.to_thread(self._set_amcl_initial_pose_sync, message)
        except Exception as exc:
            with self._lock:
                if refinement_generation == self._initial_pose_refinement_generation:
                    self._reset_localization_locked()
                    self._power_recent_log = f"!!! Initial localization could not start: {exc}"
            raise

        with self._lock:
            if refinement_generation != self._initial_pose_refinement_generation:
                return
            self._power_recent_log = (
                "> AMCL accepted the approximate position and is matching stationary lidar scans."
            )
            self._last_heartbeat_at = time.time()

        refinement_thread = threading.Thread(
            target=self._run_initial_pose_refinement,
            args=(refinement_generation, requested_at),
            daemon=True,
            name=f"{self.robot_id}-amcl-stationary",
        )
        self._initial_pose_refinement_thread = refinement_thread
        refinement_thread.start()

    def _set_amcl_initial_pose_sync(self, message: Any) -> None:
        client = getattr(self, "_set_initial_pose_client", None)
        if client is None:
            if self._initial_pose_publisher is None:
                raise RuntimeError("AMCL initial pose input is not configured.")
            self._initial_pose_publisher.publish(message)
            return

        if not client.wait_for_service(timeout_sec=8.0):
            raise RuntimeError(
                "AMCL /set_initial_pose is unavailable. Start navigation and wait for AMCL."
            )

        request = self._ros["SetInitialPose"].Request()
        request.pose = message
        try:
            future = client.call_async(request)
        except Exception as exc:
            raise RuntimeError(f"AMCL rejected the initial pose request: {exc}") from exc

        done = threading.Event()
        response: Dict[str, Any] = {}

        def _on_done(done_future: Any) -> None:
            try:
                response["result"] = done_future.result()
            except Exception as exc:
                response["error"] = exc
            finally:
                done.set()

        future.add_done_callback(_on_done)
        if not done.wait(timeout=8.0):
            raise RuntimeError("Timed out waiting for AMCL to accept the initial pose.")
        if "error" in response:
            raise RuntimeError(f"AMCL initial pose request failed: {response['error']}")

    def _run_initial_pose_refinement(
        self,
        generation: int,
        requested_at: float,
    ) -> None:
        localized = False
        service_error: Optional[str] = None
        client = self._nomotion_update_client
        update_count = max(1, int(self._config.localization_nomotion_updates))
        update_interval_s = max(0.0, float(self._config.localization_nomotion_interval_s))

        with self._lock:
            if generation != self._initial_pose_refinement_generation:
                return
            self._power_recent_log = (
                "> AMCL is matching stationary lidar scans to the map. The robot will not move."
            )

        if client is None:
            service_error = "AMCL no-motion update service is not configured"
        else:
            try:
                if not client.wait_for_service(timeout_sec=3.0):
                    service_error = "AMCL no-motion update service is unavailable"
            except Exception as exc:
                service_error = f"could not check AMCL no-motion update service: {exc}"

        if service_error is None:
            for _index in range(update_count):
                if self._shutdown_requested:
                    break
                with self._lock:
                    if generation != self._initial_pose_refinement_generation:
                        return
                    if self._localization_valid and self._last_localization_at >= requested_at:
                        localized = True
                        break
                service_error = self._request_nomotion_update_sync(client)
                if service_error is not None:
                    break
                if update_interval_s > 0.0:
                    time.sleep(update_interval_s)

        if service_error is not None:
            with self._lock:
                if generation == self._initial_pose_refinement_generation:
                    self._power_recent_log = (
                        f"> {service_error}. Waiting for AMCL's normal stationary scan updates."
                    )

        if not localized:
            confirmation_deadline = (
                time.monotonic()
                + max(0.0, float(self._config.localization_confirmation_timeout_s))
            )
            while time.monotonic() < confirmation_deadline and not self._shutdown_requested:
                with self._lock:
                    if generation != self._initial_pose_refinement_generation:
                        return
                    if self._localization_valid and self._last_localization_at >= requested_at:
                        localized = True
                        break
                time.sleep(0.05)

        with self._lock:
            if generation != self._initial_pose_refinement_generation:
                return
            self._initial_pose_refinement_active = False
            self._last_joy_cmd_at = 0.0
            self._last_heartbeat_at = time.time()
            if localized:
                if getattr(self, "_localization_safety_pause_active", False):
                    self._power_recent_log = (
                        "> AMCL localization is ready again. The safety-paused "
                        "route will remain paused until an operator resumes it."
                    )
                elif self._localization_degraded:
                    self._power_recent_log = (
                        "> AMCL pose is usable with reduced confidence. "
                        "Navigation is unlocked and AMCL will keep refining while moving."
                    )
                else:
                    self._power_recent_log = (
                        "> AMCL position and heading are ready. Navigation is unlocked."
                    )
                return

            candidate_fault = getattr(
                self,
                "_localization_candidate_fault",
                None,
            )
            if candidate_fault:
                self._localization_failure_message = (
                    f"AMCL localization failed: {candidate_fault} Retry by setting "
                    "the initial position again, or manually move the robot to a "
                    "clearer space away from nearby obstacles before retrying."
                )
                self._power_recent_log = f"!!! {self._localization_failure_message}"
                return

            xy_text = (
                f"{self._localization_xy_std_m:.2f} m"
                if self._localization_xy_std_m is not None
                else "no pose received"
            )
            yaw_text = (
                f"{math.degrees(self._localization_yaw_std_rad):.0f} deg"
                if self._localization_yaw_std_rad is not None
                else "no heading received"
            )
            self._localization_failure_message = (
                "AMCL localization failed because it remained uncertain after "
                "stationary scan matching "
                f"(position {xy_text}, heading {yaw_text}). Navigation remains locked; "
                "retry by setting the initial position again, or manually move the "
                "robot to a clearer space away from nearby obstacles before retrying."
            )
            self._power_recent_log = f"!!! {self._localization_failure_message}"

    def _request_nomotion_update_sync(self, client: Any) -> Optional[str]:
        try:
            future = client.call_async(self._ros["Empty"].Request())
        except Exception as exc:
            return f"AMCL no-motion update request failed: {exc}"

        done = threading.Event()
        response: Dict[str, Any] = {}

        def _on_done(done_future: Any) -> None:
            try:
                response["result"] = done_future.result()
            except Exception as exc:
                response["error"] = exc
            finally:
                done.set()

        future.add_done_callback(_on_done)
        if not done.wait(timeout=1.0):
            return "AMCL no-motion update request timed out"
        if "error" in response:
            return f"AMCL no-motion update request failed: {response['error']}"
        return None

    async def set_goal_pose(self, x: float, y: float, yaw: float) -> None:
        # This is UI/navigation context only. Publishing /goal_pose here would
        # command bt_navigator before the tracked mission action is dispatched.
        with self._lock:
            self._last_goal_pose = {"x": float(x), "y": float(y), "yaw": float(yaw)}
            self._power_recent_log = (
                f"> Destination selected: x={x:.2f}, y={y:.2f}, yaw={yaw:.2f}. "
                "Waiting for mission dispatch."
            )
            self._last_heartbeat_at = time.time()

    async def save_map(self, map_name: str) -> Dict[str, Any]:
        if self._catalog_launcher_enabled():
            response = await asyncio.to_thread(self._save_local_map, map_name)
            with self._lock:
                self._apply_launcher_status_locked(response)
                self._power_recent_log = f"> Saved map: {map_name}"
                self._last_heartbeat_at = time.time()
            return response
        response = await asyncio.to_thread(
            self._send_launcher_request_sync,
            {"action": "save_map", "map_name": map_name},
        )
        with self._lock:
            self._apply_launcher_status_locked(response)
            self._power_recent_log = f"> Saved map: {map_name}"
            self._last_heartbeat_at = time.time()
        return response

    async def delete_map(self, map_name: str) -> Dict[str, Any]:
        if self._catalog_launcher_enabled():
            response = await asyncio.to_thread(self._delete_local_map, map_name)
            with self._lock:
                self._apply_launcher_status_locked(response)
                self._power_recent_log = f"> Deleted map: {map_name}"
                self._last_heartbeat_at = time.time()
            return response
        response = await asyncio.to_thread(
            self._send_launcher_request_sync,
            {"action": "delete_map", "map_name": map_name},
        )
        with self._lock:
            self._apply_launcher_status_locked(response)
            self._power_recent_log = f"> Deleted map: {map_name}"
            self._last_heartbeat_at = time.time()
        return response

    async def load_map_preview(self, map_name: str) -> Dict[str, Any]:
        if self._catalog_launcher_enabled():
            response = await asyncio.to_thread(self._load_local_map_preview_response, map_name)
            preview_map = response.get("preview_map")
            if not isinstance(preview_map, dict):
                raise RuntimeError("Map preview data is unavailable.")
            with self._lock:
                self._apply_launcher_status_locked(response)
                self._last_heartbeat_at = time.time()
            return dict(preview_map)
        response = await asyncio.to_thread(
            self._send_launcher_request_sync,
            {"action": "load_map_preview", "map_name": map_name},
        )
        preview_map = response.get("preview_map")
        if not isinstance(preview_map, dict):
            raise RuntimeError("Launcher did not return map preview data.")
        with self._lock:
            self._apply_launcher_status_locked(response)
            self._last_heartbeat_at = time.time()
        return dict(preview_map)

    def operator_snapshot(self, include_map: bool = True) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            navigation_action_available = bool(
                self._navigate_client is not None
                and self._navigate_client.server_is_ready()
            )
            nav2_lifecycle_ready = self._nav2_lifecycle_ready_locked()
            if self._catalog_launcher_enabled():
                if self._local_launcher_enabled():
                    self._prune_local_processes_locked()
                self._refresh_local_maps_locked()
            source_map = self._display_map_snapshot or self._map_snapshot
            map_snapshot = None
            if include_map and source_map is not None:
                map_snapshot = {
                    "width": source_map["width"],
                    "height": source_map["height"],
                    "resolution": source_map["resolution"],
                    "origin": dict(source_map["origin"]),
                    "data": list(source_map["data"]),
                    "updated_at": source_map["updated_at"],
                }
                if source_map is self._display_map_snapshot and self._current_map_name:
                    map_snapshot["name"] = _display_map_name(self._current_map_name)

            localization_ready = self._compute_localization_ok_locked(now)
            localization_fault = getattr(
                self,
                "_localization_plausibility_fault",
                None,
            )
            localization_failure_message = getattr(
                self,
                "_localization_failure_message",
                None,
            )
            localization_safety_pause_active = bool(getattr(
                self,
                "_localization_safety_pause_active",
                False,
            ))
            localization_safety_pause_reason = getattr(
                self,
                "_localization_safety_pause_reason",
                None,
            )
            localization_stop_in_progress = bool(getattr(
                self,
                "_localization_stop_in_progress",
                False,
            ))
            localization_stop_in_progress = bool(
                localization_stop_in_progress
                or getattr(self, "_late_goal_stop_workers", 0) > 0
            )
            navigation_goal_drain_pending = (
                self._navigation_goal_drain_pending_locked()
            )
            current_mission_owns_goal_drain = bool(
                navigation_goal_drain_pending
                and self._current_mission_id
                and self._state in {
                    MissionState.REQUESTED,
                    MissionState.EN_ROUTE,
                    MissionState.RETURNING,
                }
                and not localization_stop_in_progress
                and getattr(self, "_late_goal_stop_workers", 0) == 0
            )
            previous_goal_drained = bool(
                not navigation_goal_drain_pending
                or current_mission_owns_goal_drain
            )
            accepted_map_pose_available = bool(
                localization_ready
                or getattr(self, "_accepted_map_pose_available", False)
            )
            if localization_fault:
                localization_phase = "invalid_jump"
            elif localization_failure_message:
                localization_phase = "failed"
            elif localization_safety_pause_active:
                localization_phase = "safety_paused"
            elif localization_ready:
                localization_phase = "ready"
            elif self._initial_pose_refinement_active:
                localization_phase = "stationary_refinement"
            elif self._localization_requested:
                localization_phase = "waiting_for_amcl"
            else:
                localization_phase = "not_started"

            required_physical_maps_ready = bool(
                self._map_snapshot is not None
                and (
                    not self._config.keepout_map_topic
                    or not self._requires_keepout_map()
                    or self._keepout_map_snapshot is not None
                )
            )
            startup_checks = {
                "pi_discovered": self._compute_connection_ok_locked(now),
                "pi_ready": self._health_signal_ready_locked("pi_ready", now),
                "hardware": self._health_signal_ready_locked("hardware", now),
                "lidar_health": self._health_signal_ready_locked("lidar", now),
                "odometry_health": self._health_signal_ready_locked("odometry", now),
                "controller": self._health_signal_ready_locked("controller", now),
                "obstacle_safety": self._health_signal_ready_locked("obstacle_safety", now),
                "startup_gate": self._health_signal_ready_locked("startup_gate", now),
                "filtered_scan": (
                    now - float(getattr(self, "_last_filtered_scan_at", 0.0))
                ) <= self._readiness_signal_timeout_s(),
                "odometry": (
                    now - float(getattr(self, "_last_odom_at", 0.0))
                ) <= self._readiness_signal_timeout_s(),
                "odom_to_base_link": self._tf_available_locked(
                    "odom",
                    "base_link",
                ),
                "map": bool(
                    self._current_map_name
                    and required_physical_maps_ready
                ),
                "amcl": self._initial_pose_input_ready_locked(),
            }
            startup_ready = all(startup_checks.values())
            startup_messages = {
                "pi_discovered": "Waiting to discover the Raspberry Pi.",
                "pi_ready": "Waiting for /robot_health/ready from the Pi.",
                "hardware": "Waiting for healthy Pi hardware and joint states.",
                "lidar_health": "Waiting for healthy raw and filtered lidar data.",
                "odometry_health": "Waiting for the Pi odometry health signal.",
                "controller": "Waiting for the Pi motor controller health signal.",
                "obstacle_safety": "Waiting for the Pi obstacle-safety health signal.",
                "startup_gate": "Waiting for the Pi startup motion gate to open.",
                "filtered_scan": "Waiting for fresh /scan_filtered data.",
                "odometry": "Waiting for fresh /diff_cont/odom data.",
                "odom_to_base_link": "Waiting for the Pi odom → base_link transform.",
                "map": "Waiting for the active navigation map.",
                "amcl": "Waiting for the AMCL initial-pose service.",
            }
            startup_message = "Robot stack is ready for an initial position."
            if not startup_ready:
                for check_name in startup_checks:
                    if not startup_checks[check_name]:
                        startup_message = startup_messages[check_name]
                        break

            initial_pose_available = startup_ready
            navigation_checks = {
                **startup_checks,
                "localization": localization_ready,
                "map_free_localization": (
                    self._localization_candidate_is_map_free_locked()
                ),
                "localization_motion_anchor": (
                    self._localization_has_motion_anchor_locked()
                ),
                "localization_stop_complete": not localization_stop_in_progress,
                "previous_goal_drained": previous_goal_drained,
                "map_to_odom": self._tf_available_locked("map", "odom"),
                "nav2_lifecycle": nav2_lifecycle_ready,
                "navigate_to_pose": navigation_action_available,
            }
            navigation_messages = {
                **startup_messages,
                "localization": (
                    localization_failure_message
                    or "Waiting for a usable AMCL pose."
                ),
                "map_free_localization": (
                    "The latest AMCL footprint overlaps a wall, unknown space, "
                    "or a keepout cell. Waiting for a map-free update."
                ),
                "localization_motion_anchor": (
                    "Waiting for an AMCL pose synchronized with fresh odometry."
                ),
                "localization_stop_complete": (
                    "Waiting for the localization safety stop to finish."
                ),
                "previous_goal_drained": (
                    "Waiting for the previous Nav2 goal to report a terminal "
                    "result before navigation can resume."
                ),
                "map_to_odom": "Waiting for the central map → odom transform.",
                "nav2_lifecycle": (
                    "Waiting for the Nav2 planner, controller, behavior, and "
                    "velocity timers to finish activating."
                ),
                "navigate_to_pose": "Waiting for the NavigateToPose action server.",
            }
            navigation_available = all(navigation_checks.values())
            navigation_message = "Navigation is unlocked."
            if not navigation_available:
                for check_name in navigation_checks:
                    if not navigation_checks[check_name]:
                        navigation_message = navigation_messages[check_name]
                        break

            manual_drive = self._manual_drive_readiness_locked(
                now,
                startup_checks=startup_checks,
            )

            return {
                "map_available": source_map is not None,
                "map": map_snapshot,
                "map_updated_at": source_map["updated_at"] if source_map is not None else None,
                "goal_pose": dict(self._current_goal_pose) if self._current_goal_pose is not None else (
                    dict(self._last_goal_pose) if self._last_goal_pose is not None else None
                ),
                "initial_pose": dict(self._last_initial_pose) if self._last_initial_pose is not None else None,
                "system_commands_available": self._local_launcher_enabled() or self._system_command_publisher is not None,
                "initial_pose_available": initial_pose_available,
                "goal_pose_available": navigation_available,
                "navigation_available": navigation_available,
                "navigation_action_available": navigation_action_available,
                "nav2_lifecycle": {
                    "ready": nav2_lifecycle_ready,
                    "states": dict(getattr(self, "_nav2_lifecycle_states", {})),
                },
                "manual_drive_available": manual_drive["ready"],
                "manual_drive": manual_drive,
                "last_system_command": self._last_system_command,
                "saved_maps": list(self._saved_map_names),
                "current_map_name": self._current_map_name,
                "maps_directory": self._maps_directory,
                "launcher_message": self._launcher_message,
                "launcher_processes": dict(self._launcher_processes),
                "navigation_stop": dict(getattr(self, "_last_stop_status", {})),
                "startup": {
                    "phase": "ready" if startup_ready else "checking",
                    "ready": startup_ready,
                    "message": startup_message,
                    "checks": startup_checks,
                },
                "navigation": {
                    "ready": navigation_available,
                    "message": navigation_message,
                    "checks": navigation_checks,
                    "missing": [
                        check_name
                        for check_name, ready in navigation_checks.items()
                        if not ready
                    ],
                },
                "localization": {
                    "phase": localization_phase,
                    "requested": self._localization_requested,
                    "ready": localization_ready,
                    "failed": bool(localization_failure_message),
                    "message": (
                        localization_failure_message
                        or (
                            "Localization safety pause: "
                            f"{localization_safety_pause_reason} The last accepted "
                            "pose is retained; resume explicitly after localization "
                            "is ready."
                            if localization_safety_pause_active
                            else None
                        )
                    ),
                    "refinement_active": self._initial_pose_refinement_active,
                    "degraded": self._localization_degraded,
                    "safety_pause_active": localization_safety_pause_active,
                    "safety_pause_reason": localization_safety_pause_reason,
                    "stop_in_progress": localization_stop_in_progress,
                    "accepted_map_pose_available": accepted_map_pose_available,
                    "quality": (
                        "invalid"
                        if localization_fault or localization_failure_message
                        else "reduced"
                        if localization_ready and self._localization_degraded
                        else "good"
                        if localization_ready
                        else "unknown"
                    ),
                    "plausibility_fault": localization_fault,
                    "candidate_fault": getattr(
                        self,
                        "_localization_candidate_fault",
                        None,
                    ),
                    "confident_samples": self._localization_confident_samples,
                    "usable_samples": self._localization_usable_samples,
                    "required_samples": max(1, self._config.localization_required_samples),
                    "unconfident_samples": self._localization_unconfident_samples,
                    "map_fault_samples": getattr(
                        self,
                        "_localization_map_fault_samples",
                        0,
                    ),
                    "required_map_fault_samples": max(
                        1,
                        self._config.localization_map_fault_samples,
                    ),
                    "xy_std_m": self._localization_xy_std_m,
                    "yaw_std_rad": self._localization_yaw_std_rad,
                    "last_pose_at": (
                        self._last_localization_pose_at
                        if self._last_localization_pose_at > 0.0
                        else None
                    ),
                },
            }

    def manual_drive_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return self._manual_drive_readiness_locked(time.time())

    def _readiness_signal_timeout_s(self) -> float:
        return max(
            0.1,
            float(self._config.readiness_signal_timeout_s),
        )

    def _health_signal_ready_locked(self, name: str, now: float) -> bool:
        health_values = getattr(self, "_health_values", {})
        health_updated_at = getattr(self, "_health_updated_at", {})
        return bool(health_values.get(name, False)) and (
            now - float(health_updated_at.get(name, 0.0))
        ) <= self._readiness_signal_timeout_s()

    def _manual_drive_readiness_locked(
        self,
        now: float,
        *,
        startup_checks: Optional[Dict[str, bool]] = None,
    ) -> Dict[str, Any]:
        runtime_checks = startup_checks or {
            "pi_discovered": self._compute_connection_ok_locked(now),
            "pi_ready": self._health_signal_ready_locked("pi_ready", now),
            "hardware": self._health_signal_ready_locked("hardware", now),
            "lidar_health": self._health_signal_ready_locked("lidar", now),
            "odometry_health": self._health_signal_ready_locked("odometry", now),
            "controller": self._health_signal_ready_locked("controller", now),
            "obstacle_safety": self._health_signal_ready_locked("obstacle_safety", now),
            "startup_gate": self._health_signal_ready_locked("startup_gate", now),
            "filtered_scan": (
                now - float(getattr(self, "_last_filtered_scan_at", 0.0))
            ) <= self._readiness_signal_timeout_s(),
            "odometry": (
                now - float(getattr(self, "_last_odom_at", 0.0))
            ) <= self._readiness_signal_timeout_s(),
            "odom_to_base_link": self._tf_available_locked("odom", "base_link"),
        }
        check_names = (
            "pi_discovered",
            "pi_ready",
            "hardware",
            "lidar_health",
            "odometry_health",
            "controller",
            "obstacle_safety",
            "startup_gate",
            "filtered_scan",
            "odometry",
            "odom_to_base_link",
        )
        checks = {
            check_name: bool(runtime_checks.get(check_name, False))
            for check_name in check_names
        }
        checks["manual_command_topic"] = (
            bool(self._config.joystick_topic)
            and getattr(self, "_manual_command_publisher", None) is not None
        )
        messages = {
            "pi_discovered": "Manual recovery is locked until the Raspberry Pi is discovered.",
            "pi_ready": "Manual recovery is locked until the Pi reports ready.",
            "hardware": "Manual recovery is locked until Pi hardware is healthy.",
            "lidar_health": "Manual recovery is locked until lidar is healthy.",
            "odometry_health": "Manual recovery is locked until odometry is healthy.",
            "controller": "Manual recovery is locked until the motor controller is healthy.",
            "obstacle_safety": "Manual recovery is locked until obstacle safety is healthy.",
            "startup_gate": "Manual recovery is locked until the startup motion gate opens.",
            "filtered_scan": "Manual recovery is locked until filtered lidar data is fresh.",
            "odometry": "Manual recovery is locked until wheel odometry is fresh.",
            "odom_to_base_link": "Manual recovery is locked until the Pi odom → base_link transform is available.",
            "manual_command_topic": "Manual recovery is unavailable because /cmd_vel_joy is not configured.",
        }
        missing = [
            check_name
            for check_name, ready in checks.items()
            if not ready
        ]
        message = "Manual recovery drive is ready."
        if missing:
            message = messages[missing[0]]
        return {
            "ready": not missing,
            "message": message,
            "checks": checks,
            "missing": missing,
        }

    def _initial_pose_input_ready_locked(self) -> bool:
        client = getattr(self, "_set_initial_pose_client", None)
        if client is not None:
            service_is_ready = getattr(client, "service_is_ready", None)
            if callable(service_is_ready):
                try:
                    if bool(service_is_ready()):
                        return True
                except Exception:
                    pass

        publisher = self._initial_pose_publisher
        if publisher is not None:
            get_subscription_count = getattr(
                publisher,
                "get_subscription_count",
                None,
            )
            if callable(get_subscription_count):
                try:
                    return int(get_subscription_count()) > 0
                except Exception:
                    return False
        return False

    def power_snapshot(self) -> RobotPowerStatus:
        with self._lock:
            now = time.time()
            last_ready_at = float(getattr(self, "_last_pi_ready_at", 0.0))
            signal_age_ms = (
                max(0.0, (now - last_ready_at) * 1000.0)
                if last_ready_at > 0.0
                else None
            )
            battery_percent = self._power_battery_percent
            if battery_percent is None:
                battery_percent = battery_percent_from_voltage(self._battery_v)
            return RobotPowerStatus(
                available=True,
                mode=self._compute_mode_locked(now).value,
                battery_percent=battery_percent,
                latency_ms=signal_age_ms,
                recent_log=self._power_recent_log,
            )

    def _compute_mode_locked(self, now: float) -> RobotMode:
        if self._config.joystick_topic and (now - self._last_joy_cmd_at) <= self._config.manual_override_timeout_s:
            return RobotMode.MANUAL_OVERRIDE
        return RobotMode.AUTO

    def _compute_connection_ok_locked(self, now: float) -> bool:
        last_pi_signal_at = float(getattr(self, "_last_pi_signal_at", 0.0))
        return (
            last_pi_signal_at > 0.0
            and (now - last_pi_signal_at) <= self._config.connection_timeout_s
        )

    def _tf_available_locked(self, target_frame: str, source_frame: str) -> bool:
        tf_buffer = getattr(self, "_tf_buffer", None)
        if tf_buffer is None:
            return False
        try:
            time_class = getattr(self, "_ros", {}).get("Time")
            lookup_time = time_class() if time_class is not None else None
            return bool(
                tf_buffer.can_transform(
                    target_frame,
                    source_frame,
                    lookup_time,
                )
            )
        except Exception:
            return False

    def _compute_localization_ok_locked(self, now: float) -> bool:
        if not self._localization_valid:
            return False
        if self._config.localization_timeout_s <= 0.0:
            return True
        return (now - self._last_localization_at) <= self._config.localization_timeout_s

    def _compute_blocked_locked(self, now: float, mode: RobotMode) -> bool:
        if mode == RobotMode.MANUAL_OVERRIDE:
            return False
        if self._state != MissionState.EN_ROUTE:
            return False
        if self._pause_requested or self._cancel_requested:
            return False
        if self._current_goal_pose is None:
            return False
        distance_to_goal = math.hypot(
            self._current_goal_pose["x"] - self._pose.get("x", 0.0),
            self._current_goal_pose["y"] - self._pose.get("y", 0.0),
        )
        if distance_to_goal <= self._config.goal_tolerance_m:
            return False
        moving = (
            abs(self._linear_speed) >= self._config.stall_speed_epsilon
            or abs(self._angular_speed) >= self._config.stall_angular_speed_epsilon
        )
        if moving:
            return False
        if (now - self._goal_active_since) < self._config.stall_detect_after_s:
            return False
        return (now - self._last_motion_at) >= self._config.stall_detect_after_s

    def shutdown(self) -> None:
        if self._shutdown_requested:
            return

        self._shutdown_requested = True
        self._resume_event.set()
        self._cancel_active_goal()
        if getattr(self, "_navigation_zero_publisher", None) is not None:
            self._finish_navigation_stop("Central navigation shutdown.")
        if self._local_launcher_enabled():
            with self._lock:
                for key in list(self._local_processes):
                    self._stop_local_process_locked(key)

        if self._mission_thread and self._mission_thread.is_alive():
            self._mission_thread.join(timeout=1.0)
        if (
            self._initial_pose_refinement_thread
            and self._initial_pose_refinement_thread.is_alive()
        ):
            self._initial_pose_refinement_thread.join(timeout=1.0)

        if self._executor is not None:
            self._executor.shutdown()
        if self._node is not None:
            self._node.destroy_node()
        if self._context is not None:
            self._ros["rclpy"].shutdown(context=self._context)
        if self._spin_thread and self._spin_thread.is_alive():
            self._spin_thread.join(timeout=1.0)


def _map_layer_paths(navigation_map_path: Path) -> Optional[Dict[str, Path]]:
    suffix = "_navigation"
    if not navigation_map_path.stem.endswith(suffix):
        return None
    profile_name = navigation_map_path.stem[:-len(suffix)]
    return {
        "keepout": navigation_map_path.with_name(f"{profile_name}_keepout.yaml"),
        "display": navigation_map_path.with_name(f"{profile_name}_display.yaml"),
    }


def _display_map_name(navigation_map_name: str) -> str:
    suffix = "_navigation"
    if navigation_map_name.endswith(suffix):
        return f"{navigation_map_name[:-len(suffix)]}_display"
    return navigation_map_name


def _map_grid_geometry(
    map_snapshot: Optional[Dict[str, Any]],
) -> tuple[Optional[tuple[Any, ...]], Optional[str]]:
    if map_snapshot is None:
        return None, None
    try:
        width = int(map_snapshot["width"])
        height = int(map_snapshot["height"])
        resolution = float(map_snapshot["resolution"])
        origin = map_snapshot["origin"]
        origin_x = float(origin["x"])
        origin_y = float(origin["y"])
        origin_yaw = float(origin.get("yaw", 0.0))
        data = map_snapshot["data"]
    except (KeyError, TypeError, ValueError):
        return None, "The navigation map metadata is invalid."
    if (
        width <= 0
        or height <= 0
        or not math.isfinite(resolution)
        or resolution <= 0.0
        or not all(
            math.isfinite(value)
            for value in (origin_x, origin_y, origin_yaw)
        )
    ):
        return None, "The navigation map metadata is invalid."
    try:
        if len(data) < width * height:
            return None, "The navigation map metadata is invalid."
    except TypeError:
        return None, "The navigation map metadata is invalid."
    return (
        width,
        height,
        resolution,
        origin_x,
        origin_y,
        origin_yaw,
        data,
    ), None


def _map_cell_fault(
    geometry: tuple[Any, ...],
    world_x: float,
    world_y: float,
) -> Optional[str]:
    (
        width,
        height,
        resolution,
        origin_x,
        origin_y,
        origin_yaw,
        data,
    ) = geometry
    if not math.isfinite(world_x) or not math.isfinite(world_y):
        return "The reported position is not finite."

    dx = world_x - origin_x
    dy = world_y - origin_y
    map_x = math.cos(origin_yaw) * dx + math.sin(origin_yaw) * dy
    map_y = -math.sin(origin_yaw) * dx + math.cos(origin_yaw) * dy
    col = math.floor(map_x / resolution)
    row = math.floor(map_y / resolution)
    if col < 0 or col >= width or row < 0 or row >= height:
        return "The reported position extends outside the navigation map."

    occupancy = int(data[row * width + col])
    if occupancy < 0:
        return "The reported position overlaps unknown navigation-map space."
    if occupancy >= _LOCALIZATION_MAP_OCCUPIED_THRESHOLD:
        return "The reported position overlaps a static wall or obstacle."
    return None


def _localization_seed_map_fault(
    map_snapshot: Optional[Dict[str, Any]],
    x: float,
    y: float,
) -> Optional[str]:
    geometry, geometry_fault = _map_grid_geometry(map_snapshot)
    if geometry_fault or geometry is None:
        return geometry_fault
    return _map_cell_fault(geometry, x, y)


def _localization_seed_maps_fault(
    navigation_map: Optional[Dict[str, Any]],
    keepout_map: Optional[Dict[str, Any]],
    x: float,
    y: float,
    *,
    require_keepout: bool,
) -> Optional[str]:
    if navigation_map is None:
        return "The active navigation map is not available yet."
    fault = _localization_seed_map_fault(navigation_map, x, y)
    if fault:
        return fault
    if require_keepout:
        if keepout_map is None:
            return "The active hard-keepout map is not available yet."
        return _localization_seed_map_fault(keepout_map, x, y)
    return None


def _localization_footprint_map_fault(
    map_snapshot: Optional[Dict[str, Any]],
    pose: Dict[str, float],
) -> Optional[str]:
    """Reject an AMCL pose whose physical trolley footprint is not map-free."""
    geometry, geometry_fault = _map_grid_geometry(map_snapshot)
    if geometry_fault:
        return geometry_fault
    if geometry is None:
        return None

    try:
        pose_x = float(pose["x"])
        pose_y = float(pose["y"])
        pose_yaw = float(pose["yaw"])
    except (KeyError, TypeError, ValueError):
        return "AMCL reported an invalid pose."
    if not all(math.isfinite(value) for value in (pose_x, pose_y, pose_yaw)):
        return "AMCL reported an invalid pose."

    resolution = float(geometry[2])
    sample_step = max(0.01, resolution * 0.5)
    length = _LOCALIZATION_FOOTPRINT_FRONT_M - _LOCALIZATION_FOOTPRINT_REAR_M
    width = 2.0 * _LOCALIZATION_FOOTPRINT_HALF_WIDTH_M
    x_steps = max(1, math.ceil(length / sample_step))
    y_steps = max(1, math.ceil(width / sample_step))
    pose_cos = math.cos(pose_yaw)
    pose_sin = math.sin(pose_yaw)
    checked_cells = set()

    for x_index in range(x_steps + 1):
        local_x = (
            _LOCALIZATION_FOOTPRINT_REAR_M
            + length * x_index / x_steps
        )
        for y_index in range(y_steps + 1):
            local_y = (
                -_LOCALIZATION_FOOTPRINT_HALF_WIDTH_M
                + width * y_index / y_steps
            )
            world_x = pose_x + pose_cos * local_x - pose_sin * local_y
            world_y = pose_y + pose_sin * local_x + pose_cos * local_y

            # Avoid looking up the same map cell dozens of times while still
            # sampling the footprint at half-cell spacing.
            (
                _,
                _,
                grid_resolution,
                origin_x,
                origin_y,
                origin_yaw,
                _,
            ) = geometry
            dx = world_x - origin_x
            dy = world_y - origin_y
            map_x = math.cos(origin_yaw) * dx + math.sin(origin_yaw) * dy
            map_y = -math.sin(origin_yaw) * dx + math.cos(origin_yaw) * dy
            col = math.floor(map_x / grid_resolution)
            row = math.floor(map_y / grid_resolution)
            cell_key = (col, row)
            if cell_key in checked_cells:
                continue
            checked_cells.add(cell_key)

            cell_fault = _map_cell_fault(geometry, world_x, world_y)
            if cell_fault:
                return (
                    "AMCL pose rejected: the robot footprint is not map-free. "
                    f"{cell_fault}"
                )

    return None


def _localization_pose_maps_fault(
    navigation_map: Optional[Dict[str, Any]],
    keepout_map: Optional[Dict[str, Any]],
    pose: Dict[str, float],
    *,
    require_keepout: bool,
) -> Optional[str]:
    """Validate the whole trolley against every required physical map."""
    if navigation_map is None:
        return "AMCL pose cannot be checked because the navigation map is unavailable."
    fault = _localization_footprint_map_fault(navigation_map, pose)
    if fault:
        return fault
    if require_keepout:
        if keepout_map is None:
            return "AMCL pose cannot be checked because the hard-keepout map is unavailable."
        return _localization_footprint_map_fault(keepout_map, pose)
    return None


def _occupancy_grid_snapshot(msg: Any, updated_at: float) -> Dict[str, Any]:
    info = msg.info
    origin = info.origin
    return {
        "width": int(info.width),
        "height": int(info.height),
        "resolution": float(info.resolution),
        "origin": {
            "x": float(origin.position.x),
            "y": float(origin.position.y),
            "yaw": _quaternion_to_yaw(
                origin.orientation.x,
                origin.orientation.y,
                origin.orientation.z,
                origin.orientation.w,
            ),
        },
        "data": list(msg.data),
        "updated_at": updated_at,
    }


def _expanded_path(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value))).resolve()


def _bool_arg(value: bool) -> str:
    return "true" if bool(value) else "false"


def _ros_workspace_command(workspace: Path, ros_args: List[str]) -> List[str]:
    setup_file = workspace / "install" / "setup.bash"
    ros_command = " ".join(shlex.quote(str(part)) for part in ros_args)
    if setup_file.exists():
        return ["bash", "-lc", f"source {shlex.quote(str(setup_file))} && exec {ros_command}"]
    return ["bash", "-lc", f"exec {ros_command}"]


def _image_path_from_map_yaml(map_yaml_path: Path) -> Optional[Path]:
    raw = yaml.safe_load(map_yaml_path.read_text(encoding="utf-8")) or {}
    image = raw.get("image")
    if not image:
        return None
    image_path = Path(str(image))
    if not image_path.is_absolute():
        image_path = map_yaml_path.parent / image_path
    return image_path


def _load_map_preview_from_yaml(map_yaml_path: Path) -> Dict[str, Any]:
    raw = yaml.safe_load(map_yaml_path.read_text(encoding="utf-8")) or {}
    image_path = _image_path_from_map_yaml(map_yaml_path)
    if image_path is None or not image_path.exists():
        raise RuntimeError(f"Map image for '{map_yaml_path.stem}' was not found.")

    width, height, max_value, pixels = _read_pgm(image_path)
    mode = str(raw.get("mode", "trinary")).strip().lower()
    negate = int(raw.get("negate", 0) or 0)
    occupied_thresh = float(raw.get("occupied_thresh", 0.65))
    free_thresh = float(raw.get("free_thresh", 0.25))
    threshold_range = occupied_thresh - free_thresh
    if threshold_range <= 0.0:
        raise RuntimeError(
            f"Map '{map_yaml_path.stem}' requires occupied_thresh "
            "to be greater than free_thresh."
        )
    origin_values = list(raw.get("origin", [0.0, 0.0, 0.0]))
    while len(origin_values) < 3:
        origin_values.append(0.0)

    occupancy = [0] * (width * height)
    scale = float(max_value) if max_value else 255.0
    for image_row in range(height):
        # PGM rows start at the top; OccupancyGrid rows start at the map origin
        # along the bottom. Match nav2_map_server so the UI's grid-to-canvas
        # transform restores the original image orientation.
        grid_row = height - 1 - image_row
        for col in range(width):
            pixel = pixels[(image_row * width) + col]
            probability = (float(pixel) / scale) if negate else ((scale - float(pixel)) / scale)
            grid_index = (grid_row * width) + col
            if probability > occupied_thresh:
                occupancy[grid_index] = 100
            elif probability < free_thresh:
                occupancy[grid_index] = 0
            elif mode == "scale":
                occupancy[grid_index] = round(
                    ((probability - free_thresh) / threshold_range) * 100.0
                )
            else:
                occupancy[grid_index] = -1

    return {
        "name": map_yaml_path.stem,
        "width": width,
        "height": height,
        "resolution": float(raw.get("resolution", 0.05)),
        "origin": {
            "x": float(origin_values[0]),
            "y": float(origin_values[1]),
            "yaw": float(origin_values[2]),
        },
        "data": occupancy,
        "updated_at": map_yaml_path.stat().st_mtime,
    }


def _read_pgm(path: Path) -> tuple[int, int, int, List[int]]:
    data = path.read_bytes()
    index = 0

    def next_token() -> bytes:
        nonlocal index
        while index < len(data):
            byte = data[index]
            if byte in b" \t\r\n":
                index += 1
                continue
            if byte == ord("#"):
                while index < len(data) and data[index] not in b"\r\n":
                    index += 1
                continue
            break
        start = index
        while index < len(data) and data[index] not in b" \t\r\n":
            index += 1
        if start == index:
            raise RuntimeError(f"Invalid PGM file: {path}")
        return data[start:index]

    magic = next_token()
    width = int(next_token())
    height = int(next_token())
    max_value = int(next_token())
    expected = width * height

    if magic == b"P5":
        if index < len(data) and data[index] in b" \t\r\n":
            index += 1
        payload = data[index:index + expected]
        if len(payload) != expected:
            raise RuntimeError(f"PGM image has unexpected size: {path}")
        return width, height, max_value, list(payload)

    if magic == b"P2":
        pixels = [int(next_token()) for _ in range(expected)]
        return width, height, max_value, pixels

    raise RuntimeError(f"Unsupported PGM format '{magic.decode(errors='replace')}' in {path}")


def _load_packaged_demo_maps() -> List[Dict[str, Any]]:
    workspace_root = Path(__file__).resolve().parents[3]
    demo_maps = [
        workspace_root / "src" / "my_bot" / "maps" / "atrium_navigation.yaml",
    ]
    loaded_maps: List[Dict[str, Any]] = []
    for yaml_path in demo_maps:
        if not yaml_path.exists():
            continue
        try:
            loaded_maps.append(_load_map_preview_from_yaml(yaml_path))
        except RuntimeError:
            continue
    return loaded_maps


def create_robot_adapter_from_env(dest_config: DestinationConfig) -> RobotAdapter:
    backend = os.getenv("MISSION_CONTROL_ROBOT_BACKEND", "sim").strip().lower()
    robot_id = os.getenv("MISSION_CONTROL_ROBOT_ID", "robot-1").strip() or "robot-1"

    if backend == "sim":
        speed_scale = _env_float("MISSION_CONTROL_SIM_SPEED_SCALE", 1.0)
        ui_map_path = _env_optional_str("MISSION_CONTROL_UI_MAP", None)
        return SimRobotAdapter(robot_id, speed_scale=speed_scale, ui_map_path=ui_map_path)
    if backend == "ros2":
        return Ros2RobotAdapter(robot_id=robot_id, dest_config=dest_config, config=Ros2AdapterConfig.from_env())

    raise ValueError("MISSION_CONTROL_ROBOT_BACKEND must be 'sim' or 'ros2'.")


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return float(default)
    return float(raw)


def _env_optional_str(name: str, default: Optional[str]) -> Optional[str]:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip()
    return value or None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return bool(default)
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _yaw_to_quaternion(yaw: float) -> tuple[float, float]:
    return math.sin(yaw / 2.0), math.cos(yaw / 2.0)


def _normalize_angle(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def _quaternion_to_yaw(x: float, y: float, z: float, w: float) -> float:
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def _import_ros2_modules() -> Dict[str, Any]:
    try:
        import rclpy
        from tf2_ros import Buffer, TransformListener
        from action_msgs.msg import GoalStatus
        from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped, Twist
        from lifecycle_msgs.srv import GetState
        from nav2_msgs.action import NavigateToPose
        from nav2_msgs.srv import SetInitialPose
        from nav_msgs.msg import OccupancyGrid, Odometry
        from rclpy.action import ActionClient
        from rclpy.context import Context
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.node import Node
        from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
        from rclpy.time import Time
        from sensor_msgs.msg import BatteryState, LaserScan
        from std_msgs.msg import Bool, String
        from std_srvs.srv import Empty
    except ImportError as exc:
        raise RuntimeError(
            "ROS 2 Python packages are unavailable. Source your ROS 2 environment "
            "before starting mission_control with MISSION_CONTROL_ROBOT_BACKEND=ros2."
        ) from exc

    return {
        "rclpy": rclpy,
        "ActionClient": ActionClient,
        "BatteryState": BatteryState,
        "Bool": Bool,
        "Buffer": Buffer,
        "Context": Context,
        "DurabilityPolicy": DurabilityPolicy,
        "Empty": Empty,
        "GoalStatus": GoalStatus,
        "GetState": GetState,
        "HistoryPolicy": HistoryPolicy,
        "LaserScan": LaserScan,
        "NavigateToPose": NavigateToPose,
        "Node": Node,
        "OccupancyGrid": OccupancyGrid,
        "Odometry": Odometry,
        "PoseStamped": PoseStamped,
        "PoseWithCovarianceStamped": PoseWithCovarianceStamped,
        "QoSProfile": QoSProfile,
        "ReliabilityPolicy": ReliabilityPolicy,
        "SetInitialPose": SetInitialPose,
        "SingleThreadedExecutor": SingleThreadedExecutor,
        "String": String,
        "TransformListener": TransformListener,
        "Twist": Twist,
        "Time": Time,
        "qos_profile_sensor_data": __import__(
            "rclpy.qos",
            fromlist=["qos_profile_sensor_data"],
        ).qos_profile_sensor_data,
    }
