const state = {
  destinations: [],
  home: null,
  robots: [],
  missions: [],
  socket: null,
  connection: {
    websocketConnected: false,
    lastStatusAt: 0,
    lastOperatorAt: 0,
    reconnectTimer: null,
    watchTimer: null,
  },
  activeScreen: "start",
  robotSelectionTouched: false,
  operatorPanel: {
    data: null,
    mapPreview: null,
    mapPreviewName: null,
    mapPreviewInFlight: false,
    refreshTimer: null,
    renderedMapKey: null,
    renderedMapCanvas: null,
    frames: {},
    addMapMode: "move",
    locationFilter: "",
    locationsExpanded: false,
    pendingGoal: null,
    initialPoseMode: false,
    mapView: {
      zoom: 1,
      panX: 0,
      panY: 0,
      followRobot: false,
      isPanning: false,
      panPointerId: null,
      startClientX: 0,
      startClientY: 0,
      startPanX: 0,
      startPanY: 0,
      pointerMoved: false,
    },
  },
  returnPromptDismissed: new Set(),
  manualDrive: {
    activeTimer: null,
    currentLinear: 0,
    currentAngular: 0,
    requestInFlight: false,
    pendingCommand: null,
    activeButton: null,
  },
  pointMissionInFlight: false,
  batteryDisplayByRobot: new Map(),
};

const MANUAL_BASE_SPEED = 0.5;
const MANUAL_MAX_SPEED = 0.7;
const MANUAL_ACCEL_RATE = 0.1;
const MANUAL_TICK_MS = 100;
const DEFAULT_MAP_NAME = "downstairs_test_july1";
const DASHBOARD_MAP_DISPLAY_NAME = "Applied Science Building";
const DESTINATION_LABELS_BELOW_TARGET = new Set([
  "asb 9971",
  "asb 980",
  "asb 9705",
  "asb 9703",
]);
const DESTINATION_LABEL_OFFSET_M = 4.25;
const DASHBOARD_MIN_ZOOM = 0.75;
const DASHBOARD_MAX_ZOOM = 4;
const ROBOT_FOCUS_ZOOM = 1.6;
const ROBOT_FOOTPRINT_FRONT_M = 0.9;
const ROBOT_FOOTPRINT_REAR_M = 0.1;
const ROBOT_FOOTPRINT_WIDTH_M = 0.6;
const MAP_FREE_OCCUPANCY_MAX = 0;
const INTERNAL_DESTINATION_NAMES = new Set(["temp destination"]);
const OPEN_AREA_CLICK_MESSAGE = "Choose a white open area. Gray, black, and unknown areas cannot be selected.";
const MESSAGE_SUCCESS_TIMEOUT_MS = 4500;
const MESSAGE_ERROR_TIMEOUT_MS = 7000;
const STATUS_STALE_AFTER_MS = 4000;
const OPERATOR_STALE_AFTER_MS = 7000;
const BATTERY_DISCHARGE_CURVE = [
  [20.4, 0],
  [21.0, 5],
  [21.6, 10],
  [22.2, 20],
  [22.5, 30],
  [22.8, 40],
  [23.1, 50],
  [23.4, 60],
  [23.7, 70],
  [24.0, 80],
  [24.3, 88],
  [24.6, 94],
  [24.9, 98],
  [25.2, 100],
];
const BATTERY_DISPLAY_STEP = 10;
const BATTERY_DISPLAY_HYSTERESIS = 2;
const messageTimers = new WeakMap();
const elements = {
  selectedRobot: document.getElementById("selected-robot"),
  selectedRobotBattery: document.getElementById("selected-robot-battery"),
  selectedRobotBatteryFill: document.getElementById("selected-robot-battery-fill"),
  selectedRobotBatteryValue: document.getElementById("selected-robot-battery-value"),
  serverConnectionBadge: document.getElementById("server-connection-badge"),
  serverConnectionLabel: document.getElementById("server-connection-label"),
  connectionAlert: document.getElementById("connection-alert"),
  operationModeBadge: document.getElementById("operation-mode-badge"),
  dashboardMapTitle: document.getElementById("demo-map-title"),
  dashboardMapShell: document.getElementById("dashboard-map-shell"),
  destinationOverlay: document.getElementById("destination-overlay"),
  locationSearchPanel: document.getElementById("location-search-panel"),
  locationSearch: document.getElementById("location-search"),
  locationResultsToggle: document.getElementById("location-results-toggle"),
  locationResults: document.getElementById("location-results"),
  mapZoomIn: document.getElementById("map-zoom-in"),
  mapZoomOut: document.getElementById("map-zoom-out"),
  mapFit: document.getElementById("map-fit"),
  mapCenterRobot: document.getElementById("map-center-robot"),
  mapConfirmPopover: document.getElementById("map-confirm-popover"),
  mapConfirmText: document.getElementById("map-confirm-text"),
  confirmMapGo: document.getElementById("confirm-map-go"),
  confirmMapCancel: document.getElementById("confirm-map-cancel"),
  navigationStopButton: document.getElementById("navigation-stop-button"),
  robotBrainPanel: document.getElementById("robot-brain-panel"),
  robotBrainTitle: document.getElementById("robot-brain-title"),
  robotStatePi: document.getElementById("robot-state-pi"),
  robotStateBattery: document.getElementById("robot-state-battery"),
  robotStateLatency: document.getElementById("robot-state-latency"),
  headerMode: document.getElementById("header-mode"),
  headerBattery: document.getElementById("header-battery"),
  headerConnection: document.getElementById("header-connection"),
  headerLatency: document.getElementById("header-latency"),
  headerMap: document.getElementById("header-map"),

  startRobotId: document.getElementById("start-robot-id"),
  startMode: document.getElementById("start-mode"),
  startBattery: document.getElementById("start-battery"),
  startConnection: document.getElementById("start-connection"),
  startLatency: document.getElementById("start-latency"),
  startMap: document.getElementById("start-map"),
  startLock: document.getElementById("start-lock"),
  destinationsList: document.getElementById("destinations-list"),
  setInitialPositionButton: document.getElementById("set-initial-position-button"),
  manageMapsButton: document.getElementById("manage-maps-button"),
  startNextButton: document.getElementById("start-next-button"),
  startNextMessage: document.getElementById("start-next-message"),

  manageCurrentMap: document.getElementById("manage-current-map"),
  savedMapSelect: document.getElementById("saved-map-select"),
  selectMapButton: document.getElementById("select-map-button"),
  mappingModeButton: document.getElementById("mapping-mode-button"),
  addDestinationButton: document.getElementById("add-destination-button"),
  manageMessage: document.getElementById("manage-message"),

  mappingStatus: document.getElementById("mapping-status"),
  mappingMapName: document.getElementById("mapping-map-name"),
  startMappingButton: document.getElementById("start-mapping-button"),
  saveMappingButton: document.getElementById("save-mapping-button"),
  mappingMessage: document.getElementById("mapping-message"),
  mappingMapCanvas: document.getElementById("mapping-map-canvas"),
  mappingMapPlaceholder: document.getElementById("mapping-map-placeholder"),
  mappingMapMeta: document.getElementById("mapping-map-meta"),

  addSelectedMap: document.getElementById("add-selected-map"),
  addMapModes: document.getElementById("add-map-modes"),
  addMapCanvas: document.getElementById("add-map-canvas"),
  addMapPlaceholder: document.getElementById("add-map-placeholder"),
  destinationName: document.getElementById("destination-name"),
  destinationX: document.getElementById("destination-x"),
  destinationY: document.getElementById("destination-y"),
  destinationYaw: document.getElementById("destination-yaw"),
  useCurrentPoseButton: document.getElementById("use-current-pose-button"),
  initialPoseX: document.getElementById("initial-pose-x"),
  initialPoseY: document.getElementById("initial-pose-y"),
  sendInitialPoseButton: document.getElementById("send-initial-pose-button"),
  saveDestinationButton: document.getElementById("save-destination-button"),
  addDestinationMessage: document.getElementById("add-destination-message"),

  openNewRequestButton: document.getElementById("open-new-request-button"),
  manualModeButton: document.getElementById("manual-mode-button"),
  manualPopoverClose: document.getElementById("manual-popover-close"),
  requestForm: document.getElementById("request-form"),
  requester: document.getElementById("requester"),
  operatorId: document.getElementById("operator-id"),
  requestDestination: document.getElementById("request-destination"),
  tripType: document.getElementById("trip-type"),
  returnDestinationField: document.getElementById("return-destination-field"),
  returnDestination: document.getElementById("return-destination"),
  requestRobot: document.getElementById("request-robot"),
  requestNotes: document.getElementById("request-notes"),
  createRequestButton: document.getElementById("create-request-button"),
  createAnotherButton: document.getElementById("create-another-button"),
  goStateButton: document.getElementById("go-state-button"),
  requestMessage: document.getElementById("request-message"),

  pendingRequestsList: document.getElementById("pending-requests-list"),
  stateMessage: document.getElementById("state-message"),
  clearPendingButton: document.getElementById("clear-pending-button"),
  cancelCurrentMissionButton: document.getElementById("cancel-current-mission-button"),
  activeMissionCard: document.getElementById("active-mission-card"),
  stateRobotId: document.getElementById("state-robot-id"),
  stateBattery: document.getElementById("state-battery"),
  stateMode: document.getElementById("state-mode"),
  stateConnection: document.getElementById("state-connection"),
  stateLatency: document.getElementById("state-latency"),
  stateLock: document.getElementById("state-lock"),
  stateWarning: document.getElementById("state-warning"),
  stateMapCanvas: document.getElementById("state-map-canvas"),
  stateMapPlaceholder: document.getElementById("state-map-placeholder"),
  stateMapMeta: document.getElementById("state-map-meta"),
  saveTempDestinationButton: document.getElementById("save-temp-destination-button"),
  mapMessage: document.getElementById("map-message"),
  manualDriveShell: document.getElementById("manual-drive-shell"),
  manualControlPanel: document.getElementById("manual-control-panel"),
  manualPad: document.getElementById("manual-pad"),
  manualStatus: document.getElementById("manual-status"),
  manualMessage: document.getElementById("manual-message"),
  clearCompletedButton: document.getElementById("clear-completed-button"),
  clearAllButton: document.getElementById("clear-all-button"),
  queueMessage: document.getElementById("queue-message"),
  missionsBody: document.getElementById("missions-body"),
  returnModal: document.getElementById("return-modal"),
  returnModalText: document.getElementById("return-modal-text"),
  returnModalButton: document.getElementById("return-modal-button"),
  returnModalStayButton: document.getElementById("return-modal-stay-button"),
  calibrationStatusDot: document.getElementById("calibration-status-dot"),
  calibrationLocalization: document.getElementById("calibration-localization"),
  calibrationInitialPose: document.getElementById("calibration-initial-pose"),
  calibrationGoalPose: document.getElementById("calibration-goal-pose"),
  calibrationUseCurrentPoseButton: document.getElementById("calibration-use-current-pose-button"),
  calibrationPoseX: document.getElementById("calibration-pose-x"),
  calibrationPoseY: document.getElementById("calibration-pose-y"),
  calibrationSendInitialPoseButton: document.getElementById("calibration-send-initial-pose-button"),
  calibrationMessage: document.getElementById("calibration-message"),
};

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-nav-screen]").forEach((button) => {
    button.addEventListener("click", () => showScreen(button.dataset.navScreen));
  });

  elements.selectedRobot.addEventListener("change", async () => {
    state.robotSelectionTouched = true;
    state.manualDrive.pendingCommand = null;
    stopManualDrive({ sendStop: false, silent: true });
    state.operatorPanel.data = null;
    state.connection.lastOperatorAt = 0;
    const robot = getSelectedRobot();
    if (isRobotVisibleOnMap(robot)) {
      enableRobotFollow();
    } else {
      resetDashboardMapView();
    }
    setInitialPoseMode(false, { render: false, announce: false });
    clearPendingMapGoal();
    state.operatorPanel.frames = {};
    await loadOperatorPanel();
    await loadDashboardMapPreview({ silent: true });
    renderAll();
  });

  elements.manageMapsButton.addEventListener("click", () => showScreen("manage"));
  elements.setInitialPositionButton.addEventListener("click", toggleInitialPoseMode);
  elements.locationSearch.addEventListener("input", () => {
    state.operatorPanel.locationFilter = elements.locationSearch.value.trim().toLowerCase();
    renderLocationResults();
  });
  elements.locationResultsToggle.addEventListener("click", () => {
    state.operatorPanel.locationsExpanded = !state.operatorPanel.locationsExpanded;
    renderLocationResults();
  });
  elements.locationResults.addEventListener("click", handleLocationClick);
  elements.destinationOverlay.addEventListener("click", handleLocationClick);
  elements.mapZoomIn.addEventListener("click", () => zoomDashboardMap(1.25));
  elements.mapZoomOut.addEventListener("click", () => zoomDashboardMap(0.8));
  elements.mapFit.addEventListener("click", () => resetDashboardMapView());
  elements.mapCenterRobot.addEventListener("click", centerDashboardMapOnRobot);
  elements.confirmMapGo.addEventListener("click", executePendingMapGoal);
  elements.confirmMapCancel.addEventListener("click", () => {
    setInitialPoseMode(false, { render: false, announce: false });
    clearPendingMapGoal();
  });
  elements.calibrationUseCurrentPoseButton.addEventListener("click", fillCalibrationPoseFromRobot);
  elements.calibrationSendInitialPoseButton.addEventListener("click", handleSendCalibrationInitialPose);
  elements.startNextButton.addEventListener("click", handleStartNext);
  elements.selectMapButton.addEventListener("click", handleSelectMap);
  elements.savedMapSelect.addEventListener("change", () => {
    if (elements.savedMapSelect.value) {
      void handleSelectMap();
    }
  });
  elements.mappingModeButton.addEventListener("click", () => showScreen("mapping"));
  elements.addDestinationButton.addEventListener("click", handleOpenAddDestination);
  elements.startMappingButton.addEventListener("click", handleStartMapping);
  elements.saveMappingButton.addEventListener("click", handleSaveMapping);
  elements.addMapModes.addEventListener("click", handleAddMapMode);
  elements.addMapCanvas.addEventListener("click", handleAddMapClick);
  elements.useCurrentPoseButton.addEventListener("click", fillInitialPoseFromRobot);
  elements.sendInitialPoseButton.addEventListener("click", handleSendInitialPose);
  elements.saveDestinationButton.addEventListener("click", handleSaveDestination);
  elements.openNewRequestButton.addEventListener("click", () => showScreen("new-request"));
  elements.manualModeButton.addEventListener("click", toggleManualControls);
  elements.manualPopoverClose.addEventListener("click", closeManualControls);
  elements.tripType.addEventListener("change", syncTripType);
  elements.requestForm.addEventListener("submit", handleCreateRequest);
  elements.createAnotherButton.addEventListener("click", resetRequestForm);
  elements.goStateButton.addEventListener("click", () => showScreen("state"));
  elements.pendingRequestsList.addEventListener("click", handleStartRequestClick);
  elements.clearPendingButton.addEventListener("click", () =>
    handleQueueReset({
      button: elements.clearPendingButton,
      endpoint: "/admin/requests/clear-pending",
      confirmText: "Clear all pending requests?",
      successLabel: "Pending requests cleared",
      messageTarget: elements.stateMessage,
    })
  );
  elements.cancelCurrentMissionButton.addEventListener("click", handleCancelCurrentMission);
  elements.activeMissionCard.addEventListener("click", handleMissionAction);
  elements.missionsBody.addEventListener("click", handleMissionAction);
  elements.returnModalButton.addEventListener("click", handleMissionAction);
  elements.returnModalStayButton.addEventListener("click", handleReturnStay);
  elements.saveTempDestinationButton.addEventListener("click", handleSaveTempDestination);
  elements.stateMapCanvas.addEventListener("click", handleDashboardMapClick);
  elements.stateMapCanvas.addEventListener("pointerdown", handleDashboardMapPointerDown);
  elements.stateMapCanvas.addEventListener("pointermove", handleDashboardMapPointerMove);
  elements.stateMapCanvas.addEventListener("pointerup", handleDashboardMapPointerUp);
  elements.stateMapCanvas.addEventListener("pointercancel", handleDashboardMapPointerUp);
  elements.stateMapCanvas.addEventListener("contextmenu", (event) => event.preventDefault());
  elements.clearCompletedButton.addEventListener("click", () =>
    handleQueueReset({
      button: elements.clearCompletedButton,
      endpoint: "/admin/missions/clear-completed",
      confirmText: "Clear completed mission history?",
      successLabel: "Completed missions cleared",
    })
  );
  elements.clearAllButton.addEventListener("click", () =>
    handleQueueReset({
      button: elements.clearAllButton,
      endpoint: "/admin/missions/clear-all",
      confirmText: "Clear the started mission queue and history? Pending requests are separate.",
      successLabel: "Queue and history cleared",
    })
  );

  document.body.addEventListener("click", handleNavigationStopAction);
  document.body.addEventListener("pointerdown", handleManualPadPointerDown);

  document.addEventListener("wheel", handleDashboardMapWheel, { capture: true, passive: false });
  window.addEventListener("resize", () => syncDashboardCanvasSize());
  window.addEventListener("pointerup", () => stopManualDrive());
  window.addEventListener("pointercancel", () => stopManualDrive());
  window.addEventListener("blur", () => stopManualDrive({ silent: true }));
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !elements.manualControlPanel.classList.contains("hidden")) {
      closeManualControls();
    }
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      stopManualDrive({ silent: true });
    }
  });

  void boot();
});

