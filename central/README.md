# IntelliTrolley central supervisor

These scripts are the single owner of the central ROS and Mission Control
processes. Mission Control attaches to the graph in `supervised` mode and does
not start a second Nav2 or SLAM stack.

## First setup

Use Ubuntu 22.04 with ROS 2 Humble:

```bash
./central/setup_wsl.sh
```

Review `~/.config/intellitrolley/central.env`. The Pi and central computer must
use the same `ROS_DOMAIN_ID`.

For networks that filter multicast, generate a durable explicit Pi peer with:

```bash
intellitrolley-central configure-network 172.20.10.9 0
```

The Windows setup and **Configure Robot Network** shortcut call this command
automatically with the values selected by the operator.

For Windows distribution, build `../packaging/build_central_package.sh` and
follow the packaged `windows/README.md`. The preview installer places releases
in the WSL Linux filesystem and keeps mutable data outside the release.

## Start and stop

```bash
./central/start_central_stack.sh navigation
./central/start_central_stack.sh mapping
./central/start_central_stack.sh ui-only
./central/stop_central_stack.sh
./central/central_doctor.sh
./central/configure_central_network.sh 172.20.10.9 0
```

Navigation mode validates the navigation, keepout, and display layers before
starting. Mapping mode starts SLAM without AMCL or Nav2. UI-only mode uses the
simulated adapter and has no ROS hardware dependency.

Runtime maps, destinations, mission history, and logs live under
`~/.local/share/intellitrolley` by default so application updates do not
overwrite them.

The default configuration listens on all network interfaces so a phone on the
robot-hosted Wi-Fi can open the dashboard. This build does not provide user
authentication or TLS. Use it only on the private robot network, do not expose
port 8000 through a router, and leave browser clients on the same origin as the
central API.

## Phone manual recovery

A separate phone UI can use the central Mission Control API for low-speed
recovery driving. Host that UI on the same Mission Control origin and send
commands only to:

```text
POST /robots/{robot_id}/manual-drive
```

The central backend publishes those commands only on `/cmd_vel_joy`. It never
bypasses the Pi safety node or velocity mux. A nonzero command is accepted only
while the Pi, lidar, odometry, controller, obstacle safety, startup gate, and
manual command channel are live. Map, AMCL, and Nav2 readiness are deliberately
not required, because manual recovery must remain available when localization
or navigation is the problem.

The phone must stream a held direction at 10 Hz and send `linear: 0`,
`angular: 0` on release, pointer cancellation, page hiding, or lost focus.
The Pi stops a stale manual stream automatically. The first nonzero recovery
command pauses an active autonomous mission; releasing the phone control does
not resume that mission. An operator must explicitly resume it after checking
that the robot is clear.

The controlled stop cancels Mission Control first, publishes a short bounded
sequence of zeros only on `/cmd_vel_nav_raw`, and then stops the owned ROS
process group. It never sends commands to the Pi controller topic.
