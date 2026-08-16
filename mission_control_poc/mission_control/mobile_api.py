from __future__ import annotations

import math
from typing import Any, Dict, Optional


def map_occupancy_at_world(
    map_data: Dict[str, Any],
    x: float,
    y: float,
) -> Optional[int]:
    """Return the display-map occupancy at a map-frame point."""

    try:
        resolution = float(map_data["resolution"])
        width = int(map_data["width"])
        height = int(map_data["height"])
        origin = map_data["origin"]
        origin_x = float(origin["x"])
        origin_y = float(origin["y"])
        point_x = float(x)
        point_y = float(y)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None

    if (
        not all(
            math.isfinite(value)
            for value in (resolution, origin_x, origin_y, point_x, point_y)
        )
        or resolution <= 0.0
        or width <= 0
        or height <= 0
    ):
        return None

    grid_x = math.floor((point_x - origin_x) / resolution)
    grid_y = math.floor((point_y - origin_y) / resolution)
    if grid_x < 0 or grid_x >= width or grid_y < 0 or grid_y >= height:
        return None

    try:
        occupancy = int(map_data["data"][(grid_y * width) + grid_x])
    except (IndexError, KeyError, TypeError, ValueError, OverflowError):
        return None
    return occupancy


def is_open_display_map_point(map_data: Dict[str, Any], x: float, y: float) -> bool:
    """Match the dashboard rule: only pure-white/free display cells are valid."""

    return map_occupancy_at_world(map_data, x, y) == 0