async function boot() {
  syncDashboardCanvasSize({ render: false });
  connectStatusStream();
  startConnectionWatch();
  await Promise.allSettled([loadDestinations(), loadSnapshot()]);
  await loadOperatorPanel({ silent: true });
  await loadDashboardMapPreview({ silent: true });
  startOperatorPanelRefresh();
  syncTripType();
  renderAll();
}

async function loadDestinations() {
  const response = await fetch("/destinations");
  const payload = await response.json();
  state.destinations = (payload.destinations ?? []).filter(isOperatorVisibleDestination);
  state.home = isInternalDestinationName(payload.home) ? null : (payload.home ?? null);
  populateDestinationSelects();
  renderDestinations();
}

function isInternalDestinationName(name) {
  return INTERNAL_DESTINATION_NAMES.has(String(name || "").trim().toLowerCase());
}

function isOperatorVisibleDestination(destination) {
  return Boolean(destination?.name) && !isInternalDestinationName(destination.name);
}

async function loadSnapshot() {
  const response = await fetch("/status");
  const payload = await response.json();
  applySnapshot(payload);
}

function connectStatusStream() {
  if (state.socket && [WebSocket.CONNECTING, WebSocket.OPEN].includes(state.socket.readyState)) {
    return;
  }
  if (state.connection.reconnectTimer !== null) {
    window.clearTimeout(state.connection.reconnectTimer);
    state.connection.reconnectTimer = null;
  }
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${window.location.host}/ws/status`);
  state.socket = socket;
  socket.addEventListener("open", () => {
    if (state.socket !== socket) {
      socket.close();
      return;
    }
    state.connection.websocketConnected = true;
    renderConnectionState();
    if (!state.destinations.length) {
      void loadDestinations().catch(() => {});
    }
  });
  socket.addEventListener("message", (event) => {
    if (state.socket !== socket) {
      return;
    }
    try {
      applySnapshot(JSON.parse(event.data));
    } catch (_error) {
      socket.close();
    }
  });
  socket.addEventListener("close", () => {
    if (state.socket !== socket) {
      return;
    }
    state.connection.websocketConnected = false;
    state.manualDrive.pendingCommand = null;
    stopManualDrive({ sendStop: false, silent: true });
    clearPendingMapGoal({ render: false });
    renderConnectionState();
    state.connection.reconnectTimer = window.setTimeout(connectStatusStream, 2000);
  });
  socket.addEventListener("error", () => socket.close());
}

function applySnapshot(snapshot) {
  state.connection.lastStatusAt = Date.now();
  state.robots = snapshot.robots ?? [];
  state.missions = snapshot.missions ?? [];
  populateRobotSelects();
  renderAll();
}

function startConnectionWatch() {
  if (state.connection.watchTimer !== null) {
    window.clearInterval(state.connection.watchTimer);
  }
  state.connection.watchTimer = window.setInterval(() => {
    renderConnectionState();
    renderRobotBrain();
    renderManualAvailability();
    renderLocationResults();
    renderMapConfirmPopover();
  }, 1000);
}

function startOperatorPanelRefresh() {
  if (state.operatorPanel.refreshTimer !== null) {
    window.clearInterval(state.operatorPanel.refreshTimer);
  }
  state.operatorPanel.refreshTimer = window.setInterval(async () => {
    await loadOperatorPanel({ silent: true });
    await loadDashboardMapPreview({ silent: true });
  }, 3000);
}

async function loadOperatorPanel({ silent = false } = {}) {
  const robot = getMapContextRobot();
  if (!robot) {
    state.operatorPanel.data = null;
    renderAll();
    return;
  }

  try {
    const previousData = state.operatorPanel.data;
    const previousMap = previousData?.map || null;
    const includeMap = !previousMap;
    const response = await fetch(
      `/robots/${encodeURIComponent(robot.id)}/operator-panel?include_map=${includeMap ? "true" : "false"}`
    );
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "Robot panel load failed");
    }
    if (
      !payload.map &&
      previousMap &&
      payload.current_map_name === previousData?.current_map_name &&
      (
        payload.map_updated_at == null ||
        Number(payload.map_updated_at) === Number(previousMap.updated_at)
      )
    ) {
      payload.map = previousMap;
    }
    const previousLocalizationFailure = getLocalizationFailureMessage(previousData);
    state.operatorPanel.data = payload;
    state.connection.lastOperatorAt = Date.now();
    const localizationFailure = getLocalizationFailureMessage(payload);
    if (localizationFailure && localizationFailure !== previousLocalizationFailure) {
      setMessage(elements.mapMessage, localizationFailure, true);
      setMessage(elements.addDestinationMessage, localizationFailure, true);
      setMessage(elements.calibrationMessage, localizationFailure, true);
    }
  } catch (error) {
    if (!silent) {
      setMessage(elements.stateMessage, error.message || "Robot panel load failed", true);
    }
    if (!state.operatorPanel.data) {
      state.operatorPanel.data = null;
    }
  } finally {
    renderAll();
  }
}

async function loadDashboardMapPreview({ silent = false } = {}) {
  const robot = getMapContextRobot();
  if (!robot || state.operatorPanel.mapPreviewInFlight) {
    return;
  }
  if (state.operatorPanel.data?.map_available && state.operatorPanel.data?.map) {
    return;
  }
  const mapName = currentMapName() || getSavedMaps()[0] || DEFAULT_MAP_NAME;
  if (state.operatorPanel.mapPreviewName === mapName && state.operatorPanel.mapPreview) {
    return;
  }

  state.operatorPanel.mapPreviewInFlight = true;
  try {
    const response = await fetch(
      `/robots/${encodeURIComponent(robot.id)}/maps/${encodeURIComponent(mapName)}/preview`
    );
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "Map preview load failed.");
    }
    state.operatorPanel.mapPreview = payload.map;
    state.operatorPanel.mapPreviewName = mapName;
    resetDashboardMapView({ render: false });
  } catch (error) {
    if (!silent) {
      setMessage(elements.mapMessage, error.message || "Map preview load failed.", true);
    }
  } finally {
    state.operatorPanel.mapPreviewInFlight = false;
    renderAll();
  }
}

function renderAll() {
  renderConnectionState();
  renderHeader();
  renderRobotBrain();
  renderStartRobotState();
  renderMapSetup();
  renderStateRobotState();
  renderCalibrationPanel();
  renderPendingRequests();
  renderActiveMission();
  renderMissions();
  renderReturnPrompt();
  renderManualAvailability();
  renderMaps();
  renderLocationResults();
  syncTripType();
  highlightNav();
}

function showScreen(name) {
  const nextScreen = name || "start";
  if (state.activeScreen === "state" && nextScreen !== "state") {
    stopManualDrive({ sendStop: isControlDataFresh(), silent: true });
  }
  state.activeScreen = nextScreen;
  document.querySelectorAll(".screen").forEach((screen) => {
    screen.classList.toggle("hidden", screen.id !== `screen-${state.activeScreen}`);
  });
  if (state.activeScreen === "add-destination") {
    renderMap("add");
  }
  if (state.activeScreen === "mapping") {
    renderMap("mapping");
  }
  if (state.activeScreen === "state") {
    renderMap("state");
    renderReturnPrompt();
  }
  highlightNav();
}

function highlightNav() {
  document.querySelectorAll(".top-nav [data-nav-screen]").forEach((button) => {
    button.classList.toggle("active", button.dataset.navScreen === state.activeScreen);
  });
}

function isStatusFresh(now = Date.now()) {
  return (
    state.connection.websocketConnected &&
    state.connection.lastStatusAt > 0 &&
    now - state.connection.lastStatusAt <= STATUS_STALE_AFTER_MS
  );
}

function isOperatorPanelFresh(now = Date.now()) {
  return (
    state.connection.lastOperatorAt > 0 &&
    now - state.connection.lastOperatorAt <= OPERATOR_STALE_AFTER_MS
  );
}

function isControlDataFresh() {
  return isStatusFresh() && isOperatorPanelFresh();
}

function renderConnectionState() {
  const statusFresh = isStatusFresh();
  const connected = state.connection.websocketConnected;
  const label = statusFresh ? "Live" : connected ? "Waiting" : "Reconnecting";

  setText(elements.headerConnection, label);
  if (elements.serverConnectionBadge) {
    elements.serverConnectionBadge.classList.toggle("is-connecting", connected && !statusFresh);
    elements.serverConnectionBadge.classList.toggle("is-offline", !connected);
    setText(elements.serverConnectionLabel, statusFresh
      ? "Live"
      : connected
        ? "Waiting"
        : "Reconnecting");
  }
  if (elements.connectionAlert) {
    elements.connectionAlert.classList.toggle("hidden", statusFresh);
  }
  document.body.classList.toggle("data-stale", !statusFresh);
}

function getOperatingMode() {
  const data = state.operatorPanel.data ?? {};
  const processes = data.launcher_processes ?? {};
  const message = String(data.launcher_message || "").toLowerCase();
  if (message.includes("ui-only") || message.includes("ui only")) {
    return { key: "preview", label: "UI preview" };
  }
  if (processes.slam || data.mapping_active) {
    return { key: "mapping", label: "Mapping" };
  }
  const navigationContext = processes.nav || data.navigation_mode || data.current_map_name;
  if (navigationContext && data.localization && !data.localization.ready) {
    return { key: "localization", label: "Localization" };
  }
  if (navigationContext) {
    return { key: "navigation", label: "Navigation" };
  }
  return { key: "standby", label: "Standby" };
}

function populateDestinationSelects() {
  const destinationOptions = state.destinations
    .map((destination) => `<option value="${escapeHtml(destination.name)}">${escapeHtml(destination.name)}</option>`)
    .join("");
  elements.requestDestination.innerHTML = destinationOptions;
  elements.returnDestination.innerHTML =
    `<option value="">${state.home ? `Home (${escapeHtml(state.home)})` : "Use Home"}</option>` + destinationOptions;
}

function populateRobotSelects() {
  const selectedRobotId = elements.selectedRobot.value;
  const requestRobotId = elements.requestRobot.value;
  const robots = sortedRobotsForSelection();
  const robotOptions = robots
    .map((robot) => {
      const suffix = robotConnectionLabel(robot) === "Connected" ? "" : " (offline)";
      return `<option value="${escapeHtml(robot.id)}">${escapeHtml(robot.id + suffix)}</option>`;
    })
    .join("");

  elements.selectedRobot.innerHTML = '<option value="">Map overview</option>' + robotOptions;
  if (
    selectedRobotId &&
    [...elements.selectedRobot.options].some((option) => option.value === selectedRobotId)
  ) {
    elements.selectedRobot.value = selectedRobotId;
  } else if (!state.robotSelectionTouched && robots.length) {
    elements.selectedRobot.value = robots[0].id;
  } else {
    elements.selectedRobot.value = "";
  }

  elements.requestRobot.innerHTML = '<option value="">Auto-select available robot</option>' + robotOptions;
  if ([...elements.requestRobot.options].some((option) => option.value === requestRobotId)) {
    elements.requestRobot.value = requestRobotId;
  }
}

function renderHeader() {
  const robot = getSelectedRobot();
  const power = robot?.power ?? {};
  const mode = displayRobotMode(robot?.mode);
  const battery = batteryPercentForDisplay(robot);
  const mapName = dashboardMapDisplayName();
  const operatingMode = getOperatingMode();

  renderSelectedRobotBattery(robot, battery, robot?.battery_v);
  setText(elements.headerMode, robot ? mode : "--");
  setText(elements.headerBattery, battery == null ? "--" : `${battery}%`);
  setText(elements.headerConnection, robot ? robotConnectionLabel(robot) : "--");
  setText(elements.headerLatency, power.latency_ms == null ? "--" : `${formatNumber(power.latency_ms)} ms`);
  setText(elements.headerMap, mapName || "No map");
  setText(elements.dashboardMapTitle, mapName || "No map selected");
  setText(elements.operationModeBadge, operatingMode.label);
  if (elements.operationModeBadge) {
    elements.operationModeBadge.classList.toggle("is-mapping", operatingMode.key === "mapping");
    elements.operationModeBadge.classList.toggle("is-localization", operatingMode.key === "localization");
    elements.operationModeBadge.classList.toggle("is-preview", operatingMode.key === "preview");
  }
}

function renderSelectedRobotBattery(robot, battery, voltage) {
  if (!elements.selectedRobotBattery) {
    return;
  }

  elements.selectedRobotBattery.classList.toggle("hidden", !robot);
  if (!robot) {
    return;
  }

  const numericBattery = Number(battery);
  const hasBattery = battery != null && Number.isFinite(numericBattery);
  const batteryPercent = hasBattery ? Math.max(0, Math.min(100, numericBattery)) : 0;
  const batteryLabel = hasBattery ? `${Math.round(batteryPercent)}%` : "--";
  const numericVoltage = Number(voltage);
  const voltageLabel = Number.isFinite(numericVoltage) && numericVoltage > 0
    ? `, ${numericVoltage.toFixed(2)} V`
    : "";

  setText(elements.selectedRobotBatteryValue, batteryLabel);
  if (elements.selectedRobotBatteryFill) {
    elements.selectedRobotBatteryFill.style.width = `${batteryPercent}%`;
  }
  elements.selectedRobotBattery.classList.toggle("is-unavailable", !hasBattery);
  elements.selectedRobotBattery.classList.toggle("is-low", hasBattery && batteryPercent <= 20);
  elements.selectedRobotBattery.classList.toggle("is-critical", hasBattery && batteryPercent <= 10);
  elements.selectedRobotBattery.setAttribute(
    "aria-label",
    `${robot.id} battery: ${batteryLabel}${voltageLabel}`,
  );
  elements.selectedRobotBattery.title = `${robot.id} battery: ${batteryLabel}${voltageLabel}`;
}

function getRobotReadiness(robot = getSelectedRobot()) {
  const data = robot && state.operatorPanel.data?.robot_id === robot.id
    ? state.operatorPanel.data
    : null;
  const startup = data?.startup ?? {};
  const localization = data?.localization ?? {};
  const manualDrive = data?.manual_drive ?? {};
  const connected = Boolean(robot) && robotConnectionLabel(robot) === "Connected";
  const dataFresh = isControlDataFresh();
  const startupReady = Boolean(data && startup.ready);
  const initialPoseReady = Boolean(dataFresh && startupReady && data?.initial_pose_available);
  const localizationReady = Boolean(Number(robot?.localization_valid)) || Boolean(localization.ready);
  const navigationActionReady = Boolean(
    data?.navigation_action_available ?? data?.navigation_available
  );
  const navigationReady = Boolean(
    dataFresh &&
    startupReady &&
    localizationReady &&
    navigationActionReady &&
    data?.navigation_available
  );
  const manualDriveReady = Boolean(
    dataFresh &&
    connected &&
    data &&
    (data.manual_drive_available ?? manualDrive.ready)
  );

  return {
    data,
    startup,
    localization,
    manualDrive,
    connected,
    dataFresh,
    startupReady,
    initialPoseReady,
    localizationReady,
    navigationActionReady,
    navigationReady,
    manualDriveReady,
    manualDriveMessage: String(
      !dataFresh
        ? "Live status is stale. Manual recovery remains locked while Mission Control reconnects."
        : manualDrive.message || "Waiting for the Pi safety stack before enabling manual recovery."
    ),
    startupMessage: String(
      !dataFresh
        ? "Live status is stale. Motion controls remain locked while Mission Control reconnects."
        : startup.message || "Waiting for the Pi robot stack to become ready."
    ),
  };
}

function getLocalizationFailureMessage(data = state.operatorPanel.data) {
  const localization = data?.localization ?? {};
  if (
    !["failed", "safety_paused", "invalid_jump"].includes(localization.phase)
    && !localization.failed
  ) {
    return "";
  }
  return String(
    localization.message
      || "AMCL localization failed. Retry by setting the initial position again, or move the robot to a clearer space before retrying."
  );
}

function navigationLockLabel(readiness) {
  if (!readiness.startupReady) {
    return "Waiting";
  }
  if (!readiness.localizationReady) {
    return "Set position";
  }
  if (!readiness.navigationReady) {
    return "Starting Nav2";
  }
  return "Ready";
}

function renderRobotBrain() {
  const panel = elements.robotBrainPanel;
  if (!panel) {
    return;
  }

  const robot = getSelectedRobot();
  panel.classList.remove("is-idle", "is-ready", "is-working", "is-blocked");
  if (!robot) {
    panel.classList.add("is-idle");
    setText(elements.robotBrainTitle, "Select a robot");
    setText(elements.robotStatePi, "--");
    setText(elements.robotStateBattery, "--");
    setText(elements.robotStateLatency, "--");
    return;
  }

  const readiness = getRobotReadiness(robot);
  const { connected, dataFresh, initialPoseReady } = readiness;
  const data = readiness.data;
  const power = robot.power ?? {};
  const battery = batteryPercentForDisplay(robot);
  const latency = power.latency_ms;
  const numericBattery = Number(battery);
  const numericLatency = Number(latency);
  const operatingMode = getOperatingMode();
  const localizationFailure = getLocalizationFailureMessage(data);
  const activeRobotState = String(robot.state || data?.robot_state || "");
  const routeStateVisible = ["Requested", "En-route", "Returning", "Paused"]
    .includes(activeRobotState);

  if (!dataFresh) {
    setText(elements.robotBrainTitle, "Status unavailable");
    panel.classList.add("is-blocked");
  } else if (operatingMode.key === "preview") {
    setText(elements.robotBrainTitle, robot.state || robot.mode || "UI preview");
    panel.classList.add("is-ready");
  } else if (!connected) {
    setText(elements.robotBrainTitle, "Robot offline");
    panel.classList.add("is-blocked");
  } else if (!readiness.startupReady) {
    setText(elements.robotBrainTitle, readiness.startupMessage);
    panel.classList.add("is-working");
  } else if (localizationFailure) {
    setText(elements.robotBrainTitle, localizationFailure);
    panel.classList.add("is-blocked");
  } else if (routeStateVisible) {
    setText(elements.robotBrainTitle, activeRobotState);
    panel.classList.add(activeRobotState === "Paused" ? "is-blocked" : "is-working");
  } else if (!readiness.navigationReady) {
    setText(
      elements.robotBrainTitle,
      data?.navigation?.message || "Waiting for localization and Nav2.",
    );
    panel.classList.add("is-working");
  } else {
    setText(elements.robotBrainTitle, robot.state || robot.mode || "Connected");
    panel.classList.add("is-ready");
  }

  const piLabel = operatingMode.key === "preview" && dataFresh
    ? "Simulated"
    : dataFresh && connected
      ? "Yes"
      : "No";
  setText(elements.robotStatePi, piLabel);
  setText(
    elements.robotStateBattery,
    battery == null || !Number.isFinite(numericBattery) ? "--" : `${Math.round(numericBattery)}%`,
  );
  setText(
    elements.robotStateLatency,
    latency == null || !Number.isFinite(numericLatency) ? "--" : `${Math.round(numericLatency)} ms`,
  );

  elements.setInitialPositionButton.disabled = !initialPoseReady;
}

function renderStartRobotState() {
  const robot = getSelectedRobot();
  const power = robot?.power ?? {};
  const battery = batteryPercentForDisplay(robot);
  const mode = displayRobotMode(robot?.mode);
  const readiness = getRobotReadiness(robot);

  setText(elements.startRobotId, robot?.id || "--");
  setText(elements.startMode, robot ? mode : "--");
  setText(elements.startBattery, battery == null ? "--" : `${battery}%`);
  setText(elements.startConnection, robot ? robotConnectionLabel(robot) : "--");
  setText(elements.startLatency, power.latency_ms == null ? "--" : `${formatNumber(power.latency_ms)} ms`);
  setText(elements.startMap, dashboardMapDisplayName() || "No map");
  setText(elements.startLock, robot ? navigationLockLabel(readiness) : "--");
}

function renderStateRobotState() {
  const robot = getSelectedRobot();
  const power = robot?.power ?? {};
  const battery = batteryPercentForDisplay(robot);
  const mode = displayRobotMode(robot?.mode);
  const readiness = getRobotReadiness(robot);

  setText(elements.stateRobotId, robot?.id || "--");
  setText(elements.stateBattery, battery == null ? "--" : `${battery}%`);
  setText(elements.stateMode, robot ? mode : "--");
  setText(elements.stateConnection, robot ? robotConnectionLabel(robot) : "--");
  setText(elements.stateLatency, power.latency_ms == null ? "--" : `${formatNumber(power.latency_ms)} ms`);
  setText(elements.stateLock, robot ? navigationLockLabel(readiness) : "--");
  setText(elements.stateWarning, robot ? robotWarningLabel(robot, battery) : "--");
}

function renderCalibrationPanel() {
  const robot = getSelectedRobot();
  const data = state.operatorPanel.data;
  const initialPose = data?.initial_pose;
  const goalPose = data?.goal_pose;
  const localizationValid = Boolean(robot?.localization_valid);
  const connected = robot && robotConnectionLabel(robot) === "Connected";
  const readiness = getRobotReadiness(robot);

  const localizationFailure = getLocalizationFailureMessage(data);
  setText(
    elements.calibrationLocalization,
    robot ? (localizationValid ? "Ready" : localizationFailure ? "Failed" : "Not confirmed") : "--",
  );
  setText(elements.calibrationInitialPose, initialPose ? formatPoseShort(initialPose) : "Not set");
  setText(elements.calibrationGoalPose, goalPose ? formatPoseShort(goalPose) : "No goal");

  if (elements.calibrationStatusDot) {
    elements.calibrationStatusDot.classList.remove("ready", "warning", "error");
    if (!robot || !connected) {
      elements.calibrationStatusDot.classList.add("error");
    } else if (!localizationValid) {
      elements.calibrationStatusDot.classList.add("warning");
    } else {
      elements.calibrationStatusDot.classList.add("ready");
    }
  }
  elements.calibrationUseCurrentPoseButton.disabled = !readiness.initialPoseReady;
  elements.calibrationSendInitialPoseButton.disabled = !readiness.initialPoseReady;
  elements.useCurrentPoseButton.disabled = !readiness.initialPoseReady;
  elements.sendInitialPoseButton.disabled = !readiness.initialPoseReady;
}

function renderDestinations() {
  if (!state.destinations.length) {
    elements.destinationsList.innerHTML = '<p class="empty-state">No destinations configured.</p>';
    return;
  }

  elements.destinationsList.innerHTML = state.destinations
    .map((destination) => {
      const pose = destination.pose ?? {};
      return `
        <div class="destination-row">
          <strong>${escapeHtml(destination.name)}</strong>
          <span>x ${formatNumber(pose.x)}, y ${formatNumber(pose.y)}, yaw ${formatNumber(pose.yaw)}</span>
          <span class="muted">${escapeHtml(destination.notes || "")}</span>
        </div>
      `;
    })
    .join("");
}

function renderMapSetup() {
  const savedMaps = getSavedMaps();
  const current = currentMapName();
  const previousValue = elements.savedMapSelect.value;
  elements.manageCurrentMap.textContent = `Current Map: ${dashboardMapDisplayName() || current || "No map selected"}`;
  elements.addSelectedMap.textContent = current || "No map selected";

  elements.savedMapSelect.innerHTML =
    savedMaps.length
      ? savedMaps.map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join("")
      : '<option value="">No saved maps found</option>';

  if (savedMaps.includes(previousValue)) {
    elements.savedMapSelect.value = previousValue;
  } else if (current && savedMaps.includes(current)) {
    elements.savedMapSelect.value = current;
  }
  elements.selectMapButton.disabled = !elements.savedMapSelect.value;
}

function renderPendingRequests() {
  const requests = getPendingRequests();
  const navigationReady = getRobotReadiness().navigationReady;
  if (!requests.length) {
    elements.pendingRequestsList.innerHTML = '<p class="empty-state">No pending requests.</p>';
    return;
  }

  elements.pendingRequestsList.innerHTML = requests
    .map((request) => {
      const route = formatRoute(request);
      return `
        <div class="request-row">
          <strong>${formatRequestNumber(request)}</strong>
          <span>Destination: ${escapeHtml(route)}</span>
          <span>Robot: ${escapeHtml(request.assigned_robot_id || "Auto")}</span>
          <button class="primary-button" type="button" data-start-request="${escapeHtml(request.id)}" ${navigationReady ? "" : "disabled"}>Start Mission</button>
        </div>
      `;
    })
    .join("");
}

function renderActiveMission() {
  const mission = state.missions.find((item) =>
    item.state !== "Requested" &&
    item.state !== "Completed" &&
    item.outcome !== "Canceled" &&
    item.outcome !== "Failed" &&
    item.outcome !== "Aborted"
  );

  if (!mission) {
    elements.activeMissionCard.innerHTML = '<p class="empty-state">No active mission.</p>';
    elements.cancelCurrentMissionButton.disabled = true;
    elements.cancelCurrentMissionButton.removeAttribute("data-action");
    elements.cancelCurrentMissionButton.removeAttribute("data-mission-id");
    return;
  }

  const canCancel = mission.state !== "Completed";
  if (canCancel) {
    elements.cancelCurrentMissionButton.disabled = false;
    elements.cancelCurrentMissionButton.dataset.action = "cancel";
    elements.cancelCurrentMissionButton.dataset.missionId = mission.id;
  } else {
    elements.cancelCurrentMissionButton.disabled = true;
    elements.cancelCurrentMissionButton.removeAttribute("data-action");
    elements.cancelCurrentMissionButton.removeAttribute("data-mission-id");
  }

  const returnDestination = mission.from_dest || state.home || "Home";
  const returnCallout =
    mission.state === "WaitingForReturn"
      ? `
        <div class="return-callout">
          <strong>Arrived at ${escapeHtml(mission.to_dest)}.</strong>
          <span>The robot is waiting at the destination.</span>
          <div class="mission-actions">
            <button class="action-button return" type="button" data-action="return" data-mission-id="${escapeHtml(mission.id)}">Return to ${escapeHtml(returnDestination)}</button>
          </div>
        </div>
      `
      : "";

  elements.activeMissionCard.innerHTML = `
    <div class="mission-summary">
      <strong>Mission from ${formatRequestNumber(mission)}</strong>
      <span>Destination: ${escapeHtml(formatRoute(mission))}</span>
      <span>Robot: ${escapeHtml(mission.assigned_robot_id || "Auto")}</span>
      <span>State: ${escapeHtml(displayMissionStatus(mission))}</span>
      <span>Requester: ${escapeHtml(mission.requested_by || "--")}</span>
    </div>
    ${returnCallout}
    <div class="mission-actions">
      ${buildMissionActionButton("pause", mission)}
      ${buildMissionActionButton("resume", mission)}
      ${buildMissionActionButton("cancel", mission)}
    </div>
  `;
}

function renderMissions() {
  const startedMissions = state.missions.filter((mission) => mission.state !== "Requested");
  if (!startedMissions.length) {
    elements.missionsBody.innerHTML = '<tr><td colspan="5" class="empty-state">No started missions yet.</td></tr>';
    return;
  }

  elements.missionsBody.innerHTML = startedMissions
    .map((mission) => `
      <tr>
        <td><span class="tag ${slugify(displayMissionStatus(mission))}">${escapeHtml(displayMissionStatus(mission))}</span></td>
        <td>
          <strong>${escapeHtml(formatRoute(mission))}</strong>
          <span class="muted">${formatRequestNumber(mission)}</span>
        </td>
        <td>${escapeHtml(mission.assigned_robot_id || "Auto")}</td>
        <td>${escapeHtml(mission.requested_by || "--")}</td>
        <td><div class="mission-actions">${buildMissionActionButton("return", mission)}${buildMissionActionButton("pause", mission)}${buildMissionActionButton("resume", mission)}${buildMissionActionButton("cancel", mission)}</div></td>
      </tr>
    `)
    .join("");
}

function renderReturnPrompt() {
  const mission = getWaitingForReturnMission();
  if (
    !mission ||
    state.activeScreen !== "state" ||
    state.returnPromptDismissed.has(mission.id)
  ) {
    hideReturnModal();
    return;
  }

  const returnDestination = mission.from_dest || state.home || "Home";
  elements.returnModalText.textContent = `The robot arrived at ${mission.to_dest} and is waiting. Click Return when it should go back to ${returnDestination}.`;
  elements.returnModalButton.textContent = `Return to ${returnDestination}`;
  elements.returnModalButton.dataset.action = "return";
  elements.returnModalButton.dataset.missionId = mission.id;
  elements.returnModal.classList.remove("hidden");
}

function hideReturnModal() {
  elements.returnModal.classList.add("hidden");
  elements.returnModalButton.removeAttribute("data-action");
  elements.returnModalButton.removeAttribute("data-mission-id");
}

function handleReturnStay() {
  const mission = getWaitingForReturnMission();
  if (mission) {
    state.returnPromptDismissed.add(mission.id);
  }
  hideReturnModal();
}

function renderManualAvailability() {
  const robot = getSelectedRobot();
  const readiness = getRobotReadiness(robot);
  const shells = [...document.querySelectorAll(".manual-drive-shell")];
  const buttons = [...document.querySelectorAll("[data-manual-linear], [data-manual-stop]")];
  const available = isManualDriveAvailable(robot);
  shells.forEach((shell) => shell.classList.toggle("is-disabled", !available));
  buttons.forEach((button) => {
    button.disabled = !available;
    if (!available) {
      button.classList.remove("is-active");
    }
  });
  document.querySelectorAll("[data-manual-status]").forEach((status) => {
    status.textContent = available
      ? `Manual drive ready for ${robot.id}.`
      : readiness.manualDriveMessage;
  });
  if (!available) {
    stopManualDrive({ sendStop: isControlDataFresh(), silent: true });
  }
}

function renderMaps() {
  renderMap("mapping");
  renderMap("add");
  renderMap("state");
}

function syncDashboardCanvasSize({ render = true } = {}) {
  const rect = elements.dashboardMapShell.getBoundingClientRect();
  const pixelRatio = Math.max(1, Math.min(2, Number(window.devicePixelRatio) || 1));
  const targetWidth = Math.max(1, Math.round(rect.width * pixelRatio));
  const targetHeight = Math.max(1, Math.round(rect.height * pixelRatio));
  if (
    elements.stateMapCanvas.width === targetWidth &&
    elements.stateMapCanvas.height === targetHeight
  ) {
    return false;
  }
  elements.stateMapCanvas.width = targetWidth;
  elements.stateMapCanvas.height = targetHeight;
  delete state.operatorPanel.frames[elements.stateMapCanvas.id];
  if (render) {
    renderMap("state");
  }
  return true;
}

function renderMap(kind) {
  const config = getMapConfig(kind);
  if (!config) {
    return;
  }
  const map = getMapForKind(kind);
  const robot = getSelectedRobot();

  if (!map) {
    config.placeholder.classList.remove("hidden");
    config.placeholder.textContent =
      kind === "state"
        ? `Loading ${dashboardMapDisplayName() || "map"}.`
        : currentMapName()
          ? "Waiting for live map data."
          : "Select a map to show the live map.";
    setText(config.meta, "No live map yet");
    clearCanvas(config.canvas);
    if (kind === "state") {
      renderDestinationOverlay();
    }
    return;
  }

  config.placeholder.classList.add("hidden");
  const mapLabel = map.name || currentMapName() || "Live map";
  setText(config.meta, `${mapLabel}: ${map.width} x ${map.height}, ${formatNumber(map.resolution)} m/cell`);
  drawMap(config.canvas, map, {
    robot: isRobotVisibleOnMap(robot) ? robot : null,
    pendingGoal: kind === "state" ? state.operatorPanel.pendingGoal : null,
    view: kind === "state" ? state.operatorPanel.mapView : null,
  });
  if (kind === "state") {
    renderDestinationOverlay();
    renderMapConfirmPopover();
  }
}

function getMapForKind(kind) {
  const data = state.operatorPanel.data;
  if (data?.map_available && data.map) {
    return data.map;
  }
  if (kind === "state") {
    return state.operatorPanel.mapPreview;
  }
  return null;
}

function getDashboardLocations() {
  const locationsByName = new Map();
  state.destinations.forEach((destination) => {
    const pose = destination.pose || {};
    if ([pose.x, pose.y].some((value) => value == null || Number.isNaN(Number(value)))) {
      return;
    }
    const key = destination.name.toLowerCase();
    if (locationsByName.has(key)) {
      return;
    }
    locationsByName.set(key, {
      id: `destination-${slugify(destination.name)}`,
      name: destination.name,
      pose: {
        x: Number(pose.x),
        y: Number(pose.y),
        yaw: Number(pose.yaw || 0),
      },
      source: "configured",
      configuredDestination: true,
    });
  });
  return [...locationsByName.values()].sort((a, b) => a.name.localeCompare(b.name));
}

function renderLocationResults() {
  if (!elements.locationResults) {
    return;
  }
  const filter = state.operatorPanel.locationFilter;
  const resultsVisible = state.operatorPanel.locationsExpanded || Boolean(filter);
  elements.locationResults.classList.toggle("hidden", !resultsVisible);
  elements.locationSearchPanel.classList.toggle("is-results-open", resultsVisible);
  elements.locationResultsToggle.setAttribute("aria-expanded", resultsVisible ? "true" : "false");
  elements.locationResultsToggle.setAttribute(
    "aria-label",
    resultsVisible ? "Hide saved locations" : "Show saved locations",
  );
  elements.locationResultsToggle.title = resultsVisible
    ? "Hide saved locations"
    : "Show saved locations";
  const locations = getDashboardLocations().filter((location) =>
    !filter || location.name.toLowerCase().includes(filter)
  );
  if (!locations.length) {
    elements.locationResults.innerHTML = '<p class="empty-state">No locations found.</p>';
    return;
  }
  const readiness = getRobotReadiness();
  const locationActionsReady = state.operatorPanel.initialPoseMode
    ? readiness.initialPoseReady
    : readiness.navigationReady;

  elements.locationResults.innerHTML = locations
    .map((location) => `
      <button class="location-result" type="button" data-location-id="${escapeHtml(location.id)}" ${locationActionsReady ? "" : "disabled"}>
        <span>${escapeHtml(location.name)}</span>
      </button>
    `)
    .join("");
}

function renderDestinationOverlay() {
  if (!elements.destinationOverlay) {
    return;
  }
  const frame = state.operatorPanel.frames[elements.stateMapCanvas.id];
  if (!frame) {
    elements.destinationOverlay.innerHTML = "";
    return;
  }

  const readiness = getRobotReadiness();
  const locationActionsReady = state.operatorPanel.initialPoseMode
    ? readiness.initialPoseReady
    : readiness.navigationReady;
  elements.destinationOverlay.innerHTML = getDashboardLocations()
    .map((location) => {
      const targetPoint = worldToCanvasWithFrame(location.pose, frame);
      if (
        !isPointWithinFrame(targetPoint, frame) ||
        targetPoint.x < 0 ||
        targetPoint.x > elements.stateMapCanvas.width ||
        targetPoint.y < 0 ||
        targetPoint.y > elements.stateMapCanvas.height
      ) {
        return "";
      }
      const placeBelowTarget = DESTINATION_LABELS_BELOW_TARGET.has(location.name.toLowerCase());
      const verticalOffset = placeBelowTarget
        ? -DESTINATION_LABEL_OFFSET_M
        : DESTINATION_LABEL_OFFSET_M;
      const labelPose = { ...location.pose, y: location.pose.y + verticalOffset };
      const labelPoint = worldToCanvasWithFrame(labelPose, frame);
      const left = (labelPoint.x / elements.stateMapCanvas.width) * 100;
      const top = (labelPoint.y / elements.stateMapCanvas.height) * 100;
      const placementClass = placeBelowTarget ? " is-below-target" : "";
      return `
        <button
          class="map-destination-chip${placementClass}"
          type="button"
          data-location-id="${escapeHtml(location.id)}"
          ${locationActionsReady ? "" : "disabled"}
          style="left:${left.toFixed(3)}%;top:${top.toFixed(3)}%;"
        >${escapeHtml(location.name)}</button>
      `;
    })
    .join("");
}

function handleLocationClick(event) {
  const button = event.target.closest("[data-location-id]");
  if (!button) {
    return;
  }
  event.preventDefault();
  const readiness = getRobotReadiness();
  const actionReady = state.operatorPanel.initialPoseMode
    ? readiness.initialPoseReady
    : readiness.navigationReady;
  if (button.disabled || !actionReady) {
    setMessage(
      elements.mapMessage,
      state.operatorPanel.initialPoseMode
        ? readiness.startupMessage
        : "Navigation is locked. Complete startup and initial localization first.",
      true,
    );
    return;
  }
  const location = getDashboardLocations().find((item) => item.id === button.dataset.locationId);
  if (!location) {
    return;
  }
  promptMapGoal({
    kind: state.operatorPanel.initialPoseMode ? "initial_pose" : "goal",
    name: location.name,
    pose: location.pose,
    configuredDestination: location.configuredDestination,
  });
}

function getMapConfig(kind) {
  const configs = {
    mapping: {
      canvas: elements.mappingMapCanvas,
      placeholder: elements.mappingMapPlaceholder,
      meta: elements.mappingMapMeta,
    },
    add: {
      canvas: elements.addMapCanvas,
      placeholder: elements.addMapPlaceholder,
      meta: null,
    },
    state: {
      canvas: elements.stateMapCanvas,
      placeholder: elements.stateMapPlaceholder,
      meta: elements.stateMapMeta,
    },
  };
  return configs[kind] || null;
}

function zoomDashboardMap(factor) {
  const view = state.operatorPanel.mapView;
  const nextZoom = Math.max(DASHBOARD_MIN_ZOOM, Math.min(DASHBOARD_MAX_ZOOM, view.zoom * factor));
  if (Math.abs(nextZoom - view.zoom) < 0.001) {
    return;
  }
  view.zoom = nextZoom;
  renderMap("state");
}

function handleDashboardMapWheel(event) {
  if (!event.ctrlKey || event.deltaY === 0) {
    return;
  }
  event.preventDefault();
  event.stopPropagation();
  zoomDashboardMap(event.deltaY < 0 ? 1.1 : 0.9);
}

function enableRobotFollow({ render = true } = {}) {
  const view = state.operatorPanel.mapView;
  view.zoom = ROBOT_FOCUS_ZOOM;
  view.followRobot = true;
  view.pointerMoved = false;
  if (render) {
    renderMap("state");
  }
}

function resetDashboardMapView({ render = true } = {}) {
  const view = state.operatorPanel.mapView;
  view.zoom = 1;
  view.panX = 0;
  view.panY = 0;
  view.followRobot = false;
  view.pointerMoved = false;
  if (render) {
    renderMap("state");
  }
}

function centerDashboardMapOnRobot() {
  const robot = getSelectedRobot();
  if (!isRobotVisibleOnMap(robot)) {
    setMessage(elements.mapMessage, "The robot marker will appear after Robot brain reports localization ready.", true);
    return;
  }
  enableRobotFollow();
}

function handleDashboardMapPointerDown(event) {
  if (state.activeScreen !== "start" || event.button !== 2) {
    return;
  }
  event.preventDefault();
  const view = state.operatorPanel.mapView;
  view.isPanning = true;
  view.panPointerId = event.pointerId;
  view.startClientX = event.clientX;
  view.startClientY = event.clientY;
  view.startPanX = view.panX;
  view.startPanY = view.panY;
  view.pointerMoved = false;
  elements.stateMapCanvas.setPointerCapture(event.pointerId);
  elements.stateMapCanvas.classList.add("is-panning");
}

function handleDashboardMapPointerMove(event) {
  const view = state.operatorPanel.mapView;
  if (!view.isPanning || event.pointerId !== view.panPointerId) {
    return;
  }
  const rect = elements.stateMapCanvas.getBoundingClientRect();
  const dx = (event.clientX - view.startClientX) * (elements.stateMapCanvas.width / rect.width);
  const dy = (event.clientY - view.startClientY) * (elements.stateMapCanvas.height / rect.height);
  if (Math.abs(dx) > 2 || Math.abs(dy) > 2) {
    view.pointerMoved = true;
    view.followRobot = false;
  }
  view.panX = view.startPanX + dx;
  view.panY = view.startPanY + dy;
  renderMap("state");
}

function handleDashboardMapPointerUp(event) {
  const view = state.operatorPanel.mapView;
  if (!view.isPanning || event.pointerId !== view.panPointerId) {
    return;
  }
  view.isPanning = false;
  view.panPointerId = null;
  if (elements.stateMapCanvas.hasPointerCapture(event.pointerId)) {
    elements.stateMapCanvas.releasePointerCapture(event.pointerId);
  }
  elements.stateMapCanvas.classList.remove("is-panning");
  window.setTimeout(() => {
    view.pointerMoved = false;
  }, 0);
}

function toggleInitialPoseMode() {
  const robot = getSelectedRobot();
  if (!robot) {
    setMessage(elements.mapMessage, "Select a robot before setting its initial position.", true);
    return;
  }
  const readiness = getRobotReadiness(robot);
  if (!readiness.initialPoseReady) {
    setMessage(elements.mapMessage, readiness.startupMessage, true);
    return;
  }
  if (!getMapForKind("state")) {
    setMessage(elements.mapMessage, "Wait for the map to load before setting the initial position.", true);
    return;
  }
  const movingMission = state.missions.find((mission) =>
    mission.assigned_robot_id === robot.id && ["En-route", "Returning"].includes(mission.state)
  );
  if (movingMission) {
    setMessage(elements.mapMessage, "Pause or stop navigation before changing the robot position.", true);
    return;
  }
  setInitialPoseMode(!state.operatorPanel.initialPoseMode);
}

function setInitialPoseMode(enabled, { render = true, announce = true } = {}) {
  state.operatorPanel.initialPoseMode = Boolean(enabled);
  elements.setInitialPositionButton.classList.toggle("active", state.operatorPanel.initialPoseMode);
  elements.setInitialPositionButton.setAttribute(
    "aria-pressed",
    state.operatorPanel.initialPoseMode ? "true" : "false",
  );
  elements.dashboardMapShell.classList.toggle(
    "setting-initial-position",
    state.operatorPanel.initialPoseMode,
  );
  if (!state.operatorPanel.initialPoseMode && state.operatorPanel.pendingGoal?.kind === "initial_pose") {
    state.operatorPanel.pendingGoal = null;
  }
  if (announce) {
    setMessage(
      elements.mapMessage,
      state.operatorPanel.initialPoseMode
        ? "Click the robot's approximate position. AMCL will refine position and heading while stationary."
        : "Initial-position selection canceled.",
      false,
    );
  }
  if (render) {
    renderLocationResults();
    renderMap("state");
  }
}

function handleStartNext() {
  if (!currentMapName()) {
    setMessage(elements.startNextMessage, "Please create or select a map before creating a request.", true);
    return;
  }
  setMessage(elements.startNextMessage, "", false);
  showScreen("assign");
}

function toggleManualControls() {
  if (!elements.manualControlPanel.classList.contains("hidden")) {
    closeManualControls();
    return;
  }
  elements.manualControlPanel.classList.remove("hidden");
  elements.manualModeButton.classList.add("active");
  elements.manualModeButton.setAttribute("aria-expanded", "true");
  elements.manualModeButton.setAttribute("aria-label", "Close manual controls");
  renderManualAvailability();
}

function closeManualControls() {
  stopManualDrive({ sendStop: isControlDataFresh(), silent: true });
  elements.manualControlPanel.classList.add("hidden");
  elements.manualModeButton.classList.remove("active");
  elements.manualModeButton.setAttribute("aria-expanded", "false");
  elements.manualModeButton.setAttribute("aria-label", "Open manual controls");
  renderManualAvailability();
}

async function handleSelectMap() {
  const mapName = elements.savedMapSelect.value;
  if (!mapName) {
    setMessage(elements.manageMessage, "Choose a saved map first.", true);
    return;
  }
  try {
    await sendSystemCommand("launch_nav", { mapName, messageTarget: elements.manageMessage });
    setMessage(elements.manageMessage, `Selected map: ${mapName}`, false);
    await loadOperatorPanel({ silent: true });
    await loadDashboardMapPreview({ silent: true });
  } catch (error) {
    setMessage(elements.manageMessage, error.message || "Map selection failed.", true);
  }
}

function handleOpenAddDestination() {
  const savedMaps = getSavedMaps();
  if (!savedMaps.length) {
    setMessage(elements.mappingMessage, "A map is required before adding destinations. Please create or select a map first.", true);
    showScreen("mapping");
    return;
  }
  if (!currentMapName()) {
    setMessage(elements.manageMessage, "Select a saved map before adding a destination.", true);
    return;
  }
  setMessage(elements.addDestinationMessage, "", false);
  showScreen("add-destination");
}

async function handleStartMapping() {
  try {
    await sendSystemCommand("launch_slam", { messageTarget: elements.mappingMessage });
    elements.mappingStatus.textContent = "Mapping mode started";
    setMessage(elements.mappingMessage, "Mapping started. Drive the robot to build the map.", false);
    await loadOperatorPanel({ silent: true });
  } catch (error) {
    setMessage(elements.mappingMessage, error.message || "Mapping failed to start.", true);
  }
}

async function handleSaveMapping() {
  const mapName = elements.mappingMapName.value.trim();
  const robot = getSelectedRobot();
  if (!robot) {
    setMessage(elements.mappingMessage, "Select a robot before saving a map.", true);
    return;
  }
  if (!mapName) {
    setMessage(elements.mappingMessage, "Enter a map name before saving.", true);
    return;
  }

  elements.saveMappingButton.disabled = true;
  try {
    const response = await fetch(`/robots/${encodeURIComponent(robot.id)}/maps/save`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        map_name: mapName,
        command_source: getCommandSource(),
      }),
    });
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.detail || "Map save failed.");
    }

    await sendSystemCommand("launch_nav", { mapName, messageTarget: elements.mappingMessage });
    elements.mappingMapName.value = "";
    setMessage(elements.startNextMessage, `Map saved and selected: ${mapName}`, false);
    await loadOperatorPanel({ silent: true });
    await loadDashboardMapPreview({ silent: true });
    showScreen("start");
  } catch (error) {
    setMessage(elements.mappingMessage, error.message || "Map save failed.", true);
  } finally {
    elements.saveMappingButton.disabled = false;
  }
}

function handleAddMapMode(event) {
  const button = event.target.closest("[data-map-mode]");
  if (!button) {
    return;
  }
  state.operatorPanel.addMapMode = button.dataset.mapMode || "move";
  elements.addMapModes.querySelectorAll("[data-map-mode]").forEach((modeButton) => {
    modeButton.classList.toggle("active", modeButton === button);
  });
}

function handleAddMapClick(event) {
  const mode = state.operatorPanel.addMapMode;
  if (mode === "move") {
    setMessage(elements.addDestinationMessage, "Choose Set Destination Point or Set Initial Pose Point before clicking the map.", false);
    return;
  }
  const world = canvasPointToWorld(elements.addMapCanvas, event);
  if (!world) {
    setMessage(elements.addDestinationMessage, "Click inside the live map area.", true);
    return;
  }
  if (!isWorldPointOpenForCanvas(elements.addMapCanvas, world)) {
    setMessage(elements.addDestinationMessage, OPEN_AREA_CLICK_MESSAGE, true);
    return;
  }

  if (mode === "destination") {
    elements.destinationX.value = world.x.toFixed(2);
    elements.destinationY.value = world.y.toFixed(2);
    if (!elements.destinationYaw.value) {
      elements.destinationYaw.value = "0";
    }
    setMessage(elements.addDestinationMessage, `Destination point set at x ${formatNumber(world.x)}, y ${formatNumber(world.y)}.`, false);
    return;
  }

  elements.initialPoseX.value = world.x.toFixed(2);
  elements.initialPoseY.value = world.y.toFixed(2);
  setMessage(elements.addDestinationMessage, `Initial pose point set at x ${formatNumber(world.x)}, y ${formatNumber(world.y)}.`, false);
}

function fillInitialPoseFromRobot() {
  const robot = getSelectedRobot();
  if (!robot) {
    setMessage(elements.addDestinationMessage, "Select a robot before using current pose.", true);
    return;
  }
  elements.initialPoseX.value = Number(robot.x ?? 0).toFixed(2);
  elements.initialPoseY.value = Number(robot.y ?? 0).toFixed(2);
  setMessage(elements.addDestinationMessage, "Approximate position filled from the current robot pose.", false);
}

function fillCalibrationPoseFromRobot() {
  const robot = getSelectedRobot();
  if (!robot) {
    setMessage(elements.calibrationMessage, "Select a robot before using current pose.", true);
    return;
  }
  elements.calibrationPoseX.value = Number(robot.x ?? 0).toFixed(2);
  elements.calibrationPoseY.value = Number(robot.y ?? 0).toFixed(2);
  setMessage(elements.calibrationMessage, "Approximate position filled from the current robot pose.", false);
}

async function handleSendInitialPose() {
  const robot = getSelectedRobot();
  if (!robot) {
    setMessage(elements.addDestinationMessage, "Select a robot before sending an initial pose.", true);
    return;
  }
  const readiness = getRobotReadiness(robot);
  if (!readiness.initialPoseReady) {
    setMessage(elements.addDestinationMessage, readiness.startupMessage, true);
    return;
  }

  const x = Number(elements.initialPoseX.value);
  const y = Number(elements.initialPoseY.value);
  if ([x, y].some((value) => Number.isNaN(value))) {
    setMessage(elements.addDestinationMessage, "Enter valid initial pose values before sending.", true);
    return;
  }

  elements.sendInitialPoseButton.disabled = true;
  try {
    await sendInitialPose(robot.id, x, y);
    setMessage(elements.addDestinationMessage, `Approximate position sent at x ${formatNumber(x)}, y ${formatNumber(y)}. AMCL is refining from stationary lidar scans.`, false);
    await loadOperatorPanel({ silent: true });
  } catch (error) {
    setMessage(elements.addDestinationMessage, error.message || "Initial pose update failed.", true);
  } finally {
    elements.sendInitialPoseButton.disabled = !getRobotReadiness(robot).initialPoseReady;
  }
}

async function handleSendCalibrationInitialPose() {
  const robot = getSelectedRobot();
  if (!robot) {
    setMessage(elements.calibrationMessage, "Select a robot before sending an initial pose.", true);
    return;
  }
  const readiness = getRobotReadiness(robot);
  if (!readiness.initialPoseReady) {
    setMessage(elements.calibrationMessage, readiness.startupMessage, true);
    return;
  }

  const x = Number(elements.calibrationPoseX.value);
  const y = Number(elements.calibrationPoseY.value);
  if ([x, y].some((value) => Number.isNaN(value))) {
    setMessage(elements.calibrationMessage, "Enter valid initial pose values before sending.", true);
    return;
  }

  elements.calibrationSendInitialPoseButton.disabled = true;
  try {
    await sendInitialPose(robot.id, x, y);
    setMessage(elements.calibrationMessage, `Approximate position sent at x ${formatNumber(x)}, y ${formatNumber(y)}. AMCL is refining from stationary lidar scans.`, false);
    await loadOperatorPanel({ silent: true });
  } catch (error) {
    setMessage(elements.calibrationMessage, error.message || "Initial pose update failed.", true);
  } finally {
    elements.calibrationSendInitialPoseButton.disabled =
      !getRobotReadiness(robot).initialPoseReady;
  }
}

async function handleDashboardMapClick(event) {
  if (state.activeScreen !== "start" || state.pointMissionInFlight) {
    return;
  }
  if (event.button !== 0 || state.operatorPanel.mapView.pointerMoved) {
    state.operatorPanel.mapView.pointerMoved = false;
    return;
  }

  const world = canvasPointToWorld(elements.stateMapCanvas, event);
  if (!world) {
    setMessage(elements.mapMessage, "Click inside the map area.", true);
    return;
  }
  if (!isWorldPointOpenForCanvas(elements.stateMapCanvas, world)) {
    setMessage(elements.mapMessage, OPEN_AREA_CLICK_MESSAGE, true);
    return;
  }

  const robot = getSelectedRobot();
  const readiness = getRobotReadiness(robot);
  const x = Number(world.x.toFixed(2));
  const y = Number(world.y.toFixed(2));
  if (state.operatorPanel.initialPoseMode) {
    if (!readiness.initialPoseReady) {
      setMessage(elements.mapMessage, readiness.startupMessage, true);
      return;
    }
    promptMapGoal({
      kind: "initial_pose",
      name: "",
      pose: { x, y, yaw: 0 },
      configuredDestination: false,
    });
    return;
  }
  if (!readiness.navigationReady) {
    setMessage(
      elements.mapMessage,
      "Navigation is locked. Wait for startup, set the initial position, and wait for Navigation: Unlocked.",
      true,
    );
    return;
  }
  promptMapGoal({
    kind: "goal",
    name: "",
    pose: { x, y, yaw: Number(robot?.yaw ?? 0) },
    configuredDestination: false,
  });
}

function promptMapGoal(goal) {
  state.operatorPanel.pendingGoal = {
    kind: goal.kind || "goal",
    name: goal.name || "",
    configuredDestination: Boolean(goal.configuredDestination),
    pose: {
      x: Number(Number(goal.pose.x).toFixed(2)),
      y: Number(Number(goal.pose.y).toFixed(2)),
      yaw: Number(Number(goal.pose.yaw || 0).toFixed(2)),
    },
  };
  setMessage(elements.mapMessage, "", false);
  renderMap("state");
}

function clearPendingMapGoal({ render = true } = {}) {
  state.operatorPanel.pendingGoal = null;
  if (elements.mapConfirmPopover) {
    elements.mapConfirmPopover.classList.add("hidden");
  }
  if (render) {
    renderMap("state");
  }
}

function renderMapConfirmPopover() {
  const pending = state.operatorPanel.pendingGoal;
  const popover = elements.mapConfirmPopover;
  if (!popover || !pending) {
    if (popover) {
      popover.classList.add("hidden");
    }
    return;
  }

  const frame = state.operatorPanel.frames[elements.stateMapCanvas.id];
  if (!frame) {
    popover.classList.add("hidden");
    return;
  }

  const point = worldToCanvasWithFrame(pending.pose, frame);
  const left = (point.x / elements.stateMapCanvas.width) * 100;
  const top = (point.y / elements.stateMapCanvas.height) * 100;
  const isInitialPose = pending.kind === "initial_pose";
  const readiness = getRobotReadiness();
  elements.mapConfirmText.textContent = isInitialPose
    ? "Use this approximate position? AMCL will refine position and heading as lidar scans arrive."
    : pending.name
      ? `Are you sure you want to go to ${pending.name}?`
      : "Are you sure you want to go to this point?";
  elements.confirmMapGo.textContent = isInitialPose ? "Set Position" : "Go";
  elements.confirmMapGo.disabled = isInitialPose
    ? !readiness.initialPoseReady
    : !readiness.navigationReady;
  popover.style.left = `${Math.max(2, Math.min(88, left)).toFixed(3)}%`;
  popover.style.top = `${Math.max(8, Math.min(86, top)).toFixed(3)}%`;
  popover.classList.remove("hidden");
}

async function executePendingMapGoal() {
  if (!state.operatorPanel.pendingGoal || state.pointMissionInFlight) {
    return;
  }

  const robot = getSelectedRobot();
  if (!robot) {
    setMessage(elements.mapMessage, "Select a robot first.", true);
    return;
  }

  const pending = state.operatorPanel.pendingGoal;
  const isInitialPose = pending.kind === "initial_pose";
  const readiness = getRobotReadiness(robot);
  if (isInitialPose && !readiness.initialPoseReady) {
    setMessage(elements.mapMessage, readiness.startupMessage, true);
    renderRobotBrain();
    return;
  }
  if (!isInitialPose && !readiness.navigationReady) {
    setMessage(
      elements.mapMessage,
      "Navigation is locked. Check Robot brain and wait for Navigation: Unlocked.",
      true,
    );
    renderRobotBrain();
    return;
  }
  const destinationLabel = pending.name || `x ${formatNumber(pending.pose.x)}, y ${formatNumber(pending.pose.y)}`;
  state.pointMissionInFlight = true;
  elements.confirmMapGo.disabled = true;
  setMessage(
    elements.mapMessage,
    isInitialPose
      ? `Setting ${robot.id}'s approximate position at ${destinationLabel}.`
      : `Sending ${robot.id} to ${destinationLabel}.`,
    false,
  );

  try {
    if (isInitialPose) {
      await sendInitialPose(robot.id, pending.pose.x, pending.pose.y);
      setInitialPoseMode(false, { render: false, announce: false });
    } else {
      await sendDashboardGoal(robot, pending);
    }
    state.operatorPanel.pendingGoal = null;
    await Promise.all([loadDestinations(), loadSnapshot(), loadOperatorPanel({ silent: true })]);
    setMessage(
      elements.mapMessage,
      isInitialPose
        ? "Approximate position sent. The robot will stay still while AMCL refines position and heading."
        : `Destination set to ${destinationLabel}.`,
      false,
    );
  } catch (error) {
    setMessage(
      elements.mapMessage,
      error.message || (isInitialPose ? "Initial position update failed." : "Point mission failed."),
      true,
    );
    await Promise.all([loadSnapshot(), loadOperatorPanel({ silent: true })]);
  } finally {
    state.pointMissionInFlight = false;
    renderMap("state");
  }
}

