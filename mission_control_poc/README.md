# Mission Control PoC

This is a small backend service that implements **System Control & Logistics** requirements from your spec:
- **Mission request validation** (destination exists in config)
- **Action state control**: *Idle → En-route → Paused → Completed*
- **Manual safe pause** (operator-triggered)
- **Scheduling policy** (queue + conflict prevention)
- **Blocked/trapped detection** with retries and help-request escalation
- **Mission logging** (mission table + event timeline)
- **Status interface** with ≥1 Hz refresh (WebSocket)
- **Config-driven destinations** (YAML) with reload endpoint

It started as a ROS2-independent mission-control backend. This repo now includes the ROS2/Nav2 adapter as well, so the server can run in:
- `sim` mode: the original in-process simulated robot
- `ros2` mode: direct Nav2 integration from `robot_server` without editing `catering_bot-main`

---

## Quick start

### Recommended: central supervisor

The Raspberry Pi owns hardware access, motor control, local sensing, odometry,
robot frames below `odom`, and motion safety. The central computer owns
SLAM/AMCL, `map → odom`, maps, Nav2, Mission Control, and the web UI.

Run the shared supervisor from the `robot_server` repository:

```bash
./central/setup_wsl.sh
./central/start_central_stack.sh ui-only
./central/start_central_stack.sh navigation
./central/start_central_stack.sh mapping
./central/stop_central_stack.sh
./central/central_doctor.sh
```

Mission Control attaches in `supervised` mode and never starts a second Nav2
stack. Runtime maps, destinations, mission history, and logs are preserved
under `~/.local/share/intellitrolley`.

The display map is gzip-compressed and transferred once per browser session.
The three-second operator refresh then requests state without the large map
array, avoiding the repeated multi-megabyte JSON parsing that previously froze
the UI.

The dashboard follows a strict startup sequence:

1. **Startup Ready** requires a discovered and ready Pi, all Pi health signals,
   fresh `/scan_filtered`, fresh `/diff_cont/odom`, `odom → base_link`, an
   active map, and the AMCL initial-pose service.
2. **Set Initial Position** unlocks only after those checks pass.
3. Destination and mission controls unlock only after AMCL has a usable pose
   and the central `map → odom` transform and `NavigateToPose` action are ready.

The API enforces the same gates, so a stale browser cannot bypass them.
Manual recovery has a separate backend-enforced readiness gate. It requires
the live Pi hardware and safety path but does not require a map, AMCL, or Nav2.
The first nonzero manual command pauses any active mission, and that mission
stays paused until an operator explicitly resumes it.

### Phone manual-recovery API

Serve the phone frontend from the same Mission Control origin and poll:

```text
GET /robots/{robot_id}/operator-panel?include_map=false
```

Enable its directional controls only when `manual_drive.ready` is true. While a
direction is held, send this request at 10 Hz:

```http
POST /robots/{robot_id}/manual-drive
Content-Type: application/json

{
  "linear": 0.5,
  "angular": 0.0,
  "command_source": {
    "type": "operator",
    "id": "phone-1"
  }
}
```

`linear` is metres per second and `angular` is radians per second; each must be
between `-1.0` and `1.0`. Send both as zero on pointer release/cancel, browser
blur, or page hiding. A zero command remains permitted when readiness has been
lost so the client can make a best-effort stop. The Pi independently drops
stale manual input after 0.25 seconds.

If an autonomous mission was moving, the first accepted nonzero response
contains its ID in `command.paused_mission_id`. The phone should tell the
operator that navigation is paused; it must never resume automatically.

### Phone point-navigation API

Fetch the live operator data and display map from:

```text
GET /robots/{robot_id}/operator-panel?include_map=true
```

Wait until `navigation_available` is true. Convert the selected display-map
pixel into `map` coordinates using its `origin`, `resolution`, `width`, and
`height`, then send the point in metres:

```http
POST /robots/{robot_id}/navigate-to-point
Content-Type: application/json

{
  "x": 10.84,
  "y": 29.72,
  "yaw": 0.0,
  "requested_by": "phone-1",
  "notes": "Point selected on the phone map.",
  "command_source": {
    "type": "operator",
    "id": "phone-1"
  }
}
```

The endpoint applies the same display rule as the dashboard: the selected cell
must be white/free (`occupancy == 0`). It rejects points on grey obstacles,
unknown space, or outside the map. A successful response includes the created
`mission_id`. Use the status WebSocket or `GET /status` to follow it, and use
`POST /missions/{mission_id}/cancel` with a `command_source` body to cancel it.

