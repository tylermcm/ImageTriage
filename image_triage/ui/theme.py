from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


class AppearanceMode(str, Enum):
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


def _system_prefers_dark(app: QApplication) -> bool:
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


def resolve_theme(mode: AppearanceMode, app: QApplication) -> ThemePalette:
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
    return _dark_theme()


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


def build_app_palette(theme: ThemePalette) -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, theme.window_bg.qcolor())
    palette.setColor(QPalette.ColorRole.WindowText, theme.text_primary.qcolor())
    palette.setColor(QPalette.ColorRole.Base, theme.input_bg.qcolor())
    palette.setColor(QPalette.ColorRole.AlternateBase, theme.panel_alt_bg.qcolor())
    palette.setColor(QPalette.ColorRole.ToolTipBase, theme.panel_bg.qcolor())
    palette.setColor(QPalette.ColorRole.ToolTipText, theme.text_primary.qcolor())
    palette.setColor(QPalette.ColorRole.Text, theme.text_primary.qcolor())
    palette.setColor(QPalette.ColorRole.Button, theme.toolbar_bg.qcolor())
    palette.setColor(QPalette.ColorRole.ButtonText, theme.text_primary.qcolor())
    palette.setColor(QPalette.ColorRole.BrightText, theme.text_primary.qcolor())
    palette.setColor(QPalette.ColorRole.Link, theme.accent.qcolor())
    palette.setColor(QPalette.ColorRole.Highlight, theme.selection_outline.qcolor())
    palette.setColor(QPalette.ColorRole.HighlightedText, theme.text_primary.qcolor())
    palette.setColor(QPalette.ColorRole.PlaceholderText, theme.text_muted.qcolor())
    palette.setColor(QPalette.ColorRole.Mid, theme.border.qcolor())
    palette.setColor(QPalette.ColorRole.Midlight, theme.border_muted.qcolor())
    palette.setColor(QPalette.ColorRole.Dark, theme.border_muted.qcolor())
    palette.setColor(QPalette.ColorRole.Light, theme.raised_bg.qcolor())
    return palette


