# IntelliTrolley Mission Control

The web UI and Mission Control backend live in
[`mission_control_poc`](mission_control_poc/README.md). The central-computer
supervisor and setup commands live in [`central`](central/README.md).

For local UI work without ROS hardware:

```bash
./central/start_central_stack.sh ui-only
```

## Windows/WSL preview package

Build the native Windows setup executable with:

```bash
./packaging/build_windows_installer.sh
```

This requires the NSIS compiler. The resulting setup executable and its
SHA-256 file are written under `dist/`, along with a standalone Windows
installation guide that can be sent to testers. The setup embeds the clean,
manifest-verified WSL payload and creates native Windows launcher shortcuts.

The lower-level ZIP/PowerShell package can still be built with:

```bash
./packaging/build_central_package.sh
```

The ZIP and its SHA-256 file are written under `dist/`. It contains the
PowerShell installer/start/stop/doctor/uninstall layer, a clean WSL payload,
and the central ROS/Mission Control source. Generated environments, caches,
runtime databases, logs, and user data are excluded.

The executable is currently unsigned and is intended for private Windows/WSL
acceptance before code signing and public distribution.

The installer also registers the validated `intellitrolley://` URL protocol.
The Pi recovery provisioning page uses it after a confirmed facility-network
switch to launch the existing administrator-approved Windows/WSL network
configuration flow. Wi-Fi passwords never enter the URL or the central
package.
