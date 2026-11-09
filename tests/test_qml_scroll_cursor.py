"""Native autoscroll artwork and cursor lifetime."""
import sys

import pytest
from PySide6.QtGui import QGuiApplication

from image_triage.qml_shell.scroll_cursor import ScrollCursor
from test_qml_shell import qapp  # noqa: F401


@pytest.mark.skipif(sys.platform != "win32", reason="Windows common-control cursor resources")
def test_native_scroll_cursors_load_and_restore_without_leaking_an_override(qapp):
    cursor = ScrollCursor(parent=qapp)
    assert cursor.native
    assert cursor.anchorSource.startswith("data:image/png;base64,")
    assert cursor.anchorWidth > 0 and cursor.anchorHeight > 0
    previous = QGuiApplication.overrideCursor()
    previous_key = previous.pixmap().cacheKey() if previous else None
    try:
        cursor.begin()
        cursor.begin()  # Idempotent: one matching end must restore the old cursor.
        neutral_key = QGuiApplication.overrideCursor().pixmap().cacheKey()
        cursor.direction(-1)
        assert QGuiApplication.overrideCursor().pixmap().cacheKey() != neutral_key
        cursor.direction(1)
        assert QGuiApplication.overrideCursor().pixmap().cacheKey() != neutral_key
        cursor.direction(0)
        assert QGuiApplication.overrideCursor().pixmap().cacheKey() == neutral_key
    finally:
        cursor.end()
        cursor.end()
    restored = QGuiApplication.overrideCursor()
    assert (restored.pixmap().cacheKey() if restored else None) == previous_key