def build_app_stylesheet(theme: ThemePalette) -> str:
    return f"""
        QMainWindow {{
            background-color: {theme.window_bg.css};
            color: {theme.text_primary.css};
        }}
        QWidget#centralContainer {{
            background-color: {theme.window_bg.css};
            color: {theme.text_primary.css};
        }}
        QMenuBar {{
            background-color: {theme.chrome_bg.css};
            border-bottom: 1px solid {theme.border.css};
            color: {theme.text_primary.css};
            padding: 1px 8px;
        }}
        QMenuBar::item {{
            background: transparent;
            border-radius: 6px;
            padding: 5px 10px;
            margin: 1px 2px;
        }}
        QMenuBar::item:selected {{
            background: {theme.accent_soft.css};
        }}
        QMenu {{
            background-color: {theme.panel_bg.css};
            border: 1px solid {theme.border.css};
            color: {theme.text_primary.css};
            padding: 6px;
        }}
        QMenu::item {{
            border-radius: 8px;
            padding: 6px 28px 6px 12px;
            margin: 1px 0;
        }}
        QMenu::item:selected {{
            background-color: {theme.selection_fill.css};
        }}
        QMenu::separator {{
            height: 1px;
            background: {theme.border_muted.css};
            margin: 6px 4px;
        }}
        QToolBar#primaryToolbar {{
            background-color: {theme.toolbar_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 8px;
            spacing: 2px;
            padding: 0px 4px;
        }}
        QToolBar#primaryToolbar[toolbarMerged="true"] {{
            background-color: transparent;
            border: none;
            border-radius: 0px;
            padding: 0px;
        }}
        QToolBar#primaryToolbar::separator {{
            width: 1px;
            background: {theme.border_muted.css};
            margin: 1px 5px;
        }}
        QToolBar#primaryToolbar QToolButton#primaryToolbarButton {{
            font-weight: 600;
            min-height: 22px;
            padding: 1px 8px;
        }}
        QToolButton {{
            background-color: transparent;
            border: 1px solid transparent;
            border-radius: 6px;
            color: {theme.text_primary.css};
            padding: 4px 8px;
        }}
        QToolButton:hover {{
            background-color: {theme.input_hover_bg.css};
            border-color: {theme.border.css};
        }}
        QToolButton:pressed {{
            background-color: {theme.raised_bg.css};
        }}
        QToolButton:checked {{
            background-color: {theme.accent_soft.css};
            border-color: {theme.accent.css};
            color: {theme.text_primary.css};
        }}
        QToolButton:disabled {{
            color: {theme.text_disabled.css};
        }}
        QToolButton::menu-indicator {{
            image: none;
        }}
        QPushButton {{
            background-color: {theme.raised_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 7px;
            color: {theme.text_primary.css};
            padding: 6px 12px;
        }}
        QPushButton:hover {{
            background-color: {theme.input_hover_bg.css};
        }}
        QPushButton:pressed {{
            background-color: {theme.input_bg.css};
        }}
        QPushButton:checked {{
            background-color: {theme.accent_soft.css};
            border-color: {theme.accent.css};
        }}
        QPushButton:disabled {{
            color: {theme.text_disabled.css};
            border-color: {theme.border_muted.css};
        }}
        QLineEdit {{
            background-color: {theme.input_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 7px;
            color: {theme.text_primary.css};
            min-height: 28px;
            padding: 2px 10px;
            selection-background-color: {theme.selection_fill.css};
        }}
        QLineEdit:hover {{
            background-color: {theme.input_hover_bg.css};
        }}
        QLineEdit:focus {{
            background-color: {theme.input_hover_bg.css};
            border-color: {theme.accent.css};
        }}
        QComboBox {{
            background-color: {theme.input_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 7px;
            color: {theme.text_primary.css};
            min-height: 28px;
            padding: 2px 10px;
        }}
        QComboBox:hover {{
            background-color: {theme.input_hover_bg.css};
        }}
        QComboBox::drop-down {{
            border: none;
            width: 22px;
        }}
        QComboBox QAbstractItemView {{
            background-color: {theme.panel_bg.css};
            border: 1px solid {theme.border.css};
            color: {theme.text_primary.css};
            outline: none;
            selection-background-color: {theme.selection_fill.css};
        }}
        QComboBox#settingsSessionCombo {{
            padding-left: 0px;
            padding-right: 10px;
        }}
        QLineEdit#settingsSessionLineEdit,
        QComboBox#settingsSessionCombo QLineEdit {{
            background-color: transparent;
            border: none;
            padding: 0px;
            margin: 0px;
            color: {theme.text_primary.css};
            selection-background-color: {theme.selection_fill.css};
        }}
        QTreeView, QListWidget {{
            background-color: {theme.panel_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 8px;
            color: {theme.text_primary.css};
            alternate-background-color: {theme.panel_alt_bg.css};
            outline: none;
        }}
        QWidget#workspaceCenterColumn {{
            background-color: transparent;
            border: none;
        }}
        QWidget#floatingPanelPalette {{
            background-color: {theme.panel_bg.css};
            border: 1px solid {theme.border.css};
        }}
        QWidget#floatingPanelCardHost {{
            background-color: transparent;
            border: none;
        }}
        QWidget#floatingPanelResizeRow {{
            background-color: {theme.panel_bg.css};
            border-top: 1px solid {theme.border_muted.css};
            max-height: 10px;
        }}
        QTabWidget#floatingPanelTabs::pane {{
            border: none;
            background-color: transparent;
        }}
        QTabWidget#floatingPanelTabs QTabBar::tab {{
            background-color: {theme.input_bg.css};
            border: 1px solid {theme.border_muted.css};
            border-bottom: none;
            color: {theme.text_muted.css};
            font-size: 11px;
            font-weight: 650;
            padding: 6px 12px;
        }}
        QTabWidget#floatingPanelTabs QTabBar::tab:selected {{
            background-color: {theme.panel_bg.css};
            color: {theme.text_primary.css};
            border-color: {theme.border.css};
        }}
        QTabWidget#floatingPanelTabs QTabBar::tab:hover:!selected {{
            background-color: {theme.input_hover_bg.css};
            color: {theme.text_secondary.css};
        }}
        QTabWidget#dockedPanelTabs::pane {{
            border: none;
            background-color: transparent;
        }}
        QTabWidget#dockedPanelTabs QTabBar::tab {{
            background-color: {theme.input_bg.css};
            border: 1px solid {theme.border_muted.css};
            border-bottom: none;
            border-top-left-radius: 7px;
            border-top-right-radius: 7px;
            color: {theme.text_muted.css};
            font-size: 11px;
            font-weight: 650;
            padding: 6px 12px;
        }}
        QTabWidget#dockedPanelTabs QTabBar::tab:selected {{
            background-color: {theme.panel_bg.css};
            color: {theme.text_primary.css};
            border-color: {theme.border.css};
            margin-bottom: -1px;
            padding-bottom: 7px;
        }}
        QTabWidget#dockedPanelTabs QTabBar::tab:hover:!selected {{
            background-color: {theme.input_hover_bg.css};
            color: {theme.text_secondary.css};
        }}
        QTabWidget#dockedPanelTabs QWidget#libraryWorkspacePanel,
        QTabWidget#dockedPanelTabs QWidget#inspectorWorkspacePanel {{
            border-top-left-radius: 0px;
        }}
        QWidget#appTopBar {{
            background-color: {theme.toolbar_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 10px;
        }}
        QToolButton#appTopBarButton {{
            background-color: {theme.raised_bg.css};
            border: 1px solid {theme.border_muted.css};
            border-radius: 7px;
            padding: 0px;
            color: {theme.text_secondary.css};
            font-family: "Segoe UI Symbol", "Segoe UI";
        }}
        QToolButton#appTopBarButton:hover {{
            background-color: {theme.input_hover_bg.css};
            border-color: {theme.border.css};
            color: {theme.text_primary.css};
        }}
        QToolButton#appTopBarButton:checked {{
            background-color: {theme.input_hover_bg.css};
            border-color: {theme.border.css};
            color: {theme.text_primary.css};
        }}
        QToolButton#appTopBarButton:disabled {{
            background-color: {theme.panel_alt_bg.css};
            color: {theme.text_disabled.css};
        }}
        QToolButton#appTopBarButton::menu-indicator {{
            image: none;
            width: 0px;
        }}
        QLineEdit#workspaceSearchField {{
            background-color: {theme.input_bg.css};
            border: 1px solid {theme.border_muted.css};
            border-radius: 7px;
            padding: 4px 8px;
            color: {theme.text_primary.css};
        }}
        QSlider#topbarZoomSlider::groove:horizontal {{
            height: 2px;
            background: {theme.border.css};
            border-radius: 1px;
        }}
        QSlider#topbarZoomSlider::handle:horizontal {{
            width: 8px;
            height: 8px;
            margin: -3px 0px;
            border-radius: 4px;
            background: {theme.text_muted.css};
        }}
        QSlider#topbarZoomSlider::handle:horizontal:hover {{
            background: {theme.text_secondary.css};
        }}
        QLabel#topbarZoomIconSmall {{
            color: {theme.text_muted.css};
            font-family: "Segoe UI Symbol";
            font-size: 13px;
        }}
        QLabel#topbarZoomIconLarge {{
            color: {theme.text_muted.css};
            font-family: "Segoe UI Symbol";
            font-size: 18px;
        }}
        QWidget#appTopBar QToolButton#appTopBarActionButton,
        QWidget#appTopBar QToolButton#workspacePresetsButton {{
            background-color: {theme.raised_bg.css};
            border: 1px solid {theme.border_muted.css};
            border-radius: 7px;
            color: {theme.text_secondary.css};
            padding: 4px 10px;
            font-size: 12px;
            font-weight: 600;
        }}
        QWidget#appTopBar QToolButton#appTopBarIconButton {{
            background-color: {theme.raised_bg.css};
            border: 1px solid {theme.border_muted.css};
            border-radius: 7px;
            color: {theme.text_secondary.css};
            padding: 0px;
        }}
        QWidget#appTopBar QToolButton#appTopBarActionButton:hover,
        QWidget#appTopBar QToolButton#workspacePresetsButton:hover,
        QWidget#appTopBar QToolButton#appTopBarIconButton:hover {{
            background-color: {theme.input_hover_bg.css};
            border-color: {theme.border.css};
            color: {theme.text_primary.css};
        }}
        QWidget#appTopBar QToolButton#appTopBarActionButton::menu-indicator,
        QWidget#appTopBar QToolButton#workspacePresetsButton::menu-indicator,
        QWidget#appTopBar QToolButton#appTopBarIconButton::menu-indicator {{
            image: none;
            width: 0px;
        }}
        QTabBar#leftModeTabs {{
            qproperty-drawBase: 0;
        }}
        QTabBar#leftModeTabs::tab {{
            background-color: transparent;
            border: none;
            border-bottom: 2px solid transparent;
            color: {theme.text_muted.css};
            padding: 6px 12px;
            margin-right: 4px;
            font-size: 12px;
            font-weight: 650;
        }}
        QTabBar#leftModeTabs::tab:selected {{
            color: {theme.text_primary.css};
            border-bottom: 2px solid {theme.accent.css};
        }}
        QTabBar#leftModeTabs::tab:hover:!selected {{
            color: {theme.text_secondary.css};
        }}
        QWidget#libraryWorkspacePanel {{
            background-color: {theme.panel_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 8px;
        }}
        QWidget#inspectorWorkspacePanel {{
            background-color: transparent;
            border: none;
        }}
        QWidget#libraryPanelHeader {{
            background-color: transparent;
            border: none;
        }}
        QWidget#inspectorPanelHeader {{
            background-color: transparent;
            border: none;
        }}
        QWidget#libraryPanelViewport {{
            background-color: transparent;
            border: none;
        }}
        QWidget#inspectorPanelViewport {{
            background-color: transparent;
            border: none;
        }}
        QWidget#libraryPanelContent, QWidget#inspectorPanelContent {{
            background-color: transparent;
            border: none;
        }}
        QWidget#generatedLeftTaskRail {{
            background-color: {theme.chrome_bg.css};
            border-right: 1px solid {theme.border_muted.css};
            border-top-left-radius: 8px;
            border-bottom-left-radius: 8px;
        }}
        QToolButton#generatedLeftRailButton {{
            background-color: transparent;
            border: 1px solid transparent;
            border-radius: 7px;
            padding: 0px;
        }}
        QToolButton#generatedLeftRailButton:hover {{
            background-color: {theme.input_hover_bg.css};
            border-color: transparent;
        }}
        QWidget#libraryStack {{
            background-color: transparent;
            border: none;
            padding: 8px;
        }}
        QFrame#leftQuickActionsPanel, QFrame#reviewWorkflowPanel {{
            background-color: transparent;
            border: none;
        }}
        QLabel#reviewSectionTitle {{
            color: {theme.text_primary.css};
            font-size: 12px;
            font-weight: 750;
        }}
        QLabel#reviewSelectionCount, QLabel#reviewGroupSummary {{
            color: {theme.text_muted.css};
            font-size: 10px;
            font-weight: 650;
        }}
        QLabel#reviewDecisionLabel {{
            color: {theme.text_primary.css};
            font-size: 12px;
            font-weight: 700;
        }}
        QLabel#reviewDecisionMeta {{
            color: {theme.text_muted.css};
            font-size: 10px;
        }}
        QFrame#reviewDecisionMarker {{
            background-color: {theme.text_disabled.css};
            border: none;
            border-radius: 4px;
        }}
        QFrame#reviewDecisionMarker[decisionState="keeper"] {{
            background-color: {theme.success.css};
        }}
        QFrame#reviewDecisionMarker[decisionState="rejected"] {{
            background-color: {theme.danger.css};
        }}
        QFrame#reviewDecisionMarker[decisionState="mixed"] {{
            background-color: {theme.warning.css};
        }}
        QFrame#reviewDecisionMarker[decisionState="unreviewed"] {{
            background-color: {theme.text_muted.css};
        }}
        QFrame#reviewSectionDivider {{
            background-color: {theme.border_muted.css};
            border: none;
            min-height: 1px;
            max-height: 1px;
        }}
        QPushButton#reviewCommandButton, QPushButton#reviewPrimaryDecisionButton {{
            background-color: {theme.raised_bg.css};
            border: 1px solid {theme.border_muted.css};
            border-radius: 6px;
            color: {theme.text_secondary.css};
            font-size: 11px;
            font-weight: 650;
            min-height: 26px;
            padding: 0px 7px;
        }}
        QPushButton#reviewPrimaryDecisionButton {{
            min-height: 30px;
            color: {theme.text_primary.css};
        }}
        QPushButton#reviewCommandButton:hover, QPushButton#reviewPrimaryDecisionButton:hover {{
            background-color: {theme.input_hover_bg.css};
            border-color: {theme.border.css};
            color: {theme.text_primary.css};
        }}
        QPushButton#reviewCommandButton:disabled, QPushButton#reviewPrimaryDecisionButton:disabled {{
            background-color: {theme.raised_bg.css};
            border-color: {theme.text_muted.css};
            color: {theme.text_muted.css};
        }}
        QToolButton#leftQuickActionIcon {{
            background-color: {theme.raised_bg.css};
            border: 1px solid {theme.border_muted.css};
            border-radius: 6px;
            padding: 0px;
            min-height: 26px;
        }}
        QToolButton#leftQuickActionIcon:hover {{
            background-color: {theme.input_hover_bg.css};
            border-color: {theme.border.css};
        }}
        QToolButton#leftQuickActionIcon:disabled {{
            background-color: {theme.raised_bg.css};
            border-color: {theme.text_muted.css};
        }}
        QWidget#reviewControlsPane {{
            background-color: {theme.input_bg.css};
            border: none;
        }}
        QSplitter#leftBodySplitter::handle:vertical {{
            background-color: {theme.border.css};
            margin: 0px;
        }}
        QSplitter#leftBodySplitter::handle:vertical:hover {{
            background-color: {theme.text_muted.css};
        }}
        QFrame#leftSettingsBar {{
            background-color: {theme.panel_bg.css};
            border: none;
            border-top: 1px solid {theme.border.css};
            border-bottom-right-radius: 8px;
        }}
        QToolButton#leftSettingsBarButton {{
            background-color: transparent;
            border: 1px solid transparent;
            border-radius: 6px;
            color: {theme.text_muted.css};
            padding: 0px;
        }}
        QToolButton#leftSettingsBarButton:hover {{
            background-color: {theme.input_hover_bg.css};
            color: {theme.text_primary.css};
        }}
        QLabel#leftPreviewTitle {{
            color: {theme.text_primary.css};
            font-size: 12px;
            font-weight: 750;
        }}
        QToolButton#leftAiActivityTextButton {{
            background-color: transparent;
            border: none;
            color: {theme.text_muted.css};
            font-size: 11px;
            font-weight: 650;
            min-height: 18px;
            padding: 0px;
            text-align: left;
        }}
        QToolButton#leftAiActivityTextButton:hover {{
            color: {theme.text_primary.css};
            background-color: transparent;
            border: none;
        }}
        QToolButton#leftAiActivityTextButton:checked {{
            color: {theme.text_primary.css};
            background-color: transparent;
            border: none;
        }}
        QScrollArea#inspectorScrollArea, QWidget#inspectorBody {{
            background-color: transparent;
            border: none;
        }}
        QScrollBar#inspectorOverlayScrollBar:vertical {{
            background: transparent;
            width: 8px;
            margin: 2px 1px 2px 0px;
        }}
        QScrollBar#inspectorOverlayScrollBar::handle:vertical {{
            background-color: {theme.border.css};
            border-radius: 3px;
            min-height: 36px;
        }}
        QScrollBar#inspectorOverlayScrollBar::handle:vertical:hover {{
            background-color: {theme.text_muted.css};
        }}
        QScrollBar#inspectorOverlayScrollBar::add-line:vertical,
        QScrollBar#inspectorOverlayScrollBar::sub-line:vertical {{
            height: 0px;
            background: transparent;
            border: none;
        }}
        QScrollBar#inspectorOverlayScrollBar::add-page:vertical,
        QScrollBar#inspectorOverlayScrollBar::sub-page:vertical {{
            background: transparent;
        }}
        QWidget#inspectorPreviewCard {{
            background-color: {theme.panel_alt_bg.css};
            border: 1px solid {theme.border_muted.css};
            border-radius: 8px;
        }}
        QWidget#inspectorHeaderBar {{
            background-color: transparent;
            border: none;
        }}
        QToolButton#inspectorHeaderButton, QToolButton#inspectorHeaderCloseButton {{
            background-color: transparent;
            border: none;
            border-radius: 5px;
            color: {theme.text_muted.css};
            font-size: 14px;
            padding: 0px;
        }}
        QToolButton#inspectorHeaderButton:hover {{
            background-color: {theme.input_hover_bg.css};
            color: {theme.text_primary.css};
        }}
        QToolButton#inspectorHeaderCloseButton:hover {{
            background-color: {theme.danger.css};
            color: #ffffff;
        }}
        QLabel#inspectorPreviewImage {{
            background-color: {theme.image_bg.css};
            border: none;
            border-radius: 6px;
            color: {theme.text_muted.css};
            font-size: 11px;
            font-weight: 650;
            padding: 2px;
        }}
        QWidget#inspectorSection {{
            background-color: {theme.panel_alt_bg.css};
            border: 1px solid {theme.border_muted.css};
            border-radius: 8px;
        }}
        QWidget#inspectorSectionHeader {{
            background-color: transparent;
        }}
        QLabel#inspectorSectionTitle {{
            color: {theme.text_secondary.css};
            font-size: 12px;
            font-weight: 650;
            padding: 0px;
        }}
        QToolButton#inspectorSectionToggle {{
            background-color: transparent;
            border: none;
            color: {theme.text_muted.css};
            min-width: 14px;
            max-width: 14px;
            min-height: 14px;
            max-height: 14px;
            padding: 0px;
        }}
        QToolButton#inspectorSectionToggle:hover {{
            color: {theme.text_primary.css};
        }}
        QLabel#inspectorKey {{
            color: {theme.text_muted.css};
            font-size: 11px;
            min-width: 64px;
        }}
        QLabel#inspectorValue {{
            color: {theme.text_secondary.css};
            font-size: 11px;
        }}
        QPushButton#inspectorActionButton {{
            background-color: {theme.input_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 6px;
            color: {theme.text_primary.css};
            min-height: 24px;
            padding: 4px 8px;
            text-align: center;
        }}
        QPushButton#inspectorActionButton:hover {{
            background-color: {theme.input_hover_bg.css};
        }}
        QPushButton#inspectorActionButton:disabled {{
            color: {theme.text_disabled.css};
            border-color: {theme.border_muted.css};
        }}
        QTreeView#folderTree, QListWidget#favoritesList {{
            background-color: transparent;
            border: none;
            show-decoration-selected: 1;
        }}
        QTreeView::item, QListWidget::item {{
            min-height: 22px;
            padding: 2px 7px;
            border-radius: 5px;
            margin: 1px 0px;
        }}
        QTreeView::item:selected, QListWidget::item:selected {{
            background-color: {theme.input_hover_bg.css};
            color: {theme.text_primary.css};
        }}
        QTreeView::branch {{
            background: transparent;
        }}
        QTableView#detailsTableView {{
            background-color: {theme.chrome_bg.css};
            alternate-background-color: {theme.panel_alt_bg.css};
            border: 1px solid {theme.border_muted.css};
            border-radius: 6px;
            color: {theme.text_primary.css};
            gridline-color: transparent;
            outline: none;
            selection-background-color: {theme.selection_fill.css};
            selection-color: {theme.text_primary.css};
        }}
        QTableView#detailsTableView QTableCornerButton::section {{
            background-color: {theme.panel_alt_bg.css};
            border: none;
            border-top-left-radius: 6px;
            border-bottom: 1px solid {theme.border.css};
        }}
        QTableView#detailsTableView QHeaderView::section:first {{
            border-top-left-radius: 6px;
        }}
        QTableView#detailsTableView QHeaderView::section:last {{
            border-top-right-radius: 6px;
        }}
        QTableView#detailsTableView QHeaderView::section {{
            min-height: 38px;
            padding-top: 6px;
            padding-bottom: 4px;
        }}
        QTableView#detailsTableView::item {{
            border: none;
            padding: 5px 8px;
        }}
        QTableView#detailsTableView::item:selected {{
            background-color: {theme.selection_fill.css};
            color: {theme.text_primary.css};
        }}
        QWidget#detailsPreviewPane {{
            background-color: {theme.panel_alt_bg.css};
            border: 1px solid {theme.border_muted.css};
            border-radius: 6px;
        }}
        QLabel#detailsPreviewImage {{
            background-color: {theme.image_bg.css};
            border: 1px solid {theme.border_muted.css};
            border-radius: 6px;
            color: {theme.text_muted.css};
        }}
        QCheckBox#detailsPreviewToggle {{
            color: {theme.text_secondary.css};
            spacing: 6px;
        }}
        QLabel#detailsStatusStrip {{
            background-color: {theme.chrome_bg.css};
            border-top: 1px solid {theme.border_muted.css};
            color: {theme.text_muted.css};
            font-size: 11px;
            padding: 4px 8px;
        }}
        QHeaderView::section {{
            background-color: {theme.panel_alt_bg.css};
            color: {theme.text_secondary.css};
            border: none;
            border-bottom: 1px solid {theme.border.css};
            padding: 6px 8px;
        }}
        QWidget#summaryStrip {{
            background-color: {theme.panel_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 12px;
        }}
        QWidget#workspaceBar {{
            background-color: {theme.toolbar_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 8px;
        }}
        QFrame#workspaceBarDivider {{
            background-color: {theme.border_muted.css};
            border: none;
            min-width: 1px;
            max-width: 1px;
            min-height: 24px;
            margin: 2px 0px;
        }}
        QWidget#workspaceBarChrome {{
            background-color: transparent;
            border: none;
        }}
        QLabel#workspaceBarDragHandle {{
            color: {theme.text_muted.css};
            font-size: 14px;
            font-weight: 700;
            padding: 0px 2px;
        }}
        QLabel#workspaceBarDragHandle:hover {{
            color: {theme.text_secondary.css};
        }}
        QFrame#toolbarEditOverlay {{
            background-color: rgba(0, 0, 0, 132);
            border: 1px solid {theme.accent_soft.css};
            border-radius: 12px;
        }}
        QDialog#toolbarCustomizerDialog {{
            background-color: {theme.window_bg.css};
            color: {theme.text_primary.css};
        }}
        QScrollArea#toolbarCustomizerPreviewScroll {{
            background-color: transparent;
            border: none;
        }}
        QFrame#toolbarCustomizerPreviewHost {{
            background-color: transparent;
            border: none;
        }}
        QFrame#toolbarCustomizerPreviewBar {{
            background-color: {theme.toolbar_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 12px;
        }}
        QPushButton#toolbarCustomizerPreviewButton {{
            background-color: {theme.input_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 8px;
            color: {theme.text_primary.css};
            font-weight: 600;
            min-height: 28px;
            padding: 4px 12px;
        }}
        QPushButton#toolbarCustomizerPreviewButton:hover {{
            background-color: {theme.input_hover_bg.css};
        }}
        QPushButton#toolbarCustomizerPreviewButton:checked {{
            background-color: {theme.accent_soft.css};
            border-color: {theme.accent.css};
        }}
        QFrame#toolbarEditSidebar {{
            background-color: {theme.panel_bg.with_alpha(232).css};
            border: 1px solid {theme.border.css};
            border-radius: 10px;
            min-width: 170px;
            max-width: 210px;
        }}
        QFrame#toolbarEditContent {{
            background-color: rgba(0, 0, 0, 0);
            border: none;
        }}
        QFrame#toolbarEditChip {{
            background-color: {theme.panel_bg.with_alpha(218).css};
            border: 1px solid {theme.border.css};
            border-radius: 8px;
        }}
        QLabel#toolbarEditTitle {{
            color: {theme.text_primary.css};
            font-size: 12px;
            font-weight: 700;
        }}
        QLabel#toolbarEditHint {{
            color: {theme.text_muted.css};
            font-size: 11px;
            font-weight: 600;
        }}
        QToolButton#toolbarEditAddButton, QToolButton#toolbarEditModeButton {{
            background-color: {theme.raised_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 8px;
            color: {theme.text_primary.css};
            min-height: 24px;
            padding: 2px 8px;
        }}
        QToolButton#toolbarEditModeButton:checked {{
            background-color: {theme.accent_soft.css};
            border-color: {theme.accent.css};
        }}
        QToolButton#toolbarEditMoveButton, QToolButton#toolbarEditRemoveButton {{
            background-color: {theme.raised_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 7px;
            color: {theme.text_primary.css};
            min-width: 18px;
            max-width: 18px;
            min-height: 18px;
            max-height: 18px;
            padding: 0px;
        }}
        QToolButton#toolbarEditRemoveButton {{
            background-color: {theme.danger_soft.css};
            border-color: {theme.danger.css};
        }}
        QPushButton#toolbarEditPaletteButton, QPushButton#toolbarEditResetButton, QPushButton#toolbarEditDoneButton {{
            border-radius: 8px;
            min-height: 24px;
            padding: 3px 8px;
            text-align: left;
        }}
        QPushButton#toolbarEditDoneButton {{
            background-color: {theme.accent_soft.css};
            border-color: {theme.accent.css};
        }}
        QWidget#workspaceControls {{
            background-color: transparent;
            border: none;
        }}
        QLabel#paneTitle {{
            color: {theme.text_primary.css};
            font-family: "Segoe UI Variable Display", "Segoe UI";
            font-size: 13px;
            font-weight: 750;
            letter-spacing: 0px;
            padding: 0 1px 2px 1px;
        }}
        QLabel#panelHeaderSubtitle {{
            color: {theme.text_muted.css};
            font-size: 11px;
            font-weight: 600;
            padding: 0 1px;
        }}
        QToolButton#workspacePanelButton, QToolButton#workspacePanelCloseButton {{
            background-color: transparent;
            border: 1px solid transparent;
            border-radius: 8px;
            color: {theme.text_secondary.css};
            font-family: "Segoe UI Symbol", "Segoe UI Variable Display", "Segoe UI";
            font-size: 13px;
            font-weight: 600;
            padding: 0px;
        }}
        QToolButton#workspacePanelButton:hover {{
            background-color: {theme.input_hover_bg.css};
            border-color: {theme.border.css};
            color: {theme.text_primary.css};
        }}
        QToolButton#zenMenuPinButton {{
            background-color: transparent;
            border: 1px solid transparent;
            border-radius: 0px;
            color: {theme.text_secondary.css};
            margin: 2px 10px 2px 6px;
            min-width: 30px;
            min-height: 30px;
            padding: 0px;
        }}
        QToolButton#zenMenuPinButton:hover {{
            background-color: transparent;
            border-color: transparent;
            color: {theme.text_primary.css};
        }}
        QToolButton#zenMenuPinButton:checked {{
            background-color: transparent;
            border-color: transparent;
            color: {theme.text_primary.css};
        }}
        QWidget#menuCornerWidget {{
            background-color: transparent;
        }}
        QToolButton#updateDownloadButton {{
            background-color: transparent;
            border: 1px solid transparent;
            border-radius: 8px;
            color: {theme.text_muted.css};
            margin: 1px 2px 1px 4px;
            padding: 0px;
        }}
        QToolButton#updateDownloadButton:hover {{
            background-color: {theme.input_hover_bg.css};
            border-color: {theme.border.css};
            color: {theme.text_primary.css};
        }}
        QToolButton#updateDownloadButton[updateAvailable="true"] {{
            background-color: {theme.success_soft.css};
            border-color: {theme.success.css};
            color: {theme.success.css};
        }}
        QToolButton#updateDownloadButton[updateAvailable="true"]:hover {{
            background-color: {theme.success_soft.css};
            border-color: {theme.success.css};
            color: {theme.success.css};
        }}
        QLabel#zenHintOverlay {{
            background-color: {theme.badge_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 8px;
            color: {theme.badge_text.css};
            font-size: 12px;
            font-weight: 650;
            padding: 7px 12px;
        }}
        QToolButton#workspacePanelCloseButton:hover {{
            background-color: {theme.danger_soft.css};
            border-color: {theme.danger.css};
            color: {theme.text_primary.css};
        }}
        QLabel#sectionLabel {{
            color: {theme.text_muted.css};
            font-size: 11px;
            font-weight: 600;
            padding: 0 2px;
        }}
        QListWidget#settingsSectionList {{
            background-color: {theme.panel_alt_bg.css};
            border: none;
            border-right: 1px solid {theme.border_muted.css};
            color: {theme.text_secondary.css};
            font-size: 13px;
            outline: 0;
            padding: 18px 8px;
        }}
        QListWidget#settingsSectionList::item {{
            padding: 8px 14px;
            border-radius: 8px;
            margin: 1px 4px;
        }}
        QListWidget#settingsSectionList::item:hover {{
            background-color: {theme.input_hover_bg.css};
            color: {theme.text_primary.css};
        }}
        QListWidget#settingsSectionList::item:selected {{
            background-color: {theme.accent_soft.css};
            color: {theme.text_primary.css};
            font-weight: 600;
        }}
        QStackedWidget#settingsPages {{
            background-color: {theme.window_bg.css};
        }}
        QWidget#settingsPageContent {{
            background-color: {theme.window_bg.css};
        }}
        QLabel#settingsPageTitle {{
            color: {theme.text_primary.css};
            font-size: 18px;
            font-weight: 700;
            padding-bottom: 4px;
        }}
        QFrame#settingsPageSeparator {{
            background-color: {theme.border_muted.css};
            border: none;
        }}
        QLabel#settingsRowLabel {{
            color: {theme.text_secondary.css};
            font-size: 12px;
            font-weight: 500;
        }}
        QFrame#settingsFooter {{
            background-color: {theme.panel_alt_bg.css};
            border-top: 1px solid {theme.border_muted.css};
        }}
        QLabel#inspectorValue {{
            color: {theme.text_primary.css};
            font-size: 12px;
            padding: 0 2px;
        }}
        QLabel#inspectorHint {{
            color: {theme.text_muted.css};
            font-size: 11px;
            padding: 4px 2px 0 2px;
        }}
        QFrame#sectionDivider {{
            background-color: {theme.border_muted.css};
            max-height: 1px;
            min-height: 1px;
            border: none;
        }}
        QComboBox#pathComboBox {{
            background-color: {theme.raised_bg.css};
            color: {theme.text_secondary.css};
            min-height: 28px;
            padding: 2px 8px;
        }}
        QComboBox#pathComboBox:focus {{
            border-color: {theme.accent.css};
            color: {theme.text_primary.css};
        }}
        QWidget#pathControl {{
            background-color: transparent;
        }}
        QToolButton#pathNavButton {{
            min-width: 38px;
            max-width: 38px;
            min-height: 28px;
            max-height: 28px;
            margin: 0px 1px;
            padding: 0px;
            color: {theme.text_secondary.css};
        }}
        QToolButton#pathNavButton:hover {{
            background-color: {theme.input_hover_bg.css};
            border-color: {theme.border.css};
            color: {theme.text_primary.css};
        }}
        QToolButton#pathNavButton:disabled {{
            color: {theme.text_disabled.css};
            background-color: transparent;
            border-color: transparent;
        }}
        QLabel#toolbarSelectionCount {{
            color: {theme.text_secondary.css};
            font-size: 11px;
            font-weight: 600;
            padding: 0 4px;
        }}
        QLabel#toolbarSelectionCount[toolbarPreviewSelected="true"] {{
            color: {theme.text_primary.css};
        }}
        QLabel#secondaryText {{
            color: {theme.text_secondary.css};
        }}
        QLabel#filterSummaryLabel {{
            color: {theme.text_muted.css};
            padding: 0 2px;
        }}
        QLabel#mutedText {{
            color: {theme.text_muted.css};
        }}
        QLabel#trainRankerTitle {{
            color: {theme.text_primary.css};
            font-size: 14px;
            font-weight: 700;
        }}
        QLabel#trainRankerSummary {{
            color: {theme.text_secondary.css};
            font-size: 12px;
            padding: 1px 2px;
        }}
        QTextBrowser#helpMarkdownView {{
            background-color: transparent;
            border: none;
            border-radius: 0;
            color: {theme.text_primary.css};
            font-size: 14px;
            padding: 2px 4px;
            selection-background-color: {theme.selection_fill.css};
        }}
        QListWidget#helpPageList {{
            background-color: {theme.raised_bg.css};
            border: 1px solid {theme.border_muted.css};
            border-radius: 10px;
            padding: 6px;
            outline: none;
        }}
        QListWidget#helpPageList::item {{
            color: {theme.text_secondary.css};
            font-size: 13px;
            padding: 9px 10px;
            margin: 2px 0;
            border-radius: 6px;
        }}
        QListWidget#helpPageList::item:selected {{
            background-color: {theme.selection_fill.css};
            color: {theme.text_primary.css};
        }}
        QToolButton#contextHelpButton {{
            background-color: {theme.input_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 13px;
            color: {theme.text_secondary.css};
            font-weight: 700;
        }}
        QToolButton#contextHelpButton:hover {{
            background-color: {theme.input_hover_bg.css};
            color: {theme.text_primary.css};
        }}
        QPlainTextEdit#aiTrainingLogView, QPlainTextEdit#aiReviewProgressLog {{
            background-color: {theme.chrome_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 14px;
            color: {theme.text_primary.css};
            padding: 10px 12px;
            font-family: Consolas, "Cascadia Mono", "Courier New";
            selection-background-color: {theme.selection_fill.css};
        }}
        QWidget#aiTrainingStatsCard {{
            background-color: {theme.panel_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 14px;
        }}
        QWidget#commandPaletteOverlay {{
            background-color: rgba(0, 0, 0, 0.22);
        }}
        QFrame#commandPaletteCard {{
            background-color: {theme.panel_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 18px;
        }}
        QListWidget#commandPaletteList {{
            background-color: transparent;
            border: 1px solid {theme.border_muted.css};
            border-radius: 14px;
            outline: none;
            padding: 6px;
        }}
        QListWidget#commandPaletteList::item {{
            background-color: transparent;
            border: none;
            margin: 2px 0;
        }}
        QListWidget#commandPaletteList::item:selected {{
            background-color: {theme.selection_fill.css};
            border-radius: 12px;
        }}
        QLabel#commandPaletteTitle {{
            color: {theme.text_primary.css};
            font-family: "Segoe UI", "Segoe UI Variable Text";
            font-size: 14px;
            font-weight: 600;
            min-height: 20px;
            padding: 0 0 2px 0;
            margin: 0;
        }}
        QLabel#commandPaletteSubtitle {{
            color: {theme.text_muted.css};
            font-size: 11px;
            padding: 0;
            margin: 0;
        }}
        QLabel#commandPaletteShortcut {{
            color: {theme.text_muted.css};
            font-size: 11px;
            padding: 0 2px 0 8px;
        }}
        QToolButton#workspaceFiltersButton, QToolButton#workspacePresetsButton {{
            min-height: 28px;
            border-radius: 7px;
            padding: 4px 10px;
        }}
        QToolButton#workspaceIconButton {{
            background-color: {theme.input_bg.css};
            border: 1px solid {theme.border_muted.css};
            border-radius: 7px;
            padding: 3px;
        }}
        QToolButton#workspaceIconButton:hover {{
            background-color: {theme.input_hover_bg.css};
            border-color: {theme.border.css};
        }}
        QToolButton#workspaceIconButton:pressed,
        QToolButton#workspaceIconButton:checked {{
            background-color: {theme.accent_soft.css};
            border-color: {theme.accent.css};
        }}
        QToolButton#statusFilterClearButton {{
            padding: 2px 8px;
        }}
        QTabBar#modeTabs::tab {{
            background-color: {theme.input_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 7px;
            color: {theme.text_secondary.css};
            min-width: 120px;
            max-width: 120px;
            padding: 6px 14px;
            margin-right: 6px;
        }}
        QTabBar#modeTabs::tab:selected {{
            background-color: {theme.accent_soft.css};
            border-color: {theme.accent.css};
            color: {theme.text_primary.css};
        }}
        QTabBar#modeTabs::tab:hover:!selected {{
            background-color: {theme.input_hover_bg.css};
        }}
        QProgressBar {{
            background-color: {theme.input_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 9px;
            color: {theme.text_secondary.css};
            text-align: center;
        }}
        QProgressBar::chunk {{
            background-color: {theme.accent.css};
            border-radius: 8px;
        }}
        QStatusBar {{
            background-color: {theme.chrome_bg.css};
            border-top: 1px solid {theme.border.css};
            color: {theme.text_secondary.css};
        }}
        QStatusBar::item {{
            border: none;
        }}
        QScrollBar:vertical {{
            background-color: {theme.chrome_bg.css};
            border: none;
            width: 11px;
            margin: 0px;
        }}
        QScrollBar::handle:vertical {{
            background-color: {theme.border.css};
            border-radius: 5px;
            min-height: 36px;
            margin: 2px;
        }}
        QScrollBar::handle:vertical:hover {{
            background-color: {theme.text_muted.css};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
            border: none;
            background: transparent;
        }}
        QScrollBar:horizontal {{
            background-color: {theme.chrome_bg.css};
            border: none;
            height: 11px;
            margin: 0px;
        }}
        QScrollBar::handle:horizontal {{
            background-color: {theme.border.css};
            border-radius: 5px;
            min-width: 36px;
            margin: 2px;
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0px;
            border: none;
            background: transparent;
        }}
        QTreeView#folderTree QHeaderView::section {{
            background-color: transparent;
            border: none;
            border-bottom: 1px solid {theme.border_muted.css};
            color: {theme.text_muted.css};
            padding: 0;
            height: 0px;
        }}
        QSplitter::handle {{
            background-color: transparent;
        }}
        QSplitter::handle:horizontal {{
            width: 4px;
        }}
        QSplitter::handle:vertical {{
            height: 4px;
        }}
        QSplitter::handle:hover {{
            background-color: {theme.accent_soft.css};
        }}
    """
