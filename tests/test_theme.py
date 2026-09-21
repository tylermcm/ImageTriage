from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from image_triage.fonts import UI_FONT_STACK
from image_triage.ui.theme import (
    WORKSPACE_METRICS,
    AppearanceMode,
    appearance_profile_modes,
    build_app_stylesheet,
    contrast_ratio,
    default_theme,
    resolve_theme,
)


def test_library_sidebar_uses_the_application_font_stack() -> None:
    stylesheet = build_app_stylesheet(default_theme())

    assert "QWidget#libraryPanelContent QTreeView" in stylesheet
    assert "QWidget#libraryPanelContent QTabBar" in stylesheet
    assert f"font-family: {UI_FONT_STACK};" in stylesheet


def test_slate_is_the_default_application_palette() -> None:
    assert default_theme().name == "slate"
    assert appearance_profile_modes()[0] == AppearanceMode.SLATE


def test_slate_paints_the_designs_surfaces_flat() -> None:
    """Slate is specified as exact surface colours, so it carries no backdrop
    glow; the glows would otherwise wash every chrome surface with white."""
    theme = resolve_theme(AppearanceMode.SLATE, QApplication.instance() or QApplication([]))
    assert theme.backdrop_glow_primary is None
    assert theme.window_bg.css == "rgb(14, 15, 17)"      # grid
    assert theme.chrome_bg.css == "rgb(20, 21, 23)"      # rail, menu bar, status bar
    assert theme.panel_bg.css == "rgb(24, 25, 28)"       # library pane and inspector
    assert theme.toolbar_bg.css == "rgb(23, 24, 26)"     # floating button bar
    assert theme.panel_alt_bg.css == "rgb(22, 23, 25)"   # settings bar
    assert theme.raised_bg.css == "rgb(32, 34, 38)"      # search bar
    assert theme.selection_fill.css == "rgb(28, 40, 60)"  # rail button highlight


def test_only_backdrop_themes_turn_the_window_chrome_translucent() -> None:
    app = QApplication.instance() or QApplication([])
    indigo = build_app_stylesheet(resolve_theme(AppearanceMode.INDIGO, app))
    graphite = build_app_stylesheet(resolve_theme(AppearanceMode.GRAPHITE, app))

    assert "QMainWindow, QWidget#centralContainer" in indigo
    assert "QMainWindow, QWidget#centralContainer" not in graphite


def test_toolbar_strip_has_docked_and_floating_surfaces() -> None:
    stylesheet = build_app_stylesheet(default_theme())

    assert 'QFrame#appTopBar[toolbarPlacement="docked"]' in stylesheet
    assert 'QFrame#appTopBar[toolbarPlacement="floating"]' in stylesheet


def test_workspace_metrics_expose_the_supported_spacing_and_radius_scale() -> None:
    assert (
        WORKSPACE_METRICS.space_4,
        WORKSPACE_METRICS.space_6,
        WORKSPACE_METRICS.space_8,
        WORKSPACE_METRICS.space_12,
        WORKSPACE_METRICS.space_16,
    ) == (4, 6, 8, 12, 16)
    assert (WORKSPACE_METRICS.radius_4, WORKSPACE_METRICS.radius_7) == (4, 7)


def test_workspace_text_contrast_is_preserved_across_every_palette() -> None:
    app = QApplication.instance() or QApplication([])
    background_names = ("window_bg", "toolbar_bg", "panel_bg", "raised_bg", "input_bg")
    for mode in appearance_profile_modes(include_auto=False):
        theme = resolve_theme(mode, app)
        backgrounds = [getattr(theme, name) for name in background_names]
        assert min(contrast_ratio(theme.text_primary, background) for background in backgrounds) >= 7.0
        assert min(contrast_ratio(theme.text_secondary, background) for background in backgrounds) >= 4.5
        assert min(contrast_ratio(theme.text_muted, background) for background in backgrounds) >= 3.0
        assert min(contrast_ratio(theme.text_disabled, background) for background in backgrounds) >= 1.8
        assert theme.text_secondary != theme.text_primary


def test_workspace_focus_and_selection_states_use_theme_tokens() -> None:
    theme = default_theme()
    stylesheet = build_app_stylesheet(theme)

    assert "QLineEdit#workspaceSearchField:focus" in stylesheet
    assert "QFrame#pathSuggestionPopup" in stylesheet
    assert "QListWidget#pathSuggestionList::item:selected" in stylesheet
    assert f"border: 2px solid {theme.selection_outline.css};" in stylesheet
    assert f"background-color: {theme.selection_fill.css};" in stylesheet
    assert f"background-color: {theme.accent_soft.css};" in stylesheet
    assert f"border-color: {theme.accent.css};" in stylesheet


def test_sidebar_navigation_selection_uses_theme_tokens() -> None:
    theme = default_theme()
    stylesheet = build_app_stylesheet(theme)

    rail_rule = stylesheet.split("QToolButton#leftNavButton:checked", 1)[1].split("}", 1)[0]
    assert f"background-color: {theme.selection_fill.css};" in rail_rule
    assert f"color: {theme.accent.css};" in rail_rule
    assert "QTreeView#folderTree::item:selected" in stylesheet
    assert "QListWidget#faceGroupsList::item:selected" in stylesheet
    assert "QListWidget#projectsList::item:selected" in stylesheet
    assert stylesheet.count(f"background-color: {theme.selection_fill.css};") >= 4


def test_projects_header_does_not_add_an_oversized_gap_above_its_list() -> None:
    stylesheet = build_app_stylesheet(default_theme())

    projects_rule = stylesheet.split(
        'QWidget#navSectionHeader[sectionRole="projects"]', 1
    )[1].split("}", 1)[0]
    assert "min-height: 36px;" in projects_rule
    assert "padding-top: 0px;" in projects_rule