async function sendDashboardGoal(robot, pending) {
  const { x, y, yaw } = pending.pose;

  const activeMission = getActiveMission();
  if (activeMission) {
    throw new Error("Stop or cancel the active mission before sending a new destination.");
  }

  let toDestination = "Temp Destination";
  if (pending.configuredDestination && state.destinations.some((destination) => destination.name === pending.name)) {
    toDestination = pending.name;
  } else {
    await upsertTempMapDestination(pending);
  }

  const requestResponse = await fetch("/requests", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      requested_by: "map-click",
      command_source: getCommandSource(),
      to_destination: toDestination,
      schedule_type: "single",
      assigned_robot_id: robot.id,
      notes: pending.name
        ? `Named destination ${pending.name} on ${dashboardMapDisplayName()}.`
        : `Point selected at x ${formatNumber(x)}, y ${formatNumber(y)}.`,
    }),
  });
  const requestBody = await requestResponse.json();
  if (!requestResponse.ok) {
    throw new Error(requestBody.detail || "Point request creation failed.");
  }

  if (!isControlDataFresh()) {
    throw new Error(
      "The request was saved, but navigation was not started because live status became unavailable.",
    );
  }
  const startResponse = await fetch(`/requests/${encodeURIComponent(requestBody.request_id)}/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ command_source: getCommandSource() }),
  });
  const startBody = await startResponse.json();
  if (!startResponse.ok) {
    throw new Error(startBody.detail || "Point mission start failed.");
  }
}

async function upsertTempMapDestination(pending) {
  const { x, y, yaw } = pending.pose;
  const response = await fetch("/destinations/temp", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      x,
      y,
      yaw,
      notes: pending.name
        ? `Selected ${pending.name} on ${dashboardMapDisplayName()}.`
        : `Map click on ${dashboardMapDisplayName()}.`,
      command_source: getCommandSource(),
    }),
  });
  const body = await response.json();
  if (!response.ok) {
    throw new Error(body.detail || "Point destination save failed.");
  }
}

async function handleSaveDestination() {
  const name = elements.destinationName.value.trim();
  const x = Number(elements.destinationX.value);
  const y = Number(elements.destinationY.value);
  const yaw = Number(elements.destinationYaw.value || 0);
  const robot = getSelectedRobot();
  if (!name) {
    setMessage(elements.addDestinationMessage, "Enter a destination name.", true);
    return;
  }
  if ([x, y, yaw].some((value) => Number.isNaN(value))) {
    setMessage(elements.addDestinationMessage, "Enter valid destination x, y, and yaw values.", true);
    return;
  }

  elements.saveDestinationButton.disabled = true;
  try {
    if (robot) {
      const initialValues = [elements.initialPoseX.value, elements.initialPoseY.value];
      if (initialValues.some((value) => value.trim() !== "")) {
        const ix = Number(elements.initialPoseX.value);
        const iy = Number(elements.initialPoseY.value);
        if ([ix, iy].some((value) => Number.isNaN(value))) {
          throw new Error("Enter valid initial pose values or leave them blank.");
        }
        await sendInitialPose(robot.id, ix, iy);
      }
    }

    const response = await fetch("/destinations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        x,
        y,
        yaw,
        notes: `Saved from ${currentMapName() || "map"}`,
        command_source: getCommandSource(),
      }),
    });
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.detail || "Destination save failed.");
    }

    elements.destinationName.value = "";
    elements.destinationX.value = "";
    elements.destinationY.value = "";
    elements.destinationYaw.value = "0";
    await loadDestinations();
    setMessage(elements.startNextMessage, `Destination saved: ${name}`, false);
    showScreen("start");
  } catch (error) {
    setMessage(elements.addDestinationMessage, error.message || "Destination save failed.", true);
  } finally {
    elements.saveDestinationButton.disabled = false;
  }
}

function syncTripType() {
  const isRoundTrip = elements.tripType.value === "round_trip";
  elements.returnDestinationField.hidden = !isRoundTrip;
  elements.returnDestination.disabled = !isRoundTrip;
}

async function handleCreateRequest(event) {
  event.preventDefault();
  const payload = {
    requested_by: elements.requester.value.trim(),
    command_source: getCommandSource(),
    to_destination: elements.requestDestination.value,
    schedule_type: elements.tripType.value,
    notes: elements.requestNotes.value.trim(),
  };
  if (payload.schedule_type === "round_trip" && elements.returnDestination.value) {
    payload.from_destination = elements.returnDestination.value;
  }
  if (elements.requestRobot.value) {
    payload.assigned_robot_id = elements.requestRobot.value;
  }

  elements.createRequestButton.disabled = true;
  try {
    const response = await fetch("/requests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.detail || "Request creation failed.");
    }
    setMessage(elements.requestMessage, `Request #${String(body.request_number).padStart(3, "0")} created.`, false);
    elements.createAnotherButton.classList.remove("hidden");
    elements.goStateButton.classList.remove("hidden");
    await loadSnapshot();
  } catch (error) {
    setMessage(elements.requestMessage, error.message || "Request creation failed.", true);
  } finally {
    elements.createRequestButton.disabled = false;
  }
}

