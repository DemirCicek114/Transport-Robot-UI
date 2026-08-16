"""Regression checks for the central-computer integration boundary."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from mission_control.robot_adapter import Ros2AdapterConfig, Ros2RobotAdapter


MISSION_CONTROL_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MISSION_CONTROL_ROOT.parent
CENTRAL_ROOT = REPOSITORY_ROOT / "central"
WINDOWS_ROOT = REPOSITORY_ROOT / "windows"
PACKAGING_ROOT = REPOSITORY_ROOT / "packaging"


class TestCentralContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.adapter_source = (
            MISSION_CONTROL_ROOT / "mission_control" / "robot_adapter.py"
        ).read_text(encoding="utf-8")
        cls.app_source = (MISSION_CONTROL_ROOT / "app.py").read_text(
            encoding="utf-8"
        )
        cls.start_script = (CENTRAL_ROOT / "start_central_stack.sh").read_text(
            encoding="utf-8"
        )
        cls.setup_script = (CENTRAL_ROOT / "setup_wsl.sh").read_text(
            encoding="utf-8"
        )
        cls.configure_network_script = (
            CENTRAL_ROOT / "configure_central_network.sh"
        ).read_text(encoding="utf-8")
        cls.windows_common = (
            WINDOWS_ROOT / "IntelliTrolley.Common.ps1"
        ).read_text(encoding="utf-8")
        cls.windows_install = (
            WINDOWS_ROOT / "Install-IntelliTrolley.ps1"
        ).read_text(encoding="utf-8")
        cls.windows_network = (
            WINDOWS_ROOT / "Configure-IntelliTrolleyNetwork.ps1"
        ).read_text(encoding="utf-8")
        cls.windows_provisioning = (
            WINDOWS_ROOT / "Apply-IntelliTrolleyProvisioning.ps1"
        ).read_text(encoding="utf-8")
        cls.windows_doctor = (
            WINDOWS_ROOT / "Test-IntelliTrolley.ps1"
        ).read_text(encoding="utf-8")
        cls.package_script = (
            PACKAGING_ROOT / "build_central_package.sh"
        ).read_text(encoding="utf-8")
        cls.installer_script = (
            PACKAGING_ROOT / "build_windows_installer.sh"
        ).read_text(encoding="utf-8")
        cls.nsis_setup = (
            PACKAGING_ROOT / "windows" / "IntelliTrolleySetup.nsi"
        ).read_text(encoding="utf-8")
        cls.nsis_launcher = (
            PACKAGING_ROOT / "windows" / "IntelliTrolleyLauncher.nsi"
        ).read_text(encoding="utf-8")

    def test_ros_defaults_follow_the_pi_central_contract(self) -> None:
        config = Ros2AdapterConfig()
        self.assertEqual(config.odom_topic, "/diff_cont/odom")
        self.assertEqual(config.filtered_scan_topic, "/scan_filtered")
        self.assertEqual(config.pi_ready_topic, "/robot_health/ready")
        self.assertEqual(config.navigation_command_topic, "/cmd_vel_nav_raw")
        self.assertEqual(config.joystick_topic, "/cmd_vel_joy")
        self.assertEqual(config.battery_topic, "/battery_state")

    def test_retired_power_and_operator_latch_interfaces_are_absent(self) -> None:
        active_sources = "\n".join((self.adapter_source, self.app_source))
        self.assertNotIn("/robot/power_command", active_sources)
        self.assertNotIn("/robot_health/operator_stop_active", active_sources)
        self.assertNotIn("set_power_mode", active_sources)
        self.assertNotIn("safety_lock", active_sources)
        self.assertNotIn("/power/set-mode", active_sources)

    def test_central_outputs_do_not_bypass_pi_motion_safety(self) -> None:
        active_sources = "\n".join((self.adapter_source, self.start_script))
        for forbidden_topic in (
            "/cmd_vel_nav_safe",
            "/cmd_vel_joy_safe",
            "/cmd_vel_safety",
            "/diff_cont/cmd_vel_unstamped",
        ):
            self.assertNotIn(forbidden_topic, active_sources)
        self.assertIn("/cmd_vel_nav_raw", self.start_script)
        self.assertIn("/cmd_vel_joy", self.adapter_source)
        self.assertIn("MISSION_CONTROL_ROS2_LAUNCHER_MODE=supervised", self.start_script)

    def test_supervisor_has_exclusive_modes_and_controlled_shutdown(self) -> None:
        for mode in ("navigation", "mapping", "ui-only"):
            self.assertIn(mode, self.start_script)
        self.assertIn("flock -n", self.start_script)
        self.assertIn("start_owned_process", self.start_script)
        self.assertIn("central-server.pid", self.start_script)
        self.assertIn("exec 9>&-", self.start_script)
        self.assertIn(
            'kill -0 -- "-${process_group_id}"',
            self.start_script,
        )
        self.assertIn(
            'stop_process_group "${process_group_id}" TERM',
            self.start_script,
        )
        self.assertIn(
            'stop_process_group "${process_group_id}" KILL',
            self.start_script,
        )
        self.assertIn("publish_navigation_zeros", self.start_script)
        self.assertIn("trap shutdown_stack", self.start_script)
        self.assertNotIn("rm -rf", self.start_script)

    def test_supervisor_scripts_are_executable(self) -> None:
        for script_name in (
            "start_central_stack.sh",
            "stop_central_stack.sh",
            "central_doctor.sh",
            "configure_central_network.sh",
            "setup_wsl.sh",
        ):
            script = CENTRAL_ROOT / script_name
            self.assertTrue(script.is_file())
            self.assertTrue(os.access(script, os.X_OK))

    def test_browser_api_is_same_origin_by_default(self) -> None:
        self.assertNotIn("CORSMiddleware", self.app_source)
        self.assertNotIn("allow_origins", self.app_source)

    def test_supervised_map_catalog_does_not_use_launcher_bridge(self) -> None:
        adapter = object.__new__(Ros2RobotAdapter)
        adapter._config = Ros2AdapterConfig(launcher_mode="supervised")
        self.assertTrue(adapter._catalog_launcher_enabled())
        self.assertFalse(adapter._local_launcher_enabled())

    def test_phase_one_windows_package_surface_is_complete(self) -> None:
        for script_name in (
            "Install-IntelliTrolley.ps1",
            "Start-IntelliTrolley.ps1",
            "Stop-IntelliTrolley.ps1",
            "Test-IntelliTrolley.ps1",
            "Uninstall-IntelliTrolley.ps1",
            "IntelliTrolley.Common.ps1",
            "Configure-IntelliTrolleyNetwork.ps1",
            "Apply-IntelliTrolleyProvisioning.ps1",
        ):
            self.assertTrue((WINDOWS_ROOT / script_name).is_file())
        self.assertTrue((WINDOWS_ROOT / "README.md").is_file())
        self.assertTrue((PACKAGING_ROOT / "build_central_package.sh").is_file())
        self.assertTrue((PACKAGING_ROOT / "build_windows_installer.sh").is_file())
        self.assertTrue(
            (PACKAGING_ROOT / "windows" / "IntelliTrolleySetup.nsi").is_file()
        )
        self.assertTrue(
            (PACKAGING_ROOT / "windows" / "IntelliTrolleyLauncher.nsi").is_file()
        )

    def test_package_excludes_mutable_and_generated_content(self) -> None:
        for excluded_pattern in (
            "__pycache__/",
            "*.pyc",
            ".pytest_cache/",
            ".venv*",
            "*.sqlite3",
        ):
            self.assertIn(excluded_pattern, self.package_script)
        self.assertIn("release-manifest.json", self.package_script)
        self.assertIn("sha256sum", self.package_script)
        self.assertIn("PACKAGE-VERSION", self.package_script)

    def test_clean_wsl_payload_copy_does_not_require_rsync(self) -> None:
        install_payload = (
            PACKAGING_ROOT / "linux" / "install_payload.sh"
        ).read_text(encoding="utf-8")
        self.assertIn('cp -a "${SOURCE_WORKSPACE}/."', install_payload)
        self.assertNotIn("rsync", install_payload)

    def test_windows_launchers_use_fixed_wsl_wrapper_and_never_unregister_distro(self) -> None:
        windows_source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(WINDOWS_ROOT.glob("*.ps1"))
        )
        self.assertIn('ValidateSet("navigation", "mapping", "ui-only")', windows_source)
        self.assertIn('"intellitrolley-central"', windows_source)
        self.assertIn("Test-IntelliTrolleyReleaseManifest", windows_source)
        self.assertNotIn("--unregister", windows_source)
        self.assertNotIn("Invoke-Expression", windows_source)

    def test_native_installer_embeds_verified_package_and_safe_shortcuts(self) -> None:
        self.assertIn('File /r "${PACKAGE_ROOT}\\*"', self.nsis_setup)
        self.assertIn("Install-IntelliTrolley.ps1", self.nsis_setup)
        self.assertIn("IntelliTrolley-Central.exe", self.nsis_setup)
        self.assertNotIn("/ui-only", self.nsis_setup)
        self.assertNotIn("Launch the IntelliTrolley UI test", self.nsis_setup)
        self.assertNotIn("MUI_FINISHPAGE_RUN", self.nsis_setup)
        self.assertIn("/navigation", self.nsis_setup)
        self.assertIn(
            'Delete "$SMPROGRAMS\\IntelliTrolley Central\\Start Mapping.lnk"',
            self.nsis_setup,
        )
        self.assertNotIn(
            'CreateShortCut \\\n    "$SMPROGRAMS\\IntelliTrolley Central\\Start Mapping.lnk"',
            self.nsis_setup,
        )
        self.assertIn("Configure Robot Wi-Fi.lnk", self.nsis_setup)
        self.assertIn("Uninstall-IntelliTrolley.ps1", self.nsis_setup)
        self.assertNotIn("--unregister", self.nsis_setup)
        self.assertIn("build_central_package.sh", self.installer_script)
        self.assertIn("INSTALL_GUIDE_NAME=", self.installer_script)
        self.assertIn('windows/README.md"', self.installer_script)

    def test_native_launcher_accepts_only_fixed_actions(self) -> None:
        for action in (
            "/ui-only",
            "/navigation",
            "/mapping",
            "/doctor",
            "/network",
            "/stop",
            "/install",
        ):
            self.assertIn(action, self.nsis_launcher)
        self.assertNotIn("Invoke-Expression", self.nsis_launcher)
        self.assertNotIn("--unregister", self.nsis_launcher)
        self.assertIn("http://zrpi-desktop.local:8090/", self.nsis_launcher)
        network_action = self.nsis_launcher.split("network:", 1)[1].split(
            "provisioning:", 1
        )[0]
        self.assertNotIn("wsl.exe", network_action)
        self.assertNotIn("Configure-IntelliTrolleyNetwork.ps1", network_action)

    def test_pi_provisioning_handoff_uses_validated_custom_protocol(self) -> None:
        self.assertIn(
            'WriteRegStr HKLM "Software\\Classes\\intellitrolley"',
            self.nsis_setup,
        )
        self.assertIn('"URL Protocol" ""', self.nsis_setup)
        self.assertIn('StrCmp $1 "intellitrolley://" provisioning', self.nsis_launcher)
        self.assertIn("Apply-IntelliTrolleyProvisioning.ps1", self.nsis_launcher)
        self.assertIn('$uri.Scheme -ne "intellitrolley"', self.windows_provisioning)
        self.assertIn('$uri.Host -ne "configure-network"', self.windows_provisioning)
        self.assertIn(
            "Assert-IntelliTrolleyRobotNetwork",
            self.windows_provisioning,
        )
        self.assertIn(
            "Configure-IntelliTrolleyNetwork.ps1",
            self.windows_provisioning,
        )
        self.assertNotIn("Invoke-Expression", self.windows_provisioning)

    def test_windows_installer_configures_scoped_robot_networking(self) -> None:
        combined_source = "\n".join(
            (
                self.windows_common,
                self.windows_install,
                self.windows_network,
                self.nsis_setup,
            )
        )
        self.assertIn("networkingMode=mirrored", combined_source)
        self.assertIn("New-NetFirewallHyperVRule", combined_source)
        self.assertIn("New-NetFirewallRule", combined_source)
        self.assertIn('"7400-7511"', combined_source)
        self.assertIn('"8000"', combined_source)
        self.assertIn("RobotAddress", combined_source)
        self.assertIn("RobotSubnet", combined_source)
        self.assertIn("RequestExecutionLevel admin", self.nsis_setup)
        self.assertIn("Type CONFIGURE", self.windows_network)
        self.assertIn("sudo systemctl restart my-bot-robot.service", self.windows_network)
        self.assertNotIn("DefaultInboundAction Allow", combined_source)

    def test_network_configuration_is_durable_and_diagnostics_avoid_stale_daemon(self) -> None:
        self.assertIn("--peers", self.configure_network_script)
        self.assertIn("CYCLONEDDS_URI", self.configure_network_script)
        self.assertIn("ROS_DOMAIN_ID", self.configure_network_script)
        self.assertIn("ros2 daemon stop", self.start_script + (
            CENTRAL_ROOT / "central_doctor.sh"
        ).read_text(encoding="utf-8"))
        doctor_source = (CENTRAL_ROOT / "central_doctor.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("--no-daemon", doctor_source)
        self.assertIn("--kill-after", doctor_source)
        self.assertNotIn("timeout 3s ros2", doctor_source)
        self.assertIn("Test-IntelliTrolleyFirewallRules", self.windows_doctor)
        self.assertIn("http://127.0.0.1:8000/health", self.windows_doctor)

    def test_wsl_setup_pins_ros_key_and_installs_only_central_runtime(self) -> None:
        self.assertIn("ROS_KEY_SHA256=", self.setup_script)
        self.assertIn("sha256sum --check --status", self.setup_script)
        self.assertIn(
            "http://packages.ros.org/ros2/ubuntu jammy main",
            self.setup_script,
        )
        self.assertNotIn(
            "https://packages.ros.org/ros2/ubuntu jammy main",
            self.setup_script,
        )
        self.assertIn("ros-humble-navigation2", self.setup_script)
        self.assertIn("ros-humble-slam-toolbox", self.setup_script)
        self.assertIn("colcon build --symlink-install --packages-select my_bot", self.setup_script)
        self.assertNotIn("rosdep install --from-paths", self.setup_script)


if __name__ == "__main__":
    unittest.main()
