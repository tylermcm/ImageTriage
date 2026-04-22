from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


class AppearanceMode(str, Enum):
    DARK = "dark"
    MIDNIGHT = "midnight"
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
        badge_bg=ColorToken(21, 22, 25, 220),
        badge_text=ColorToken(244, 246, 250),
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
        badge_bg=ColorToken(9, 14, 22, 210),
        badge_text=ColorToken(244, 247, 251),
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
    if mode == AppearanceMode.LIGHT:
        return _light_theme()
    return _dark_theme() if _system_prefers_dark(app) else _light_theme()


def default_theme() -> ThemePalette:
    return _dark_theme()


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
            padding: 2px 8px;
        }}
        QMenuBar::item {{
            background: transparent;
            border-radius: 8px;
            padding: 6px 10px;
            margin: 2px 2px;
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
            border-radius: 12px;
            spacing: 2px;
            padding: 0px 4px;
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
            border-radius: 8px;
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
            border-radius: 10px;
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
            border-radius: 10px;
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
        QTreeView, QListWidget {{
            background-color: {theme.panel_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 12px;
            color: {theme.text_primary.css};
            alternate-background-color: {theme.panel_alt_bg.css};
            outline: none;
        }}
        QWidget#workspaceCenterColumn {{
            background-color: transparent;
            border: none;
        }}
        QWidget#libraryWorkspacePanel {{
            background-color: {theme.panel_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 14px;
        }}
        QWidget#inspectorWorkspacePanel {{
            background-color: {theme.chrome_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 14px;
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
        QTreeView#folderTree, QListWidget#favoritesList {{
            background-color: transparent;
            border: none;
        }}
        QTreeView::item, QListWidget::item {{
            padding: 4px 6px;
            border-radius: 8px;
        }}
        QTreeView::item:selected, QListWidget::item:selected {{
            background-color: {theme.selection_fill.css};
            color: {theme.text_primary.css};
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
            border-radius: 12px;
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
            font-size: 18px;
            font-weight: 700;
            letter-spacing: 0.4px;
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
        QLabel#pathLabel {{
            background-color: {theme.raised_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 10px;
            color: {theme.text_secondary.css};
            padding: 6px 10px;
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
        QTextBrowser#helpMarkdownView {{
            background-color: {theme.panel_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 14px;
            color: {theme.text_primary.css};
            padding: 12px 14px;
            selection-background-color: {theme.selection_fill.css};
        }}
        QPlainTextEdit#aiTrainingLogView {{
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
            padding: 4px 10px;
        }}
        QToolButton#statusFilterClearButton {{
            padding: 2px 8px;
        }}
        QTabBar#modeTabs::tab {{
            background-color: {theme.input_bg.css};
            border: 1px solid {theme.border.css};
            border-radius: 12px;
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