function resetRequestForm() {
  elements.requestNotes.value = "";
  elements.createAnotherButton.classList.add("hidden");
  elements.goStateButton.classList.add("hidden");
  setMessage(elements.requestMessage, "", false);
}

async function handleStartRequestClick(event) {
  const button = event.target.closest("[data-start-request]");
  if (!button) {
    return;
  }
  if (!getRobotReadiness().navigationReady) {
    setMessage(
      elements.stateMessage,
      "Navigation is locked. Complete startup and initial localization first.",
      true,
    );
    return;
  }
  const requestId = button.dataset.startRequest;
  button.disabled = true;
  try {
    const response = await fetch(`/requests/${encodeURIComponent(requestId)}/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command_source: getCommandSource() }),
    });
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.detail || "Mission start failed.");
    }
    setMessage(elements.stateMessage, `Mission started from Request #${String(body.request_number).padStart(3, "0")}.`, false);
    await Promise.all([loadSnapshot(), loadOperatorPanel({ silent: true })]);
  } catch (error) {
    setMessage(elements.stateMessage, error.message || "Mission start failed.", true);
  } finally {
    button.disabled = false;
  }
}

async function handleCancelCurrentMission(event) {
  const button = event.currentTarget;
  const activeMission = state.missions.find((item) =>
    item.state !== "Requested" &&
    item.state !== "Completed" &&
    item.outcome !== "Canceled" &&
    item.outcome !== "Failed" &&
    item.outcome !== "Aborted"
  );

  if (activeMission) {
    button.dataset.action = "cancel";
    button.dataset.missionId = activeMission.id;
    await handleMissionAction({ target: button });
    return;
  }

  setMessage(elements.stateMessage, "No active mission to cancel.", false);
}

async function handleNavigationStopAction(event) {
  const button = event.target.closest("[data-navigation-stop]");
  if (!button) {
    return;
  }
  const messageTarget = state.activeScreen === "state" ? elements.stateMessage : elements.mapMessage;
  const robot = getSelectedRobot();
  if (!robot) {
    setMessage(messageTarget, "Select a robot before stopping navigation.", true);
    return;
  }
  const activeMission = state.missions.find((mission) =>
    mission.assigned_robot_id === robot.id &&
    mission.state !== "Requested" &&
    mission.state !== "Completed" &&
    mission.outcome !== "Canceled" &&
    mission.outcome !== "Failed" &&
    mission.outcome !== "Aborted"
  );
  if (!activeMission) {
    setMessage(messageTarget, "No active navigation to stop.", false);
    return;
  }

  button.disabled = true;
  try {
    const response = await fetch(`/missions/${encodeURIComponent(activeMission.id)}/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command_source: getCommandSource() }),
    });
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.detail || "Navigation stop failed.");
    }
    clearPendingMapGoal();
    if (state.operatorPanel.data) {
      state.operatorPanel.data.goal_pose = null;
    }
    setMessage(
      messageTarget,
      "Navigation stopped.",
      false,
    );
    await Promise.all([loadSnapshot(), loadOperatorPanel({ silent: true })]);
  } catch (error) {
    setMessage(messageTarget, error.message || "Navigation stop failed.", true);
  } finally {
    button.disabled = false;
  }
}

async function handleMissionAction(event) {
  const button = event.target.closest("[data-action]");
  if (!button) {
    return;
  }
  const action = button.dataset.action;
  const missionId = button.dataset.missionId;
  if (!missionId) {
    return;
  }
  button.disabled = true;
  try {
    const response = await fetch(`/missions/${encodeURIComponent(missionId)}/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command_source: getCommandSource() }),
    });
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.detail || `${action} failed.`);
    }
    if (action === "return") {
      state.returnPromptDismissed.add(missionId);
      hideReturnModal();
      setMessage(elements.stateMessage, `Return trip started for ${formatRequestNumberById(missionId)}.`, false);
    } else {
      setMessage(elements.stateMessage, `${displayMissionAction(action)} sent.`, false);
    }
    await loadSnapshot();
  } catch (error) {
    setMessage(elements.stateMessage, error.message || `${action} failed.`, true);
  } finally {
    button.disabled = false;
  }
}

