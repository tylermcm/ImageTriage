from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields
from enum import Enum

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtGui import QGuiApplication





class AppearanceMode(str, Enum):
    SLATE = "slate"
    INDIGO = "indigo"
    DARK = "dark"
    MIDNIGHT = "midnight"
    GRAPHITE = "graphite"
    FOREST = "forest"
    HIGH_CONTRAST = "high_contrast"
    WARM_NEUTRAL = "warm_neutral"
    LIGHT = "light"
    AUTO = "auto"


@dataclass(frozen=True, slots=True)
class ColorToken:
    red: int
    green: int
    blue: int
    alpha: int = 255

    @property
    def css(self) -> str:
        if self.alpha >= 255:
            return f"rgb({self.red}, {self.green}, {self.blue})"
        return f"rgba({self.red}, {self.green}, {self.blue}, {self.alpha})"

    def qcolor(self) -> QColor:
        return QColor(self.red, self.green, self.blue, self.alpha)

    def with_alpha(self, alpha: int) -> "ColorToken":
        return ColorToken(self.red, self.green, self.blue, alpha=max(0, min(255, alpha)))


@dataclass(frozen=True, slots=True)
class WorkspaceMetrics:
    space_4: int = 4
    space_6: int = 6
    space_8: int = 8
    space_12: int = 12
    space_16: int = 16
    radius_4: int = 4
    radius_7: int = 7


WORKSPACE_METRICS = WorkspaceMetrics()


@dataclass(frozen=True, slots=True)
class ThemePalette:
    name: str
    is_dark: bool
    window_bg: ColorToken
    chrome_bg: ColorToken
    toolbar_bg: ColorToken
    panel_bg: ColorToken
    panel_alt_bg: ColorToken
    raised_bg: ColorToken
    input_bg: ColorToken
    input_hover_bg: ColorToken
    border: ColorToken
    border_muted: ColorToken
    text_primary: ColorToken
    text_secondary: ColorToken
    text_muted: ColorToken
    text_disabled: ColorToken
    accent: ColorToken
    accent_hover: ColorToken
    accent_soft: ColorToken
    selection_fill: ColorToken
    selection_outline: ColorToken
    success: ColorToken
    success_soft: ColorToken
    warning: ColorToken
    warning_soft: ColorToken
    danger: ColorToken
    danger_soft: ColorToken
    image_bg: ColorToken
    badge_bg: ColorToken
    badge_text: ColorToken
    # Optional window backdrop: two soft glows (top-left, bottom-right) painted
    # behind see-through chrome. Themes without them paint window_bg flat.
    backdrop_glow_primary: ColorToken | None = None
    backdrop_glow_secondary: ColorToken | None = None
    # Drive capacity meters run from start to end; themes without them use
    # the accent throughout.
    meter_start: ColorToken | None = None
    meter_end: ColorToken | None = None


def _slate_theme() -> ThemePalette:
    """The design's own palette: flat neutral surfaces under a blue accent.

    Each surface is the exact value the layout was drawn with, so no glow sits
    behind the chrome; leaving the backdrop glows unset keeps every panel
    opaque instead of washing it with white (see ``_backdrop_rules``).
    """
    return ThemePalette(
        name="slate",
        is_dark=True,
        window_bg=ColorToken(14, 15, 17),       # #0e0f11 grid background
        chrome_bg=ColorToken(20, 21, 23),       # #141517 rail, menu bar, status bar
        toolbar_bg=ColorToken(23, 24, 26),      # #17181a floating button bar
        panel_bg=ColorToken(24, 25, 28),        # #18191c drives/folders and inspector
        panel_alt_bg=ColorToken(22, 23, 25),    # #161719 settings bar
        raised_bg=ColorToken(32, 34, 38),       # #202226 search bar
        input_bg=ColorToken(20, 21, 23),
        input_hover_bg=ColorToken(38, 40, 44),
        border=ColorToken(38, 40, 44),
        border_muted=ColorToken(30, 32, 36),
        text_primary=ColorToken(231, 233, 236),
        text_secondary=ColorToken(196, 201, 208),
        text_muted=ColorToken(138, 144, 154),
        text_disabled=ColorToken(93, 99, 108),
        accent=ColorToken(76, 141, 255),
        accent_hover=ColorToken(111, 164, 255),
        accent_soft=ColorToken(76, 141, 255, 46),
        selection_fill=ColorToken(28, 40, 60),  # #1c283c rail button highlight
        selection_outline=ColorToken(120, 170, 255),
        success=ColorToken(224, 86, 122),
        success_soft=ColorToken(92, 34, 56, 222),
        warning=ColorToken(232, 193, 90),
        warning_soft=ColorToken(88, 68, 24, 222),
        danger=ColorToken(217, 83, 79),
        danger_soft=ColorToken(96, 34, 38, 222),
        image_bg=ColorToken(14, 15, 17),
        badge_bg=ColorToken(12, 13, 20, 224),
        badge_text=ColorToken(244, 246, 250),
        meter_start=ColorToken(76, 141, 255),
        meter_end=ColorToken(79, 209, 197),
    )


