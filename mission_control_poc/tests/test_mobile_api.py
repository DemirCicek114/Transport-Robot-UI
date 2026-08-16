from __future__ import annotations

import unittest

from mission_control.mobile_api import is_open_display_map_point, map_occupancy_at_world


class MobileMapPointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.map_data = {
            "width": 3,
            "height": 2,
            "resolution": 0.5,
            "origin": {"x": 10.0, "y": 20.0, "yaw": 0.0},
            # Bottom row: free, occupied, unknown. Top row: all free.
            "data": [0, 100, -1, 0, 0, 0],
        }

    def test_white_display_cell_is_accepted(self) -> None:
        self.assertEqual(map_occupancy_at_world(self.map_data, 10.1, 20.1), 0)
        self.assertTrue(is_open_display_map_point(self.map_data, 10.1, 20.1))

    def test_obstacle_unknown_and_outside_cells_are_rejected(self) -> None:
        self.assertFalse(is_open_display_map_point(self.map_data, 10.6, 20.1))
        self.assertFalse(is_open_display_map_point(self.map_data, 11.1, 20.1))
        self.assertFalse(is_open_display_map_point(self.map_data, 9.9, 20.1))
        self.assertFalse(is_open_display_map_point(self.map_data, 10.1, 21.1))

    def test_malformed_map_is_rejected(self) -> None:
        self.assertIsNone(map_occupancy_at_world({}, 0.0, 0.0))
        self.assertFalse(is_open_display_map_point({}, 0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
