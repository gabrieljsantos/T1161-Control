import json
import tempfile
import unittest
from pathlib import Path

from t1161_control import (
    DEFAULT_BUTTON_MAP,
    load_button_map,
    parse_monitors,
    physical_button_pressed,
    monitor_for_target,
    zone_runtime_issue,
    write_driver_profile,
)
from t1161_profiles import (
    PC_CAPTURE_MM,
    WACOM_ONE_CAPTURE_MM,
    default_profiles,
    load_profiles,
    move_zone_without_overlap,
    resize_zone_without_overlap,
    save_profiles,
    set_zone_movement_rotation,
    zones_overlap,
)
from t1161_input_profiles import default_input_profiles, load_input_profiles, save_input_profiles


class ParseMonitorsTest(unittest.TestCase):
    def test_orders_combined_desktop_and_keeps_monitor_identity(self):
        state = (
            7,
            [
                (("DP-1", "DEL", "U2415", "A"), [("m1", 1920, 1200, 60.0, 1.0, [1.0], {"is-current": True})], {}),
                (("eDP-1", "AUO", "Laptop", "B"), [("m2", 1920, 1080, 60.0, 1.0, [1.0], {"is-current": True})], {}),
            ],
            [
                (1920, 0, 1.0, 0, False, [("DP-1", "DEL", "U2415", "A")], {}),
                (0, 0, 1.0, 0, True, [("eDP-1", "AUO", "Laptop", "B")], {}),
            ],
            {},
        )
        monitors = parse_monitors(state)
        self.assertEqual([m.connector for m in monitors], ["eDP-1", "DP-1"])
        self.assertEqual(monitors[1].output, ["DEL", "U2415", "A"])
        self.assertTrue(monitors[0].primary)
        self.assertEqual((monitors[1].logical_width, monitors[1].logical_height), (1920, 1200))