def _indigo_theme() -> ThemePalette:
    return ThemePalette(
        name="indigo",
        is_dark=True,
        # Near-black navy under a violet glow (top left) and a teal glow
        # (bottom right); chrome surfaces are faint white washes over it.
        window_bg=ColorToken(9, 10, 15),
        chrome_bg=ColorToken(15, 16, 22),
        toolbar_bg=ColorToken(16, 17, 23),
        panel_bg=ColorToken(21, 22, 30),
        panel_alt_bg=ColorToken(18, 19, 26),
        raised_bg=ColorToken(30, 31, 42),
        input_bg=ColorToken(15, 16, 22),
        input_hover_bg=ColorToken(34, 35, 48),
        border=ColorToken(38, 40, 52),
        border_muted=ColorToken(28, 30, 40),
        text_primary=ColorToken(236, 238, 244),
        text_secondary=ColorToken(190, 194, 206),
        text_muted=ColorToken(141, 147, 163),
        text_disabled=ColorToken(85, 91, 106),
        accent=ColorToken(76, 141, 255),
        accent_hover=ColorToken(111, 164, 255),
        accent_soft=ColorToken(76, 141, 255, 46),
        selection_fill=ColorToken(76, 141, 255, 46),
        selection_outline=ColorToken(120, 170, 255),
        success=ColorToken(255, 107, 154),
        success_soft=ColorToken(92, 34, 56, 222),
        warning=ColorToken(230, 180, 80),
        warning_soft=ColorToken(88, 68, 24, 222),
        danger=ColorToken(255, 107, 94),
        danger_soft=ColorToken(96, 34, 38, 222),
        image_bg=ColorToken(11, 12, 18),
        badge_bg=ColorToken(12, 13, 20, 224),
        badge_text=ColorToken(244, 246, 250),
        backdrop_glow_primary=ColorToken(29, 26, 58),
        backdrop_glow_secondary=ColorToken(15, 42, 51),
        meter_start=ColorToken(76, 141, 255),
        meter_end=ColorToken(79, 209, 197),
    )


