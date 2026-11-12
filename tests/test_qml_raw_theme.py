"""The in-app RAW editor's panel takes its colours and font from the app, like every other window."""
from __future__ import annotations

import re

from image_triage.qml_shell import app as shell_app

RAW_FILES = sorted(shell_app.QML_DIR.glob("Raw*.qml"))


def test_the_raw_panels_do_not_name_a_font_of_their_own():
    assert RAW_FILES
    for path in RAW_FILES:
        assert "font.family" not in path.read_text(encoding="utf-8"), path.name


def test_the_panel_surfaces_come_from_the_theme_in_every_theme():
    for name in ("RawEditorPanel.qml", "RawAdjustmentSection.qml"):
        text = (shell_app.QML_DIR / name).read_text(encoding="utf-8")
        # a dark palette written into the file is what stopped the other themes at the panel's edge
        assert "theme.light ?" not in text and not re.search(r'"#[0-9a-fA-F]{6}"', text), name
    panel = (shell_app.QML_DIR / "RawEditorPanel.qml").read_text(encoding="utf-8")
    for token in ("surface: theme.s1", "raised: theme.s2", "line: theme.line", "ink: theme.fg", "muted: theme.fg2", "subtle: theme.fg3", "well: theme.base"):
        assert token in panel, token
