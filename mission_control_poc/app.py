from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from mission_control.api_models import (
    CreateMissionRequest,
    CreateMissionResponse,
    CreateRequestResponse,
    DestinationSaveRequest,
    MissionCommandRequest,
    MissionDetail,
    RobotGoalPoseRequest,
    RobotInitialPoseRequest,
    RobotMapDeleteRequest,
    RobotMapSaveRequest,
    RobotManualDriveRequest,
    RobotNavigateToPointRequest,
    RobotSystemCommandRequest,
    RobotTelemetryIn,
    StatusSnapshot,
    TempDestinationRequest,
)
from mission_control.config_loader import DestinationConfig
from mission_control.mobile_api import is_open_display_map_point
from mission_control.robot_adapter import create_robot_adapter_from_env
from mission_control.scheduler import MissionControl, MissionCreate
from mission_control.storage import Storage
from mission_control.types import CommandSource
from mission_control.types import MissionState


BASE_DIR = Path(__file__).parent
DB_PATH = Path(
    os.getenv("MISSION_CONTROL_DB_PATH", str(BASE_DIR / "mission_control.sqlite3"))
).expanduser()
DEST_CONFIG_PATH = Path(
    os.getenv(
        "MISSION_CONTROL_DESTINATIONS_PATH",
        str(BASE_DIR / "config" / "destinations.yaml"),
    )
).expanduser()
UI_DIR = BASE_DIR / "ui"
SERVER_BUILD = "central-contract-readiness"

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
DEST_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

storage = Storage(DB_PATH)
storage.init()

dest_config = DestinationConfig(DEST_CONFIG_PATH)
dest_config.load()

mc = MissionControl(storage=storage, dest_config=dest_config)

# Register the configured robot adapter. Default backend is the simulated robot.
mc.register_robot(create_robot_adapter_from_env(dest_config))

mobile_point_lock = asyncio.Lock()

app = FastAPI(title="Mission Control PoC", version="0.1.0")
app.mount("/ui-assets", StaticFiles(directory=str(UI_DIR)), name="ui-assets")

app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=4)


@app.middleware("http")
async def disable_ui_caching(request, call_next):
    response = await call_next(request)
    if request.url.path == "/ui" or request.url.path.startswith("/ui-assets/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.on_event("startup")
async def _startup() -> None:
    await mc.start()


@app.on_event("shutdown")
async def _shutdown() -> None:
    await mc.stop()


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/ui")


@app.get("/ui", include_in_schema=False)
def ui() -> FileResponse:
    return FileResponse(UI_DIR / "index.html")


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "build": SERVER_BUILD}


@app.get("/status", response_model=StatusSnapshot)
def status() -> Dict[str, Any]:
    return mc.snapshot()