def _dark_theme() -> ThemePalette:
    return ThemePalette(
        name="dark",
        is_dark=True,
        # Tuned to the UI-prototype colour spec (see ui/prototype_style.py).
        window_bg=ColorToken(7, 7, 7),          # #070707 viewport / outer
        chrome_bg=ColorToken(13, 13, 13),       # #0d0d0d rail / darkest chrome
        toolbar_bg=ColorToken(20, 20, 21),      # #141415 top bar
        panel_bg=ColorToken(22, 21, 22),        # #161516 directory panel
        panel_alt_bg=ColorToken(21, 21, 21),    # #151515 floating cards
        raised_bg=ColorToken(32, 32, 31),       # #20201f buttons
        input_bg=ColorToken(17, 17, 17),
        input_hover_bg=ColorToken(24, 24, 24),  # #181818 rail hover
        border=ColorToken(37, 38, 40),          # #252628 dividers
        border_muted=ColorToken(30, 30, 31),
        text_primary=ColorToken(242, 245, 247),
        text_secondary=ColorToken(187, 195, 204),
        text_muted=ColorToken(111, 120, 131),
        text_disabled=ColorToken(80, 88, 98),
        accent=ColorToken(25, 195, 125),
        accent_hover=ColorToken(60, 219, 150),
        accent_soft=ColorToken(25, 195, 125, 44),
        selection_fill=ColorToken(25, 195, 125, 38),
        selection_outline=ColorToken(46, 213, 142),
        success=ColorToken(35, 210, 122),
        success_soft=ColorToken(23, 88, 54, 218),
        warning=ColorToken(238, 167, 38),
        warning_soft=ColorToken(96, 66, 15, 218),
        danger=ColorToken(239, 78, 78),
        danger_soft=ColorToken(96, 28, 34, 218),
        image_bg=ColorToken(7, 7, 7),
        badge_bg=ColorToken(8, 10, 13, 224),
        badge_text=ColorToken(246, 248, 250),
    )


def _midnight_theme() -> ThemePalette:
    return ThemePalette(
        name="midnight",
        is_dark=True,
        # Restored from the older blue-black profile: cool panels, blue
        # selection, and a neutral image well so photo colour stays honest.
        window_bg=ColorToken(9, 11, 15),
        chrome_bg=ColorToken(12, 14, 18),
        toolbar_bg=ColorToken(22, 25, 31),
        panel_bg=ColorToken(31, 35, 42),
        panel_alt_bg=ColorToken(25, 29, 35),
        raised_bg=ColorToken(36, 41, 49),
        input_bg=ColorToken(17, 20, 25),
        input_hover_bg=ColorToken(43, 49, 59),
        border=ColorToken(55, 63, 75),
        border_muted=ColorToken(39, 45, 54),
        text_primary=ColorToken(236, 242, 249),
        text_secondary=ColorToken(180, 192, 207),
        text_muted=ColorToken(122, 137, 157),
        text_disabled=ColorToken(83, 96, 115),
        accent=ColorToken(82, 133, 218),
        accent_hover=ColorToken(112, 161, 241),
        accent_soft=ColorToken(82, 133, 218, 54),
        selection_fill=ColorToken(82, 133, 218, 58),
        selection_outline=ColorToken(111, 161, 238),
        success=ColorToken(72, 191, 127),
        success_soft=ColorToken(31, 82, 59, 222),
        warning=ColorToken(220, 173, 77),
        warning_soft=ColorToken(88, 68, 24, 222),
        danger=ColorToken(239, 105, 111),
        danger_soft=ColorToken(94, 39, 47, 222),
        image_bg=ColorToken(8, 10, 13),
        badge_bg=ColorToken(11, 15, 21, 224),
        badge_text=ColorToken(244, 248, 252),
    )


def _graphite_theme() -> ThemePalette:
    return ThemePalette(
        name="graphite",
        is_dark=True,
        window_bg=ColorToken(14, 14, 14),
        chrome_bg=ColorToken(19, 19, 19),
        toolbar_bg=ColorToken(30, 31, 32),
        panel_bg=ColorToken(36, 37, 39),
        panel_alt_bg=ColorToken(29, 30, 32),
        raised_bg=ColorToken(49, 50, 52),
        input_bg=ColorToken(22, 23, 24),
        input_hover_bg=ColorToken(57, 58, 61),
        border=ColorToken(72, 74, 77),
        border_muted=ColorToken(50, 52, 55),
        text_primary=ColorToken(240, 241, 242),
        text_secondary=ColorToken(190, 194, 198),
        text_muted=ColorToken(128, 134, 140),
        text_disabled=ColorToken(89, 94, 100),
        accent=ColorToken(118, 165, 255),
        accent_hover=ColorToken(146, 184, 255),
        accent_soft=ColorToken(118, 165, 255, 48),
        selection_fill=ColorToken(118, 165, 255, 48),
        selection_outline=ColorToken(146, 184, 255),
        success=ColorToken(77, 190, 124),
        success_soft=ColorToken(35, 82, 56, 222),
        warning=ColorToken(219, 169, 71),
        warning_soft=ColorToken(88, 68, 22, 222),
        danger=ColorToken(237, 100, 105),
        danger_soft=ColorToken(91, 37, 43, 222),
        image_bg=ColorToken(12, 12, 12),
        badge_bg=ColorToken(17, 18, 20, 224),
        badge_text=ColorToken(245, 246, 247),
    )


