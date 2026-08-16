"""Static contract checks for the dependency-free Mission Control web UI."""

from __future__ import annotations

import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
UI_ROOT = PROJECT_ROOT / "ui"


class _IdCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name == "id" and value:
                self.ids.append(value)


class TestUiContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (UI_ROOT / "index.html").read_text(encoding="utf-8")
        cls.css = (UI_ROOT / "styles.css").read_text(encoding="utf-8")
        cls.javascript = (UI_ROOT / "app.js").read_text(encoding="utf-8")

    def test_every_javascript_element_id_exists_once(self) -> None:
        parser = _IdCollector()
        parser.feed(self.html)
        duplicate_ids = sorted({element_id for element_id in parser.ids if parser.ids.count(element_id) > 1})
        self.assertEqual(duplicate_ids, [])

        referenced_ids = set(re.findall(r'getElementById\("([^"]+)"\)', self.javascript))
        missing_ids = sorted(referenced_ids - set(parser.ids))
        self.assertEqual(missing_ids, [])

    def test_navigation_stop_is_available_without_extra_helper_copy(self) -> None:
        self.assertIn(">Stop navigation</span>", self.html)
        self.assertIn('title="Stop navigation"', self.html)
        self.assertIn('"Navigation stopped."', self.javascript)
        self.assertNotIn("stop-disclaimer", self.html)
        self.assertNotIn("route-stop-note", self.html)
        self.assertNotIn("data-power-mode", self.html)
        self.assertNotIn("Robot On", self.html)

    def test_stale_state_locks_motion_and_drops_pending_manual_commands(self) -> None:
        self.assertIn("Motion controls are locked while Mission Control reconnects.", self.html)
        self.assertIn("function isControlDataFresh()", self.javascript)
        self.assertIn("state.manualDrive.pendingCommand = null;", self.javascript)
        self.assertIn("commandExpired", self.javascript)
        self.assertIn("data-stale", self.css)

    def test_manual_recovery_uses_backend_readiness_and_keeps_navigation_paused(self) -> None:
        self.assertIn("data.manual_drive_available ?? manualDrive.ready", self.javascript)
        self.assertIn("manualDriveReady", self.javascript)
        self.assertIn("MANUAL_TICK_MS = 100", self.javascript)
        self.assertIn("body.command?.paused_mission_id", self.javascript)
        self.assertIn(
            "It will stay paused until an operator resumes it.",
            self.javascript,
        )

    def test_last_known_pose_remains_visible_when_localization_is_uncertain(self) -> None:
        self.assertIn(
            "{ localizationValid: Boolean(Number(markers.robot.localization_valid)) }",
            self.javascript,
        )
        self.assertIn("ctx.globalAlpha = localizationValid ? 1 : 0.48;", self.javascript)
        self.assertIn('ctx.strokeStyle = "#ad6800";', self.javascript)
        visibility_function = re.search(
            r"function isRobotVisibleOnMap\(robot\) \{(?P<body>.*?)\n\}",
            self.javascript,
            re.DOTALL,
        ).group("body")
        self.assertIn("robot.x != null", visibility_function)
        self.assertIn("robot.y != null", visibility_function)
        self.assertIn("acceptedMapPose", visibility_function)
        self.assertIn("accepted_map_pose_available", visibility_function)
        self.assertIn("Number.isFinite(Number(robot.x))", visibility_function)

    def test_localization_safety_pause_message_is_presented(self) -> None:
        failure_function = re.search(
            r"function getLocalizationFailureMessage\(.*?\) \{(?P<body>.*?)\n\}",
            self.javascript,
            re.DOTALL,
        ).group("body")
        self.assertIn('"safety_paused"', failure_function)
        self.assertIn('"invalid_jump"', failure_function)

    def test_localization_warning_accepts_sqlite_boolean_values(self) -> None:
        warning_function = re.search(
            r"function robotWarningLabel\(.*?\) \{(?P<body>.*?)\n\}",
            self.javascript,
            re.DOTALL,
        ).group("body")
        self.assertIn("robot?.localization_valid != null", warning_function)
        self.assertIn("Number(robot.localization_valid) === 0", warning_function)

    def test_resume_waits_for_live_navigation_readiness(self) -> None:
        action_function = re.search(
            r"function buildMissionActionButton\(.*?\) \{(?P<body>.*?)\n\}",
            self.javascript,
            re.DOTALL,
        ).group("body")
        self.assertIn('if (action === "resume")', action_function)
        self.assertIn("getRobotReadiness(assignedRobot)", action_function)
        self.assertIn("!readiness.navigationReady", action_function)
        self.assertIn('disabled aria-disabled="true"', action_function)

    def test_active_route_state_precedes_idle_navigation_readiness(self) -> None:
        brain_function = re.search(
            r"function renderRobotBrain\(\) \{(?P<body>.*?)\n\}",
            self.javascript,
            re.DOTALL,
        ).group("body")
        route_state_index = brain_function.index("routeStateVisible")
        readiness_index = brain_function.index("!readiness.navigationReady")
        self.assertLess(route_state_index, readiness_index)
        self.assertIn('"En-route"', brain_function)

    def test_compact_robot_state_and_operating_mode_are_visible(self) -> None:
        self.assertIn('id="operation-mode-badge"', self.html)
        for field_id in (
            "robot-state-pi",
            "robot-state-battery",
            "robot-state-latency",
        ):
            self.assertIn(f'id="{field_id}"', self.html)
        self.assertIn("Applied Science Building", self.javascript)
        self.assertNotIn('id="robot-readiness-list"', self.html)

    def test_robot_brain_uses_the_readiness_panel_data(self) -> None:
        self.assertIn("const data = readiness.data;", self.javascript)
        self.assertIn(
            'data?.navigation?.message || "Waiting for localization and Nav2."',
            self.javascript,
        )
        self.assertIn(".robot-brain-head strong", self.css)
        self.assertIn("overflow-wrap: anywhere;", self.css)
        self.assertIn("white-space: normal;", self.css)

    def test_idle_status_waits_for_full_navigation_readiness(self) -> None:
        self.assertIn("function navigationLockLabel(readiness)", self.javascript)
        self.assertIn('return "Starting Nav2";', self.javascript)
        self.assertIn(
            "setText(elements.startLock, robot ? navigationLockLabel(readiness)",
            self.javascript,
        )
        self.assertIn(
            "setText(elements.stateLock, robot ? navigationLockLabel(readiness)",
            self.javascript,
        )

    def test_stationary_global_localization_button_is_not_operator_visible(self) -> None:
        self.assertNotIn('id="localize-robot-button"', self.html)
        self.assertNotIn("handleLocalizeRobot", self.javascript)
        self.assertNotIn("/localize`", self.javascript)

    def test_battery_fallback_uses_the_full_6s_lipo_voltage_range(self) -> None:
        self.assertIn("BATTERY_DISCHARGE_CURVE", self.javascript)
        self.assertIn("[24.0, 80]", self.javascript)
        self.assertIn("[25.2, 100]", self.javascript)
        self.assertNotIn("(Number(voltage) - 20.0) / 4.0", self.javascript)

    def test_battery_display_uses_stable_ten_percent_steps(self) -> None:
        self.assertIn("const BATTERY_DISPLAY_STEP = 10;", self.javascript)
        self.assertIn("const BATTERY_DISPLAY_HYSTERESIS = 2;", self.javascript)
        self.assertIn("function batteryPercentForDisplay(robot)", self.javascript)
        self.assertIn("batteryDisplayByRobot: new Map()", self.javascript)
        self.assertNotIn('`${formatNumber(battery)}%`', self.javascript)

    def test_phone_layout_has_dedicated_breakpoints(self) -> None:
        self.assertIn("@media (max-width: 620px)", self.css)
        self.assertIn("@media (max-width: 430px)", self.css)
        self.assertIn("100dvh", self.css)
        self.assertIn('id="robot-brain-panel"', self.html)

    def test_selected_room_labels_render_below_their_targets(self) -> None:
        for destination in ("asb 9971", "asb 980", "asb 9705", "asb 9703"):
            self.assertIn(f'"{destination}"', self.javascript)
        self.assertIn("DESTINATION_LABELS_BELOW_TARGET", self.javascript)
        self.assertIn('" is-below-target"', self.javascript)
        self.assertIn("DESTINATION_LABEL_OFFSET_M = 4.25", self.javascript)
        self.assertIn("? -DESTINATION_LABEL_OFFSET_M", self.javascript)
        self.assertIn(": DESTINATION_LABEL_OFFSET_M", self.javascript)
        self.assertIn("location.pose.y + verticalOffset", self.javascript)
        self.assertIn("worldToCanvasWithFrame(labelPose, frame)", self.javascript)
        self.assertIn(".map-destination-chip.is-below-target", self.css)
        self.assertIn("transform: translate(-50%, -100%)", self.css)
        self.assertIn("transform: translate(-50%, 0)", self.css)
        self.assertIn("border-bottom: 8px solid", self.css)

    def test_dashboard_canvas_and_destination_overlay_share_one_coordinate_space(self) -> None:
        self.assertIn("function syncDashboardCanvasSize", self.javascript)
        self.assertIn("elements.stateMapCanvas.width = targetWidth", self.javascript)
        self.assertIn("elements.stateMapCanvas.height = targetHeight", self.javascript)
        self.assertIn('window.addEventListener("resize", () => syncDashboardCanvasSize())', self.javascript)
        self.assertIn("position: absolute;", self.css)

    def test_saved_locations_are_collapsed_until_requested_or_searched(self) -> None:
        self.assertIn('id="location-results-toggle"', self.html)
        self.assertIn('class="location-results hidden" id="location-results"', self.html)
        self.assertIn("state.operatorPanel.locationsExpanded || Boolean(filter)", self.javascript)
        self.assertIn('setAttribute("aria-expanded", resultsVisible ? "true" : "false")', self.javascript)
        self.assertIn(".location-results-toggle", self.css)

    def test_temp_map_click_destination_is_never_operator_visible(self) -> None:
        self.assertIn(
            'const INTERNAL_DESTINATION_NAMES = new Set(["temp destination"]);',
            self.javascript,
        )
        self.assertIn(
            "(payload.destinations ?? []).filter(isOperatorVisibleDestination)",
            self.javascript,
        )
        self.assertIn("function isInternalDestinationName(name)", self.javascript)
        self.assertIn("function isOperatorVisibleDestination(destination)", self.javascript)

    def test_map_cursor_has_a_high_contrast_custom_asset(self) -> None:
        cursor_svg = (UI_ROOT / "map-cursor.svg").read_text(encoding="utf-8")
        self.assertIn('--map-target-cursor: url("/ui-assets/map-cursor.svg', self.css)
        self.assertGreaterEqual(self.css.count("cursor: var(--map-target-cursor);"), 4)
        self.assertIn("#050b0e", cursor_svg)
        self.assertIn("#fff200", cursor_svg)

    def test_map_clicks_only_accept_confirmed_open_cells(self) -> None:
        self.assertIn("MAP_FREE_OCCUPANCY_MAX = 0", self.javascript)
        self.assertIn("function mapOccupancyAtWorld(mapData, world)", self.javascript)
        self.assertIn("function isWorldPointOpen(mapData, world)", self.javascript)
        self.assertIn("occupancy >= 0 && occupancy <= MAP_FREE_OCCUPANCY_MAX", self.javascript)
        self.assertGreaterEqual(
            self.javascript.count("isWorldPointOpenForCanvas("),
            3,
        )
        self.assertIn(
            "Choose a white open area. Gray, black, and unknown areas cannot be selected.",
            self.javascript,
        )

    def test_visible_state_controls_are_wired(self) -> None:
        self.assertIn('id="cancel-current-mission-button"', self.html)
        self.assertIn("return canEnableManualDrive(robot);", self.javascript)
        self.assertNotIn("Enable Manual Control", self.html)
        self.assertIn(
            'document.body.addEventListener("pointerdown", handleManualPadPointerDown);',
            self.javascript,
        )
        self.assertIn('document.querySelectorAll(".manual-drive-shell")', self.javascript)
        self.assertIn('document.querySelectorAll("[data-manual-message]")', self.javascript)

    def test_dashboard_manual_controls_use_a_compact_safe_popover(self) -> None:
        self.assertIn('id="manual-mode-button"', self.html)
        self.assertIn('aria-controls="manual-control-panel"', self.html)
        self.assertIn('id="manual-control-panel"', self.html)
        self.assertEqual(self.html.count('id="manual-control-panel"'), 1)
        self.assertEqual(self.html.count('id="manual-mode-button"'), 1)
        self.assertIn("function closeManualControls()", self.javascript)
        self.assertIn('elements.manualModeButton.setAttribute("aria-expanded", "true")', self.javascript)
        self.assertIn("stopManualDrive({ sendStop: isControlDataFresh(), silent: true });", self.javascript)
        self.assertIn(".manual-control-popover", self.css)


if __name__ == "__main__":
    unittest.main()