@app.websocket("/ws/status")
async def ws_status(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            await ws.send_json(mc.snapshot())
            await asyncio.sleep(1.0)  # ≥1 Hz status interface (S3.2.8)
    except Exception:
        # client disconnected
        return


@app.get("/destinations")
def list_destinations() -> Dict[str, Any]:
    return {"destinations": [d.__dict__ for d in dest_config.list()], "home": dest_config.home()}


@app.post("/destinations/reload")
def reload_destinations() -> Dict[str, Any]:
    dest_config.load()
    return {"ok": True, "count": len(dest_config.list()), "home": dest_config.home()}


def _resolve_target_robot_id(preferred_robot_id: str | None) -> str | None:
    registered_robot_ids = mc.registered_robot_ids()
    if preferred_robot_id:
        if preferred_robot_id not in registered_robot_ids:
            raise ValueError(f"Unknown assigned_robot_id: {preferred_robot_id}")
        return preferred_robot_id
    if len(registered_robot_ids) == 1:
        return registered_robot_ids[0]
    return None


def _command_source_from_model(model: Any) -> CommandSource:
    return CommandSource(
        source_type=model.type,
        source_id=model.id,
        meta=model.meta,
    )


def _request_number_for(mission_id: str) -> int:
    missions = sorted(storage.list_missions(limit=1000), key=lambda mission: float(mission.get("created_at") or 0))
    for index, mission in enumerate(missions, start=1):
        if mission["id"] == mission_id:
            return index
    return len(missions)


def _return_destination_for(mission: Dict[str, Any]) -> str | None:
    return mission.get("from_dest") or dest_config.home()


def _startup_not_ready_message(operator_snapshot: Dict[str, Any]) -> str:
    startup = operator_snapshot.get("startup") or {}
    return str(startup.get("message") or "Waiting for the Pi robot stack to become ready.")


async def _sync_robot_goal_context(
    robot_id: str,
    destination_name: str,
    command_source: CommandSource,
) -> None:
    destinations, _ = dest_config.get()
    destination = destinations.get(destination_name)
    if destination is None:
        raise ValueError(f"Invalid destination: {destination_name}")

    goal_pose = destination.pose or {}
    await mc.set_robot_goal_pose(
        robot_id,
        float(goal_pose.get("x", 0.0)),
        float(goal_pose.get("y", 0.0)),
        float(goal_pose.get("yaw", 0.0)),
        command_source,
    )


async def _sync_robot_navigation_context(
    robot_id: str,
    destination_name: str,
    command_source: CommandSource,
) -> None:
    destinations, _ = dest_config.get()
    destination = destinations.get(destination_name)
    if destination is None:
        raise ValueError(f"Invalid destination: {destination_name}")

    snapshot = mc.snapshot()
    robot_status = next((robot for robot in snapshot["robots"] if robot["id"] == robot_id), None)
    if not robot_status or not robot_status.get("localization_valid"):
        raise ValueError(
            "Robot is not localized yet. Check Robot brain and wait for AMCL to unlock navigation."
        )

    operator_snapshot = mc.robot_operator_snapshot(robot_id, include_map=False)
    if not operator_snapshot.get("navigation_available"):
        startup = operator_snapshot.get("startup") or {}
        if not startup.get("ready"):
            raise ValueError(
                f"Robot startup is not ready. {_startup_not_ready_message(operator_snapshot)}"
            )
        raise ValueError(
            "Navigation is still starting. Wait for Robot brain to show Navigation: Unlocked."
        )

    await _sync_robot_goal_context(robot_id, destination_name, command_source)


@app.post("/missions", response_model=CreateMissionResponse)
async def create_mission(req: CreateMissionRequest) -> CreateMissionResponse:
    try:
        if not dest_config.validate(req.to_destination):
            raise ValueError(f"Invalid destination: {req.to_destination}")
        if req.from_destination and not dest_config.validate(req.from_destination):
            raise ValueError(f"Invalid from_destination: {req.from_destination}")
        if req.schedule_type not in ("single", "round_trip"):
            raise ValueError("schedule_type must be 'single' or 'round_trip'")

        command_source = CommandSource(
            source_type=req.command_source.type,
            source_id=req.command_source.id,
            meta=req.command_source.meta,
        )
        target_robot_id = _resolve_target_robot_id(req.assigned_robot_id)
        if target_robot_id:
            await _sync_robot_navigation_context(target_robot_id, req.to_destination, command_source)

        mission_id = mc.create_mission(
            MissionCreate(
                requested_by=req.requested_by,
                command_source=command_source,
                to_destination=req.to_destination,
                schedule_type=req.schedule_type,
                from_destination=req.from_destination,
                assigned_robot_id=req.assigned_robot_id,
                notes=req.notes,
            )
        )
        return CreateMissionResponse(mission_id=mission_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/requests", response_model=CreateRequestResponse)
async def create_request(req: CreateMissionRequest) -> CreateRequestResponse:
    try:
        if not dest_config.validate(req.to_destination):
            raise ValueError(f"Invalid destination: {req.to_destination}")
        if req.from_destination and not dest_config.validate(req.from_destination):
            raise ValueError(f"Invalid return destination: {req.from_destination}")
        if req.schedule_type not in ("single", "round_trip"):
            raise ValueError("Trip type must be one-way or round trip.")

        request_id = mc.create_request(
            MissionCreate(
                requested_by=req.requested_by,
                command_source=_command_source_from_model(req.command_source),
                to_destination=req.to_destination,
                schedule_type=req.schedule_type,
                from_destination=req.from_destination,
                assigned_robot_id=req.assigned_robot_id,
                notes=req.notes,
            )
        )
        return CreateRequestResponse(request_id=request_id, request_number=_request_number_for(request_id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.get("/requests")
def list_requests() -> Dict[str, Any]:
    missions = storage.list_missions()
    requests = [mission for mission in missions if mission["state"] == MissionState.REQUESTED.value]
    return {"requests": requests}


@app.post("/requests/{request_id}/start")
async def start_request(request_id: str, req: MissionCommandRequest) -> Dict[str, Any]:
    try:
        mission = storage.get_mission(request_id)
        if not mission:
            raise KeyError("request not found")
        if mission["state"] != MissionState.REQUESTED.value:
            raise RuntimeError("Only pending requests can be started.")
        if not dest_config.validate(mission["to_dest"]):
            raise ValueError("Cannot start mission: destination is missing.")

        target_robot_id = _resolve_target_robot_id(mission.get("assigned_robot_id"))
        if target_robot_id is None:
            raise ValueError("Cannot start mission: choose a robot first.")

        snapshot = mc.snapshot()
        robot_status = next((robot for robot in snapshot["robots"] if robot["id"] == target_robot_id), None)
        if robot_status is None:
            raise ValueError("Cannot start mission: robot is not available.")
        if not robot_status.get("connection_ok"):
            raise ValueError("Cannot start mission: robot is not connected.")

        operator_snapshot = mc.robot_operator_snapshot(target_robot_id)
        if not operator_snapshot.get("current_map_name"):
            raise ValueError("Cannot start mission: no map selected.")
        if not robot_status.get("localization_valid"):
            raise ValueError(
                "Cannot start mission: AMCL is not ready. Check Robot brain for localization details."
            )
        if not operator_snapshot.get("navigation_available"):
            startup = operator_snapshot.get("startup") or {}
            if not startup.get("ready"):
                raise ValueError(
                    "Cannot start mission: "
                    + _startup_not_ready_message(operator_snapshot)
                )
            raise ValueError(
                "Cannot start mission: Nav2 is still starting. "
                "Wait for Navigation: Unlocked."
            )

        command_source = _command_source_from_model(req.command_source)
        await _sync_robot_navigation_context(target_robot_id, mission["to_dest"], command_source)
        mc.start_request(request_id, command_source, assigned_robot_id=target_robot_id)
        return {
            "ok": True,
            "request_id": request_id,
            "mission_id": request_id,
            "request_number": _request_number_for(request_id),
        }
    except KeyError:
        raise HTTPException(status_code=404, detail="request not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.get("/missions")
def list_missions() -> Dict[str, Any]:
    return {"missions": storage.list_missions()}


@app.get("/missions/{mission_id}", response_model=MissionDetail)
def mission_detail(mission_id: str) -> Dict[str, Any]:
    try:
        return mc.mission_detail(mission_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="mission not found")


@app.post("/admin/missions/clear-completed")
async def clear_completed_missions() -> Dict[str, Any]:
    deleted_missions = await mc.clear_completed_history()
    return {"ok": True, "deleted_missions": deleted_missions}


@app.post("/admin/requests/clear-pending")
async def clear_pending_requests() -> Dict[str, Any]:
    deleted_missions = await mc.clear_pending_requests()
    return {"ok": True, "deleted_missions": deleted_missions}


@app.post("/admin/missions/clear-all")
async def clear_all_missions() -> Dict[str, Any]:
    try:
        deleted_missions = await mc.clear_all_missions()
        return {"ok": True, "deleted_missions": deleted_missions}
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/missions/{mission_id}/pause")
async def pause_mission(mission_id: str, req: MissionCommandRequest) -> Dict[str, Any]:
    try:
        await mc.pause_mission(mission_id, CommandSource(source_type=req.command_source.type, source_id=req.command_source.id, meta=req.command_source.meta))
        return {"ok": True}
    except KeyError:
        raise HTTPException(status_code=404, detail="mission not found")
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/missions/{mission_id}/resume")
async def resume_mission(mission_id: str, req: MissionCommandRequest) -> Dict[str, Any]:
    try:
        await mc.resume_mission(mission_id, CommandSource(source_type=req.command_source.type, source_id=req.command_source.id, meta=req.command_source.meta))
        return {"ok": True}
    except KeyError:
        raise HTTPException(status_code=404, detail="mission not found")
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/missions/{mission_id}/cancel")
async def cancel_mission(mission_id: str, req: MissionCommandRequest) -> Dict[str, Any]:
    try:
        await mc.cancel_mission(mission_id, CommandSource(source_type=req.command_source.type, source_id=req.command_source.id, meta=req.command_source.meta))
        return {"ok": True}
    except KeyError:
        raise HTTPException(status_code=404, detail="mission not found")
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/missions/{mission_id}/return")
async def return_mission(mission_id: str, req: MissionCommandRequest) -> Dict[str, Any]:
    try:
        mission = storage.get_mission(mission_id)
        if not mission:
            raise KeyError("mission not found")
        if mission["state"] != MissionState.WAITING_FOR_RETURN.value:
            raise RuntimeError("Mission is not waiting for return.")

        return_destination = _return_destination_for(mission)
        if not return_destination or not dest_config.validate(return_destination):
            raise ValueError("Cannot return: return destination is missing.")

        robot_id = mission.get("assigned_robot_id")
        if not robot_id:
            raise ValueError("Cannot return: assigned robot is missing.")

        snapshot = mc.snapshot()
        robot_status = next((robot for robot in snapshot["robots"] if robot["id"] == robot_id), None)
        if robot_status is None:
            raise ValueError("Cannot return: robot is not available.")
        if not robot_status.get("connection_ok"):
            raise ValueError("Cannot return: robot is not connected.")

        operator_snapshot = mc.robot_operator_snapshot(robot_id)
        if not operator_snapshot.get("current_map_name"):
            raise ValueError("Cannot return: no map selected.")
        if not robot_status.get("localization_valid"):
            raise ValueError("Cannot return: robot start position is not available.")
        if not operator_snapshot.get("navigation_available"):
            raise ValueError(
                "Cannot return: navigation is not unlocked yet. Check Robot brain."
            )

        command_source = _command_source_from_model(req.command_source)
        await _sync_robot_goal_context(robot_id, return_destination, command_source)
        result = await mc.start_return_trip(mission_id, command_source)
        return {
            "ok": True,
            "mission_id": mission_id,
            "request_number": _request_number_for(mission_id),
            **result,
        }
    except KeyError:
        raise HTTPException(status_code=404, detail="mission not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/robots/{robot_id}/manual-drive")
async def send_robot_manual_drive_command(robot_id: str, req: RobotManualDriveRequest) -> Dict[str, Any]:
    try:
        command = await mc.send_robot_manual_drive_command(
            robot_id,
            req.linear,
            req.angular,
            CommandSource(
                source_type=req.command_source.type,
                source_id=req.command_source.id,
                meta=req.command_source.meta,
            ),
        )
        return {"ok": True, "command": command}
    except KeyError:
        raise HTTPException(status_code=404, detail="robot not found")
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/robots/{robot_id}/navigate-to-point")
async def navigate_robot_to_point(
    robot_id: str,
    req: RobotNavigateToPointRequest,
) -> Dict[str, Any]:
    """Validate a white display-map point and start one navigation mission."""

    try:
        async with mobile_point_lock:
            target_robot_id = _resolve_target_robot_id(robot_id)
            if target_robot_id is None:
                raise ValueError("Choose a robot before sending a destination.")

            robot_status = next(
                (
                    robot
                    for robot in mc.snapshot()["robots"]
                    if robot["id"] == target_robot_id
                ),
                None,
            )
            if robot_status is None:
                raise ValueError("Robot is not available.")
            if robot_status.get("current_mission_id"):
                raise RuntimeError(
                    "Stop, cancel, or finish the active mission before sending a new destination."
                )

            operator_snapshot = mc.robot_operator_snapshot(
                target_robot_id,
                include_map=True,
            )
            map_snapshot = operator_snapshot.get("map")
            if not isinstance(map_snapshot, dict):
                raise ValueError("The active display map is not available.")
            if not is_open_display_map_point(map_snapshot, req.x, req.y):
                raise ValueError(
                    "The destination must be inside a white open area of the display map."
                )

            command_source = _command_source_from_model(req.command_source)
            destination = dest_config.upsert_destination(
                "Temp Destination",
                {"x": req.x, "y": req.y, "yaw": req.yaw},
                notes=req.notes or f"Point sent by {req.requested_by}.",
            )
            await _sync_robot_navigation_context(
                target_robot_id,
                destination.name,
                command_source,
            )

            mission_id = mc.create_request(
                MissionCreate(
                    requested_by=req.requested_by,
                    command_source=command_source,
                    to_destination=destination.name,
                    schedule_type="single",
                    assigned_robot_id=target_robot_id,
                    notes=req.notes or (
                        f"Mobile point selected at x {req.x:.2f}, y {req.y:.2f}."
                    ),
                )
            )
            mc.start_request(
                mission_id,
                command_source,
                assigned_robot_id=target_robot_id,
            )
            return {
                "ok": True,
                "mission_id": mission_id,
                "request_id": mission_id,
                "request_number": _request_number_for(mission_id),
                "robot_id": target_robot_id,
                "destination": {
                    "x": req.x,
                    "y": req.y,
                    "yaw": req.yaw,
                },
            }
    except KeyError:
        raise HTTPException(status_code=404, detail="robot not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/robots/{robot_id}/localize")
async def localize_robot(robot_id: str, req: MissionCommandRequest) -> Dict[str, Any]:
    try:
        operator_snapshot = mc.robot_operator_snapshot(robot_id, include_map=False)
        if not operator_snapshot.get("initial_pose_available"):
            raise RuntimeError(
                "Robot startup is not ready. "
                + _startup_not_ready_message(operator_snapshot)
            )
        result = await mc.localize_robot(
            robot_id,
            CommandSource(
                source_type=req.command_source.type,
                source_id=req.command_source.id,
                meta=req.command_source.meta,
            ),
        )
        return {"ok": True, "localization": result}
    except KeyError:
        raise HTTPException(status_code=404, detail="robot not found")
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/robots/{robot_id}/system-command")
async def send_robot_system_command(robot_id: str, req: RobotSystemCommandRequest) -> Dict[str, Any]:
    try:
        command = await mc.send_robot_system_command(
            robot_id,
            req.command,
            CommandSource(
                source_type=req.command_source.type,
                source_id=req.command_source.id,
                meta=req.command_source.meta,
            ),
            map_name=req.map_name,
        )
        return {"ok": True, "command": command}
    except KeyError:
        raise HTTPException(status_code=404, detail="robot not found")
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/robots/{robot_id}/initial-pose")
async def set_robot_initial_pose(robot_id: str, req: RobotInitialPoseRequest) -> Dict[str, Any]:
    try:
        operator_snapshot = mc.robot_operator_snapshot(robot_id, include_map=False)
        if not operator_snapshot.get("initial_pose_available"):
            raise RuntimeError(
                "Robot startup is not ready. "
                + _startup_not_ready_message(operator_snapshot)
            )
        pose = await mc.set_robot_initial_pose(
            robot_id,
            req.x,
            req.y,
            req.yaw,
            CommandSource(
                source_type=req.command_source.type,
                source_id=req.command_source.id,
                meta=req.command_source.meta,
            ),
        )
        return {"ok": True, "initial_pose": pose}
    except KeyError:
        raise HTTPException(status_code=404, detail="robot not found")
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/robots/{robot_id}/goal-pose")
async def set_robot_goal_pose(robot_id: str, req: RobotGoalPoseRequest) -> Dict[str, Any]:
    try:
        operator_snapshot = mc.robot_operator_snapshot(robot_id, include_map=False)
        if not operator_snapshot.get("navigation_available"):
            raise RuntimeError(
                "Navigation is locked. Wait for startup, initial localization, "
                "and Navigation: Unlocked."
            )
        pose = await mc.set_robot_goal_pose(
            robot_id,
            req.x,
            req.y,
            req.yaw,
            CommandSource(
                source_type=req.command_source.type,
                source_id=req.command_source.id,
                meta=req.command_source.meta,
            ),
        )
        return {"ok": True, "goal_pose": pose}
    except KeyError:
        raise HTTPException(status_code=404, detail="robot not found")
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/robots/{robot_id}/maps/save")
async def save_robot_map(robot_id: str, req: RobotMapSaveRequest) -> Dict[str, Any]:
    try:
        result = await mc.save_robot_map(
            robot_id,
            req.map_name,
            CommandSource(
                source_type=req.command_source.type,
                source_id=req.command_source.id,
                meta=req.command_source.meta,
            ),
        )
        return {"ok": True, **result}
    except KeyError:
        raise HTTPException(status_code=404, detail="robot not found")
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/robots/{robot_id}/maps/delete")
async def delete_robot_map(robot_id: str, req: RobotMapDeleteRequest) -> Dict[str, Any]:
    try:
        result = await mc.delete_robot_map(
            robot_id,
            req.map_name,
            CommandSource(
                source_type=req.command_source.type,
                source_id=req.command_source.id,
                meta=req.command_source.meta,
            ),
        )
        return {"ok": True, **result}
    except KeyError:
        raise HTTPException(status_code=404, detail="robot not found")
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.get("/robots/{robot_id}/maps/{map_name}/preview")
async def robot_map_preview(robot_id: str, map_name: str) -> Dict[str, Any]:
    try:
        preview_map = await mc.load_robot_map_preview(robot_id, map_name)
        return {"ok": True, "map": preview_map}
    except KeyError:
        raise HTTPException(status_code=404, detail="robot not found")
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/destinations/temp")
def upsert_temp_destination(req: TempDestinationRequest) -> Dict[str, Any]:
    destination = dest_config.upsert_destination(
        "Temp Destination",
        {"x": req.x, "y": req.y, "yaw": req.yaw},
        notes=req.notes or "Updated from the live map panel.",
    )
    return {"ok": True, "destination": destination.__dict__, "home": dest_config.home()}


@app.post("/destinations")
def upsert_destination(req: DestinationSaveRequest) -> Dict[str, Any]:
    destination = dest_config.upsert_destination(
        req.name.strip(),
        {"x": req.x, "y": req.y, "yaw": req.yaw},
        notes=req.notes,
    )
    return {"ok": True, "destination": destination.__dict__, "home": dest_config.home()}


@app.get("/robots/{robot_id}/operator-panel")
def robot_operator_panel(robot_id: str, include_map: bool = True) -> Dict[str, Any]:
    try:
        return mc.robot_operator_snapshot(robot_id, include_map=include_map)
    except KeyError:
        raise HTTPException(status_code=404, detail="robot not found")


@app.post("/robots/{robot_id}/telemetry")
def ingest_robot_telemetry(robot_id: str, tel: RobotTelemetryIn) -> Dict[str, Any]:
    mc.ingest_robot_telemetry(robot_id, tel.model_dump(exclude_none=True))
    return {"ok": True}


# Convenience endpoint for PoC: add another simulated robot without restarting.
@app.post("/robots/{robot_id}/sim/add")
def add_sim_robot(robot_id: str) -> Dict[str, Any]:
    from mission_control.robot_adapter import SimRobotAdapter

    if storage.get_robot(robot_id):
        raise HTTPException(status_code=400, detail="robot already exists")
    mc.register_robot(SimRobotAdapter(robot_id, speed_scale=1.0))
    return {"ok": True, "robot_id": robot_id}
