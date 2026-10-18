from __future__ import annotations

import unittest

from image_triage.ui.display_metrics import (
    COMPACT_DISPLAY,
    SPACIOUS_DISPLAY,
    STANDARD_DISPLAY,
    display_profile_for_preference,
    display_profile_for_size,
    normalize_display_profile_preference,
)


class DisplayMetricsTests(unittest.TestCase):
    def test_profiles_use_logical_window_size(self) -> None:
        self.assertIs(display_profile_for_size(1920, 1040), STANDARD_DISPLAY)
        self.assertIs(display_profile_for_size(2048, 1152), STANDARD_DISPLAY)
        self.assertIs(display_profile_for_size(3840, 2160), SPACIOUS_DISPLAY)

    def test_wider_toolbar_buttons_keep_every_buttons_position(self) -> None:
        # Buttons were widened by TOPBAR_BUTTON_EXTRA_PX and the space between them shrunk by the same amount, so
        # the distance from one button's left edge to the next (the pitch) is what it always was in each profile.
        was = {"compact": 40, "standard": 42, "spacious": 45}
        for profile in (COMPACT_DISPLAY, STANDARD_DISPLAY, SPACIOUS_DISPLAY):
            hover = profile.topbar_slot_button_width + 2 * profile.topbar_hover_margin
            pitch = max(profile.topbar_slot_cell_min, hover) + profile.topbar_slot_spacing
            self.assertEqual(was[profile.name], pitch, profile.name)
            self.assertGreaterEqual(profile.topbar_slot_spacing, 1)

    def test_compact_profile_requires_both_axes_to_be_usable(self) -> None:
        self.assertIs(display_profile_for_size(1359, 900), COMPACT_DISPLAY)
        self.assertIs(display_profile_for_size(1600, 759), COMPACT_DISPLAY)

    def test_profile_metrics_are_monotonic(self) -> None:
        self.assertLess(COMPACT_DISPLAY.library_width, STANDARD_DISPLAY.library_width)
        self.assertLess(STANDARD_DISPLAY.library_width, SPACIOUS_DISPLAY.library_width)
        self.assertLess(COMPACT_DISPLAY.inspector_width, STANDARD_DISPLAY.inspector_width)
        self.assertLess(STANDARD_DISPLAY.inspector_width, SPACIOUS_DISPLAY.inspector_width)
        self.assertLess(COMPACT_DISPLAY.topbar_slot_button_width, STANDARD_DISPLAY.topbar_slot_button_width)
        self.assertLess(STANDARD_DISPLAY.editor_content_width, SPACIOUS_DISPLAY.editor_content_width)
        self.assertLess(COMPACT_DISPLAY.settings_nav_width, STANDARD_DISPLAY.settings_nav_width)

    def test_explicit_preference_overrides_automatic_breakpoint(self) -> None:
        self.assertIs(display_profile_for_preference(3840, 2160, "compact"), COMPACT_DISPLAY)
        self.assertIs(display_profile_for_preference(1024, 700, "comfortable"), STANDARD_DISPLAY)
        self.assertIs(display_profile_for_preference(1024, 700, "large"), SPACIOUS_DISPLAY)
        self.assertEqual("automatic", normalize_display_profile_preference("unknown"))

    def test_unknown_panel_key_is_rejected(self) -> None:
        with self.assertRaises(KeyError):
            STANDARD_DISPLAY.panel_widths("center")


if __name__ == "__main__":
    unittest.main()
