"""Straighten, rotate and flip in the crop tool: what the editor shows, and where clicks land."""
import pytest
from PySide6.QtGui import QColor, QImage

from image_triage import raw_crop
from image_triage.qml_shell import raw_preview
from image_triage.qml_shell.raw_editor import RawEditor
from test_qml_shell import qapp, pump  # noqa: F401


@pytest.fixture
def raw(qapp):
    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    image = QImage(60, 40, QImage.Format_RGBA8888)
    image.fill(QColor("gray"))
    for x in range(8, 12):
        for y in range(4, 8):
            image.setPixelColor(x, y, QColor(255, 0, 0))
    frame = ({"histogram": [[0] * 256] * 3, "fullSize": [6000, 4000]}, image)
    raw._proxy_arrived(raw._revision, (frame, frame, 0, "sample.NEF"))
    yield raw
    raw.shutdown()


def shown(raw):
    """The picture QML would get for the editor's preview address."""
    from PySide6.QtCore import QSize

    size = QSize()
    return raw_preview._Images().requestImage(raw.preview.removeprefix("image://rawpreview/"), size, QSize())


def test_an_untouched_photo_is_addressed_and_sized_as_before(raw):
    assert "?" not in raw.preview and (raw.fullWidth, raw.fullHeight) == (6000, 4000)
    assert not raw.cropTurned and raw.cropAngle == 0
    assert raw.cropView == {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}


def test_rotate_turns_the_preview_and_swaps_the_reported_size(raw):
    raw.rotateCrop(1)
    assert raw.values["hostCrop"]["rotate"] == 90 and raw.cropTurned and raw.dirty
    assert (raw.fullWidth, raw.fullHeight) == (4000, 6000)
    picture = shown(raw)
    assert (picture.width(), picture.height()) == (40, 60)
    assert picture.pixelColor(40 - 6, 10).red() > 200                 # the marker went from top-left to top-right
    raw.rotateCrop(-1)
    assert "?" not in raw.preview and not raw.dirty                    # back where it was: nothing unsaved
    raw.undo()
    assert raw.values["hostCrop"]["rotate"] == 90                      # each press is an undo step


def test_flip_mirrors_what_is_shown_even_after_a_rotation(raw):
    raw.rotateCrop(1)
    before = shown(raw)
    raw.flipCrop(True)
    after = shown(raw)
    assert after == before.flipped(__import__("PySide6.QtCore", fromlist=["Qt"]).Qt.Orientation.Horizontal)
    raw.flipCrop(False)
    raw.flipCrop(False)
    assert shown(raw) == after


def test_straighten_shows_the_finished_picture_and_the_slider_follows_what_is_seen(raw):
    raw.setCropAngle(5)
    assert raw.cropAngle == 5 and raw.values["hostCrop"]["angle"] == 5
    width, height = raw.fullWidth, raw.fullHeight
    assert width < 6000 and width / height == pytest.approx(1.5, abs=0.01)
    assert (shown(raw).width(), shown(raw).height()) == raw_crop.turned_size(raw.values["hostCrop"], 60, 40)
    raw.flipCrop(True)                                                 # mirrored: the same tilt now turns the other way on screen
    assert raw.cropAngle == -5 and raw.values["hostCrop"]["angle"] == 5
    raw.setCropAngle(-2)
    assert raw.values["hostCrop"]["angle"] == 2
    raw.setCropAngle(400)
    assert raw.cropAngle == 45                                         # within the range, whatever is asked


def test_the_crop_tool_shows_the_whole_frame_turned_by_quarters_only(raw):
    raw.setCrop(0.1, 0.2, 0.3, 0.5)
    raw.rotateCrop(1)
    raw.setCropAngle(7)
    raw.setCropEditing(True)
    assert (raw.fullWidth, raw.fullHeight) == (4000, 6000)             # the whole frame on its side, not straightened
    assert "crop=" not in raw.preview and "turn=90,0,0,0.0" in raw.preview
    view = raw.cropView                                                # the rectangle where it is seen on the turned frame
    assert (view["x"], view["y"], view["w"], view["h"]) == pytest.approx((0.3, 0.1, 0.5, 0.3))
    raw.setCropView(0.0, 0.0, 0.5, 1.0)                                # drawn over the left half of what is shown
    crop = raw.values["hostCrop"]
    assert (crop["x"], crop["y"], crop["w"], crop["h"]) == pytest.approx((0.0, 0.5, 1.0, 0.5))   # the bottom half as recorded
    assert crop["rotate"] == 90 and crop["angle"] == 7                 # drawing keeps the turn


def test_a_ratio_is_of_the_picture_as_shown(raw):
    raw.rotateCrop(1)                                                  # shown 4000 x 6000
    raw.setCropRatio("1:1")
    crop = raw.values["hostCrop"]
    assert crop["w"] * 6000 == pytest.approx(crop["h"] * 4000)         # square in pixels
    raw.setCropRatio("3:2")                                            # wider than tall on screen
    view = raw.cropView
    assert (view["w"] * 4000) / (view["h"] * 6000) == pytest.approx(1.5)


