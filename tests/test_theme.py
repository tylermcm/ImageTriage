from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from image_triage.ui.theme import (
    WORKSPACE_METRICS,
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
    assert 'font-family: "Segoe UI", "Segoe UI Variable Text";' in stylesheet


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

    assert "QTabBar#leftModeTabs::tab:selected" in stylesheet
    assert f"border-bottom: 3px solid {theme.selection_outline.css};" in stylesheet
    assert "QTreeView#folderTree::item:selected" in stylesheet
    assert "QListWidget#faceGroupsList::item:selected" in stylesheet
    assert "QListWidget#projectsList::item:selected" in stylesheet
    assert stylesheet.count(f"background-color: {theme.selection_fill.css};") >= 4