Native mobile HTTP clients are not subject to browser CORS. Use the central
computer's private IPv4 address and port `8000`; do not send these requests to
the Pi. The Windows package opens that port only for the configured private
robot subnet. A web app hosted on a different origin is intentionally not
allowed and should instead be served by Mission Control.

### Legacy development launchers

The earlier launcher remains available for local diagnostics:

```bash
./launch_navigation_ui.sh
```

The launcher starts Mission Control and uses:

- `atrium_navigation.yaml` for AMCL and the Nav2 static map
- `atrium_keepout.yaml` as a keepout filter in both costmaps
- `atrium_display.yaml` for the Mission Control map canvas

All three layers use the same resolution, origin, and image dimensions, so UI
clicks, robot poses, and keepout cells stay in one coordinate frame. The
launcher waits for Nav2 and all three map topics, then opens the UI. AMCL waits
for the operator to click **Localize** or set an approximate initial position.
Both paths request repeated AMCL no-motion updates so lidar can be matched while
the robot stays still. This avoids conflicting with obstacle safety near walls;
localization never starts automatically when Nav2 launches.

Layered profiles follow the naming convention `<name>_navigation.yaml`,
`<name>_keepout.yaml`, and `<name>_display.yaml`. Pass the navigation layer when
selecting a different profile:

```bash
./launch_navigation_ui.sh another_navigation
```

Legacy single-map YAML files are still supported; their keepout and display
servers are disabled automatically.

Do not run a legacy launcher at the same time as the central supervisor. Run
the environment setup once on a development computer:

```bash
./setup_env_linux.sh
```

For UI-only testing with the same Atrium profile and destinations, use:

```bash
./launch_ui_only.sh
```

This selects `atrium_navigation` by default, renders `atrium_display`, validates
that `atrium_keepout` exists, and does not start ROS, Nav2, AMCL, or TF. Pass a
different navigation profile the same way as the full launcher:

```bash
./launch_ui_only.sh another_navigation
```

Plain `./run_server.sh` remains available for the older self-contained simulated
demo.

The central supervisor sets these values automatically:

```bash
export ROS_DOMAIN_ID=42
export ROS_LOCALHOST_ONLY=0
export MISSION_CONTROL_ROBOT_BACKEND=ros2
export MISSION_CONTROL_ROS2_LAUNCHER_MODE=supervised
export MISSION_CONTROL_ROS2_MAP_DIRECTORY=$HOME/.local/share/intellitrolley/maps
```

`--reload` is convenient for the simulated backend, but it can create duplicate ROS 2 nodes/processes.

Note: the checked-in `.venv` in this folder is a macOS artifact and is not usable on Ubuntu. Use `./setup_env_linux.sh` to create `.venv-local` on this machine.

Open:
- http://127.0.0.1:8000/ui (dashboard UI)
- http://127.0.0.1:8000/docs (Swagger UI)
- WebSocket status stream: `ws://127.0.0.1:8000/ws/status`

---

## Config-driven destinations

Edit: `config/destinations.yaml`

```yaml
destinations:
  - name: "Storage"
    pose: {x: 0.0, y: 0.0, yaw: 0.0}
home_destination: "Storage"
```

Reload without restart:

```bash
curl -X POST http://127.0.0.1:8000/destinations/reload
```

---

## Example: create a mission (single trip)

```bash
curl -X POST http://127.0.0.1:8000/missions \
  -H "Content-Type: application/json" \
  -d '{
    "requested_by": "event-staff-17",
    "command_source": {"type":"user","id":"event-staff-17"},
    "to_destination": "Ballroom",
    "schedule_type": "single"
  }'
```

---

## Example: pause / resume / cancel

```bash
curl -X POST http://127.0.0.1:8000/missions/<MISSION_ID>/pause \
  -H "Content-Type: application/json" \
  -d '{"command_source":{"type":"operator","id":"supervisor-1"}}'
```

---

## Example: simulate a blocked condition (for testing)

The default robot is a **simulated robot** (`robot-1`).

```bash
curl -X POST http://127.0.0.1:8000/robots/robot-1/telemetry \
  -H "Content-Type: application/json" \
  -d '{"blocked": true}'
```

The mission control loop will:
- detect blocked within ~5 seconds
- attempt recovery (pause 2s → resume) up to 3 times
- then set the mission to **Paused** and mark `help_required=1`

Unblock:

```bash
curl -X POST http://127.0.0.1:8000/robots/robot-1/telemetry \
  -H "Content-Type: application/json" \
  -d '{"blocked": false}'
```

---

## ROS 2 / Nav2 integration

