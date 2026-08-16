# IntelliTrolley Central preview package

This preview is available as either the native Windows setup executable or the
lower-level ZIP/PowerShell package. The setup executable is not digitally
signed yet, so Windows SmartScreen may warn during testing.

The native setup creates Start-menu shortcuts for navigation, diagnostics,
the Pi Wi-Fi portal, stopping, repair, and uninstall. It wraps
the same manifest-verified WSL installer
included in the ZIP. Application releases live under
`~/.local/opt/intellitrolley`; maps, destinations, logs, settings, and mission
history remain outside the release under `~/.local/share/intellitrolley`.

The setup network page configures mirrored WSL networking, an explicit central
DDS peer, and app-owned firewall rules scoped to the selected Pi and private
robot subnet (plus Windows loopback for the local browser). The separate
Configure Robot Wi-Fi shortcut opens `http://zrpi-desktop.local:8090/` in the
default Windows browser when the computer is on the same network as the Pi.
UI-only mode remains available without a Pi. Phone access and physical robot
motion still require their respective acceptance tests.