def _forest_theme() -> ThemePalette:
    return ThemePalette(
        name="forest",
        is_dark=True,
        window_bg=ColorToken(8, 14, 12),
        chrome_bg=ColorToken(12, 19, 16),
        toolbar_bg=ColorToken(20, 29, 25),
        panel_bg=ColorToken(27, 38, 33),
        panel_alt_bg=ColorToken(21, 31, 27),
        raised_bg=ColorToken(38, 53, 46),
        input_bg=ColorToken(14, 22, 19),
        input_hover_bg=ColorToken(44, 61, 53),
        border=ColorToken(55, 76, 66),
        border_muted=ColorToken(38, 54, 47),
        text_primary=ColorToken(235, 243, 238),
        text_secondary=ColorToken(184, 199, 191),
        text_muted=ColorToken(121, 143, 132),
        text_disabled=ColorToken(83, 101, 92),
        accent=ColorToken(61, 178, 126),
        accent_hover=ColorToken(88, 205, 151),
        accent_soft=ColorToken(61, 178, 126, 48),
        selection_fill=ColorToken(61, 178, 126, 48),
        selection_outline=ColorToken(90, 204, 153),
        success=ColorToken(81, 203, 130),
        success_soft=ColorToken(31, 84, 57, 222),
        warning=ColorToken(219, 176, 82),
        warning_soft=ColorToken(88, 70, 24, 222),
        danger=ColorToken(235, 99, 105),
        danger_soft=ColorToken(91, 37, 43, 222),
        image_bg=ColorToken(7, 10, 9),
        badge_bg=ColorToken(9, 16, 13, 226),
        badge_text=ColorToken(243, 248, 245),
    )


def _high_contrast_theme() -> ThemePalette:
    return ThemePalette(
        name="high_contrast",
        is_dark=True,
        window_bg=ColorToken(0, 0, 0),
        chrome_bg=ColorToken(0, 0, 0),
        toolbar_bg=ColorToken(12, 12, 12),
        panel_bg=ColorToken(18, 18, 18),
        panel_alt_bg=ColorToken(10, 10, 10),
        raised_bg=ColorToken(32, 32, 32),
        input_bg=ColorToken(6, 6, 6),
        input_hover_bg=ColorToken(42, 42, 42),
        border=ColorToken(130, 130, 130),
        border_muted=ColorToken(76, 76, 76),
        text_primary=ColorToken(255, 255, 255),
        text_secondary=ColorToken(224, 224, 224),
        text_muted=ColorToken(172, 172, 172),
        text_disabled=ColorToken(116, 116, 116),
        accent=ColorToken(255, 204, 0),
        accent_hover=ColorToken(255, 221, 74),
        accent_soft=ColorToken(255, 204, 0, 58),
        selection_fill=ColorToken(255, 204, 0, 64),
        selection_outline=ColorToken(255, 222, 76),
        success=ColorToken(74, 255, 146),
        success_soft=ColorToken(19, 84, 48, 230),
        warning=ColorToken(255, 204, 0),
        warning_soft=ColorToken(92, 72, 0, 230),
        danger=ColorToken(255, 92, 92),
        danger_soft=ColorToken(96, 22, 28, 230),
        image_bg=ColorToken(0, 0, 0),
        badge_bg=ColorToken(0, 0, 0, 238),
        badge_text=ColorToken(255, 255, 255),
    )