def test_tools_that_draw_on_the_recorded_picture_show_it_not_turned(raw):
    raw.setCrop(0.5, 0.0, 0.5, 1.0)
    raw.rotateCrop(1)
    raw.setSourceView(True)
    assert "turn=" not in raw.preview and "crop=" in raw.preview
    assert (raw.fullWidth, raw.fullHeight) == (3000, 4000)
    assert raw.source_point(0.5, 0.5) == pytest.approx((0.75, 0.5))    # crop only, as the masks have always worked
    assert raw.maskEditor._source_point(0.5, 0.5) == pytest.approx((0.75, 0.5))
    raw.setSourceView(False)
    assert "turn=" in raw.preview and (raw.fullWidth, raw.fullHeight) == (4000, 3000)


def test_a_click_on_the_turned_picture_lands_on_the_right_place_in_the_recording(raw, monkeypatch):
    raw.setCrop(0.0, 0.0, 0.5, 1.0)                                    # the left half, which holds the marker
    raw.rotateCrop(1)
    raw.flipCrop(False)
    picture = shown(raw)
    spot = next((x, y) for x in range(picture.width()) for y in range(picture.height()) if picture.pixelColor(x, y).red() > 200 and picture.pixelColor(x, y).green() < 80)
    u, v = (spot[0] + 0.5) / picture.width(), (spot[1] + 0.5) / picture.height()
    x, y = raw.source_point(u, v)
    assert 8 / 60 <= x <= 12 / 60 and 4 / 40 <= y <= 8 / 40           # inside the marker, in the picture as recorded
    asked = []
    monkeypatch.setattr(raw, "_enqueue", lambda action, sample=None: asked.append((action, sample)))
    raw.pickWhiteBalance(u, v)
    assert asked[-1][0] == "whiteBalance" and asked[-1][1] == pytest.approx((x, y))
    monkeypatch.setattr(raw_preview, "executable", lambda: "helper")
    raw.pointColor.setPicking(True)
    raw.pointColor.pick(u, v)
    assert asked[-1][1]["area"][:2] == pytest.approx([x, y])


def test_reset_crop_also_undoes_the_turn_and_old_crops_still_work(raw):
    raw.rotateCrop(2); raw.setCropAngle(3)
    raw.resetCrop()
    assert not raw.cropTurned and "?" not in raw.preview
    raw._draft = {"hostCrop": {"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5, "ratio": "Free"}}       # saved before turning existed
    assert raw.values["hostCrop"]["rotate"] == 0 and raw.cropAngle == 0 and "crop=" in raw.preview and "turn=" not in raw.preview
    raw.flipCrop(True)
    assert raw.values["hostCrop"] == {"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5, "ratio": "Free", "rotate": 0, "flipH": True, "flipV": False, "angle": 0.0}


def test_the_recipe_with_a_turn_is_one_the_app_accepts(raw):
    from image_triage import photocraft_bridge as bridge

    raw.rotateCrop(1); raw.setCropAngle(-4); raw.flipCrop(True)
    assert bridge.classify_recipe({"version": bridge.RECIPE_VERSION, "cameraRaw": raw._draft})[0] != bridge.RECIPE_INVALID


# ---- in the real window -----------------------------------------------------------------------------------------------------------

from test_qml_shell_viewer import shell  # noqa: E402,F401


def test_the_crop_page_turns_the_photo_and_other_tools_switch_views(shell, qapp):
    from PySide6.QtCore import QObject
    from PySide6.QtQuick import QQuickItem

    engine, window, model, popout, warnings = shell
    popout.open(0)
    raw = popout.rawEditor
    raw.open("sample.NEF")
    image = QImage(60, 40, QImage.Format_RGBA8888)
    image.fill(QColor("gray"))
    frame = ({"histogram": [[0] * 256] * 3, "fullSize": [6000, 4000]}, image)
    raw._proxy_arrived(raw._revision, (frame, frame, 0, "sample.NEF"))
    pump(qapp, 0.4)
    dock = window.findChild(QQuickItem, "viewerRawEditorPanel")
    dock.setProperty("tool", "Crop")
    pump(qapp, 0.1)
    assert raw.cropEditing and not raw.sourceView

    def walk(node):
        yield node
        for child in node.childItems():
            yield from walk(child)

    def named(name):
        return next(node for node in walk(dock) if node.objectName() == name)

    named("rawCropRotateRight").clicked.emit()
    named("rawCropFlipH").clicked.emit()
    pump(qapp, 0.1)
    crop = raw.values["hostCrop"]
    assert crop["rotate"] == 90 and crop["flipH"] and (raw.fullWidth, raw.fullHeight) == (4000, 6000)
    slider = named("rawCropAngle_slider")
    slider.setProperty("value", 6)
    slider.moved.emit()
    assert raw.cropAngle == pytest.approx(6)
    dock.setProperty("straightening", True)                           # the slider is held: the finished picture, with the grid
    pump(qapp, 0.1)
    assert not raw.cropEditing and window.findChild(QObject, "rawStraightenGrid").property("visible")
    dock.setProperty("straightening", False)
    pump(qapp, 0.05)
    assert raw.cropEditing
    dock.setProperty("tool", "Mask")
    pump(qapp, 0.1)
    assert raw.sourceView and not raw.cropEditing and named("rawSourceViewNote").property("visible")
    assert (raw.fullWidth, raw.fullHeight) == (6000, 4000)             # as recorded while masking
    dock.setProperty("tool", "Adjust")
    pump(qapp, 0.1)
    assert not raw.sourceView and raw.fullWidth < raw.fullHeight       # the finished picture again, on its side
    raw.cancel()
    pump(qapp, 0.2)
    assert not [w for w in warnings if "RawEditorPanel.qml" in w or "RawCropOverlay.qml" in w or "TypeError" in w], warnings