function handleManualPadPointerDown(event) {
  const button = event.target.closest("[data-manual-linear], [data-manual-stop]");
  if (!button || button.disabled) {
    return;
  }
  event.preventDefault();
  if (button.dataset.manualStop === "true") {
    stopManualDrive({ sendStop: false, silent: true });
    button.classList.add("is-active");
    window.setTimeout(() => button.classList.remove("is-active"), 180);
    queueManualDriveCommand(0, 0);
    setManualMessage("Stop Movement sent.", false);
    return;
  }

  startManualDrive(
    Number(button.dataset.manualLinear),
    Number(button.dataset.manualAngular),
    button.dataset.manualLabel || button.textContent.trim(),
    button
  );
}

async function handleSaveTempDestination() {
  const goalPose = state.operatorPanel.data?.goal_pose;
  if (!goalPose) {
    setMessage(elements.mapMessage, "No goal position is available to save.", true);
    return;
  }
  try {
    const response = await fetch("/destinations/temp", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        x: goalPose.x,
        y: goalPose.y,
        yaw: goalPose.yaw || 0,
        notes: "Saved from State map.",
        command_source: getCommandSource(),
      }),
    });
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.detail || "Temp destination save failed.");
    }
    await loadDestinations();
    setMessage(elements.mapMessage, "Goal saved as Temp Destination.", false);
  } catch (error) {
    setMessage(elements.mapMessage, error.message || "Temp destination save failed.", true);
  }
}

