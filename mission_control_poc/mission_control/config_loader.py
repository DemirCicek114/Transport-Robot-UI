from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from .types import Destination


@dataclass
class DestinationConfig:
    """Loads destinations from YAML (config-driven destinations: S3.2.10)."""
    config_path: Path

    _destinations: Dict[str, Destination] | None = None
    _home: Optional[str] = None

    def load(self) -> Tuple[Dict[str, Destination], Optional[str]]:
        raw = yaml.safe_load(self.config_path.read_text())
        dests: Dict[str, Destination] = {}
        for d in raw.get("destinations", []):
            name = str(d["name"])
            pose = dict(d.get("pose", {}))
            notes = str(d.get("notes", "")) if d.get("notes") is not None else ""
            dests[name] = Destination(name=name, pose=pose, notes=notes)

        home = raw.get("home_destination")
        self._destinations = dests
        self._home = str(home) if home is not None else None
        return dests, self._home

    def get(self) -> Tuple[Dict[str, Destination], Optional[str]]:
        if self._destinations is None:
            return self.load()
        return self._destinations, self._home

    def validate(self, destination_name: str) -> bool:
        dests, _ = self.get()
        return destination_name in dests

    def list(self) -> List[Destination]:
        dests, _ = self.get()
        return list(dests.values())

    def home(self) -> Optional[str]:
        _, home = self.get()
        return home

    def upsert_destination(self, name: str, pose: Dict[str, float], notes: str = "") -> Destination:
        raw = yaml.safe_load(self.config_path.read_text()) or {}
        destinations = list(raw.get("destinations", []))

        normalized_pose = {
            "x": float(pose.get("x", 0.0)),
            "y": float(pose.get("y", 0.0)),
            "yaw": float(pose.get("yaw", 0.0)),
        }

        updated = False
        for index, destination in enumerate(destinations):
            if str(destination.get("name")) != name:
                continue
            destinations[index] = {
                "name": name,
                "pose": normalized_pose,
                "notes": notes,
            }
            updated = True
            break

        if not updated:
            destinations.append(
                {
                    "name": name,
                    "pose": normalized_pose,
                    "notes": notes,
                }
            )

        raw["destinations"] = destinations
        self.config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        self.load()
        return self.get()[0][name]
