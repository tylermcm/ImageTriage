from __future__ import annotations

from image_triage.ui.theme import build_app_stylesheet, default_theme


def test_library_sidebar_uses_the_application_font_stack() -> None:
    stylesheet = build_app_stylesheet(default_theme())

    assert "QWidget#libraryPanelContent QTreeView" in stylesheet
    assert "QWidget#libraryPanelContent QTabBar" in stylesheet
    assert 'font-family: "Segoe UI", "Segoe UI Variable Text";' in stylesheet