async function handleQueueReset({ button, endpoint, confirmText, successLabel, messageTarget = elements.queueMessage }) {
  if (!window.confirm(confirmText)) {
    return;
  }
  button.disabled = true;
  try {
    const response = await fetch(endpoint, { method: "POST" });
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.detail || "Queue update failed.");
    }
    setMessage(messageTarget, `${successLabel}: ${body.deleted_missions} removed.`, false);
    await loadSnapshot();
  } catch (error) {
    setMessage(messageTarget, error.message || "Queue update failed.", true);
  } finally {
    button.disabled = false;
  }
}

async function sendSystemCommand(command, { mapName = null, messageTarget = elements.manageMessage } = {}) {
  const robot = getSelectedRobot();
  if (!robot) {
    throw new Error("Select a robot first.");
  }
  const response = await fetch(`/robots/${encodeURIComponent(robot.id)}/system-command`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      command,
      map_name: mapName,
      command_source: getCommandSource(),
    }),
  });
  const body = await response.json();
  if (!response.ok) {
    throw new Error(body.detail || "Robot command failed.");
  }
  setMessage(messageTarget, `${displaySystemCommand(command)} sent.`, false);
  return body;
}

async function sendInitialPose(robotId, x, y) {
  const response = await fetch(`/robots/${encodeURIComponent(robotId)}/initial-pose`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ x, y, command_source: getCommandSource() }),
  });
  const body = await response.json();
  if (!response.ok) {
    throw new Error(body.detail || "Initial position update failed.");
  }
  if (state.operatorPanel.data) {
    state.operatorPanel.data.initial_pose = body.initial_pose?.pose ?? { x, y, yaw: 0 };
    state.operatorPanel.data.localization = body.initial_pose?.localization ?? {
      ...(state.operatorPanel.data.localization ?? {}),
      phase: "stationary_refinement",
      requested: true,
      ready: false,
      refinement_active: true,
    };
  }
}

