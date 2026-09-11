from __future__ import annotations

"""Logical-size profiles used to keep the desktop shell readable.

Qt already converts physical pixels and operating-system DPI settings into
device-independent coordinates.  This module therefore deliberately accepts
the *logical* size of the application window, rather than a monitor's raw
resolution.  The profile changes a small set of shell metrics; individual
content widgets remain responsible for their own wrapping and overflow.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DisplayProfile:
    """A bounded shell-density profile for one logical window size."""

    name: str
    scale: float
    shell_margin: int
    shell_spacing: int
    library_width: int
    library_min_width: int
    library_max_width: int
    inspector_width: int
    inspector_min_width: int
    inspector_max_width: int
    topbar_button_height: int
    topbar_hover_margin: int
    topbar_slot_cell_min: int
    topbar_slot_button_width: int
    topbar_slot_spacing: int
    topbar_glyph_size: int
    topbar_caption_height: int
    topbar_nav_button_size: int
    topbar_nav_font_size: int
    topbar_search_min_width: int
    topbar_search_max_width: int
    topbar_zoom_width: int
    topbar_path_min_width: int
    topbar_path_max_width: int
    left_rail_width: int
    left_rail_button_size: int
    left_rail_icon_size: int
    left_rail_add_icon_size: int
    inspector_label_width: int
    inspector_section_header_height: int
    inspector_header_button_width: int
    inspector_header_button_height: int
    inspector_spacing: int
    inspector_histogram_min_height: int
    inspector_histogram_max_height: int
    settings_min_width: int
    settings_min_height: int
    settings_width: int
    settings_height: int
    settings_nav_width: int
    settings_pages_min_width: int
    settings_page_min_width: int
    settings_page_margin_x: int
    settings_page_margin_y: int
    settings_row_margin_x: int
    settings_row_margin_y: int
    settings_row_spacing: int
    settings_row_label_width: int
    settings_shortcut_label_width: int
    settings_shortcut_reset_width: int
    editor_content_width: int
    editor_tool_rail_width: int
    editor_tool_button_width: int
    editor_tool_button_height: int
    editor_tool_icon_size: int

    def panel_widths(self, key: str) -> tuple[int, int, int]:
        if key == "library":
            return self.library_width, self.library_min_width, self.library_max_width
        if key == "inspector":
            return self.inspector_width, self.inspector_min_width, self.inspector_max_width
        raise KeyError(key)


def _scaled(value: int, scale: float, *, minimum: int = 0) -> int:
    return max(minimum, int(round(value * scale)))


def _profile(name: str, scale: float) -> DisplayProfile:
    return DisplayProfile(
        name=name,
        scale=scale,
        shell_margin=_scaled(8, scale, minimum=6),
        shell_spacing=_scaled(8, scale, minimum=6),
        library_width=_scaled(316, scale, minimum=276),
        library_min_width=_scaled(292, scale, minimum=264),
        library_max_width=_scaled(460, scale, minimum=400),
        inspector_width=_scaled(300, scale, minimum=276),
        inspector_min_width=_scaled(300, scale, minimum=276),
        inspector_max_width=_scaled(460, scale, minimum=400),
        topbar_button_height=_scaled(34, scale, minimum=32),
        topbar_hover_margin=_scaled(2, scale, minimum=2),
        topbar_slot_cell_min=_scaled(36, scale, minimum=34),
        topbar_slot_button_width=_scaled(34, scale, minimum=32),
        topbar_slot_spacing=_scaled(4, scale, minimum=3),
        topbar_glyph_size=_scaled(22, scale, minimum=20),
        topbar_caption_height=_scaled(12, scale, minimum=11),
        topbar_nav_button_size=_scaled(34, scale, minimum=32),
        topbar_nav_font_size=_scaled(16, scale, minimum=15),
        topbar_search_min_width=_scaled(250, scale, minimum=210),
        topbar_search_max_width=_scaled(450, scale, minimum=380),
        topbar_zoom_width=_scaled(118, scale, minimum=104),
        topbar_path_min_width=_scaled(220, scale, minimum=184),
        topbar_path_max_width=_scaled(460, scale, minimum=390),
        left_rail_width=_scaled(42, scale, minimum=40),
        left_rail_button_size=_scaled(30, scale, minimum=28),
        left_rail_icon_size=_scaled(20, scale, minimum=18),
        left_rail_add_icon_size=_scaled(22, scale, minimum=20),
        inspector_label_width=_scaled(96, scale, minimum=86),
        inspector_section_header_height=_scaled(24, scale, minimum=22),
        inspector_header_button_width=_scaled(24, scale, minimum=22),
        inspector_header_button_height=_scaled(22, scale, minimum=20),
        inspector_spacing=_scaled(6, scale, minimum=5),
        inspector_histogram_min_height=_scaled(54, scale, minimum=50),
        inspector_histogram_max_height=_scaled(70, scale, minimum=64),
        settings_min_width=_scaled(760, scale, minimum=700),
        settings_min_height=_scaled(520, scale, minimum=480),
        settings_width=_scaled(880, scale, minimum=800),
        settings_height=_scaled(620, scale, minimum=560),
        settings_nav_width=_scaled(196, scale, minimum=176),
        settings_pages_min_width=_scaled(560, scale, minimum=510),
        settings_page_min_width=_scaled(500, scale, minimum=454),
        settings_page_margin_x=_scaled(30, scale, minimum=24),
        settings_page_margin_y=_scaled(26, scale, minimum=22),
        settings_row_margin_x=_scaled(14, scale, minimum=12),
        settings_row_margin_y=_scaled(10, scale, minimum=8),
        settings_row_spacing=_scaled(14, scale, minimum=12),
        settings_row_label_width=_scaled(142, scale, minimum=128),
        settings_shortcut_label_width=_scaled(284, scale, minimum=250),
        settings_shortcut_reset_width=_scaled(64, scale, minimum=58),
        editor_content_width=_scaled(336, scale, minimum=304),
        editor_tool_rail_width=_scaled(46, scale, minimum=42),
        editor_tool_button_width=_scaled(36, scale, minimum=32),
        editor_tool_button_height=_scaled(34, scale, minimum=31),
        editor_tool_icon_size=_scaled(18, scale, minimum=17),
    )


COMPACT_DISPLAY = _profile("compact", 0.92)
STANDARD_DISPLAY = _profile("standard", 1.0)
SPACIOUS_DISPLAY = _profile("spacious", 1.08)


def display_profile_for_size(width: int, height: int) -> DisplayProfile:
    """Return a stable profile from a window's logical client dimensions.

    The thresholds intentionally describe usable application space rather than
    marketing labels such as 1080p, 2K, or 4K.  A 4K monitor configured at
    200% Windows scaling can therefore correctly land in the same profile as a
    1080p monitor at 100%.
    """

    width = max(0, int(width))
    height = max(0, int(height))
    if width < 1360 or height < 760:
        return COMPACT_DISPLAY
    if width >= 2400 and height >= 1300:
        return SPACIOUS_DISPLAY
    return STANDARD_DISPLAY


DISPLAY_PROFILE_NAMES = ("automatic", "compact", "standard", "spacious")


def normalize_display_profile_preference(value: object) -> str:
    normalized = str(value or "automatic").strip().casefold()
    aliases = {
        "auto": "automatic",
        "comfortable": "standard",
        "large": "spacious",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in DISPLAY_PROFILE_NAMES else "automatic"


def display_profile_for_preference(width: int, height: int, preference: object) -> DisplayProfile:
    """Resolve an automatic or user-selected logical UI density profile."""

    normalized = normalize_display_profile_preference(preference)
    if normalized == "compact":
        return COMPACT_DISPLAY
    if normalized == "standard":
        return STANDARD_DISPLAY
    if normalized == "spacious":
        return SPACIOUS_DISPLAY
    return display_profile_for_size(width, height)
