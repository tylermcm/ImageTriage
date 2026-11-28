"""A video clip in the library's viewer: it plays on the stage, opens paused, has a transport row above the filmstrip, steps frames,
and lets go of the file (no sound left playing) when the viewer moves to a photo or closes."""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest

from test_qml_shell import _load_main, pump, qapp  # noqa: F401

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def rows(tmp_path):
    from PIL import Image

    clip = shutil.copy(FIXTURES / "clip_landscape.mp4", tmp_path / "C0042.MP4")
    photo = tmp_path / "DSC_0001.jpg"
    Image.new("RGB", (64, 48), "teal").save(photo)
    base = {"decision": "", "rating": 0, "ai": -1, "raw": False}
    return [dict(base, path=str(clip), name="C0042.MP4", ext="MP4", video=True), dict(base, path=str(photo), name="DSC_0001.jpg", ext="JPG", video=False)]


def find(window, name):
    stack = [window.contentItem()]
    while stack:
        item = stack.pop()
        if item.objectName() == name:
            return item
        stack.extend(item.childItems())
    return None


def wait(qapp, condition, seconds=8.0):
    import time

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        pump(qapp, 0.05)
        if condition():
            return True
    return False


def test_a_clip_plays_in_the_viewer_with_its_own_transport(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    try:
        photos = engine.rootContext().contextProperty("photos")
        photos.setRows(rows(tmp_path), str(tmp_path))
        window.setWidth(1440)
        window.setHeight(900)
        window.show()
        popout = engine.rootContext().contextProperty("popout")
        popout.open(0)
        viewer = window.findChild(QObject, "viewer")
        stage = window.findChild(QObject, "videoStage")
        assert wait(qapp, lambda: stage.property("ready")), "the clip loads"
        assert wait(qapp, lambda: stage.isVisible()), "on the stage once the viewer has opened"
        assert viewer.property("isClip") and not stage.property("playing"), "it opens paused"
        transport = find(window, "viewerClipTransport")
        assert transport is not None and transport.isVisible()
        assert not find(window, "viewerZoomControls").isVisible(), "no zoom for a clip"
        assert 1900 <= stage.property("duration") <= 2100

        QTest.keyClick(window, Qt.Key_P)
        assert wait(qapp, lambda: stage.property("playing")), "P plays"
        QTest.keyClick(window, Qt.Key_P)
        assert wait(qapp, lambda: not stage.property("playing")), "and pauses"

        before = stage.property("position")
        QTest.keyClick(window, Qt.Key_Period)
        assert wait(qapp, lambda: stage.property("position") > before), ". steps a frame on"
        scrub = find(window, "viewerClipScrub")
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, scrub.mapToScene(QPointF(scrub.width() * 0.75, scrub.height() / 2)).toPoint())
        assert wait(qapp, lambda: abs(stage.property("position") - 0.75 * stage.property("duration")) < 120), "a click on the bar seeks"
        assert not stage.property("playing"), "a seek while paused stays paused"

        QTest.keyClick(window, Qt.Key_Right)                  # the next file, a photo: the clip is let go
        assert wait(qapp, lambda: not viewer.property("isClip"))
        assert stage.property("path") == "" and not transport.isVisible()
        assert not [w for w in warnings if "VideoStage" in w or "clipStage" in w or "ReferenceError" in w], warnings
    finally:
        window.close()
        engine.deleteLater()
        pump(qapp, 0.1)