function buildMissionActionButton(action, mission) {
  if (mission.state === "Completed") {
    return "";
  }
  if (action === "pause" && !["En-route", "Returning"].includes(mission.state)) {
    return "";
  }
  if (action === "resume" && mission.state !== "Paused") {
    return "";
  }
  if (action === "return" && mission.state !== "WaitingForReturn") {
    return "";
  }
  let disabledAttributes = "";
  if (action === "resume") {
    const assignedRobot = (state.robots || []).find(
      (robot) => robot.id === mission.assigned_robot_id,
    );
    const readiness = getRobotReadiness(assignedRobot);
    if (!readiness.navigationReady) {
      const reason = readiness.data?.navigation?.message
        || "Waiting for localization and the previous Nav2 goal to stop before resuming.";
      disabledAttributes = ` disabled aria-disabled="true" title="${escapeHtml(reason)}"`;
    }
  }
  return `<button class="action-button ${action}" type="button" data-action="${action}" data-mission-id="${escapeHtml(mission.id)}"${disabledAttributes}>${displayMissionAction(action)}</button>`;
}

function startManualDrive(linearDir, angularDir, label, button) {
  const robot = getSelectedRobot();
  if (!robot) {
    setManualMessage("Select a robot before driving manually.", true);
    return;
  }
  if (!isManualDriveAvailable(robot)) {
    setManualMessage(getRobotReadiness(robot).manualDriveMessage, true);
    return;
  }

  stopManualDrive({ sendStop: false, silent: true });
  state.manualDrive.currentLinear = linearDir * MANUAL_BASE_SPEED;
  state.manualDrive.currentAngular = angularDir * MANUAL_BASE_SPEED;
  state.manualDrive.activeButton = button;
  button.classList.add("is-active");
  queueManualDriveCommand(state.manualDrive.currentLinear, state.manualDrive.currentAngular);
  setManualMessage(`Manual drive: ${label}`, false);

  state.manualDrive.activeTimer = window.setInterval(() => {
    if (Math.abs(state.manualDrive.currentLinear) < MANUAL_MAX_SPEED) {
      state.manualDrive.currentLinear += linearDir * MANUAL_ACCEL_RATE;
      if (Math.abs(state.manualDrive.currentLinear) > MANUAL_MAX_SPEED) {
        state.manualDrive.currentLinear = linearDir * MANUAL_MAX_SPEED;
      }
    }
    if (Math.abs(state.manualDrive.currentAngular) < MANUAL_MAX_SPEED) {
      state.manualDrive.currentAngular += angularDir * MANUAL_ACCEL_RATE;
      if (Math.abs(state.manualDrive.currentAngular) > MANUAL_MAX_SPEED) {
        state.manualDrive.currentAngular = angularDir * MANUAL_MAX_SPEED;
      }
    }
    queueManualDriveCommand(state.manualDrive.currentLinear, state.manualDrive.currentAngular);
  }, MANUAL_TICK_MS);
}

function stopManualDrive({ sendStop = true, silent = false } = {}) {
  const hadCommand =
    state.manualDrive.activeTimer !== null ||
    Math.abs(state.manualDrive.currentLinear) > 1e-4 ||
    Math.abs(state.manualDrive.currentAngular) > 1e-4;

  if (state.manualDrive.activeTimer !== null) {
    window.clearInterval(state.manualDrive.activeTimer);
    state.manualDrive.activeTimer = null;
  }
  if (state.manualDrive.activeButton) {
    state.manualDrive.activeButton.classList.remove("is-active");
    state.manualDrive.activeButton = null;
  }
  state.manualDrive.currentLinear = 0;
  state.manualDrive.currentAngular = 0;

  if (sendStop && hadCommand) {
    queueManualDriveCommand(0, 0);
  }
  if (hadCommand && !silent) {
    setManualMessage("Manual drive stopped.", false);
  }
}

function queueManualDriveCommand(linear, angular) {
  const isMotionCommand = Math.abs(linear) > 1e-4 || Math.abs(angular) > 1e-4;
  if (isMotionCommand && !isControlDataFresh()) {
    state.manualDrive.pendingCommand = null;
    stopManualDrive({ sendStop: false, silent: true });
    setManualMessage("Manual command not sent because live status is unavailable.", true);
    return;
  }
  const robot = getSelectedRobot();
  if (!robot) {
    state.manualDrive.pendingCommand = null;
    return;
  }
  state.manualDrive.pendingCommand = {
    linear: Number(linear.toFixed(3)),
    angular: Number(angular.toFixed(3)),
    robotId: robot.id,
    createdAt: Date.now(),
  };
  if (!state.manualDrive.requestInFlight) {
    void flushManualDriveCommand();
  }
}

async function flushManualDriveCommand() {
  const nextCommand = state.manualDrive.pendingCommand;
  if (!nextCommand) {
    return;
  }
  const robot = getSelectedRobot();
  const isMotionCommand = Math.abs(nextCommand.linear) > 1e-4 || Math.abs(nextCommand.angular) > 1e-4;
  const commandExpired = Date.now() - Number(nextCommand.createdAt || 0) > 250;
  if (
    !robot ||
    robot.id !== nextCommand.robotId ||
    (isMotionCommand && (!isControlDataFresh() || commandExpired))
  ) {
    state.manualDrive.pendingCommand = null;
    if (isMotionCommand) {
      stopManualDrive({ sendStop: false, silent: true });
    }
    return;
  }

  state.manualDrive.pendingCommand = null;
  state.manualDrive.requestInFlight = true;
  try {
    const response = await fetch(`/robots/${encodeURIComponent(robot.id)}/manual-drive`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        linear: nextCommand.linear,
        angular: nextCommand.angular,
        command_source: getCommandSource(),
      }),
    });
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.detail || "Manual drive failed.");
    }
    if (isMotionCommand && body.command?.paused_mission_id) {
      setManualMessage(
        "Navigation paused for manual recovery. It will stay paused until an operator resumes it.",
        false,
      );
    }
  } catch (error) {
    state.manualDrive.pendingCommand = null;
    setManualMessage(error.message || "Manual drive failed.", true);
    stopManualDrive({ sendStop: false, silent: true });
  } finally {
    state.manualDrive.requestInFlight = false;
    if (state.manualDrive.pendingCommand && isControlDataFresh()) {
      void flushManualDriveCommand();
    } else if (!isControlDataFresh()) {
      state.manualDrive.pendingCommand = null;
    }
  }
}

function drawMap(canvas, mapData, markers) {
  const ctx = canvas.getContext("2d");
  clearCanvas(canvas);
  const raster = getRenderedMapRaster(mapData);
  const padding = 16;
  const view = markers.view || {};
  const baseScale = Math.min((canvas.width - padding * 2) / mapData.width, (canvas.height - padding * 2) / mapData.height);
  const scale = baseScale * (Number(view.zoom) || 1);
  const drawWidth = mapData.width * scale;
  const drawHeight = mapData.height * scale;
  const baseOffsetX = (canvas.width - drawWidth) / 2;
  const baseOffsetY = (canvas.height - drawHeight) / 2;
  if (view.followRobot && markers.robot) {
    centerMapViewOnPose(view, markers.robot, mapData, canvas, baseOffsetX, baseOffsetY, scale);
  }
  const offsetX = baseOffsetX + (Number(view.panX) || 0);
  const offsetY = baseOffsetY + (Number(view.panY) || 0);
  const frame = { map: mapData, offsetX, offsetY, drawWidth, drawHeight, scale };

  ctx.drawImage(raster, offsetX, offsetY, drawWidth, drawHeight);
  ctx.strokeStyle = "rgba(23, 39, 36, 0.24)";
  ctx.strokeRect(offsetX, offsetY, drawWidth, drawHeight);

  if (markers.pendingGoal) {
    const pendingPoint = worldToCanvasWithFrame(markers.pendingGoal.pose, frame);
    if (markers.pendingGoal.kind === "initial_pose") {
      drawPendingInitialPositionMarker(ctx, pendingPoint);
    } else {
      drawPendingGoalMarker(ctx, pendingPoint);
    }
  }
  if (markers.robot) {
    drawUrdfRobotModel(
      ctx,
      worldToCanvasWithFrame(markers.robot, frame),
      markers.robot.yaw || 0,
      frame,
      { localizationValid: Boolean(Number(markers.robot.localization_valid)) },
    );
  }

  state.operatorPanel.frames[canvas.id] = frame;
}

function getRenderedMapRaster(mapData) {
  const key = `${mapData.name || "live"}:${mapData.width}:${mapData.height}:${mapData.updated_at}`;
  if (state.operatorPanel.renderedMapKey === key && state.operatorPanel.renderedMapCanvas) {
    return state.operatorPanel.renderedMapCanvas;
  }

  const canvas = document.createElement("canvas");
  canvas.width = mapData.width;
  canvas.height = mapData.height;
  const ctx = canvas.getContext("2d");
  const imageData = ctx.createImageData(mapData.width, mapData.height);
  for (let row = 0; row < mapData.height; row += 1) {
    for (let col = 0; col < mapData.width; col += 1) {
      const sourceIndex = row * mapData.width + col;
      const value = mapData.data[sourceIndex];
      const targetRow = mapData.height - 1 - row;
      const pixelIndex = (targetRow * mapData.width + col) * 4;
      const shade = value < 0 ? 214 : 255 - Math.round((Math.min(100, Math.max(0, value)) / 100) * 255);
      imageData.data[pixelIndex] = shade;
      imageData.data[pixelIndex + 1] = shade;
      imageData.data[pixelIndex + 2] = shade;
      imageData.data[pixelIndex + 3] = 255;
    }
  }
  ctx.putImageData(imageData, 0, 0);
  state.operatorPanel.renderedMapKey = key;
  state.operatorPanel.renderedMapCanvas = canvas;
  return canvas;
}

function centerMapViewOnPose(view, pose, mapData, canvas, baseOffsetX, baseOffsetY, scale) {
  const point = worldToCanvas(pose, mapData, baseOffsetX, baseOffsetY, scale);
  if ([point.x, point.y].some((value) => !Number.isFinite(value))) {
    return;
  }
  view.panX = (canvas.width / 2) - point.x;
  view.panY = (canvas.height / 2) - point.y;
}

function canvasPointToWorld(canvas, event) {
  const frame = state.operatorPanel.frames[canvas.id];
  if (!frame) {
    return null;
  }
  const rect = canvas.getBoundingClientRect();
  const canvasX = (event.clientX - rect.left) * (canvas.width / rect.width);
  const canvasY = (event.clientY - rect.top) * (canvas.height / rect.height);
  if (
    canvasX < frame.offsetX ||
    canvasX > frame.offsetX + frame.drawWidth ||
    canvasY < frame.offsetY ||
    canvasY > frame.offsetY + frame.drawHeight
  ) {
    return null;
  }
  const gridX = (canvasX - frame.offsetX) / frame.scale;
  const gridY = frame.map.height - ((canvasY - frame.offsetY) / frame.scale);
  return {
    x: frame.map.origin.x + gridX * frame.map.resolution,
    y: frame.map.origin.y + gridY * frame.map.resolution,
  };
}

function mapOccupancyAtWorld(mapData, world) {
  const resolution = Number(mapData?.resolution);
  const width = Number(mapData?.width);
  const height = Number(mapData?.height);
  if (
    !Number.isFinite(resolution) ||
    resolution <= 0 ||
    !Number.isInteger(width) ||
    !Number.isInteger(height)
  ) {
    return null;
  }
  const gridX = Math.floor((Number(world?.x) - Number(mapData.origin?.x)) / resolution);
  const gridY = Math.floor((Number(world?.y) - Number(mapData.origin?.y)) / resolution);
  if (
    !Number.isInteger(gridX) ||
    !Number.isInteger(gridY) ||
    gridX < 0 ||
    gridX >= width ||
    gridY < 0 ||
    gridY >= height
  ) {
    return null;
  }
  const occupancy = Number(mapData.data?.[(gridY * width) + gridX]);
  return Number.isFinite(occupancy) ? occupancy : null;
}

function isWorldPointOpen(mapData, world) {
  const occupancy = mapOccupancyAtWorld(mapData, world);
  return occupancy !== null && occupancy >= 0 && occupancy <= MAP_FREE_OCCUPANCY_MAX;
}

function isWorldPointOpenForCanvas(canvas, world) {
  const frame = state.operatorPanel.frames[canvas.id];
  return Boolean(frame && isWorldPointOpen(frame.map, world));
}

function worldToCanvas(pose, mapData, offsetX, offsetY, scale) {
  const gridX = (Number(pose.x) - mapData.origin.x) / mapData.resolution;
  const gridY = (Number(pose.y) - mapData.origin.y) / mapData.resolution;
  return {
    x: offsetX + gridX * scale,
    y: offsetY + (mapData.height - gridY) * scale,
  };
}

function worldToCanvasWithFrame(pose, frame) {
  return worldToCanvas(pose, frame.map, frame.offsetX, frame.offsetY, frame.scale);
}

function isPointWithinFrame(point, frame) {
  return (
    point.x >= frame.offsetX &&
    point.x <= frame.offsetX + frame.drawWidth &&
    point.y >= frame.offsetY &&
    point.y <= frame.offsetY + frame.drawHeight
  );
}

