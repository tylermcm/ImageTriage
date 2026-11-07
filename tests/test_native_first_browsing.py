"""Browsing the popout with the editor behind: the photo's own picture is on screen before PhotoCraft is
asked for anything, the editor catches up after a dwell (or at once when it is touched), and the picture
goes away only when the editor is showing the same photo."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QObject, QRect, QSize
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from image_triage import photocraft_bridge as bridge
from image_triage.models import ImageRecord
from image_triage.nav_timing import NavigationTimer
from image_triage.scanner import normalized_path_key


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def solid(width, height, color):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return image






@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "a.jpg"
    solid(80, 60, "red").save(str(path), "JPEG")
    return str(path)


# --- geometry ---------------------------------------------------------------------------------------

HOST = QRect(0, 0, 1000, 800)


















# --- the layer widget ---------------------------------------------------------------------------------









# --- the saved render the viewer prefers --------------------------------------------------------------

def touch(path: Path, when: float, content: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, (when, when))


def test_the_small_display_render_wins_over_the_full_size_png(tmp_path):
    source = str(tmp_path / "frame.nef")
    touch(bridge.rendered_preview_path(source), 1000)
    touch(bridge.display_render_path(source), 1010)
    assert bridge.saved_render_path(source) == bridge.display_render_path(source)


def test_a_photo_without_saved_edits_has_no_render(tmp_path):
    assert bridge.saved_render_path(str(tmp_path / "frame.nef")) is None


def test_edits_saved_before_display_renders_existed_fall_back_to_the_png(tmp_path):
    source = str(tmp_path / "frame.nef")
    touch(bridge.rendered_preview_path(source), 1000)
    assert bridge.saved_render_path(source) == bridge.rendered_preview_path(source)


def test_an_old_display_render_never_shadows_newer_edits(tmp_path):
    source = str(tmp_path / "frame.nef")
    touch(bridge.display_render_path(source), 1000)
    touch(bridge.rendered_preview_path(source), 1000 + bridge.DISPLAY_STALE_AFTER_S + 5)
    assert bridge.saved_render_path(source) == bridge.rendered_preview_path(source)


def test_an_empty_display_render_is_ignored(tmp_path):
    source = str(tmp_path / "frame.nef")
    touch(bridge.display_render_path(source), 1000, b"")
    touch(bridge.rendered_preview_path(source), 1000)
    assert bridge.saved_render_path(source) == bridge.rendered_preview_path(source)


# --- the popout ---------------------------------------------------------------------------------------



















# --- the controller -----------------------------------------------------------------------------------



def fast_proc(tmp_path):
    control = Mock()
    control.document_revision.return_value = 2
    control.call.return_value = {"job": 5, "pending": True}
    proc = bridge.PhotoCraftProcess(
        process=Mock(), control=control, hwnd=123, token_file=tmp_path / "token",
        read_root=tmp_path, write_root=tmp_path / ".image_triage_edits",
    )
    proc.process.poll.return_value = None
    proc.fast = Mock()
    proc.fast.call.return_value = {"jobs": []}
    return proc


























# --- timing and autostash -----------------------------------------------------------------------------









# --- the editor is resized while a picture is laid over it ---------------------------------------------





def drawn(width, height, view_height):
    return {"window": {"width": width, "height": height}, "canvas": {"x": 0, "y": 0, "width": width, "height": height},
            "cameraRaw": {"open": True, "view": {"x": 10, "y": 40, "width": width - 300, "height": view_height}}}










# --- the editor only when its key is pressed ---------------------------------------------------------





def requests_of(window):
    asked = []
    window.photocraft_edit_requested.connect(asked.append)
    return asked


def press(window, key):
    QTest.keyClick(window, key)