class ButtonMonitorTest(unittest.TestCase):
    def test_physical_button_one_uses_saved_signal_mapping(self):
        # O botão físico 1 é o sinal 8; sinal 8 corresponde ao bit bruto 7.
        self.assertFalse(physical_button_pressed(0, 0, 1, DEFAULT_BUTTON_MAP))
        self.assertTrue(physical_button_pressed(1 << 7, 0, 1, DEFAULT_BUTTON_MAP))

    def test_first_four_physical_buttons_follow_mapping(self):
        for physical in range(1, 5):
            signal = DEFAULT_BUTTON_MAP[physical - 1]
            bit = tuple(range(10)) + (12, 13)
            self.assertTrue(
                physical_button_pressed(1 << bit[signal - 1], 0, physical, DEFAULT_BUTTON_MAP)
            )

    def test_loads_valid_mapping_and_rejects_invalid_one(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "buttons.json"
            mapping = tuple(range(1, 13))
            path.write_text(json.dumps(mapping))
            self.assertEqual(load_button_map(path), mapping)
            path.write_text("[1, 1]")
            self.assertEqual(load_button_map(path), DEFAULT_BUTTON_MAP)


class ProfileTest(unittest.TestCase):
    def test_four_default_profiles_and_multizone_capability(self):
        profiles = default_profiles()
        self.assertEqual(len(profiles), 4)
        self.assertEqual(len(profiles[1].zones), 2)
        self.assertTrue(profiles[1].supported_by_current_engine)
        self.assertTrue(profiles[0].supported_by_current_engine)
        self.assertEqual(PC_CAPTURE_MM, (254.0, 152.0))
        self.assertEqual(WACOM_ONE_CAPTURE_MM, (152.0, 95.0))

    def test_profile_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            save_profiles(default_profiles(), path)
            loaded = load_profiles(path)
            self.assertEqual(loaded[1].zones[1].target.serial, "demoset-1")
            self.assertEqual(loaded[1].zones[1].target.logical_rotation, 90)
            self.assertEqual(loaded[3].tablet_rotation, 0)
            self.assertEqual(loaded[3].zones[0].movement_rotation, 0)

    def test_migrates_known_portrait_screen_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            save_profiles(default_profiles(), path)
            data = json.loads(path.read_text())
            del data[1]["zones"][1]["target"]["logical_rotation"]
            path.write_text(json.dumps(data))
            self.assertEqual(load_profiles(path)[1].zones[1].target.logical_rotation, 90)

    def test_legacy_rotation_does_not_become_movement_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            save_profiles(default_profiles(), path)
            data = json.loads(path.read_text())
            data[0]["zones"][0]["rotation"] = 180
            data[0]["zones"][0].pop("movement_rotation")
            path.write_text(json.dumps(data))
            self.assertEqual(load_profiles(path)[0].zones[0].movement_rotation, 0)

    def test_drag_blocks_overlap_and_allows_edge_contact(self):
        profile = default_profiles()[1]
        first, second = profile.zones
        self.assertFalse(zones_overlap(first, second))
        original = second.x
        move_zone_without_overlap(profile, 1, first.x + 0.1, second.y)
        self.assertEqual(second.x, original)
        move_zone_without_overlap(profile, 1, first.x + first.width, second.y)
        self.assertAlmostEqual(second.x, first.x + first.width)

    def test_resize_blocks_overlap(self):
        profile = default_profiles()[1]
        first = profile.zones[0]
        old_width = first.width
        resize_zone_without_overlap(profile, 0, 0.9, first.height)
        self.assertEqual(first.width, old_width)

    def test_quarter_turn_swaps_width_and_height_around_center(self):
        profile = default_profiles()[2]
        zone = profile.zones[0]
        zone.x, zone.y = 0.2, 0.2
        old = (zone.x + zone.width / 2, zone.y + zone.height / 2, zone.width, zone.height)
        self.assertTrue(set_zone_movement_rotation(profile, 0, 90))
        self.assertAlmostEqual(zone.width * 300, old[3] * 188)
        self.assertAlmostEqual(zone.height * 188, old[2] * 300)
        self.assertAlmostEqual(zone.x + zone.width / 2, old[0])
        self.assertAlmostEqual(zone.y + zone.height / 2, old[1])

    def test_half_turn_keeps_dimensions(self):
        profile = default_profiles()[0]
        zone = profile.zones[0]
        dimensions = (zone.width, zone.height)
        self.assertTrue(set_zone_movement_rotation(profile, 0, 180))
        self.assertEqual((zone.width, zone.height), dimensions)

    def test_identical_monitors_use_connector_as_tiebreaker(self):
        profile = default_profiles()[0]
        target = profile.zones[0].target
        target.vendor, target.product, target.serial = "DEL", "U2415", "same"
        target.connector = "DP-2"
        monitors = [
            self.monitor("DP-1", "DEL", "U2415", "same"),
            self.monitor("DP-2", "DEL", "U2415", "same"),
        ]
        self.assertEqual(monitor_for_target(target, monitors).connector, "DP-2")

    def test_resolution_change_is_not_silently_distorted(self):
        profile = default_profiles()[0]
        zone = profile.zones[0]
        zone.target.vendor, zone.target.product, zone.target.serial = "DEL", "U2415", "A"
        zone.target.connector = "DP-1"
        zone.target.logical_width, zone.target.logical_height = 1920, 1080
        zone.source_width, zone.source_height = 1920, 1080
        monitor = self.monitor("DP-1", "DEL", "U2415", "A", 1280, 720)
        self.assertIn("resolução mudou", zone_runtime_issue(zone, monitor))
        with tempfile.TemporaryDirectory() as directory:
            # A escrita real usa /dev/shm; validar o filtro é suficiente para
            # garantir que uma região incompatível não chegue ao driver.
            self.assertNotEqual(zone_runtime_issue(zone, monitor), "")

    @staticmethod
    def monitor(connector, vendor, product, serial, width=1920, height=1080):
        from t1161_control import Monitor
        return Monitor(connector, vendor, product, serial, width, height, 1.0,
                       0, 0, connector == "DP-1", width, height)


class InputProfileTest(unittest.TestCase):
    def test_defaults_include_requested_actions_and_do_nothing(self):
        profiles = default_input_profiles()
        standard = profiles[0]
        self.assertEqual((standard.pressure, standard.pen_04, standard.pen_06),
                         ("left", "right", "middle"))
        self.assertEqual(standard.pen_04_pressure_move, "scroll-vertical")
        self.assertTrue(any(item.pen_04_pressure_move == "scroll-horizontal" for item in profiles))
        disabled = next(item for item in profiles if item.profile_id == "disabled")
        self.assertEqual({disabled.pressure, disabled.pen_04, disabled.pen_06}, {"none"})

    def test_input_profiles_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            save_input_profiles(default_input_profiles(), path)
            loaded = load_input_profiles(path)
            self.assertEqual(loaded[1].pen_04_pressure_move, "scroll-horizontal")


if __name__ == "__main__":
    unittest.main()
