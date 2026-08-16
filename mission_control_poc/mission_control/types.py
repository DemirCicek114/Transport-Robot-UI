from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class MissionState(str, Enum):
    """Action state control for pending requests, movement, round-trip waits, and history."""
    REQUESTED = "Requested"
    IDLE = "Idle"
    EN_ROUTE = "En-route"
    WAITING_FOR_RETURN = "WaitingForReturn"
    RETURNING = "Returning"
    PAUSED = "Paused"
    COMPLETED = "Completed"


class MissionOutcome(str, Enum):
    NONE = "None"
    SUCCESS = "Success"
    CANCELED = "Canceled"
    ABORTED = "Aborted"
    FAILED = "Failed"


class RobotMode(str, Enum):
    AUTO = "Auto"
    MANUAL_OVERRIDE = "ManualOverride"


@dataclass(frozen=True)
class Destination:
    name: str
    pose: Dict[str, float]
    notes: str = ""


@dataclass(frozen=True)
class CommandSource:
    """Who/what issued a command (S3.2.12)."""
    source_type: str  # e.g. "user", "operator", "system"
    source_id: str    # e.g. username/device id
    meta: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"type": self.source_type, "id": self.source_id}
        if self.meta:
            d["meta"] = self.meta
        return d
