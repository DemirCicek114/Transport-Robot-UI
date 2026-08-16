# IntelliTrolley Central: Windows 11 installation and navigation

This guide installs IntelliTrolley Central in this order:

1. Enable and update Windows Subsystem for Linux 2.
2. Install the specifically named `Ubuntu-22.04` distribution.
3. Initialize a normal Ubuntu user.
4. Install IntelliTrolley Central.
5. Configure the Raspberry Pi network.
6. Run Diagnostics and start Navigation.

This is the production navigation workflow. The dashboard is opened as part of
Navigation; no separate dashboard-only launch is required.

## Requirements

- Windows 11 22H2, build 22621, or newer
- A 64-bit Windows computer
- Windows administrator access
- Internet access during the initial WSL, ROS 2, and Python installation
- Several gigabytes of available storage
- The IntelliTrolley setup executable and its SHA-256 checksum

The preview setup executable is not digitally signed. Windows SmartScreen may
display **Unknown publisher**. Verify the checksum supplied with the installer
before approving it.

## 1. Install WSL 2

Open **PowerShell as Administrator**. Run:

```powershell
wsl --update
wsl --set-default-version 2
wsl --status
wsl --list --online
```

If `wsl --status` reports that WSL is not installed, run:

```powershell
wsl --install --no-distribution
```

Restart Windows if requested. After restarting, open an Administrator
PowerShell window again and run:

```powershell
wsl --update
wsl --set-default-version 2
```

Microsoft’s current references are:

- [Install WSL](https://learn.microsoft.com/windows/wsl/install)
- [WSL command reference](https://learn.microsoft.com/windows/wsl/basic-commands)

## 2. Install Ubuntu 22.04

In Administrator PowerShell, confirm that the required distribution name is
available:

```powershell
wsl --list --online
```

Install it:

```powershell
wsl --install -d Ubuntu-22.04
```

If the Microsoft Store download stays at 0% or is unavailable, use:

```powershell
wsl --install --web-download -d Ubuntu-22.04
```

Restart Windows if requested.

An existing distribution registered as `Ubuntu` does not have to be removed.
Multiple distributions can coexist, but IntelliTrolley uses the distribution
registered specifically as `Ubuntu-22.04`.

## 3. Initialize Ubuntu

Open **Ubuntu 22.04** from the Windows Start menu, or run:

```powershell
wsl -d Ubuntu-22.04
```

Wait for the first-launch initialization to finish. Create the requested normal
Linux username and password.

Important:

- Do not use `root` as the normal account.
- Linux does not display password characters while you type; this is normal.
- Remember this password because the IntelliTrolley installer uses `sudo`
  inside Ubuntu.

At the Ubuntu prompt, run:

```bash
exit
```

Back in PowerShell, verify Ubuntu and WSL:

```powershell
wsl --list --verbose
wsl -d Ubuntu-22.04 -- cat /etc/os-release
wsl -d Ubuntu-22.04 -- id -u
```

Expected:

- The registered distribution name is `Ubuntu-22.04`.
- The WSL version column is `2`.
- `/etc/os-release` contains `VERSION_ID="22.04"`.
- `id -u` returns a number other than `0`.

If Ubuntu is using WSL 1, convert it:

```powershell
wsl --set-version Ubuntu-22.04 2
```

## 4. Install IntelliTrolley Central

Keep Windows connected to an internet-capable network for the initial
installation. Copy `IntelliTrolley-Setup-<version>.exe` and its checksum file
to a normal local Windows folder such as **Downloads**.

Verify the executable from PowerShell:

```powershell
Get-FileHash .\IntelliTrolley-Setup-<version>.exe -Algorithm SHA256
Get-Content .\IntelliTrolley-Setup-<version>.exe.sha256
```

The two hashes must match.

Install:

1. Double-click `IntelliTrolley-Setup-<version>.exe`.
2. If SmartScreen appears, select **More info**, verify the filename, and
   choose **Run anyway**.
3. Approve the administrator prompt.
4. On the robot-network page, either enter the current robot network or leave
   robot networking disabled and configure it afterward.
5. Keep the setup and PowerShell windows open.
6. Enter the Ubuntu password when `sudo` requests it.

The first installation downloads ROS 2 Humble packages, creates a Python
environment, builds the ROS workspace, and copies the application release into
the Ubuntu filesystem. This can take several minutes.

Do not close the installer while downloads or the workspace build are running.
If setup requests an Ubuntu initialization or Windows restart, complete it and
then select **IntelliTrolley Central → Finish or Repair Installation**.

### Robot-network values

For the Pi-hosted `IntelliTrolley` hotspot, use:

```text
Robot IPv4 address: 10.42.0.1
Private robot subnet: 10.42.0.0/24
ROS domain ID: 0
```

For a facility Wi-Fi connection, do not guess the DHCP address or subnet. Use
the values displayed by the Pi provisioning page after the connection is
confirmed.

When robot networking is enabled, setup:

- enables WSL mirrored networking in `%UserProfile%\.wslconfig`;
- configures the selected ROS domain;
- generates an explicit Cyclone DDS Pi peer;
- adds Windows and Hyper-V firewall rules scoped to the robot and private
  robot subnet; and
- permits Mission Control TCP port `8000` only from Windows loopback and the
  selected private robot subnet.

Setup does not restart or move the physical robot.

## 5. Configure the Pi network

The Windows and Pi Cyclone DDS peers must point to each other. There are two
supported paths.

### From the Pi provisioning page

When Windows is connected to the Pi hotspot or the same facility Wi-Fi as the
Pi, use **IntelliTrolley Central → Configure Robot Wi-Fi**, or open:

```text
http://zrpi-desktop.local:8090/
```

The hotspot address `http://10.42.0.1:8090/` remains available if Windows does
not resolve the Pi's `.local` hostname.

Choose one of the networking options on that page. After it confirms the robot
network, select **Configure IntelliTrolley on Windows** and approve the UAC
prompt.

The `intellitrolley://` handoff contains only:

- the private Pi IPv4 address;
- the private subnet; and
- the ROS domain.

It never carries the Wi-Fi name or password.

### From the Windows Start menu

Use **IntelliTrolley Central → Configure Robot Wi-Fi** to open the Pi portal
when:

- the Pi address or private subnet changed;
- the ROS domain changed;
- you need to scan, save, switch, or remove a Wi-Fi connection; or
- you need the portal's Windows network handoff after a Wi-Fi change.

The shortcut opens the browser directly and does not launch WSL or Windows
Network Manager. The portal's explicit Windows handoff remains responsible for
updating Windows, WSL, Cyclone DDS, and scoped firewall settings when needed.

## 6. Start the Pi hardware service

On the Raspberry Pi:

```bash
sudo systemctl enable --now my-bot-robot.service
systemctl is-active my-bot-robot.service
```

Expected:

```text
active
```

Check the Pi logs if it is not active:

```bash
sudo journalctl -u my-bot-robot.service -n 100 --no-pager -o cat
```

## 7. Run Diagnostics

From the Windows Start menu, select:

**IntelliTrolley Central → Diagnostics**

Diagnostics checks:

- WSL 2 and Ubuntu 22.04;
- mirrored WSL networking;
- the installed central workspace;
- the map layers;
- the saved robot peer and ROS domain;
- scoped Windows and Hyper-V firewall rules;
- Pi reachability;
- Pi ROS topics;
- navigation action availability; and
- the required TF relationships.

Before Navigation starts, warnings about the navigation action or
`map → odom` can be expected. Warnings about Pi hardware topics should be
investigated if the Pi service is active and both computers are on the same
robot network.

## 8. Start Navigation

Before starting:

- Place the robot on the floor only after raised-wheel testing has passed.
- Make sure the path is clear.
- Confirm the emergency/operator stop is available.
- Confirm Diagnostics can see the Pi hardware topics.

From the Windows Start menu, select:

**IntelliTrolley Central → Start Navigation**

Navigation starts the central ROS stack, localization, Nav2, Mission Control,
and the dashboard. The browser opens at:

```text
http://127.0.0.1:8000/ui
```

Keep the launcher console open because it contains the central runtime logs.

The dashboard **Stop** button cancels active navigation goals. To shut down the
entire central process stack, use:

**IntelliTrolley Central → Stop IntelliTrolley**

Stopping the central stack removes the remote navigation command stream. The
Pi command timeout and local safety chain independently stop motor commands.

## Windows shortcuts and PowerShell scripts

| Shortcut or script | Purpose |
|---|---|
| **Start Navigation** / `Start-IntelliTrolley.ps1 -Mode navigation` | Starts localization, Nav2, Mission Control, and the navigation dashboard. This is the normal operating command. |
| **Stop IntelliTrolley** / `Stop-IntelliTrolley.ps1` | Requests a controlled shutdown of the central supervisor and its owned ROS/backend processes. |
| **Diagnostics** / `Test-IntelliTrolley.ps1` | Checks WSL, installation, networking, firewall rules, maps, Pi topics, actions, and TF. It does not start robot motion. |
| **Configure Robot Wi-Fi** | Opens `http://zrpi-desktop.local:8090/` in the default Windows browser without launching WSL. |
| **Finish or Repair Installation** / `Install-IntelliTrolley.ps1` | Verifies the package manifest, installs or repairs the WSL payload, rebuilds required components, and preserves mutable robot data. |
| **Uninstall** / `Uninstall-IntelliTrolley.ps1` | Stops IntelliTrolley and removes the installed application. It preserves maps, missions, and logs unless removal is explicitly requested. It does not unregister Ubuntu. |
| `Apply-IntelliTrolleyProvisioning.ps1` | Internal handler for the validated `intellitrolley://` link from the Pi provisioning page. Normally it should not be launched manually. |
| `IntelliTrolley.Common.ps1` | Shared validation, WSL, settings, IP/subnet, and firewall functions used by the other Windows scripts. It is not a standalone command. |

## Commands available inside Ubuntu

The Windows scripts call a Linux wrapper named `intellitrolley-central`.
Advanced troubleshooting can use it from `Ubuntu-22.04`:

| Command | Purpose |
|---|---|
| `intellitrolley-central start navigation` | Starts the supported navigation stack. |
| `intellitrolley-central stop` | Stops the central supervisor. |
| `intellitrolley-central doctor` | Runs the Linux-side installation and ROS checks. |
| `intellitrolley-central configure-network <robot-ip> <domain>` | Regenerates the explicit Cyclone DDS peer and central environment. |
| `intellitrolley-central version` | Prints the installed package version. |
| `intellitrolley-central uninstall keep-data` | Removes the application while preserving mutable data. Prefer the Windows uninstaller for normal use. |

Supporting Linux scripts:

| Script | Purpose |
|---|---|
| `central/setup_wsl.sh` | Installs ROS 2 Humble dependencies, creates the Python environment, initializes data/configuration directories, and builds the workspace. Called by installation or repair. |
| `central/start_central_stack.sh` | Owns the navigation ROS process group and Mission Control backend, verifies prerequisites, and prevents duplicate central stacks. |
| `central/stop_central_stack.sh` | Sends a controlled interrupt to the central supervisor and waits for shutdown. |
| `central/central_doctor.sh` | Performs Linux-side package, map, ROS topic, action, and TF checks. |
| `central/configure_central_network.sh` | Validates the private Pi address and ROS domain, writes the central environment, and generates Cyclone DDS configuration. |
| `packaging/linux/install_payload.sh` | Installs a versioned release under the Linux application directory and restores the previous release if installation fails. |

## Data and update locations

Application releases:

```text
~/.local/opt/intellitrolley
```

Maps, destinations, mission history, and logs:

```text
~/.local/share/intellitrolley
```

Network and application configuration:

```text
~/.config/intellitrolley
```

Repairing or upgrading the application does not overwrite mutable robot data.

## Troubleshooting

### Installation exits with code 3010

Windows or Ubuntu needs a manual initialization step:

1. Restart Windows if requested.
2. Open `Ubuntu-22.04`.
3. Finish creating the Linux user.
4. Close Ubuntu with `exit`.
5. Run **Finish or Repair Installation**.

### Installation exits with another error

Run **Finish or Repair Installation** and keep the PowerShell window open.
Record the final error lines. Also run:

```powershell
wsl --status
wsl --list --verbose
wsl -d Ubuntu-22.04 -- cat /etc/os-release
wsl -d Ubuntu-22.04 -- id -u
```

Do not unregister Ubuntu merely because IntelliTrolley installation failed.
The command `wsl --unregister Ubuntu-22.04` permanently deletes that
distribution and its data.

### Browser does not open

After Start Navigation reports healthy, open:

```text
http://127.0.0.1:8000/ui
```

Then run Diagnostics and inspect the launcher console.

### Pi is not discovered

Check:

1. Windows and the Pi are on the same private network.
2. The saved Pi address is current.
3. Both sides use ROS domain `0` unless deliberately configured otherwise.
4. WSL mirrored networking passes Diagnostics.
5. Scoped firewall rules pass Diagnostics.
6. `my-bot-robot.service` is active on the Pi.

Open **Configure Robot Wi-Fi** after any robot-network change and use the
portal's Windows handoff if the Pi address or subnet changed.

### Previous broad WSL firewall policy

If Diagnostics reports the old broad
`DefaultInboundAction=Allow` troubleshooting policy, first verify that the
scoped IntelliTrolley rules pass. If no unrelated WSL service needs broad
inbound access, restore the default from Administrator PowerShell:

```powershell
Set-NetFirewallHyperVVMSetting `
  -Name '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' `
  -DefaultInboundAction Block
wsl --shutdown
```

## Advanced ZIP installation

The ZIP distribution is a troubleshooting fallback. Extract it to a normal
local Windows directory; do not execute scripts from inside the ZIP.

Open Administrator PowerShell in its `windows` directory:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\Install-IntelliTrolley.ps1
.\Test-IntelliTrolley.ps1
.\Start-IntelliTrolley.ps1 -Mode navigation
```

To stop:

```powershell
.\Stop-IntelliTrolley.ps1
```

## Network security

Mission Control currently assumes a trusted private robot or facility network.
Do not forward TCP port `8000` through an internet router and do not attach the
unauthenticated preview interface to a public or untrusted network.
