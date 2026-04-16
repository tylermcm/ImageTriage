"""Theme helpers for the standalone labeling UI."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


_HOST_ROOT_ENV = "IMAGE_TRIAGE_HOST_ROOT"
_APPEARANCE_MODE_ENV = "IMAGE_TRIAGE_APPEARANCE_MODE"


@dataclass(frozen=True)
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


@dataclass(frozen=True)
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


def apply_labeling_theme(app: QApplication) -> ThemePalette:
    """Apply the Image Triage theme when available, with a local fallback."""

    theme_module = _load_host_theme_module()
    requested_mode = os.environ.get(_APPEARANCE_MODE_ENV, "dark").strip().lower() or "dark"

    if theme_module is not None:
        parse_appearance_mode = getattr(theme_module, "parse_appearance_mode")
        resolve_theme = getattr(theme_module, "resolve_theme")
        build_app_palette = getattr(theme_module, "build_app_palette")
        build_app_stylesheet = getattr(theme_module, "build_app_stylesheet")
        theme = resolve_theme(parse_appearance_mode(requested_mode), app)
        app.setPalette(build_app_palette(theme))
        base_stylesheet = build_app_stylesheet(theme)
    else:
        theme = _resolve_fallback_theme(requested_mode, app)
        app.setPalette(_build_fallback_palette(theme))
        base_stylesheet = _build_fallback_stylesheet(theme)

    app.setStyleSheet(base_stylesheet + "\n" + _build_labeling_stylesheet(theme))
    return theme


def _load_host_theme_module() -> Any | None:
    host_root_text = os.environ.get(_HOST_ROOT_ENV, "").strip()
    if not host_root_text:
        return None

    host_root = Path(host_root_text).expanduser().resolve()
    if not host_root.exists():
        return None

    if str(host_root) not in sys.path:
        sys.path.insert(0, str(host_root))

    try:
        from image_triage.ui import theme as host_theme
    except Exception:
        return None
    return host_theme


def _resolve_fallback_theme(mode: str, app: QApplication) -> ThemePalette:
    normalized = mode.strip().lower()
    if normalized == "light":
        return _light_theme()
    if normalized == "midnight":
        return _midnight_theme()
    if normalized == "auto":
        window_color = app.palette().color(QPalette.ColorRole.Window)
        return _dark_theme() if window_color.lightness() < 128 else _light_theme()
    return _dark_theme()


def _dark_theme() -> ThemePalette:
    return ThemePalette(
        name="dark",
        is_dark=True,
        window_bg=ColorToken(24, 25, 27),
        chrome_bg=ColorToken(17, 18, 20),
        toolbar_bg=ColorToken(28, 30, 33),
        panel_bg=ColorToken(38, 41, 45),
        panel_alt_bg=ColorToken(29, 31, 35),
        raised_bg=ColorToken(48, 51, 57),
        input_bg=ColorToken(21, 23, 26),
        input_hover_bg=ColorToken(32, 35, 40),
        border=ColorToken(58, 62, 69),
        border_muted=ColorToken(43, 46, 51),
        text_primary=ColorToken(235, 237, 241),
        text_secondary=ColorToken(184, 188, 197),
        text_muted=ColorToken(129, 135, 146),
        text_disabled=ColorToken(96, 101, 111),
        accent=ColorToken(96, 144, 255),
        accent_hover=ColorToken(123, 163, 255),
        accent_soft=ColorToken(96, 144, 255, 40),
        selection_fill=ColorToken(96, 144, 255, 42),
        selection_outline=ColorToken(120, 160, 250),
        success=ColorToken(88, 196, 132),
        success_soft=ColorToken(38, 84, 58, 210),
        warning=ColorToken(213, 170, 89),
        warning_soft=ColorToken(88, 67, 23, 214),
        danger=ColorToken(236, 123, 123),
        danger_soft=ColorToken(96, 42, 46, 215),
        image_bg=ColorToken(20, 21, 23),
    )


def _midnight_theme() -> ThemePalette:
    return ThemePalette(
        name="midnight",
        is_dark=True,
        window_bg=ColorToken(15, 21, 30),
        chrome_bg=ColorToken(11, 16, 24),
        toolbar_bg=ColorToken(20, 28, 39),
        panel_bg=ColorToken(23, 33, 46),
        panel_alt_bg=ColorToken(18, 26, 37),
        raised_bg=ColorToken(29, 40, 54),
        input_bg=ColorToken(16, 24, 34),
        input_hover_bg=ColorToken(24, 34, 47),
        border=ColorToken(45, 59, 78),
        border_muted=ColorToken(32, 43, 58),
        text_primary=ColorToken(232, 238, 246),
        text_secondary=ColorToken(178, 192, 211),
        text_muted=ColorToken(126, 144, 168),
        text_disabled=ColorToken(94, 108, 126),
        accent=ColorToken(88, 145, 255),
        accent_hover=ColorToken(116, 165, 255),
        accent_soft=ColorToken(88, 145, 255, 44),
        selection_fill=ColorToken(88, 145, 255, 50),
        selection_outline=ColorToken(110, 155, 235),
        success=ColorToken(77, 194, 122),
        success_soft=ColorToken(37, 88, 58, 215),
        warning=ColorToken(214, 166, 73),
        warning_soft=ColorToken(92, 70, 18, 220),
        danger=ColorToken(242, 124, 124),
        danger_soft=ColorToken(108, 43, 48, 215),
        image_bg=ColorToken(11, 17, 24),
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
    )


def _build_fallback_palette(theme: ThemePalette) -> QPalette:
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
    palette.setColor(QPalette.ColorRole.Link, theme.accent.qcolor())
    palette.setColor(QPalette.ColorRole.Highlight, theme.selection_outline.qcolor())
    palette.setColor(QPalette.ColorRole.HighlightedText, theme.text_primary.qcolor())
    palette.setColor(QPalette.ColorRole.PlaceholderText, theme.text_muted.qcolor())
    return palette


def _build_fallback_stylesheet(theme: ThemePalette) -> str:
    return f"""
        QMainWindow {{
            background-color: {theme.window_bg.css};
            color: {theme.text_primary.css};
        }}
        QMenuBar {{
            background-color: {theme.chrome_bg.css};
            border-bottom: 1px solid {theme.border.css};
            color: {theme.text_primary.css};
        }}
        QMenuBar::item {{
            border-radius: 8px;
            padding: 6px 10px;
            margin: 2px;
        }}
        QMenuBar::item:selected {{
            background-color: {theme.accent_soft.css};
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
        QPushButton {{
            background-color: {theme.raised_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 10px;
            color: {theme.text_primary.css};
            padding: 6px 12px;
        }}
        QPushButton:hover {{
            background-color: {theme.input_hover_bg.css};
        }}
        QPushButton:pressed {{
            background-color: {theme.input_bg.css};
        }}
        QComboBox {{
            background-color: {theme.input_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 10px;
            color: {theme.text_primary.css};
            min-height: 28px;
            padding: 2px 10px;
        }}
        QComboBox QAbstractItemView {{
            background-color: {theme.panel_bg.css};
            border: 1px solid {theme.border.css};
            color: {theme.text_primary.css};
            selection-background-color: {theme.selection_fill.css};
        }}
        QCheckBox {{
            color: {theme.text_secondary.css};
            spacing: 8px;
        }}
        QCheckBox::indicator {{
            width: 18px;
            height: 18px;
            border: 1px solid {theme.border.css};
            border-radius: 5px;
            background-color: {theme.input_bg.css};
        }}
        QCheckBox::indicator:checked {{
            background-color: {theme.accent.css};
            border-color: {theme.accent.css};
        }}
        QStatusBar {{
            background-color: {theme.chrome_bg.css};
            border-top: 1px solid {theme.border.css};
            color: {theme.text_secondary.css};
        }}
    """


def _build_labeling_stylesheet(theme: ThemePalette) -> str:
    return f"""
        QWidget#labelingCentralContainer {{
            background-color: {theme.window_bg.css};
            color: {theme.text_primary.css};
        }}
        QFrame#labelingHeaderPanel,
        QFrame#labelingControlPanel {{
            background-color: {theme.panel_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 16px;
        }}
        QLabel#labelingHeaderTitle {{
            color: {theme.text_primary.css};
            font-family: "Segoe UI Variable Display", "Segoe UI";
            font-size: 20px;
            font-weight: 700;
            letter-spacing: 0.4px;
        }}
        QLabel#labelingHeaderSubtitle {{
            color: {theme.text_muted.css};
            font-size: 12px;
            padding-top: 2px;
        }}
        QLabel#summaryBadge {{
            background-color: {theme.raised_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 12px;
            color: {theme.text_secondary.css};
            font-size: 11px;
            font-weight: 600;
            padding: 6px 10px;
        }}
        QLabel#summaryBadgeAccent {{
            background-color: {theme.accent_soft.css};
            border: 1px solid {theme.accent.css};
            border-radius: 12px;
            color: {theme.text_primary.css};
            font-size: 11px;
            font-weight: 700;
            padding: 6px 10px;
        }}
        QLabel#clusterRuleBadge {{
            background-color: {theme.raised_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 12px;
            color: {theme.text_secondary.css};
            font-size: 11px;
            font-weight: 700;
            padding: 6px 10px;
        }}
        QLabel#clusterRuleBadge[status="warning"] {{
            background-color: {theme.warning_soft.css};
            border-color: {theme.warning.css};
            color: {theme.text_primary.css};
        }}
        QLabel#clusterRuleBadge[status="success"] {{
            background-color: {theme.success_soft.css};
            border-color: {theme.success.css};
            color: {theme.text_primary.css};
        }}
        QLabel#clusterRuleBadge[status="info"] {{
            background-color: {theme.accent_soft.css};
            border-color: {theme.accent.css};
            color: {theme.text_primary.css};
        }}
        QTabWidget#labelingTabs::pane {{
            border: 1px solid {theme.border.css};
            border-radius: 16px;
            top: -1px;
            background-color: {theme.panel_bg.css};
        }}
        QTabWidget#labelingTabs::tab-bar {{
            left: 34px;
            top: 0px;
        }}
        QTabBar::tab {{
            background-color: {theme.toolbar_bg.css};
            border: 1px solid {theme.border.css};
            border-bottom: none;
            border-top-left-radius: 12px;
            border-top-right-radius: 12px;
            border-bottom-left-radius: 0px;
            border-bottom-right-radius: 0px;
            color: {theme.text_secondary.css};
            margin-right: 6px;
            margin-bottom: 0px;
            min-width: 92px;
            padding: 8px 14px;
        }}
        QTabBar::tab:selected {{
            background-color: {theme.panel_bg.css};
            border-color: {theme.accent.css};
            border-bottom-color: {theme.panel_bg.css};
            color: {theme.text_primary.css};
        }}
        QTabBar::tab:hover:!selected {{
            background-color: {theme.input_hover_bg.css};
        }}
        QGroupBox#imagePreviewPanel {{
            background-color: {theme.panel_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 16px;
            color: {theme.text_primary.css};
            font-weight: 600;
            margin-top: 14px;
            padding-top: 8px;
        }}
        QGroupBox#imagePreviewPanel::title {{
            subcontrol-origin: margin;
            left: 12px;
            padding: 0 6px;
            color: {theme.text_secondary.css};
        }}
        QLabel#previewMessageLabel,
        QLabel#previewImageLabel,
        QGraphicsView#previewGraphicsView {{
            background-color: {theme.image_bg.css};
            border: 1px solid {theme.border_muted.css};
            border-radius: 12px;
            color: {theme.text_secondary.css};
        }}
        QLabel#previewPathLabel {{
            color: {theme.text_primary.css};
            font-size: 12px;
        }}
        QLabel#previewMetaLabel,
        QLabel#clusterInfoLabel,
        QLabel#hintText,
        QLabel#pairMetaLabel,
        QLabel#clusterMetaLabel {{
            color: {theme.text_secondary.css};
            font-size: 12px;
        }}
        QLabel#hintText {{
            color: {theme.text_muted.css};
        }}
        QFrame#clusterCard {{
            background-color: {theme.panel_bg.css};
            border: 1px solid {theme.border_muted.css};
            border-radius: 16px;
        }}
        QFrame#clusterCard[active="true"] {{
            background-color: {theme.accent_soft.css};
            border: 2px solid {theme.accent.css};
        }}
        QComboBox#labelAssignmentCombo {{
            font-weight: 600;
        }}
        QPushButton#primaryActionButton {{
            background-color: {theme.accent.css};
            border-color: {theme.accent.css};
            color: white;
            font-weight: 700;
        }}
        QPushButton#primaryActionButton:hover {{
            background-color: {theme.accent_hover.css};
        }}
        QPushButton#secondaryActionButton {{
            font-weight: 600;
        }}
        QPushButton#dangerActionButton {{
            background-color: {theme.danger_soft.css};
            border-color: {theme.danger.css};
            color: {theme.text_primary.css};
            font-weight: 600;
        }}
        QPushButton#dangerActionButton:hover {{
            background-color: {theme.danger.css};
            color: white;
        }}
        QScrollArea#clusterScrollArea {{
            background: transparent;
            border: none;
        }}
        QWidget#clusterScrollWidget {{
            background: transparent;
        }}
    """