def _warm_neutral_theme() -> ThemePalette:
    return ThemePalette(
        name="warm_neutral",
        is_dark=False,
        window_bg=ColorToken(236, 233, 228),
        chrome_bg=ColorToken(247, 245, 241),
        toolbar_bg=ColorToken(250, 248, 244),
        panel_bg=ColorToken(255, 253, 249),
        panel_alt_bg=ColorToken(242, 239, 233),
        raised_bg=ColorToken(233, 229, 221),
        input_bg=ColorToken(249, 247, 243),
        input_hover_bg=ColorToken(239, 235, 228),
        border=ColorToken(202, 194, 182),
        border_muted=ColorToken(221, 215, 205),
        text_primary=ColorToken(39, 35, 31),
        text_secondary=ColorToken(82, 75, 67),
        text_muted=ColorToken(121, 111, 101),
        text_disabled=ColorToken(158, 149, 138),
        accent=ColorToken(46, 107, 178),
        accent_hover=ColorToken(61, 124, 197),
        accent_soft=ColorToken(46, 107, 178, 30),
        selection_fill=ColorToken(46, 107, 178, 34),
        selection_outline=ColorToken(69, 119, 190),
        success=ColorToken(41, 142, 84),
        success_soft=ColorToken(218, 242, 226),
        warning=ColorToken(159, 111, 30),
        warning_soft=ColorToken(249, 236, 210),
        danger=ColorToken(191, 69, 69),
        danger_soft=ColorToken(250, 226, 224),
        image_bg=ColorToken(226, 224, 220),
        badge_bg=ColorToken(246, 243, 238, 238),
        badge_text=ColorToken(43, 38, 34),
    )


def _light_theme() -> ThemePalette:
    return ThemePalette(
        name="light",
        is_dark=False,
        window_bg=ColorToken(237, 242, 248),
        chrome_bg=ColorToken(248, 250, 253),
        toolbar_bg=ColorToken(251, 253, 255),
        panel_bg=ColorToken(255, 255, 255),
        panel_alt_bg=ColorToken(244, 248, 252),
        raised_bg=ColorToken(236, 242, 248),
        input_bg=ColorToken(249, 251, 253),
        input_hover_bg=ColorToken(240, 245, 251),
        border=ColorToken(201, 212, 226),
        border_muted=ColorToken(220, 228, 238),
        text_primary=ColorToken(22, 34, 48),
        text_secondary=ColorToken(70, 86, 106),
        text_muted=ColorToken(109, 125, 145),
        text_disabled=ColorToken(148, 162, 178),
        accent=ColorToken(45, 108, 223),
        accent_hover=ColorToken(59, 122, 235),
        accent_soft=ColorToken(45, 108, 223, 28),
        selection_fill=ColorToken(45, 108, 223, 32),
        selection_outline=ColorToken(69, 118, 210),
        success=ColorToken(34, 148, 84),
        success_soft=ColorToken(219, 244, 228),
        warning=ColorToken(168, 118, 20),
        warning_soft=ColorToken(250, 239, 210),
        danger=ColorToken(198, 73, 73),
        danger_soft=ColorToken(252, 228, 228),
        image_bg=ColorToken(228, 235, 243),
        badge_bg=ColorToken(232, 238, 245, 235),
        badge_text=ColorToken(36, 49, 65),
    )


def parse_appearance_mode(raw: str | AppearanceMode | None) -> AppearanceMode:
    if isinstance(raw, AppearanceMode):
        return raw
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        for mode in AppearanceMode:
            if normalized == mode.value:
                return mode
    return AppearanceMode.AUTO


def appearance_profile_modes(*, include_auto: bool = True) -> tuple[AppearanceMode, ...]:
    modes = (
        AppearanceMode.SLATE,
        AppearanceMode.INDIGO,
        AppearanceMode.DARK,
        AppearanceMode.MIDNIGHT,
        AppearanceMode.GRAPHITE,
        AppearanceMode.FOREST,
        AppearanceMode.HIGH_CONTRAST,
        AppearanceMode.WARM_NEUTRAL,
        AppearanceMode.LIGHT,
    )
    if include_auto:
        return (*modes, AppearanceMode.AUTO)
    return modes