`mission_control/robot_adapter.py` now includes `Ros2RobotAdapter`. The server selects the backend from environment variables:

```bash
export MISSION_CONTROL_ROBOT_BACKEND=ros2
export MISSION_CONTROL_ROBOT_ID=robot-1
uvicorn app:app --port 8000
```

Defaults are chosen to match the current `my_bot` launch/config:

- Nav2 action: `navigate_to_pose`
- map frame: `map`
- localization topic: `/amcl_pose`
- odometry topic: `/diff_cont/odom`
- manual override topic: `/cmd_vel_joy`
- map topic: `/map`
- UI display-map topic: `/display_map`
- initial pose topic: `/initialpose`
- external goal observation topic: `/goal_pose` (Mission Control never publishes
  commands on this topic; missions use `NavigateToPose` exactly once)
- filtered lidar: `/scan_filtered`
- Pi readiness: `/robot_health/ready`
- Pi component health: `/robot_health/*_healthy` and `/robot_health/obstacle_health`
- permitted raw navigation output: `/cmd_vel_nav_raw`
- battery telemetry: `/battery_state`

The ROS 2 adapter supports these process-ownership modes:

- `supervised`: the top-level central supervisor owns Nav2/SLAM and Mission
  Control only attaches to the ROS graph; this is the production mode
- `local`: Mission Control starts Nav2 on its own machine
- `topic`: a custom launcher bridge owns the processes

The local/fallback commands are:

- mapping: `ros2 launch my_bot central_compute.launch.py use_slam:=true use_nav2:=false`
- Atrium Nav2 profile: `ros2 launch my_bot central_compute.launch.py use_slam:=false use_nav2:=true`
- legacy single map: add `map:=... use_keepout:=false use_display_map:=false`
- save map: `ros2 run nav2_map_server map_saver_cli -f ...`

The central launch rejects configurations that enable SLAM and Nav2/AMCL at
the same time.

Optional ROS2 tuning env vars:

- `MISSION_CONTROL_ROS2_NODE_NAME`
- `MISSION_CONTROL_ROS2_NAVIGATE_ACTION`
- `MISSION_CONTROL_ROS2_MAP_FRAME`
- `MISSION_CONTROL_ROS2_MAP_TOPIC`
- `MISSION_CONTROL_ROS2_KEEPOUT_MAP_TOPIC` (default `/keepout_filter_mask`)
- `MISSION_CONTROL_ROS2_KEEPOUT_MAP_REQUIRED` (default `true` for layered `*_navigation` map profiles)
- `MISSION_CONTROL_ROS2_LOCALIZATION_TOPIC`
- `MISSION_CONTROL_ROS2_ODOM_TOPIC`
- `MISSION_CONTROL_ROS2_FILTERED_SCAN_TOPIC` (default `/scan_filtered`)
- `MISSION_CONTROL_ROS2_PI_READY_TOPIC` (default `/robot_health/ready`)
- `MISSION_CONTROL_ROS2_HARDWARE_HEALTHY_TOPIC`
- `MISSION_CONTROL_ROS2_LIDAR_HEALTHY_TOPIC`
- `MISSION_CONTROL_ROS2_ODOMETRY_HEALTHY_TOPIC`
- `MISSION_CONTROL_ROS2_CONTROLLER_HEALTHY_TOPIC`
- `MISSION_CONTROL_ROS2_OBSTACLE_HEALTHY_TOPIC`
- `MISSION_CONTROL_ROS2_STARTUP_GATE_TOPIC`
- `MISSION_CONTROL_ROS2_HEALTH_LOG_TOPIC`
- `MISSION_CONTROL_ROS2_NAVIGATION_COMMAND_TOPIC` (default `/cmd_vel_nav_raw`)
- `MISSION_CONTROL_ROS2_READINESS_SIGNAL_TIMEOUT_S` (default `3.0`)
- `MISSION_CONTROL_ROS2_BATTERY_TOPIC`
- `MISSION_CONTROL_ROS2_JOYSTICK_TOPIC`
- `MISSION_CONTROL_ROS2_DISPLAY_MAP_TOPIC`
- `MISSION_CONTROL_ROS2_INITIAL_POSE_TOPIC`
- `MISSION_CONTROL_ROS2_SET_INITIAL_POSE_SERVICE` (default `/set_initial_pose`)
- `MISSION_CONTROL_ROS2_GLOBAL_LOCALIZATION_SERVICE`
- `MISSION_CONTROL_ROS2_NOMOTION_UPDATE_SERVICE` (default `/request_nomotion_update`)
- `MISSION_CONTROL_ROS2_GOAL_POSE_TOPIC`
- `MISSION_CONTROL_ROS2_LAUNCHER_MODE` (`supervised`, `local`, or `topic`)
- `MISSION_CONTROL_ROS2_EXTERNAL_MAP_NAME`
- `MISSION_CONTROL_ROS2_PACKAGE_NAME`
- `MISSION_CONTROL_ROS2_CENTRAL_WORKSPACE`
- `MISSION_CONTROL_ROS2_MAPPING_WORKSPACE`
- `MISSION_CONTROL_ROS2_NAV_WORKSPACE`
- `MISSION_CONTROL_ROS2_ROBOT_WORKSPACE`
- `MISSION_CONTROL_ROS2_MAP_DIRECTORY`
- `MISSION_CONTROL_ROS2_MAPPING_USE_JOYSTICK`
- `MISSION_CONTROL_ROS2_NAV_USE_JOYSTICK`
- `MISSION_CONTROL_ROS2_LAUNCH_RVIZ`
- `MISSION_CONTROL_ROS2_ACTION_TIMEOUT_S` (default `30`; allows the central Nav2 lifecycle startup to finish)
- `MISSION_CONTROL_ROS2_CONNECTION_TIMEOUT_S`
- `MISSION_CONTROL_ROS2_LOCALIZATION_TIMEOUT_S`
- `MISSION_CONTROL_ROS2_LOCALIZATION_MAX_XY_STD_M` (default `0.75`)
- `MISSION_CONTROL_ROS2_LOCALIZATION_MAX_YAW_STD_RAD` (default `0.75`)
- `MISSION_CONTROL_ROS2_LOCALIZATION_USABLE_XY_STD_M` (default `1.0`)
- `MISSION_CONTROL_ROS2_LOCALIZATION_USABLE_YAW_STD_RAD` (default `0.95`)
- `MISSION_CONTROL_ROS2_LOCALIZATION_LOSS_XY_STD_M` (default `1.5`)
- `MISSION_CONTROL_ROS2_LOCALIZATION_LOSS_YAW_STD_RAD` (default `1.2`)
- `MISSION_CONTROL_ROS2_LOCALIZATION_REQUIRED_SAMPLES` (default `3`; consecutive map-free stationary results are required)
- `MISSION_CONTROL_ROS2_LOCALIZATION_MAP_FAULT_SAMPLES` (default `3`; an active route stops on the first map-invalid pose, while three consecutive invalid poses enter recoverable quarantine)
- `MISSION_CONTROL_ROS2_LOCALIZATION_CONFIRMATION_MAX_TRANSLATION_M` (default `0.30`)
- `MISSION_CONTROL_ROS2_LOCALIZATION_CONFIRMATION_MAX_YAW_DELTA_RAD` (default `0.45`)
- `MISSION_CONTROL_ROS2_LOCALIZATION_LOSS_SAMPLES` (default `35`)
- `MISSION_CONTROL_ROS2_LOCALIZATION_NOMOTION_UPDATES` (default `12`)
- `MISSION_CONTROL_ROS2_LOCALIZATION_NOMOTION_INTERVAL_S` (default `0.35`)
- `MISSION_CONTROL_ROS2_INITIAL_POSE_XY_COVARIANCE` (default `0.25`)
- `MISSION_CONTROL_ROS2_INITIAL_POSE_YAW_COVARIANCE` (default `9.8696`, allowing AMCL to estimate the full heading range)
- `MISSION_CONTROL_ROS2_MANUAL_OVERRIDE_TIMEOUT_S`
- `MISSION_CONTROL_ROS2_STALL_TIMEOUT_S`
- `MISSION_CONTROL_ROS2_GOAL_TOLERANCE_M`
- `MISSION_CONTROL_ROS2_STOP_ZERO_COUNT`
- `MISSION_CONTROL_ROS2_STOP_ZERO_INTERVAL_S`
- `MISSION_CONTROL_ROS2_STOP_CONFIRMATION_TIMEOUT_S`
- `MISSION_CONTROL_DB_PATH`
- `MISSION_CONTROL_DESTINATIONS_PATH`

The adapter interface remains:

- `start_mission(mission_id, plan)`
- `pause()`
- `resume()`
- `cancel()`
- `reset_to_idle()`
- `snapshot()`

The API and scheduler retain the existing mission workflow and logging.
`Ros2RobotAdapter` resolves destination names from `config/destinations.yaml`,
sends Nav2 goals, cancels/resends goals for pause/resume, and reports robot
status back into the mission-control loop.