function drawUrdfRobotModel(ctx, point, yaw, frame, { localizationValid = true } = {}) {
  const pixelsPerMeter = frame.scale / frame.map.resolution;
  const front = ROBOT_FOOTPRINT_FRONT_M * pixelsPerMeter;
  const rear = ROBOT_FOOTPRINT_REAR_M * pixelsPerMeter;
  const width = ROBOT_FOOTPRINT_WIDTH_M * pixelsPerMeter;
  const strokeWidth = Math.max(1.5, Math.min(4, pixelsPerMeter * 0.025));
  const wheelLength = 0.1524 * pixelsPerMeter;
  const wheelWidth = 0.035306 * pixelsPerMeter;

  ctx.save();
  ctx.translate(point.x, point.y);
  ctx.rotate(-(Number(yaw) || 0));
  ctx.globalAlpha = localizationValid ? 1 : 0.48;

  // Top-down rendering of the actual URDF primitives: tabletop/chassis,
  // drive wheels, front casters, electrical box, lidar, and chassis rails.
  ctx.shadowColor = "rgba(17, 35, 45, 0.28)";
  ctx.shadowBlur = Math.max(3, Math.min(10, pixelsPerMeter * 0.08));
  ctx.shadowOffsetY = Math.max(1, Math.min(4, pixelsPerMeter * 0.025));
  ctx.fillStyle = "rgba(247, 249, 251, 0.96)";
  ctx.strokeStyle = "#485c72";
  ctx.lineWidth = strokeWidth;
  ctx.beginPath();
  ctx.rect(-rear, -width / 2, front + rear, width);
  ctx.fill();
  ctx.stroke();

  ctx.shadowColor = "transparent";
  ctx.fillStyle = "#315fbb";
  for (const side of [-1, 1]) {
    ctx.fillRect(
      (0.05 * pixelsPerMeter) - (wheelLength / 2),
      (side * 0.22 * pixelsPerMeter) - (wheelWidth / 2),
      wheelLength,
      wheelWidth,
    );
  }

  ctx.fillStyle = "#1d252c";
  for (const side of [-1, 1]) {
    ctx.beginPath();
    ctx.arc(
      0.88 * pixelsPerMeter,
      side * 0.28 * pixelsPerMeter,
      0.0635 * pixelsPerMeter,
      0,
      Math.PI * 2,
    );
    ctx.fill();
  }

  ctx.strokeStyle = "rgba(72, 92, 114, 0.58)";
  ctx.lineWidth = Math.max(1, strokeWidth * 0.55);
  ctx.beginPath();
  for (const crossRailX of [0.205, 0.57]) {
    ctx.moveTo(crossRailX * pixelsPerMeter, -0.26 * pixelsPerMeter);
    ctx.lineTo(crossRailX * pixelsPerMeter, 0.26 * pixelsPerMeter);
  }
  ctx.stroke();

  ctx.fillStyle = "#252b31";
  ctx.fillRect(
    (0.77 - 0.09) * pixelsPerMeter,
    -0.13 * pixelsPerMeter,
    0.18 * pixelsPerMeter,
    0.26 * pixelsPerMeter,
  );

  ctx.fillStyle = "#d84a57";
  ctx.beginPath();
  ctx.arc(0.81 * pixelsPerMeter, 0, 0.05 * pixelsPerMeter, 0, Math.PI * 2);
  ctx.fill();

  ctx.fillStyle = "#485c72";
  ctx.beginPath();
  ctx.moveTo(front + Math.max(4, 0.06 * pixelsPerMeter), 0);
  ctx.lineTo(front - Math.max(5, 0.08 * pixelsPerMeter), -Math.max(4, 0.045 * pixelsPerMeter));
  ctx.lineTo(front - Math.max(5, 0.08 * pixelsPerMeter), Math.max(4, 0.045 * pixelsPerMeter));
  ctx.closePath();
  ctx.fill();

  if (!localizationValid) {
    // Preserve the last accepted pose for manual recovery, but make it
    // visually unmistakable that navigation may not trust this position.
    ctx.globalAlpha = 1;
    ctx.shadowColor = "transparent";
    ctx.strokeStyle = "#ad6800";
    ctx.lineWidth = Math.max(2, strokeWidth);
    ctx.setLineDash([
      Math.max(4, 0.05 * pixelsPerMeter),
      Math.max(3, 0.035 * pixelsPerMeter),
    ]);
    const warningPadding = Math.max(3, 0.035 * pixelsPerMeter);
    ctx.strokeRect(
      -rear - warningPadding,
      (-width / 2) - warningPadding,
      front + rear + (2 * warningPadding),
      width + (2 * warningPadding),
    );
    ctx.setLineDash([]);
    ctx.fillStyle = "#ad6800";
    ctx.beginPath();
    ctx.arc(0, 0, Math.max(4, 0.045 * pixelsPerMeter), 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
}

function drawPendingInitialPositionMarker(ctx, point) {
  ctx.save();
  ctx.fillStyle = "rgba(154, 100, 28, 0.16)";
  ctx.strokeStyle = "#9a641c";
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.arc(point.x, point.y, 14, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(point.x - 19, point.y);
  ctx.lineTo(point.x + 19, point.y);
  ctx.moveTo(point.x, point.y - 19);
  ctx.lineTo(point.x, point.y + 19);
  ctx.stroke();
  ctx.restore();
}

function drawPendingGoalMarker(ctx, point) {
  ctx.save();
  ctx.fillStyle = "rgba(18, 83, 134, 0.18)";
  ctx.strokeStyle = "#125386";
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.arc(point.x, point.y, 14, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(point.x - 18, point.y);
  ctx.lineTo(point.x + 18, point.y);
  ctx.moveTo(point.x, point.y - 18);
  ctx.lineTo(point.x, point.y + 18);
  ctx.stroke();
  ctx.restore();
}

function clearCanvas(canvas) {
  if (!canvas) {
    return;
  }
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#f7faf9";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  delete state.operatorPanel.frames[canvas.id];
}

function getSelectedRobot() {
  return state.robots.find((robot) => robot.id === elements.selectedRobot.value) || null;
}

function getMapContextRobot() {
  return getSelectedRobot() || sortedRobotsForSelection()[0] || null;
}

function isRobotVisibleOnMap(robot) {
  const operatorData = state.operatorPanel.data?.robot_id === robot?.id
    ? state.operatorPanel.data
    : null;
  const acceptedMapPose = Boolean(Number(robot?.localization_valid))
    || Boolean(operatorData?.localization?.accepted_map_pose_available);
  return Boolean(
    robot
    && robot.online !== false
    && robot.connection_ok
    && acceptedMapPose
    && robot.x != null
    && robot.y != null
    && Number.isFinite(Number(robot.x))
    && Number.isFinite(Number(robot.y))
  );
}

function sortedRobotsForSelection() {
  return [...state.robots].sort((a, b) => {
    const aConnected = robotConnectionLabel(a) === "Connected" ? 0 : 1;
    const bConnected = robotConnectionLabel(b) === "Connected" ? 0 : 1;
    if (aConnected !== bConnected) {
      return aConnected - bConnected;
    }
    const aOnline = a.online === false ? 1 : 0;
    const bOnline = b.online === false ? 1 : 0;
    if (aOnline !== bOnline) {
      return aOnline - bOnline;
    }
    const aHasTelemetry = a.power ? 0 : 1;
    const bHasTelemetry = b.power ? 0 : 1;
    if (aHasTelemetry !== bHasTelemetry) {
      return aHasTelemetry - bHasTelemetry;
    }
    return String(a.id || "").localeCompare(String(b.id || ""));
  });
}

function getSavedMaps() {
  return state.operatorPanel.data?.saved_maps ?? [];
}

function currentMapName() {
  return state.operatorPanel.data?.current_map_name || "";
}

function dashboardMapDisplayName() {
  const hasActiveMap = Boolean(state.operatorPanel.mapPreview || currentMapName());
  return hasActiveMap ? DASHBOARD_MAP_DISPLAY_NAME : "";
}

function getPendingRequests() {
  return state.missions
    .filter((mission) => mission.state === "Requested")
    .sort((a, b) => Number(a.created_at || 0) - Number(b.created_at || 0));
}

function getActiveMission() {
  return state.missions.find((item) =>
    item.state !== "Requested" &&
    item.state !== "Completed" &&
    item.outcome !== "Canceled" &&
    item.outcome !== "Failed" &&
    item.outcome !== "Aborted"
  ) || null;
}

function getWaitingForReturnMission() {
  return state.missions
    .filter((mission) => mission.state === "WaitingForReturn")
    .sort((a, b) => Number(a.last_update_at || 0) - Number(b.last_update_at || 0))[0] || null;
}

function getCommandSource() {
  return {
    type: "operator",
    id: elements.operatorId.value.trim() || "dashboard-1",
  };
}

function robotConnectionLabel(robot) {
  if (!robot) {
    return "--";
  }
  return robot.online === false || !robot.connection_ok ? "Disconnected" : "Connected";
}

function robotWarningLabel(robot, battery) {
  const power = robot?.power ?? {};
  const warnings = [];
  const explicitStatus = formatExplicitStatus(robot?.error || robot?.warning || power.error || power.warning);
  if (explicitStatus) {
    warnings.push(explicitStatus);
  }
  if (robotConnectionLabel(robot) === "Disconnected") {
    warnings.push("Disconnected");
  }
  if (battery != null && Number(battery) < 20) {
    warnings.push("Low battery");
  }
  if (robot?.localization_valid != null && Number(robot.localization_valid) === 0) {
    warnings.push("Localization");
  }
  return warnings.length ? warnings.join(", ") : "None";
}

function formatExplicitStatus(value) {
  if (!value) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  if (Array.isArray(value)) {
    return value.filter(Boolean).join(", ");
  }
  return "Robot warning";
}

function isManualDriveAvailable(robot = getSelectedRobot()) {
  return canEnableManualDrive(robot);
}

function canEnableManualDrive(robot = getSelectedRobot()) {
  return getRobotReadiness(robot).manualDriveReady;
}

function formatRoute(mission) {
  if (mission.schedule_type === "round_trip") {
    return `${mission.to_dest} -> ${mission.from_dest || state.home || "Home"}`;
  }
  return mission.to_dest;
}

function formatRequestNumber(mission) {
  const ordered = [...state.missions].sort((a, b) => Number(a.created_at || 0) - Number(b.created_at || 0));
  const index = ordered.findIndex((item) => item.id === mission.id);
  return `Request #${String(index + 1 || 1).padStart(3, "0")}`;
}

function formatRequestNumberById(missionId) {
  const mission = state.missions.find((item) => item.id === missionId);
  return mission ? formatRequestNumber(mission) : "mission";
}

function formatPoseShort(pose) {
  if (!pose) {
    return "--";
  }
  return `x ${formatNumber(pose.x)}, y ${formatNumber(pose.y)}, yaw ${formatNumber(pose.yaw || 0)}`;
}

function displayMissionStatus(mission) {
  if (mission.help_required) {
    return "Needs Help";
  }
  if (mission.outcome === "Canceled") {
    return "Canceled";
  }
  if (mission.outcome === "Failed" || mission.outcome === "Aborted") {
    return "Failed";
  }
  const assignedRobot = (state.robots || []).find(
    (robot) => robot.id === mission.assigned_robot_id,
  );
  if (
    mission.state === "En-route" &&
    assignedRobot?.state === "Requested"
  ) {
    return "Preparing Navigation";
  }
  const labels = {
    Requested: "Pending Request",
    Idle: "Queued",
    "En-route": "In Progress",
    WaitingForReturn: "Waiting for Return",
    Returning: "Returning",
    Paused: "Paused",
    Completed: "Completed",
  };
  return labels[mission.state] || mission.state || "--";
}

function displayMissionAction(action) {
  const labels = {
    pause: "Pause",
    resume: "Resume",
    return: "Return",
    cancel: "Cancel Mission",
  };
  return labels[action] || action;
}

function displayRobotMode(mode) {
  const labels = {
    AUTO: "Auto",
    MANUALOVERRIDE: "Manual",
  };
  return labels[String(mode || "").toUpperCase()] || String(mode || "--");
}

function displaySystemCommand(command) {
  const labels = {
    launch_slam: "Mapping Mode",
    launch_nav: "Map selected",
    launch_robot: "Robot System",
    kill_all: "Kill Launcher Processes",
  };
  return labels[command] || command;
}

function batteryPercentFromVoltage(voltage) {
  const numericVoltage = Number(voltage);
  if (!Number.isFinite(numericVoltage) || numericVoltage <= 0) {
    return null;
  }
  if (numericVoltage <= BATTERY_DISCHARGE_CURVE[0][0]) {
    return 0;
  }
  if (numericVoltage >= BATTERY_DISCHARGE_CURVE[BATTERY_DISCHARGE_CURVE.length - 1][0]) {
    return 100;
  }
  for (let index = 1; index < BATTERY_DISCHARGE_CURVE.length; index += 1) {
    const [upperVoltage, upperPercent] = BATTERY_DISCHARGE_CURVE[index];
    if (numericVoltage <= upperVoltage) {
      const [lowerVoltage, lowerPercent] = BATTERY_DISCHARGE_CURVE[index - 1];
      const fraction = (numericVoltage - lowerVoltage) / (upperVoltage - lowerVoltage);
      return lowerPercent + fraction * (upperPercent - lowerPercent);
    }
  }
  return 100;
}

function batteryPercentForDisplay(robot) {
  if (!robot) {
    return null;
  }
  const power = robot.power ?? {};
  const rawBattery = power.battery_percent ?? batteryPercentFromVoltage(robot.battery_v);
  const numericBattery = Number(rawBattery);
  if (rawBattery == null || !Number.isFinite(numericBattery)) {
    return null;
  }

  const robotId = String(robot.id || "selected-robot");
  const clampedBattery = Math.max(0, Math.min(100, numericBattery));
  let displayedBattery = state.batteryDisplayByRobot.get(robotId);
  if (!Number.isFinite(displayedBattery)) {
    displayedBattery = Math.round(clampedBattery / BATTERY_DISPLAY_STEP) * BATTERY_DISPLAY_STEP;
  } else {
    while (
      displayedBattery < 100 &&
      clampedBattery >= displayedBattery + (BATTERY_DISPLAY_STEP / 2) + BATTERY_DISPLAY_HYSTERESIS
    ) {
      displayedBattery += BATTERY_DISPLAY_STEP;
    }
    while (
      displayedBattery > 0 &&
      clampedBattery <= displayedBattery - (BATTERY_DISPLAY_STEP / 2) - BATTERY_DISPLAY_HYSTERESIS
    ) {
      displayedBattery -= BATTERY_DISPLAY_STEP;
    }
  }
  displayedBattery = Math.max(0, Math.min(100, displayedBattery));
  state.batteryDisplayByRobot.set(robotId, displayedBattery);
  return displayedBattery;
}

function formatNumber(value) {
  if (value == null || Number.isNaN(Number(value))) {
    return "--";
  }
  return Number(value).toFixed(2);
}

function setText(element, value) {
  if (element) {
    element.textContent = value;
  }
}

function setManualMessage(message, isError) {
  document.querySelectorAll("[data-manual-message]").forEach((element) => {
    setMessage(element, message, isError);
  });
}

function setMessage(element, message, isError) {
  if (!element) {
    return;
  }
  const existingTimer = messageTimers.get(element);
  if (existingTimer) {
    window.clearTimeout(existingTimer);
    messageTimers.delete(element);
  }

  element.textContent = message || "";
  element.classList.toggle("error", Boolean(message && isError));
  element.classList.toggle("success", Boolean(message && !isError));

  if (!message) {
    delete element.dataset.messageToken;
    return;
  }

  const token = `${Date.now()}:${Math.random()}`;
  element.dataset.messageToken = token;
  const timeout = isError ? MESSAGE_ERROR_TIMEOUT_MS : MESSAGE_SUCCESS_TIMEOUT_MS;
  const timer = window.setTimeout(() => {
    if (element.dataset.messageToken !== token) {
      return;
    }
    element.textContent = "";
    element.classList.remove("error", "success");
    delete element.dataset.messageToken;
    messageTimers.delete(element);
  }, timeout);
  messageTimers.set(element, timer);
}

function slugify(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