def appearance_mode_label(mode: AppearanceMode) -> str:
    labels = {
        AppearanceMode.SLATE: "Slate",
        AppearanceMode.INDIGO: "Indigo",
        AppearanceMode.DARK: "Dark",
        AppearanceMode.MIDNIGHT: "Midnight",
        AppearanceMode.GRAPHITE: "Graphite",
        AppearanceMode.FOREST: "Forest",
        AppearanceMode.HIGH_CONTRAST: "High Contrast",
        AppearanceMode.WARM_NEUTRAL: "Warm Neutral",
        AppearanceMode.LIGHT: "Light",
        AppearanceMode.AUTO: "Auto",
    }
    return labels.get(mode, str(mode.value).replace("_", " ").title())


def _system_prefers_dark(app: QGuiApplication) -> bool:
    style_hints = app.styleHints()
    color_scheme = getattr(style_hints, "colorScheme", None)
    if callable(color_scheme):
        scheme = color_scheme()
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False
    window_color = app.palette().color(QPalette.ColorRole.Window)
    return window_color.lightness() < 128


def resolve_theme(mode: AppearanceMode, app: QGuiApplication) -> ThemePalette:
    if mode == AppearanceMode.SLATE:
        return _slate_theme()
    if mode == AppearanceMode.INDIGO:
        return _indigo_theme()
    if mode == AppearanceMode.DARK:
        return _dark_theme()
    if mode == AppearanceMode.MIDNIGHT:
        return _midnight_theme()
    if mode == AppearanceMode.GRAPHITE:
        return _graphite_theme()
    if mode == AppearanceMode.FOREST:
        return _forest_theme()
    if mode == AppearanceMode.HIGH_CONTRAST:
        return _high_contrast_theme()
    if mode == AppearanceMode.WARM_NEUTRAL:
        return _warm_neutral_theme()
    if mode == AppearanceMode.LIGHT:
        return _light_theme()
    return _dark_theme() if _system_prefers_dark(app) else _light_theme()


def default_theme() -> ThemePalette:
    return _slate_theme()


def contrast_ratio(foreground: ColorToken, background: ColorToken) -> float:
    def relative_luminance(color: ColorToken) -> float:
        channels = (color.red / 255.0, color.green / 255.0, color.blue / 255.0)
        linear = tuple(
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        )
        return (0.2126 * linear[0]) + (0.7152 * linear[1]) + (0.0722 * linear[2])

    foreground_luminance = relative_luminance(foreground)
    background_luminance = relative_luminance(background)
    lighter = max(foreground_luminance, background_luminance)
    darker = min(foreground_luminance, background_luminance)
    return (lighter + 0.05) / (darker + 0.05)


UI_GAMMA_MIN = 0.60
UI_GAMMA_MAX = 1.60


def normalize_ui_gamma(value: object) -> float:
    """Clamp a stored/user gamma value to the supported range (1.0 = off)."""
    try:
        gamma = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1.0
    if gamma != gamma:  # NaN
        return 1.0
    return round(max(UI_GAMMA_MIN, min(UI_GAMMA_MAX, gamma)), 2)


def apply_gamma(theme: ThemePalette, gamma: float) -> ThemePalette:
    """Gamma-correct every palette color so the UI can be brightened or
    darkened uniformly to compensate for monitor differences.

    Values above 1.0 lift the dark tones (brighter UI), below 1.0 deepen
    them. 1.0 returns the theme untouched. Alpha is preserved.
    """
    gamma = normalize_ui_gamma(gamma)
    if abs(gamma - 1.0) < 1e-3:
        return theme
    exponent = 1.0 / gamma
    lut = [round(255.0 * ((i / 255.0) ** exponent)) for i in range(256)]

    def _map(token: ColorToken) -> ColorToken:
        return ColorToken(lut[token.red], lut[token.green], lut[token.blue], token.alpha)

    values = {}
    for field in fields(ThemePalette):
        current = getattr(theme, field.name)
        values[field.name] = _map(current) if isinstance(current, ColorToken) else current
    return ThemePalette(**values)
